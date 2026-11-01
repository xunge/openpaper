"""Whole-document translation in reading order.

The unit of work used to be one block, then one page; both cut sentences that run
across a page break. Items are now flattened in document order and grouped by a
character budget that ignores page boundaries, so a sentence that spans pages is
translated inside one request.

A single request for the whole paper looked simpler but was the slowest possible
shape: a 13-page paper produced a ~5 minute completion that ran into max_tokens,
lost its block numbering for 63 fragments, and then needed a second phase of
one-by-one retries. Chunks are deliberately small (a few thousand characters) so
one bad completion costs seconds, progress can be reported per chunk, and the
result stays well inside the model's output budget.
"""

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .translate import _call, _is_degenerate, _post_process

logger = logging.getLogger(__name__)

# Translation is rewrite work, not reasoning. The fast model returns the same
# 8/8 blocks on a 7.3k-char sample in 21s where the pro model takes 70s, so the
# default is the fast one; override with TRANSLATE_MODEL for a quality pass.
TRANSLATE_MODEL = os.getenv("TRANSLATE_MODEL") or os.getenv(
    "DEEPSEEK_FAST_MODEL", "deepseek-flash"
)
# Per-request ceiling. The actual budget is derived from the input size below.
TRANSLATE_MAX_TOKENS = int(os.getenv("TRANSLATE_MAX_TOKENS", "8192"))
# A document small enough to finish in one request is sent in one request.
SINGLE_REQUEST_CHARS = int(os.getenv("TRANSLATE_SINGLE_LIMIT", "12000"))
CHUNK_CHARS = int(os.getenv("TRANSLATE_CHUNK_CHARS", "6000"))
CONTEXT_CHARS = 400
TRANSLATE_WORKERS = int(os.getenv("TRANSLATE_WORKERS", "6"))

# A cell that is only numbers/units/symbols has nothing to translate. Sending
# hundreds of them to the model is what produced the long retry phase on
# table-heavy papers.
_WORD = re.compile(r"[A-Za-z]{2,}")

DOC_PROMPT = """把下面编号的论文片段翻译成简体中文。

规则：
1. 保留全部 markdown 与 HTML 结构：标题层级、列表符号、表格的 <table>/<tr>/<td> 及 rowspan/colspan 属性一律不动，只翻译其中的英文正文。
2. 公式（$...$ 或 $$...$$）、代码、数字、引用编号、图号、URL、DOI 原样保留。
3. 片段之间是连续的正文，句子可能跨片段断开，请译文连贯通顺，不要在每个片段里重新起头。
4. 有序列表必须保留每一项的原编号和分隔符（如 `1.`、`2.`、`1)`），不要把所有项都写成 `1.`，也不要重新编号。
5. 输出格式严格为：每个片段以 `@@BLOCK n@@` 开头，编号与输入一致，不要合并、不要漏掉、不要增加说明。

{context}{blocks}"""


def _budget(text: str) -> int:
    """Output tokens for this input, capped by the model's own ceiling."""
    # Chinese needs roughly one token per character; 1.5x the input length is
    # generous without letting a broken completion run to the hard cap.
    return max(1024, min(TRANSLATE_MAX_TOKENS, int(len(text) * 1.5)))


def chunk_items(
    items: Sequence[Tuple[str, str]], limit: int = CHUNK_CHARS
) -> List[List[Tuple[str, str]]]:
    """Group (id, text) pairs into reading-order chunks within the char budget."""
    chunks: List[List[Tuple[str, str]]] = []
    current: List[Tuple[str, str]] = []
    size = 0
    for item_id, text in items:
        length = len(text)
        if current and size + length > limit:
            chunks.append(current)
            current = []
            size = 0
        current.append((item_id, text))
        size += length
    if current:
        chunks.append(current)
    return chunks


def _parse_reply(reply: str, chunk: List[Tuple[str, str]]) -> Dict[str, str]:
    """Map `@@BLOCK n@@` sections back to item ids, dropping bad output."""
    parts = re.split(r"@@BLOCK\s+(\d+)@@", reply)
    mapping: Dict[str, str] = {}
    for position in range(1, len(parts), 2):
        try:
            number = int(parts[position])
        except ValueError:
            continue
        body = parts[position + 1] if position + 1 < len(parts) else ""
        if not 1 <= number <= len(chunk):
            continue
        item_id, source = chunk[number - 1]
        candidate = _post_process(source, body)
        if candidate and not _is_degenerate(candidate, source):
            mapping[item_id] = candidate
    return mapping


