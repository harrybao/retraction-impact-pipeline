"""Render docs/labelling-protocol.md to the .docx the labellers are sent.

    python scripts/render_protocol.py [out.docx]

The protocol existed as a Word file that had been produced by hand, so when the
protocol was rewritten to v2 the Word file was not: the labellers' copy still
gave v1's five descriptions and the precedence rule A2 > A3 > A1 > A4, while the
round-2 sheets asked the four v2 questions. Anyone reading the attachment would
have labelled under the rules we had just replaced.

Rendering it from the Markdown makes that impossible to repeat. The Markdown is
the protocol; this is only a format.

Handles what the protocol actually uses: headings, bullets, numbered lists,
tables, block quotes, inline bold/code, and horizontal rules.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

SRC = Path("docs/labelling-protocol.md")
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else
           "data/labelling/CS43_labelling_protocol_v2.docx")

# Inline: **bold**, `code`, and *italic*. Split so each piece keeps its own run.
INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)")


def add_inline(p, text: str, bold: bool = False, italic: bool = False) -> None:
    """Write `text` into paragraph `p`, honouring **bold**, `code` and *italic*.

    Bold recurses, because the protocol writes things like
    "**every row needs the `evidence` column**" and a single pass would strip
    the asterisks and leave the backticks sitting in the middle of the
    sentence.
    """
    for piece in INLINE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**"):
            add_inline(p, piece[2:-2], bold=True, italic=italic)
        elif piece.startswith("`") and piece.endswith("`"):
            r = p.add_run(piece[1:-1])
            r.font.name = "Consolas"
            r.font.size = Pt(9)
            r.bold, r.italic = bold or None, italic or None
        elif piece.startswith("*") and piece.endswith("*") and len(piece) > 2:
            add_inline(p, piece[1:-1], bold=bold, italic=True)
        else:
            r = p.add_run(piece)
            r.bold, r.italic = bold or None, italic or None


def is_table_row(line: str) -> bool:
    return line.startswith("|") and line.endswith("|")


def split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def main() -> int:
    if not SRC.exists():
        print(f"missing {SRC}", file=sys.stderr)
        return 1
    lines = SRC.read_text(encoding="utf-8").split("\n")
    d = docx.Document()
    style = d.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)

    i = 0
    n_tables = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped in ("---", "***", "___"):
            i += 1
            continue

        # Table: a header row, a separator of dashes, then body rows.
        if (is_table_row(stripped) and i + 1 < len(lines)
                and set(lines[i + 1].strip()) <= set("|-: ")
                and "-" in lines[i + 1]):
            header = split_row(stripped)
            i += 2
            body = []
            while i < len(lines) and is_table_row(lines[i].strip()):
                body.append(split_row(lines[i].strip()))
                i += 1
            t = d.add_table(rows=1, cols=len(header))
            t.style = "Table Grid"
            for c, text in zip(t.rows[0].cells, header):
                c.paragraphs[0].clear()
                add_inline(c.paragraphs[0], text)
                for r in c.paragraphs[0].runs:
                    r.bold = True
            for row in body:
                cells = t.add_row().cells
                for c, text in zip(cells, row[: len(header)]):
                    c.paragraphs[0].clear()
                    add_inline(c.paragraphs[0], text.replace("<br>", " "))
            d.add_paragraph()
            n_tables += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = min(len(m.group(1)), 4)
            h = d.add_heading("", level=level)
            add_inline(h, m.group(2))
            i += 1
            continue

        # A block quote runs until a blank line. Its lines are joined before any
        # inline formatting is read, because emphasis wraps across them - the
        # protocol has "**four questions in a fixed\norder**" - and matching
        # line by line would print the asterisks instead of applying them.
        if stripped.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            p = d.add_paragraph()
            p.paragraph_format.left_indent = Pt(18)
            add_inline(p, " ".join(x for x in buf if x), italic=True)
            for r in p.runs:
                r.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
            continue

        m = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if m:
            depth = len(m.group(1)) // 2
            p = d.add_paragraph(style="List Bullet" if depth == 0 else "List Bullet 2")
            add_inline(p, m.group(2))
            i += 1
            continue

        m = re.match(r"^(\s*)\d+\.\s+(.*)$", line)
        if m:
            p = d.add_paragraph(style="List Number")
            add_inline(p, m.group(2))
            i += 1
            continue

        # An indented line here continues the list item above it. This document
        # has no fenced code blocks and every indented line is such a
        # continuation, so it is indented prose and keeps its inline formatting.
        # Rendering it as preformatted printed the literal ** around the very
        # rules it was emphasising ("I am unsure" is NOT A0).
        if line.startswith("    "):
            p = d.add_paragraph()
            p.paragraph_format.left_indent = Pt(30)
            add_inline(p, stripped)
            i += 1
            continue

        # A plain paragraph is the consecutive non-blank lines, joined for the
        # same reason as the block quote above. The first line is always taken:
        # this branch is the fallback, so a line that reaches it belongs to a
        # paragraph whatever it starts with, and testing the break condition
        # before consuming anything would leave `i` where it was and loop for
        # ever. (It did: a line beginning with an emphasis marker.)
        buf = [stripped]
        i += 1
        while i < len(lines):
            s = lines[i].strip()
            if (not s or s.startswith(("#", ">", "|"))
                    or re.match(r"^\s*[-*+]\s", lines[i])
                    or re.match(r"^\s*\d+\.\s", lines[i])
                    or lines[i].startswith("    ")):
                break
            buf.append(s)
            i += 1
        p = d.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        add_inline(p, " ".join(buf))
        continue

    OUT.parent.mkdir(parents=True, exist_ok=True)
    d.save(OUT)
    print(f"written: {OUT}  ({len(d.paragraphs)} paragraphs, {n_tables} tables)")
    return 0


raise SystemExit(main())
