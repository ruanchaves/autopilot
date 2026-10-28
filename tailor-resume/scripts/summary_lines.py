"""Count how many rendered lines the Summary section takes in a RenderCV PDF.

Usage: python summary_lines.py CV.pdf [MAX_LINES]

Prints the summary lines and exits 1 if there are more than MAX_LINES (default 4).
"""
import sys

import pymupdf


def summary_lines(pdf_path):
    page = pymupdf.open(pdf_path)[0]
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"]).strip()
            size = max((span["size"] for span in line["spans"]), default=0)
            if text:
                lines.append((round(line["bbox"][1], 1), text, size))
    lines.sort()

    start = next(i for i, (_, text, _) in enumerate(lines) if text == "Summary")
    heading_size = lines[start][2]
    body = []
    for _, text, size in lines[start + 1:]:
        if size >= heading_size - 0.5:  # next section heading
            break
        body.append(text)
    return body


def main(pdf_path, max_lines="4"):
    body = summary_lines(pdf_path)
    for text in body:
        print(" |", text)
    print(f"Summary lines: {len(body)}/{max_lines}")
    ok = len(body) <= int(max_lines)
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
