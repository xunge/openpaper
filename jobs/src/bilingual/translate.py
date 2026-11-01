"""English -> Chinese translation of OCR blocks.

Talks to DeepSeek directly instead of going through the shared LLM wrapper: the
wrapper has no temperature knob, and at the provider default the model degenerates
into repeating the same sentence for long block batches. Deterministic decoding
plus a repetition guard keeps the output usable.
"""

import hashlib
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

import requests

logger = logging.getLogger(__name__)

API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
MODEL = os.getenv("DEEPSEEK_FAST_MODEL", "deepseek-flash")
TIMEOUT_S = float(os.getenv("TRANSLATE_TIMEOUT_S", "120"))

SYSTEM_PROMPT = (
    "你是学术论文的翻译引擎，负责把英文论文内容译成简体中文。"
    "只输出译文本身，不要解释、不要总结、不要加任何前后缀。"
)

BATCH_PROMPT = """把下面编号的论文片段逐一翻译成简体中文。

规则：
1. 只翻译自然语言。公式、代码、表格数字、引用编号、图号、URL、DOI 原样保留。
2. 严格保持每段的 markdown 结构，表格行列数不变。
3. 专业术语首次出现写成「中文（English）」，之后只用中文。
4. 已经是中文的片段原样返回。
5. 输出格式严格为：每段以 `@@BLOCK n@@` 开头，按原顺序返回，不要合并、不要遗漏、不要增加说明。

{context}{blocks}"""

CONTEXT_HEADER = "\n## 上文（仅用于衔接，不要翻译、不要输出）\n{context}\n"

BATCH_MARKER = "@@BLOCK {n}@@"
BATCH_CHAR_LIMIT = 1200
MAX_WORKERS = 4

_CACHE: Dict[str, str] = {}


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _is_degenerate(text: str, source: str | None = None) -> bool:
    """Detect the repetition loop the model falls into on long inputs.

    Three independent signals, because the loop does not always produce sentence
    punctuation: repeated sentences, a repeated long n-gram, and an output far
    longer than its source.
    """
    if source and len(source) > 80 and len(text) > len(source) * 3.5:
        return True

    sentences = [s for s in re.split(r"[。！？\n]", text) if len(s.strip()) > 12]
    if len(sentences) >= 4:
        unique = {s.strip() for s in sentences}
        if len(unique) / len(sentences) < 0.55:
            return True

    # n-gram repetition: any 16-char window appearing three or more times.
    window = 16
    seen: dict[str, int] = {}
    for start in range(0, max(0, len(text) - window), 8):
        gram = text[start:start + window]
        seen[gram] = seen.get(gram, 0) + 1
        if seen[gram] >= 3:
            return True
    return False


def _post_process(source: str, translated: str) -> str:
    text = translated.strip()
    if not source.strip().startswith("---"):
        text = re.sub(r"^---+\s*\n", "", text)
        text = re.sub(r"\n\s*---+$", "", text)
    return text.strip()


