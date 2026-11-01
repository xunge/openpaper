"""Export the final OCR reading result as Markdown, ZIP or DOCX.

The source of truth is the same block list the bilingual reader renders:
`paper_documents.pages[].blocks[]`. MinerU's original `markdown.md` is never used
here, and no OCR is re-run.

Images are read from the configured object store by S3 key. A legacy
`image_url` is accepted only when its host matches this deployment's S3 public
endpoint and its path can be mapped back to a bucket key; arbitrary external
URLs are rejected, so Pandoc never fetches the network.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlsplit

from app.helpers.s3 import s3_service

logger = logging.getLogger(__name__)

DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
MAX_ASSET_BYTES = int(os.getenv("EXPORT_MAX_ASSET_MB", "64")) * 1024 * 1024
MAX_TOTAL_BYTES = int(os.getenv("EXPORT_MAX_TOTAL_MB", "512")) * 1024 * 1024
PANDOC_TIMEOUT = int(os.getenv("EXPORT_PANDOC_TIMEOUT", "120"))
PANDOC_CONCURRENCY = max(1, int(os.getenv("EXPORT_PANDOC_CONCURRENCY", "2")))
_PANDOC_SLOTS = threading.BoundedSemaphore(PANDOC_CONCURRENCY)
_SAFE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
_ALT = {
    "table": "Table",
    "algorithm": "Algorithm",
    "code": "Code",
    "figure": "Figure",
    "image": "Figure",
    "chart": "Figure",
}


class ExportError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _safe_key(key: Any) -> bool:
    if not isinstance(key, str) or not key.strip():
        return False
    if key.startswith("/") or "\\" in key or "\x00" in key:
        return False
    if any(part in {"", ".", ".."} for part in key.split("/")):
        return False
    return True


def _legacy_image_key(url: str) -> Optional[str]:
    """Map a trusted S3/public object URL back to an object key."""
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return None
    trusted_hosts = set()
    for env_name in ("S3_ENDPOINT_URL", "S3_PUBLIC_URL"):
        raw = os.getenv(env_name, "")
        host = urlsplit(raw).hostname if raw else None
        if host:
            trusted_hosts.add(host)
    if not parts.hostname or parts.hostname not in trusted_hosts:
        return None
    path = unquote(parts.path).lstrip("/")
    prefix = f"{s3_service.bucket_name}/"
    if not path.startswith(prefix):
        return None
    key = path[len(prefix) :]
    return key if _safe_key(key) else None


def block_image_key(block: Dict[str, Any]) -> Optional[str]:
    key = block.get("image_key")
    if isinstance(key, str) and key.strip():
        if not _safe_key(key):
            raise ExportError(500, "OCR image key is unsafe")
        return key
    legacy = block.get("image_url")
    if isinstance(legacy, str) and legacy.strip():
        mapped = _legacy_image_key(legacy)
        if mapped is None:
            # Old records can point at an upstream URL that is not this
            # deployment's storage. Skip it rather than fetching an arbitrary
            # external URL or failing the whole text export.
            logger.warning("skipping untrusted legacy OCR image URL")
            return None
        return mapped
    return None


def _asset_name(key: str, used: Dict[str, str]) -> str:
    base = key.rsplit("/", 1)[-1] or "image.png"
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "image.png"
    stem, ext = os.path.splitext(base)
    if ext.lower() not in _SAFE_EXT:
        ext = ".png"
    candidate = f"{stem}{ext}"
    index = 2
    while candidate in used and used[candidate] != key:
        candidate = f"{stem}_{index}{ext}"
        index += 1
    used[candidate] = key
    return candidate


def build_markdown(
    document: Any, lang: str = "zh", image_attrs: bool = False
) -> Tuple[str, Dict[str, str]]:
    """Render the final reading Markdown and its referenced assets.

    Returns `(markdown, {asset_filename: object_key})`. Image paths are
    `assets/<name>` so they work both in the ZIP and as Pandoc resource paths.
    """
    if lang not in {"zh", "en"}:
        raise ExportError(400, "lang must be 'zh' or 'en'")

    parts: List[str] = []
    assets: Dict[str, str] = {}
    used_names: Dict[str, str] = {}
    pages = sorted(document.pages or [], key=lambda page: page.get("index", 0))

    for page in pages:
        for block in page.get("blocks", []):
            key = block_image_key(block)
            if key is not None:
                name = _asset_name(key, used_names)
                assets[name] = key
                alt = _ALT.get(str(block.get("label") or "image"), "Figure")
                attrs = "{width=6.5in}" if image_attrs else ""
                parts.append(f"![{alt}](assets/{name}){attrs}")
                continue

            primary = "zh_md" if lang == "zh" else "en_md"
            text = (block.get(primary) or "").strip()
            if not text:
                # A partially translated document still exports its other side.
                other = "en_md" if lang == "zh" else "zh_md"
                text = (block.get(other) or "").strip()
            if text:
                parts.append(text)

    markdown = "\n\n".join(part for part in parts if part).strip()
    if not markdown:
        raise ExportError(409, "This OCR document has no content to export")
    return markdown + "\n", assets


def collect_assets(assets: Dict[str, str]) -> Dict[str, bytes]:
    files: Dict[str, bytes] = {}
    total = 0
    for name, key in assets.items():
        try:
            data = s3_service.download_bytes(key)
        except Exception as exc:
            logger.error("export asset %s could not be read: %s", key, exc)
            raise ExportError(409, f"OCR image is missing from storage: {name}") from exc
        if len(data) > MAX_ASSET_BYTES:
            raise ExportError(413, f"OCR image is too large: {name}")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ExportError(413, "OCR image export exceeds the total size limit")
        files[name] = data
    return files


def build_zip(markdown: str, asset_files: Dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("content.md", markdown.encode("utf-8"))
        for name in sorted(asset_files):
            archive.writestr(f"assets/{name}", asset_files[name])
    return buffer.getvalue()


def _pandoc_path() -> str:
    configured = os.getenv("PANDOC_PATH")
    if configured and os.path.exists(configured):
        return configured
    try:
        import pypandoc  # type: ignore

        path = pypandoc.get_pandoc_path()
        if path and os.path.exists(path):
            return str(path)
    except Exception:
        pass
    found = shutil.which("pandoc")
    if found:
        return found
    raise ExportError(
        503,
        "Pandoc is not available in the server image; rebuild the server image",
    )


def convert_docx(markdown: str, asset_files: Dict[str, bytes]) -> bytes:
    """Convert the final Markdown to DOCX with the bundled Pandoc binary."""
    pandoc = _pandoc_path()
    with tempfile.TemporaryDirectory(prefix="openpaper-export-") as tmp:
        content_path = os.path.join(tmp, "content.md")
        assets_dir = os.path.join(tmp, "assets")
        os.makedirs(assets_dir, exist_ok=True)
        with open(content_path, "w", encoding="utf-8") as handle:
            handle.write(markdown)
        for name, data in asset_files.items():
            # `name` is generated by _asset_name; reject any separators before
            # writing below the temporary assets directory.
            if "/" in name or "\\" in name or name in {"", ".", ".."}:
                raise ExportError(500, "unsafe export asset name")
            with open(os.path.join(assets_dir, name), "wb") as handle:
                handle.write(data)

        output_path = os.path.join(tmp, "export.docx")
        command = [
            pandoc,
            "content.md",
            "-f",
            "markdown+tex_math_dollars+link_attributes+pipe_tables",
            "-t",
            "docx",
            f"--resource-path={tmp}",
            "-o",
            "export.docx",
        ]
        try:
            with _PANDOC_SLOTS:
                completed = subprocess.run(
                    command,
                    cwd=tmp,
                    capture_output=True,
                    text=True,
                    timeout=PANDOC_TIMEOUT,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise ExportError(504, "Word conversion timed out") from exc
        except OSError as exc:
            raise ExportError(503, f"Could not run Pandoc: {exc}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[-600:]
            logger.error("pandoc DOCX conversion failed: %s", detail)
            raise ExportError(422, f"Word conversion failed: {detail or 'unknown error'}")
        if not os.path.exists(output_path):
            raise ExportError(500, "Word conversion produced no file")
        with open(output_path, "rb") as handle:
            data = handle.read()
        if not data.startswith(b"PK"):
            raise ExportError(500, "Word conversion produced an invalid DOCX")
        return data


def docx_cache_key(
    document: Any, lang: str, markdown: str, assets: Dict[str, str]
) -> str:
    payload = "\0".join(
        [
            lang,
            markdown,
            str(getattr(document, "updated_at", "")),
            *[f"{name}:{key}" for name, key in sorted(assets.items())],
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return (
        f"papers/{document.paper_id}/ocr/{document.id}/exports/"
        f"{lang}-{digest}.docx"
    )


def get_or_build_docx(
    document: Any, lang: str, markdown: str, assets: Dict[str, str]
) -> bytes:
    cache_key = docx_cache_key(document, lang, markdown, assets)
    try:
        s3_service.s3_client.head_object(
            Bucket=s3_service.bucket_name, Key=cache_key
        )
        cached = s3_service.download_bytes(cache_key)
        if cached.startswith(b"PK"):
            return cached
    except Exception:
        # Cache is an optimisation; a missing/unreadable object is not fatal.
        pass

    asset_files = collect_assets(assets)
    data = convert_docx(markdown, asset_files)
    try:
        s3_service.upload_bytes(cache_key, data, DOCX_MIME)
    except Exception as exc:
        logger.warning("could not cache DOCX export %s: %s", cache_key, exc)
    return data


def content_disposition(title: str, extension: str) -> str:
    base = re.sub(r'[\r\n"\\/:*?<>|]+', " ", title or "").strip() or "paper"
    base = base[:120]
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "paper"
    quoted = quote(f"{base}{extension}", safe="")
    return (
        f'attachment; filename="{ascii_name}{extension}"; '
        f"filename*=UTF-8''{quoted}"
    )
