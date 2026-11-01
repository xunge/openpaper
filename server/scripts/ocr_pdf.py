"""OCR one PDF page with either served OCR backend.

Run inside the server container (it has PyMuPDF and httpx):

    docker compose -f docker-compose.dev.yml exec -T server \
        python /app/scripts/ocr_pdf.py /app/tmp_ocr.pdf --page 1

The page image is rendered at 144 DPI (the value the model was tuned on) and sent
to the OpenAI-compatible endpoint of the chosen backend.
"""

import argparse
import base64
import sys
import time

import httpx
import pymupdf

BACKENDS = {
    "xiaomi-ocr-0": {
        "url": "http://ocr-xiaomi:8000/v1/chat/completions",
        "prompt": (
            "Extract all information from the main body of the document image and "
            "represent it in markdown format, ignoring headers and footers. Tables "
            "should be expressed in OTSL format, formulas in the document should be "
            "represented using LATEX format, and the parsing should be organized "
            "according to the reading order."
        ),
        "max_tokens": 6144,
    },
    "deepseek-ocr-2": {
        "url": "http://ocr:8000/v1/chat/completions",
        "prompt": "<image>\n<|grounding|>Convert the document to markdown. ",
        "max_tokens": 4096,
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR one page of a PDF")
    parser.add_argument("pdf", help="path to the PDF, as seen inside the container")
    parser.add_argument("--page", type=int, default=1, help="1-based page number")
    parser.add_argument("--model", default="xiaomi-ocr-0", choices=sorted(BACKENDS))
    parser.add_argument("--dpi", type=int, default=144)
    parser.add_argument("--out", help="write the raw output to this file")
    args = parser.parse_args()

    backend = BACKENDS[args.model]
    doc = pymupdf.open(args.pdf)
    if not 1 <= args.page <= doc.page_count:
        print(f"page {args.page} out of range (1..{doc.page_count})", file=sys.stderr)
        return 2

    page = doc.load_page(args.page - 1)
    pixmap = page.get_pixmap(dpi=args.dpi, alpha=False)
    data_uri = "data:image/png;base64," + base64.b64encode(
        pixmap.tobytes("png")
    ).decode("ascii")

    print(
        f"{args.pdf} page {args.page}/{doc.page_count} "
        f"({pixmap.width}x{pixmap.height} px, {args.dpi} DPI) -> {args.model}"
    )

    started = time.time()
    response = httpx.post(
        backend["url"],
        timeout=300,
        json={
            "model": args.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": backend["prompt"]},
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": backend["max_tokens"],
        },
    )
    elapsed = time.time() - started

    if response.status_code != 200:
        print(f"HTTP {response.status_code}: {response.text[:400]}", file=sys.stderr)
        return 1

    markdown = response.json()["choices"][0]["message"]["content"]
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(markdown)

    print(f"HTTP 200 in {elapsed:.1f}s | {len(markdown)} chars")
    if args.out:
        print(f"saved to {args.out}")
    print("-" * 60)
    print(markdown[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
