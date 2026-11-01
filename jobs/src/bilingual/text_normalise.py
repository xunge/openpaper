"""Markdown normalisation for OCR output.

The OCR model emits LaTeX with `\\[ … \\]` / `\\( … \\)` delimiters, which
remark-math does not understand, and splits ordered lists into separate blocks so
every item restarts at 1. Both are fixed here.
"""

import re

_DISPLAY_OPEN = re.compile(r"\\\[")
_DISPLAY_CLOSE = re.compile(r"\\\]")
_INLINE_OPEN = re.compile(r"\\\(")
_INLINE_CLOSE = re.compile(r"\\\)")
_ORDERED_ITEM = re.compile(r"^\s*(\d{1,3})[.)]\s+")
# `$$...$$` on one line is parsed by remark-math as *inline* math, and KaTeX
# rejects `\tag` (which OCR emits on numbered equations) outside display mode.
# Re-emitting every display equation as its own block makes the delimiters
# unambiguous and keeps the equation number on the right.
_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)


def normalise_math(text: str) -> str:
    """Convert LaTeX delimiters to the `$` form remark-math supports."""
    if not text:
        return text
    out = _DISPLAY_OPEN.sub("$$", text)
    out = _DISPLAY_CLOSE.sub("$$", out)
    out = _INLINE_OPEN.sub("$", out)
    out = _INLINE_CLOSE.sub("$", out)
    # `[ … \quad (1) ]` is how the model often writes a display equation.
    out = re.sub(
        r"(?m)^\s*\[\s*(\\[a-zA-Z]+[\s\S]*?)\s*\]\s*$",
        lambda m: "$$\n" + m.group(1).strip() + "\n$$",
        out,
    )
    # OCR escapes every backslash in some tables (`\( \backslash \) n`).
    out = out.replace("( \\backslash )", "\\")
    out = out.replace("\\( \\backslash \\)", "\\")

    def _as_block(match: "re.Match[str]") -> str:
        body = match.group(1).strip()
        if not body:
            return match.group(0)
        return f"\n$$\n{body}\n$$\n"

    return _DISPLAY_MATH.sub(_as_block, out)


def merge_ordered_lists(blocks: list[str]) -> list[str]:
    """Join consecutive numbered items so the list keeps 1, 2, 3 numbering.

    Markdown restarts numbering for every block, which is why every item rendered
    as "1."; a single block keeps the sequence intact.
    """
    merged: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if run:
            merged.append("\n".join(run))
            run.clear()

    for block in blocks:
        if _ORDERED_ITEM.match(block):
            run.append(block.rstrip())
        else:
            flush()
            merged.append(block)
    flush()
    return merged


_ORDERED_LINE = re.compile(r"(?m)^([ \t]*)(\d{1,3})([.)])([ \t]+)")


def align_ordered_list_markers(source: str, translated: str) -> str:
    """Restore the source numbering on a translated ordered list.

    Translators frequently emit `1.` for every item, or renumber, which makes a
    merged list read 1., 1., 1. The list is usable only when its markers follow
    the source order, so replace the translated markers when both sides have the
    same number of items. Anything else (merged/split lines) is left untouched
    rather than guessing.
    """
    source_markers = _ORDERED_LINE.findall(source or "")
    translated_matches = list(_ORDERED_LINE.finditer(translated or ""))
    if len(source_markers) < 2 or len(source_markers) != len(translated_matches):
        return translated

    pieces: list[str] = []
    cursor = 0
    for match, (indent, number, delimiter, spacing) in zip(
        translated_matches, source_markers
    ):
        pieces.append(translated[cursor : match.start()])
        pieces.append(f"{indent}{number}{delimiter}{spacing}")
        cursor = match.end()
    pieces.append(translated[cursor:])
    return "".join(pieces)