def _call(prompt: str, model: str | None = None, max_tokens: int | None = None) -> str:
    payload = {
        "model": model or MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "top_p": 1,
        "frequency_penalty": 0.2,
        "max_tokens": max_tokens or 4096,
    }
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    response = requests.post(
        f"{BASE_URL}/chat/completions", json=payload, headers=headers, timeout=TIMEOUT_S
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"] or ""


def batch_blocks(blocks: List[str], limit: int = BATCH_CHAR_LIMIT) -> List[List[int]]:
    groups: List[List[int]] = []
    current: List[int] = []
    size = 0
    for index, block in enumerate(blocks):
        if not block.strip() or _has_cjk(block):
            continue
        if current and size + len(block) > limit:
            groups.append(current)
            current = []
            size = 0
        current.append(index)
        size += len(block)
    if current:
        groups.append(current)
    return groups


def _translate_one(block: str) -> str:
    text = _post_process(
        block,
        _call(BATCH_PROMPT.format(context="", blocks=f"{BATCH_MARKER.format(n=1)}\n{block}")),
    )
    marker = BATCH_MARKER.format(n=1)
    if text.startswith(marker):
        text = text[len(marker):].strip()
    return text


def _translate_group(blocks: List[str], indices: List[int]) -> List[tuple[int, str]]:
    done = {i: _CACHE[_cache_key(blocks[i])] for i in indices if _cache_key(blocks[i]) in _CACHE}
    pending = [i for i in indices if i not in done]
    if not pending:
        return list(done.items())

    rendered = "\n\n".join(
        f"{BATCH_MARKER.format(n=position)}\n{blocks[index]}"
        for position, index in enumerate(pending, start=1)
    )
    try:
        text = _call(BATCH_PROMPT.format(blocks=rendered))
        parts = re.split(r"@@BLOCK\s+(\d+)@@", text)
        found: Dict[int, str] = {}
        for position in range(1, len(parts), 2):
            number = int(parts[position])
            body = parts[position + 1] if position + 1 < len(parts) else ""
            if 1 <= number <= len(pending):
                candidate = _post_process(blocks[pending[number - 1]], body)
                if candidate and not _is_degenerate(candidate, blocks[pending[number - 1]]):
                    found[number] = candidate
        for position, index in enumerate(pending, start=1):
            value = found.get(position)
            if value:
                _CACHE[_cache_key(blocks[index])] = value
                done[index] = value
            elif not _has_cjk(blocks[index]):
                # Batch output was missing or degenerate: retry this block alone.
                try:
                    single = _translate_one(blocks[index])
                    if single and not _is_degenerate(single, blocks[index]):
                        _CACHE[_cache_key(blocks[index])] = single
                        done[index] = single
                except Exception as exc:
                    logger.warning("single-block retry failed: %s", exc)
    except Exception as exc:
        logger.warning("batched translation failed (%d blocks): %s", len(pending), exc)

    return list(done.items())


def translate_blocks(
    blocks: List[str], operations=None, contexts: List[str] | None = None
) -> List[str]:
    """Translate every block; untranslated blocks keep their original text.

    `contexts` optionally carries the preceding source text for each block, so a
    sentence that runs across a page break is translated with its continuation in
    view instead of restarting cold on the next page.
    """
    if contexts is not None:
        return _translate_with_context(blocks, contexts)
    result = list(blocks)
    if not API_KEY:
        logger.warning("DEEPSEEK_API_KEY missing; skipping translation")
        return result

    groups = batch_blocks(blocks)
    if not groups:
        return result

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(_translate_group, blocks, group) for group in groups]
        for future in futures:
            for index, translated in future.result():
                result[index] = translated

    # Final guard: no matter which path produced a block, a repeating one must
    # not reach the reader. Keeping the English source is more useful than a
    # paragraph of the same sentence eight times.
    reverted = 0
    for index, translated in enumerate(result):
        if translated != blocks[index] and _is_degenerate(translated, blocks[index]):
            result[index] = blocks[index]
            reverted += 1
    if reverted:
        logger.warning("reverted %d degenerate translation block(s)", reverted)
    return result


def _translate_with_context(blocks: List[str], contexts: List[str]) -> List[str]:
    """One request per block, carrying the tail of the previous source as context."""
    result = list(blocks)
    if not API_KEY:
        logger.warning("DEEPSEEK_API_KEY missing; skipping translation")
        return result

    def run(index: int) -> tuple[int, str]:
        block = blocks[index]
        if not block.strip() or _has_cjk(block):
            return index, block
        context = (contexts[index] or "").strip()
        prompt = BATCH_PROMPT.format(
            context=CONTEXT_HEADER.format(context=context[-400:]) if context else "",
            blocks=f"{BATCH_MARKER.format(n=1)}\n{block}",
        )
        try:
            text = _call(prompt)
            marker = BATCH_MARKER.format(n=1)
            if text.startswith(marker):
                text = text[len(marker):]
            text = _post_process(block, text)
            if text and not _is_degenerate(text, block):
                return index, text
        except Exception as exc:
            logger.warning("context translation failed for block %d: %s", index, exc)
        return index, block

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for index, text in pool.map(run, range(len(blocks))):
            result[index] = text
    return result