def _translate_chunk(
    chunk: List[Tuple[str, str]], context: str
) -> Dict[str, str]:
    rendered = "\n\n".join(
        f"@@BLOCK {position}@@\n{text}"
        for position, (_id, text) in enumerate(chunk, start=1)
    )
    prompt = DOC_PROMPT.format(
        context=(
            f"\n## 上文（仅用于衔接，不要翻译、不要输出）\n{context[-CONTEXT_CHARS:]}\n"
            if context
            else ""
        ),
        # Stating the count explicitly cuts down on silently merged/skipped
        # blocks, which is what produced the 63-fragment retry phase before.
        blocks=f"（共 {len(chunk)} 个片段，必须输出 {len(chunk)} 个 @@BLOCK。）\n\n{rendered}",
    )
    try:
        reply = _call(prompt, model=TRANSLATE_MODEL, max_tokens=_budget(rendered))
        return _parse_reply(reply, chunk)
    except Exception as exc:
        logger.warning("chunk translation failed (%d items): %s", len(chunk), exc)
        return {}


def _translate_item(item_id: str, text: str) -> Optional[str]:
    """Single-item fallback with the same prompt and a size-aware budget."""
    chunk = [(item_id, text)]
    try:
        reply = _call(
            DOC_PROMPT.format(context="", blocks=f"@@BLOCK 1@@\n{text}"),
            model=TRANSLATE_MODEL,
            max_tokens=_budget(text),
        )
        return _parse_reply(reply, chunk).get(item_id)
    except Exception as exc:
        logger.warning("retry failed for %s: %s", item_id, exc)
        return None


def translate_document(
    items: Sequence[Tuple[str, str]],
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, str]:
    """Translate every item; returns id -> Chinese text (missing ids fall back)."""
    if not items:
        return {}

    # Blocks that already contain Chinese are passed through untouched. This also
    # keeps repeated table headers from being sent to the model again.
    results: Dict[str, str] = {}
    pending: List[Tuple[str, str]] = []
    for item_id, text in items:
        if re.search(r"[\u4e00-\u9fff]", text):
            results[item_id] = text
        elif not _WORD.search(text):
            # Numbers, units, table rules: leave them exactly as they are.
            results[item_id] = text
        else:
            pending.append((item_id, text))
    if not pending:
        return results

    total_chars = sum(len(text) for _id, text in pending)
    chunks = chunk_items(
        pending,
        SINGLE_REQUEST_CHARS if total_chars <= SINGLE_REQUEST_CHARS else CHUNK_CHARS,
    )
    # Each chunk carries the tail of the previous one so a sentence split at the
    # boundary still reads as one paragraph.
    contexts: List[str] = []
    carry = ""
    for chunk in chunks:
        contexts.append(carry)
        carry = chunk[-1][1][-CONTEXT_CHARS:]

    done = 0
    total = len(chunks)
    if on_progress:
        on_progress(0, total)
    with ThreadPoolExecutor(max_workers=TRANSLATE_WORKERS) as pool:
        futures = {
            pool.submit(_translate_chunk, chunk, contexts[index]): index
            for index, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            results.update(future.result())
            done += 1
            if on_progress:
                on_progress(done, total)

    missing = [(item_id, text) for item_id, text in pending if item_id not in results]
    if missing:
        logger.info("retrying %d item(s) that the chunked pass skipped", len(missing))

        def retry(pair: Tuple[str, str]) -> Tuple[str, Optional[str]]:
            item_id, text = pair
            return item_id, _translate_item(item_id, text)

        with ThreadPoolExecutor(max_workers=TRANSLATE_WORKERS) as pool:
            for item_id, translated in pool.map(retry, missing):
                if translated:
                    results[item_id] = translated

    still_missing = [item_id for item_id, _text in pending if item_id not in results]
    if still_missing:
        logger.warning("%d item(s) stayed in the source language", len(still_missing))
    return results
