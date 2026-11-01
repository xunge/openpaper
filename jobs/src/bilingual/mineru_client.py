"""MinerU 4.1 client: file-parse with a tasks-API fallback.

MinerU exposes two ways to parse a PDF:

* ``POST /v1/file_parse`` (port 8003 in this deployment) accepts multipart and
  either returns the result ZIP directly or ``202`` plus a status URL.
* ``POST /v1/tasks`` (port 8000) is the asynchronous contract: create a task,
  PUT the bytes to the returned upload URL, poll the task, download ``result_url``.

The two have different failure shapes, so this module keeps the HTTP concerns
(protocol, polling, retries, size limits, ZIP safety) away from the layout and
Markdown code. It never submits the same PDF twice: a timed-out file-parse request
is recovered by its client-generated ``request_id``.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

DEFAULT_FILE_PARSE_URL = "http://127.0.0.1:8003"
DEFAULT_TASKS_API_URL = "http://127.0.0.1:8000"

REQUIRED_MEMBERS = ("markdown.md", "middle_json.json", "structured_content.json")
_ASSET_RE = re.compile(r"^images/[A-Za-z0-9._/-]+\.(png|jpe?g|webp)$", re.IGNORECASE)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class MineruError(RuntimeError):
    """A MinerU request failed in a way the caller must report."""


class MineruUnavailable(MineruError):
    """The file-parse wrapper is not deployed; the tasks API may still work."""


@dataclass(frozen=True)
class MineruConfig:
    file_parse_url: str = DEFAULT_FILE_PARSE_URL
    tasks_api_url: str = DEFAULT_TASKS_API_URL
    api_mode: str = "auto"  # auto | file_parse | tasks
    tier: str = "standard"
    ocr_mode: str = "auto"
    request_timeout: float = 900.0
    connect_timeout: float = 10.0
    poll_interval: float = 2.0
    task_timeout: float = 1800.0
    file_wait_timeout: int = 600
    max_pdf_bytes: int = 100 * 1024 * 1024
    max_zip_bytes: int = 512 * 1024 * 1024
    max_uncompressed_bytes: int = 1024 * 1024 * 1024
    max_zip_entries: int = 2000
    max_asset_bytes: int = 64 * 1024 * 1024
    upload_timeout: float = 300.0
    retries: int = 3

    @classmethod
    def from_env(cls) -> "MineruConfig":
        def _float(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, str(default)))
            except ValueError:
                return default

        def _int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)))
            except ValueError:
                return default

        return cls(
            file_parse_url=os.getenv("MINERU_FILE_PARSE_URL", DEFAULT_FILE_PARSE_URL).rstrip("/"),
            tasks_api_url=os.getenv("MINERU_TASKS_API_URL", DEFAULT_TASKS_API_URL).rstrip("/"),
            api_mode=os.getenv("MINERU_API_MODE", "auto").strip().lower(),
            tier=os.getenv("MINERU_TIER", "standard").strip(),
            ocr_mode=os.getenv("MINERU_OCR_MODE", "auto").strip(),
            request_timeout=_float("MINERU_REQUEST_TIMEOUT", 900.0),
            connect_timeout=_float("MINERU_CONNECT_TIMEOUT", 10.0),
            poll_interval=_float("MINERU_POLL_INTERVAL", 2.0),
            task_timeout=_float("MINERU_TASK_TIMEOUT", 1800.0),
            file_wait_timeout=_int("MINERU_FILE_WAIT_TIMEOUT", 600),
            max_pdf_bytes=_int("MINERU_MAX_PDF_MB", 100) * 1024 * 1024,
            max_zip_bytes=_int("MINERU_MAX_ZIP_MB", 512) * 1024 * 1024,
            max_uncompressed_bytes=_int("MINERU_MAX_UNCOMPRESSED_MB", 1024) * 1024 * 1024,
            max_zip_entries=_int("MINERU_MAX_ZIP_ENTRIES", 2000),
            max_asset_bytes=_int("MINERU_MAX_ASSET_MB", 64) * 1024 * 1024,
            upload_timeout=_float("MINERU_UPLOAD_TIMEOUT", 300.0),
            retries=_int("MINERU_HTTP_RETRIES", 3),
        )


def _rewrite_loopback(url: str, base: str) -> str:
    """Rewrite MinerU's 127.0.0.1 links to the host we actually reached.

    The 8003 wrapper builds status/result URLs from the request host, but a
    directly addressed service can still echo `http://127.0.0.1:...`. Inside a
    worker container that address is the worker, not the host. Keep external CDN
    URLs untouched.
    """
    try:
        parts = urlsplit(url)
        base_parts = urlsplit(base)
    except ValueError:
        return url
    loopback = {"127.0.0.1", "localhost", "::1"}
    if parts.hostname in loopback and base_parts.hostname not in loopback:
        return urlunsplit(
            (parts.scheme, base_parts.netloc, parts.path, parts.query, parts.fragment)
        )
    return url


def _safe_member(name: str) -> bool:
    """Reject absolute paths, traversal and Windows drive names."""
    if not name or "\\" in name or "\x00" in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        return False
    if any(":" in part for part in path.parts):
        return False
    return True


@dataclass
class MineruZip:
    """A validated, in-memory MinerU result ZIP.

    Extraction is never done to disk: only the required members and images are
    read, with per-member and total limits. This removes ZIP-slip as a class.
    """

    data: bytes
    config: MineruConfig
    _zip: zipfile.ZipFile = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.data) > self.config.max_zip_bytes:
            raise MineruError("MinerU result ZIP exceeds the configured size limit")
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(self.data))
        except zipfile.BadZipFile as exc:
            raise MineruError(f"MinerU returned an invalid ZIP: {exc}") from exc

        infos = self._zip.infolist()
        if len(infos) > self.config.max_zip_entries:
            raise MineruError("MinerU ZIP has too many entries")
        total = 0
        for info in infos:
            if not _safe_member(info.filename):
                raise MineruError(f"unsafe path in MinerU ZIP: {info.filename!r}")
            if info.is_dir():
                continue
            if info.file_size > self.config.max_asset_bytes:
                raise MineruError(
                    f"ZIP member {info.filename!r} exceeds the per-file limit"
                )
            if info.compress_size and info.file_size > 10 * 1024 * 1024:
                if info.file_size / info.compress_size > 1000:
                    raise MineruError(
                        f"ZIP member {info.filename!r} has an implausible compression ratio"
                    )
            total += info.file_size
        if total > self.config.max_uncompressed_bytes:
            raise MineruError("MinerU ZIP is too large after decompression")

    @property
    def names(self) -> list[str]:
        return [info.filename for info in self._zip.infolist() if not info.is_dir()]

    def has(self, name: str) -> bool:
        try:
            self._zip.getinfo(name)
            return True
        except KeyError:
            return False

    def read(self, name: str, limit: Optional[int] = None) -> bytes:
        try:
            info = self._zip.getinfo(name)
        except KeyError as exc:
            raise MineruError(f"MinerU ZIP is missing {name!r}") from exc
        if info.is_dir():
            raise MineruError(f"{name!r} is a directory in the MinerU ZIP")
        cap = limit or self.config.max_asset_bytes
        if info.file_size > cap:
            raise MineruError(f"{name!r} exceeds the read limit")
        data = self._zip.read(info)
        if len(data) > cap:
            raise MineruError(f"{name!r} decompressed past the read limit")
        return data

    def read_json(self, name: str) -> Dict[str, Any]:
        raw = self.read(name, limit=128 * 1024 * 1024)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MineruError(f"{name!r} is not valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise MineruError(f"{name!r} must contain a JSON object")
        return value

    def iter_assets(self) -> Iterable[tuple[str, bytes]]:
        for name in self.names:
            if not _ASSET_RE.match(name):
                continue
            yield name, self.read(name)


class MineruClient:
    """HTTP client with explicit polling and bounded retries."""

    def __init__(
        self,
        config: Optional[MineruConfig] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.config = config or MineruConfig.from_env()
        self.session = session or requests.Session()
        # Deliberately no automatic POST retries: a retried create can start a
        # second parse. GETs are retried by `_get` instead.
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    # ---------------------------------------------------------------- helpers
    @property
    def _timeout(self) -> tuple[float, float]:
        return (self.config.connect_timeout, self.config.request_timeout)

    def _sleep(self, seconds: Optional[float] = None) -> None:
        time.sleep(seconds if seconds is not None else self.config.poll_interval)

    def _get(self, url: str, *, timeout: Optional[tuple[float, float]] = None) -> requests.Response:
        last: Optional[Exception] = None
        for attempt in range(1, self.config.retries + 1):
            try:
                response = self.session.get(
                    url, timeout=timeout or (self.config.connect_timeout, 60)
                )
                if response.status_code in (429, 500, 502, 503, 504):
                    last = MineruError(
                        f"GET {url} returned {response.status_code}"
                    )
                    self._sleep(min(10, 2 ** attempt))
                    continue
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last = exc
                if attempt < self.config.retries:
                    self._sleep(min(10, 2 ** attempt))
        raise MineruError(f"GET {url} failed after retries: {last}")

    def _download_zip(self, url: str, headers: Optional[Dict[str, str]] = None) -> MineruZip:
        try:
            response = self.session.get(
                url,
                stream=True,
                timeout=(self.config.connect_timeout, self.config.request_timeout),
                headers=headers or {},
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise MineruError(f"downloading MinerU result failed: {exc}") from exc

        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.config.max_zip_bytes:
            raise MineruError("MinerU result download is larger than the configured limit")
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > self.config.max_zip_bytes:
                    raise MineruError("MinerU result download exceeded the size limit")
                chunks.append(chunk)
        finally:
            response.close()
        logger.info("downloaded MinerU result ZIP (%d bytes)", total)
        return MineruZip(b"".join(chunks), self.config)

    def _status_error(self, data: Dict[str, Any]) -> str:
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("detail") or err)
        return str(err or data.get("status") or "unknown error")

    # ----------------------------------------------------------- file parse
    def _parse_file_parse(self, pdf_bytes: bytes, file_name: str) -> MineruZip:
        request_id = uuid.uuid4().hex
        if not _REQUEST_ID_RE.match(request_id):  # pragma: no cover - uuid is safe
            request_id = "req" + request_id
        logger.info(
            "MinerU file_parse submit request_id=%s size=%d", request_id, len(pdf_bytes)
        )
        data = {
            "tier": self.config.tier,
            "ocr_mode": self.config.ocr_mode,
            "output_formats": "default",
            "request_id": request_id,
            "wait_timeout": str(self.config.file_wait_timeout),
        }
        try:
            response = self.session.post(
                f"{self.config.file_parse_url}/v1/file_parse",
                files={"file": (file_name, pdf_bytes, "application/pdf")},
                data=data,
                timeout=(self.config.connect_timeout, self.config.request_timeout),
            )
        except (requests.ConnectionError, requests.ConnectTimeout) as exc:
            raise MineruUnavailable(f"file_parse is not reachable: {exc}") from exc
        except requests.ReadTimeout:
            logger.warning(
                "file_parse timed out after submit; recovering request_id=%s", request_id
            )
            return self._poll_file_parse(request_id, None)
        except requests.RequestException as exc:
            raise MineruError(f"file_parse request failed: {exc}") from exc

        if response.status_code == 200:
            content_type = response.headers.get("content-type", "")
            if "zip" not in content_type.lower() and not response.content.startswith(b"PK"):
                raise MineruError(
                    f"file_parse returned 200 with unexpected content-type {content_type!r}"
                )
            return MineruZip(response.content, self.config)
        if response.status_code == 202:
            try:
                payload = response.json()
            except ValueError as exc:
                raise MineruError("file_parse returned 202 without JSON") from exc
            return self._poll_file_parse(request_id, payload.get("status_url"))
        if response.status_code in (404, 405):
            raise MineruUnavailable(
                f"file_parse is not available (HTTP {response.status_code})"
            )
        raise MineruError(
            f"file_parse failed (HTTP {response.status_code}): {response.text[:300]}"
        )

    def _poll_file_parse(
        self, request_id: str, status_url: Optional[str]
    ) -> MineruZip:
        url = status_url or f"{self.config.file_parse_url}/v1/file_parse/{request_id}"
        url = _rewrite_loopback(
            urljoin(self.config.file_parse_url + "/", url), self.config.file_parse_url
        )
        deadline = time.monotonic() + self.config.task_timeout
        while time.monotonic() < deadline:
            try:
                response = self._get(url, timeout=(self.config.connect_timeout, 60))
                payload = response.json()
            except (ValueError, MineruError) as exc:
                logger.warning("file_parse status check failed: %s", exc)
                self._sleep()
                continue
            status = str(payload.get("status", "")).lower()
            logger.info("MinerU file_parse %s status=%s", request_id, status)
            if status in {"completed", "partial", "success", "succeeded"}:
                result_url = payload.get("result_url") or (
                    f"{self.config.file_parse_url}/v1/file_parse/{request_id}/result"
                )
                result_url = _rewrite_loopback(
                    urljoin(self.config.file_parse_url + "/", result_url),
                    self.config.file_parse_url,
                )
                return self._download_zip(result_url)
            if status in {"failed", "canceled", "cancelled", "error"}:
                raise MineruError(
                    f"MinerU file_parse failed ({status}): {self._status_error(payload)}"
                )
            self._sleep()
        raise MineruError(
            f"MinerU file_parse timed out after {self.config.task_timeout:.0f}s"
        )

    # --------------------------------------------------------------- tasks
    def _parse_tasks(self, pdf_bytes: bytes, file_name: str) -> MineruZip:
        logger.info("MinerU tasks API submit file=%s size=%d", file_name, len(pdf_bytes))
        body = {
            "tier": self.config.tier,
            "ocr_mode": self.config.ocr_mode,
            "output_formats": ["default"],
            "files": [{"file_name": file_name, "bytes": len(pdf_bytes)}],
        }
        try:
            response = self.session.post(
                f"{self.config.tasks_api_url}/v1/tasks",
                json=body,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise MineruError(f"tasks API create failed: {exc}") from exc
        if response.status_code not in (200, 201, 202):
            raise MineruError(
                f"tasks API create failed (HTTP {response.status_code}): {response.text[:300]}"
            )
        try:
            created = response.json()
        except ValueError as exc:
            raise MineruError("tasks API create returned invalid JSON") from exc

        task_id = created.get("task_id")
        files = created.get("files") or []
        if not task_id or not files:
            raise MineruError("tasks API create response has no task_id/files")
        task_file = files[0]
        file_id = task_file.get("file_id")
        upload = task_file.get("upload") or {}
        if upload.get("url"):
            self._upload_file(upload, pdf_bytes)
        elif task_file.get("status") == "waiting-file":
            raise MineruError("tasks API requested a file but returned no upload URL")
        return self._poll_task(task_id, file_id)

    def _upload_file(self, upload: Dict[str, Any], pdf_bytes: bytes) -> None:
        url = _rewrite_loopback(
            str(upload.get("url")), self.config.tasks_api_url
        )
        if not url.startswith(("http://", "https://")):
            raise MineruError("tasks API returned an unsafe upload URL")
        headers = {str(k): str(v) for k, v in (upload.get("headers") or {}).items()}
        last: Optional[Exception] = None
        for attempt in range(1, 3):
            try:
                response = self.session.put(
                    url,
                    data=pdf_bytes,
                    headers=headers,
                    timeout=(self.config.connect_timeout, self.config.upload_timeout),
                )
                if response.status_code in (429, 500, 502, 503, 504):
                    last = MineruError(f"upload returned {response.status_code}")
                    self._sleep(min(10, 2 ** attempt))
                    continue
                response.raise_for_status()
                logger.info("uploaded PDF to MinerU task storage")
                return
            except requests.RequestException as exc:
                last = exc
                if attempt < 2:
                    self._sleep(2)
        raise MineruError(f"uploading the PDF to MinerU failed: {last}")

    def _poll_task(self, task_id: str, file_id: Optional[str]) -> MineruZip:
        deadline = time.monotonic() + self.config.task_timeout
        while time.monotonic() < deadline:
            try:
                response = self._get(
                    f"{self.config.tasks_api_url}/v1/tasks/{task_id}",
                    timeout=(self.config.connect_timeout, 60),
                )
                payload = response.json()
            except (ValueError, MineruError) as exc:
                logger.warning("tasks API poll failed: %s", exc)
                self._sleep()
                continue
            status = str(payload.get("status", "")).lower()
            progress = payload.get("progress") or {}
            logger.info(
                "MinerU task %s status=%s progress=%s/%s",
                task_id,
                status,
                progress.get("completed"),
                progress.get("total"),
            )
            if status in {"failed", "canceled", "cancelled"}:
                raise MineruError(
                    f"MinerU task failed ({status}): {self._status_error(payload)}"
                )
            files = payload.get("files") or []
            target = None
            for item in files:
                if file_id is None or item.get("file_id") == file_id:
                    target = item
                    break
            if target is None and files:
                target = files[0]
            if target and target.get("result_url") and str(target.get("status")).lower() in {
                "completed",
                "partial",
            }:
                url = _rewrite_loopback(
                    urljoin(self.config.tasks_api_url + "/", str(target["result_url"])),
                    self.config.tasks_api_url,
                )
                return self._download_zip(url)
            if status in {"completed", "partial"} and target is None:
                raise MineruError("MinerU task completed without a result file")
            self._sleep()
        raise MineruError(
            f"MinerU task {task_id} timed out after {self.config.task_timeout:.0f}s"
        )

    # ---------------------------------------------------------------- public
    def parse(self, pdf_bytes: bytes, file_name: str) -> MineruZip:
        if not pdf_bytes:
            raise MineruError("refusing to submit an empty PDF")
        if len(pdf_bytes) > self.config.max_pdf_bytes:
            raise MineruError(
                f"PDF is larger than the configured MinerU limit "
                f"({len(pdf_bytes)} > {self.config.max_pdf_bytes})"
            )
        safe_name = os.path.basename(file_name) or "paper.pdf"
        if not safe_name.lower().endswith(".pdf"):
            safe_name += ".pdf"

        mode = self.config.api_mode
        if mode == "tasks":
            return self._parse_tasks(pdf_bytes, safe_name)
        if mode == "file_parse":
            return self._parse_file_parse(pdf_bytes, safe_name)
        try:
            return self._parse_file_parse(pdf_bytes, safe_name)
        except MineruUnavailable as exc:
            logger.warning("MinerU file_parse unavailable, falling back to tasks API: %s", exc)
            return self._parse_tasks(pdf_bytes, safe_name)
