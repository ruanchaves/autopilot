#!/usr/bin/env python3
"""Render a plain-text cover letter to a one-page PDF with no dependencies.

    cover_letter.py --text FILE.txt --out FILE.pdf [--name "Jane Doe"] [--contact "line"]

The text file is the letter as written: paragraphs separated by blank lines. The PDF uses
Helvetica 11pt with the applicant's name as a small header, so it looks like a letter and
not like a résumé. Characters outside WinAnsi (Latin-1 plus a few typographic marks) are
replaced, so write the letter in English or Portuguese and avoid emoji.
"""

from __future__ import annotations

import argparse
import sys
import zlib
from pathlib import Path

# Helvetica AFM widths (per 1000 em) for WinAnsi code points; everything unlisted is 556.
_W = {32: 278, 33: 278, 34: 355, 35: 556, 36: 556, 37: 889, 38: 667, 39: 191, 40: 333, 41: 333, 42: 389, 43: 584,
      44: 278, 45: 333, 46: 278, 47: 278, 58: 278, 59: 278, 60: 584, 61: 584, 62: 584, 63: 556, 64: 1015,
      65: 667, 66: 667, 67: 722, 68: 722, 69: 667, 70: 611, 71: 778, 72: 722, 73: 278, 74: 500, 75: 667, 76: 556,
      77: 833, 78: 722, 79: 778, 80: 667, 81: 778, 82: 722, 83: 667, 84: 611, 85: 722, 86: 667, 87: 944, 88: 667,
      89: 667, 90: 611, 91: 278, 92: 278, 93: 278, 94: 469, 95: 556, 96: 333, 97: 556, 98: 556, 99: 500, 100: 556,
      101: 556, 102: 278, 103: 556, 104: 556, 105: 222, 106: 222, 107: 500, 108: 222, 109: 833, 110: 556, 111: 556,
      112: 556, 113: 556, 114: 333, 115: 500, 116: 278, 117: 556, 118: 500, 119: 722, 120: 500, 121: 500, 122: 500,
      123: 334, 124: 260, 125: 334, 126: 584, 150: 556, 151: 1000, 145: 222, 146: 222, 147: 333, 148: 333, 133: 1000}
_BOLD = {**_W, 97: 556, 98: 611, 99: 556, 100: 611, 101: 556, 102: 333, 103: 611, 104: 611, 105: 278, 106: 278, 107: 556,
         108: 278, 109: 889, 110: 611, 111: 611, 112: 611, 113: 611, 114: 389, 115: 556, 116: 333, 117: 611, 118: 556,
         119: 778, 120: 556, 121: 556, 122: 500, 65: 722, 66: 722, 67: 722, 68: 722, 69: 667, 70: 611, 71: 778, 72: 722,
         73: 278, 74: 556, 75: 722, 76: 611, 77: 833, 78: 722, 79: 778, 80: 667, 81: 778, 82: 722, 83: 667, 84: 611,
         85: 722, 86: 667, 87: 944, 88: 667, 89: 667, 90: 611}
_SUBST = {"—": "–", "’": "'", "‘": "'", "“": '"', "”": '"', "…": "...", " ": " ",
          "•": "-", "→": "->"}

PAGE_W, PAGE_H, MARGIN = 595.0, 842.0, 68.0  # A4, points
BODY_PT, LEAD, HEAD_PT = 11.0, 15.0, 14.0


def to_winansi(text: str) -> bytes:
    for a, b in _SUBST.items():
        text = text.replace(a, b)
    return text.encode("cp1252", errors="replace")


def width(data: bytes, pt: float, table: dict) -> float:
    return sum(table.get(c, 556) for c in data) / 1000 * pt


def wrap(text: str, pt: float, max_w: float, table: dict) -> list[bytes]:
    lines, cur = [], b""
    for word in to_winansi(text).split(b" "):
        trial = word if not cur else cur + b" " + word
        if width(trial, pt, table) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def esc(data: bytes) -> bytes:
    return data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def build_pdf(paragraphs: list[str], name: str, contact: str) -> bytes:
    max_w = PAGE_W - 2 * MARGIN
    ops = []
    y = PAGE_H - MARGIN
    if name:
        ops.append(b"BT /F2 %.1f Tf %.1f %.1f Td (%s) Tj ET" % (HEAD_PT, MARGIN, y, esc(to_winansi(name))))
        y -= HEAD_PT + 3
    if contact:
        ops.append(b"BT /F1 9 Tf 0.35 g %.1f %.1f Td (%s) Tj ET 0 g" % (MARGIN, y, esc(to_winansi(contact))))
        y -= 12
    if name or contact:
        y -= 14
        ops.append(b"0.75 G %.1f %.1f m %.1f %.1f l S" % (MARGIN, y + 8, PAGE_W - MARGIN, y + 8))
        y -= 8
    for para in paragraphs:
        for line in wrap(para, BODY_PT, max_w, _W):
            if y < MARGIN:
                raise SystemExit("cover letter does not fit on one page: shorten it")
            ops.append(b"BT /F1 %.1f Tf %.1f %.1f Td (%s) Tj ET" % (BODY_PT, MARGIN, y, esc(line)))
            y -= LEAD
        y -= LEAD * 0.6
    stream = zlib.compress(b"\n".join(ops))
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.0f %.0f] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> "
        b"/Contents 6 0 R >>" % (PAGE_W, PAGE_H),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--contact", default="")
    a = ap.parse_args()
    text = Path(a.text).read_text(encoding="utf-8-sig").strip()
    paragraphs = [" ".join(p.split()) for p in text.split("\n\n") if p.strip()]
    words = sum(len(p.split()) for p in paragraphs)
    Path(a.out).write_bytes(build_pdf(paragraphs, a.name, a.contact))
    print(f"wrote {a.out}: {len(paragraphs)} paragraphs, {words} words")
    return 0


if __name__ == "__main__":
    sys.exit(main())
