#!/usr/bin/env python3
"""Render a setlist markdown file (Format 1 — Rich Metadata Table) to RTF.

Unlike the PDF export, RTF has no fixed page size, so the document flows
continuously with no page breaks — good for scrolling through on a phone
mid-gig. Opens natively in Word, Google Docs (upload + "Open with Google
Docs"), Pages, and TextEdit.

This is a small purpose-built markdown->RTF converter (not a generic one)
that understands exactly the subset of markdown build_setlist.py emits:
headings, bullet lists, tables, GitHub-style alert blockquotes, hr, and
inline bold/italic/code.
"""
import argparse
import os
import re
import sys

FONT_TABLE = r"{\fonttbl{\f0\fswiss Helvetica;}{\f1\fmodern Courier New;}}"
COLOR_TABLE = r"{\colortbl;\red0\green0\blue0;\red120\green120\blue120;\red180\green130\blue0;}"
HEADER = r"{\rtf1\ansi\ansicpg1252\deff0" + FONT_TABLE + COLOR_TABLE + r"\f0\fs20"

INLINE_RE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`")
TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
TABLE_SEP_RE = re.compile(r"^\|[\s:|-]+\|\s*$")
ALERT_START_RE = re.compile(r"^>\s*\[!(\w+)\]\s*$")


def rtf_escape(text):
    """Escape RTF control chars and encode non-ASCII as \\uNNNN? (with UTF-16 surrogate pairs for astral chars)."""
    out = []
    for ch in text:
        code = ord(ch)
        if ch in ("\\", "{", "}"):
            out.append("\\" + ch)
        elif code < 128:
            out.append(ch)
        elif code <= 0xFFFF:
            val = code if code < 0x8000 else code - 0x10000
            out.append(f"\\u{val}?")
        else:
            code -= 0x10000
            high = 0xD800 + (code >> 10)
            low = 0xDC00 + (code & 0x3FF)
            high = high if high < 0x8000 else high - 0x10000
            low = low if low < 0x8000 else low - 0x10000
            out.append(f"\\u{high}?\\u{low}?")
    return "".join(out)


def format_inline(text):
    """Apply **bold**, *italic*, `code` inline formatting, escaping everything else."""
    out = []
    last = 0
    for m in INLINE_RE.finditer(text):
        out.append(rtf_escape(text[last:m.start()]))
        if m.group(1) is not None:
            out.append(r"{\b " + rtf_escape(m.group(1)) + "}")
        elif m.group(2) is not None:
            out.append(r"{\i " + rtf_escape(m.group(2)) + "}")
        elif m.group(3) is not None:
            out.append(r"{\f1 " + rtf_escape(m.group(3)) + "}")
        last = m.end()
    out.append(rtf_escape(text[last:]))
    return "".join(out)


def split_row(line):
    cells = line.strip()[1:-1].split("|") if line.strip().endswith("|") else line.strip()[1:].split("|")
    return [c.strip() for c in cells]


def render_table(rows, out):
    """Render a markdown table as an RTF table (\\trowd/\\cellx/\\intbl/\\row)."""
    header = split_row(rows[0])
    body = [split_row(r) for r in rows[2:]]  # skip header + separator
    n = len(header)
    total_width = 9500
    col_width = total_width // n

    def emit_row(cells, bold):
        out.append(r"\trowd\trgaph80\trleft0")
        x = 0
        for _ in cells:
            x += col_width
            out.append(rf"\clbrdrt\brdrs\brdrw10\clbrdrb\brdrs\brdrw10\clbrdrl\brdrs\brdrw10\clbrdrr\brdrs\brdrw10\cellx{x}")
        for c in cells:
            content = format_inline(c)
            if bold:
                content = r"{\b " + content + "}" if not content.startswith(r"{\b") else content
            out.append(r"\pard\intbl " + content + r"\cell")
        out.append(r"\row")

    emit_row(header, bold=True)
    for row in body:
        # pad/truncate to header width defensively
        row = (row + [""] * n)[:n]
        emit_row(row, bold=False)
    out.append(r"\pard\par")


def render(md_path, rtf_path=None):
    with open(md_path, encoding="utf-8") as f:
        lines = f.read().split("\n")

    out = [HEADER]
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # Title
        if stripped.startswith("# "):
            out.append(r"\fs40\b " + format_inline(stripped[2:]) + r"\b0\fs20\par")
            out.append(r"\brdrb\brdrs\brdrw15\brsp20\pard\par\pard\par")
            i += 1
            continue

        # Headings
        m = re.match(r"^(#{2,3})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            size = 30 if level == 2 else 26
            out.append(rf"\fs{size}\b " + format_inline(m.group(2)) + rf"\b0\fs20\par")
            i += 1
            continue

        # Alert blockquote: > [!WARNING] ... > ...
        am = ALERT_START_RE.match(stripped)
        if am:
            alert_type = am.group(1).upper()
            i += 1
            content_lines = []
            while i < n and lines[i].lstrip().startswith(">"):
                content_lines.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append(r"\pard\li360\box\brdrs\brdrw15\brdrcf3\brsp60")
            out.append(r"\cf3\b " + rtf_escape(alert_type) + r"\b0\cf1\par")
            for cl in content_lines:
                cl = cl.strip()
                if not cl:
                    continue
                # A leading "* " or "- " here is a nested bullet, not markdown
                # emphasis syntax — strip it before inline-formatting the rest,
                # otherwise the lone "*" confuses the bold/italic parser.
                bm = re.match(r"^[*-]\s+(.*)$", cl)
                if bm:
                    out.append(r"\li720 \u8226?\tab " + format_inline(bm.group(1)) + r"\li360\par")
                else:
                    out.append(format_inline(cl) + r"\par")
            out.append(r"\pard\li0\par")
            continue

        # Bullet list item
        if stripped.startswith("- ") or stripped.startswith("* "):
            out.append(r"\pard\li360 \u8226?\tab " + format_inline(stripped[2:]) + r"\par")
            i += 1
            continue

        # Table
        if TABLE_ROW_RE.match(stripped):
            table_lines = []
            while i < n and TABLE_ROW_RE.match(lines[i].strip()):
                table_lines.append(lines[i].strip())
                i += 1
            if len(table_lines) >= 2 and TABLE_SEP_RE.match(table_lines[1]):
                render_table(table_lines, out)
            else:
                for tl in table_lines:
                    out.append(format_inline(tl) + r"\par")
            continue

        # Horizontal rule
        if re.match(r"^-{3,}\s*$", stripped):
            out.append(r"\pard\brdrb\brdrs\brdrw10\brsp20\par\pard\par")
            i += 1
            continue

        # Plain paragraph
        out.append(r"\pard " + format_inline(stripped) + r"\par")
        i += 1

    out.append("}")
    rtf_content = "\n".join(out)

    if rtf_path is None:
        rtf_path = os.path.splitext(md_path)[0] + ".rtf"
    rtf_path = os.path.abspath(rtf_path)

    with open(rtf_path, "w", encoding="utf-8") as f:
        f.write(rtf_content)

    return rtf_path


def main():
    parser = argparse.ArgumentParser(description="Render a setlist markdown file to RTF (no page breaks)")
    parser.add_argument("md_file", nargs="?", help="Path to a setlist .md file")
    parser.add_argument("--all", action="store_true", help="Render every .md file in setlists/")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    setlists_dir = os.path.join(script_dir, "..", "setlists")

    if args.all:
        md_files = sorted(
            os.path.join(setlists_dir, f) for f in os.listdir(setlists_dir) if f.endswith(".md")
        )
    elif args.md_file:
        md_files = [args.md_file]
    else:
        parser.error("Provide a .md file path or use --all")

    for md_path in md_files:
        rtf_path = render(md_path)
        print(f"✅ {rtf_path}")


if __name__ == "__main__":
    main()
