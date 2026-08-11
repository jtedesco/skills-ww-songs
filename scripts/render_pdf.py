#!/usr/bin/env python3
"""Render a setlist markdown file (Format 1 — Rich Metadata Table) to a styled PDF.

Converts GitHub-style alert blockquotes (> [!WARNING]) into colored callout
boxes, then prints the resulting HTML to PDF via headless Chrome — no paid
API or third-party PDF service required.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

try:
    import markdown
except ImportError:
    print("Error: the 'markdown' package is required. Install with: pip3 install --user markdown", file=sys.stderr)
    sys.exit(1)

CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]

ALERT_ICONS = {"warning": "⚠️", "note": "📝", "tip": "💡", "important": "❗", "caution": "🛑"}

CSS = """
  @page { margin: 0.5in; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
         color: #1a1a1a; font-size: 10.5pt; line-height: 1.45; }
  h1 { font-size: 21pt; margin: 0 0 10px 0; border-bottom: 2px solid #222; padding-bottom: 8px; }
  h2 { font-size: 13.5pt; margin: 20px 0 8px; }
  .set-block { break-before: page; page-break-before: always; }
  h3 { font-size: 11.5pt; margin: 10px 0 4px; }
  ul { margin: 4px 0 8px; padding-left: 20px; }
  li { margin: 2px 0; }
  hr { border: none; border-top: 1px solid #ddd; margin: 8px 0; }
  table { border-collapse: collapse; width: 100%; margin: 6px 0 8px; font-size: 9pt; }
  th, td { border: 1px solid #ddd; padding: 2px 5px; text-align: left; vertical-align: top; }
  /* Keep an 18-song set on one printed page: the # and Dance columns only ever
     hold a number and a ✓, so give them the minimum and let Intro (the one
     column that wraps) keep the slack. Without this the wider table pushes
     each set onto a second page and the band turns a page mid-set.
     Scoped to .song-table (see tag_song_tables) — applying these widths to
     every table squeezes the constraints table's first column to 1.6em and
     wraps each constraint name into a tower. */
  .song-table th:nth-child(1), .song-table td:nth-child(1) { width: 1.4em; }
  .song-table th:nth-child(9), .song-table td:nth-child(9) { width: 1.4em; text-align: center; padding-left: 2px; padding-right: 2px; }
  th { background: #f2f2f2; font-weight: 600; }
  tr:nth-child(even) td { background: #fafafa; }
  strong { font-weight: 600; }
  code { background: #f2f2f2; padding: 1px 4px; border-radius: 3px; font-size: 0.9em; }
  .icon { width: 0.95em; height: 0.95em; vertical-align: -0.12em; margin-right: 2px; }
  .callout { border-left: 4px solid #d4a017; background: #fff8e6; padding: 7px 12px; margin: 8px 0; border-radius: 3px; }
  .callout-title { font-weight: 700; margin-bottom: 3px; }
  .callout p { margin: 4px 0; }
  .callout ul { margin: 4px 0; }
  .callout-note { border-left-color: #0969da; background: #eff6ff; }
  .callout-tip { border-left-color: #1a7f37; background: #edfdf3; }
  .callout-important { border-left-color: #8250df; background: #f6f0ff; }
  .callout-caution { border-left-color: #cf222e; background: #fff0f0; }
"""

# Chrome embeds the full-color Apple Color Emoji font (100s of KB) just to
# render a handful of glyphs. Swap the semantic ones for tiny inline SVGs and
# drop the purely decorative ones — cuts rendered PDFs down by ~10-20x.
_ICON_SVG = {
    "✅": '<svg class="icon" viewBox="0 0 16 16"><circle cx="8" cy="8" r="8" fill="#1a7f37"/>'
          '<path d="M4.5 8.3l2.3 2.3 4.7-5.1" fill="none" stroke="#fff" stroke-width="1.8" '
          'stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "❌": '<svg class="icon" viewBox="0 0 16 16"><circle cx="8" cy="8" r="8" fill="#cf222e"/>'
          '<path d="M5 5l6 6M11 5l-6 6" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/></svg>',
    "🟢": '<svg class="icon" viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="#1a7f37"/></svg>',
    "🔴": '<svg class="icon" viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="#cf222e"/></svg>',
    "🛑": '<svg class="icon" viewBox="0 0 16 16"><circle cx="8" cy="8" r="8" fill="#cf222e"/>'
          '<rect x="4.5" y="7" width="7" height="2" fill="#fff"/></svg>',
    "⚠️": '<svg class="icon" viewBox="0 0 16 16"><path d="M8 1.5 L15 14.5 L1 14.5 Z" fill="#d4a017"/>'
          '<rect x="7.2" y="5.5" width="1.6" height="5" fill="#1a1a1a"/>'
          '<rect x="7.2" y="11.2" width="1.6" height="1.6" fill="#1a1a1a"/></svg>',
    "ℹ️": '<svg class="icon" viewBox="0 0 16 16"><circle cx="8" cy="8" r="8" fill="#0969da"/>'
          '<rect x="7.2" y="6.5" width="1.6" height="6" fill="#fff"/>'
          '<rect x="7.2" y="3.5" width="1.6" height="1.6" fill="#fff"/></svg>',
}
_ICON_SVG["⚠"] = _ICON_SVG["⚠️"]
_ICON_SVG["ℹ"] = _ICON_SVG["ℹ️"]

_DECORATIVE_EMOJI = ["📋", "⏱️", "⏱", "📊", "☕", "⏸️", "⏸", "🎵", "📝", "💡", "❗"]


def slim_emoji(html):
    """Replace color-emoji glyphs with tiny inline SVGs / drop purely decorative ones."""
    for e in _DECORATIVE_EMOJI:
        html = html.replace(e + " ", "").replace(e, "")
    for e, svg in _ICON_SVG.items():
        html = html.replace(e, svg)
    return html


def prevent_setext_headings(md_text):
    """A line of 3+ dashes immediately following non-blank text is CommonMark
    setext-heading syntax — it turns the *previous* line into an <h2>. The
    '----...' section separators in these setlists are meant as plain
    thematic breaks (<hr>), so force that reading by inserting a blank line
    wherever one is missing (e.g. right after the '**Set N Music
    Duration**...' line), instead of letting it silently swallow that line
    into a heading."""
    lines = md_text.split("\n")
    out = []
    for line in lines:
        if re.match(r"^-{3,}\s*$", line) and out and out[-1].strip() != "":
            out.append("")
        out.append(line)
    return "\n".join(out)


def convert_alerts(md_text):
    """Turn GitHub-style '> [!WARNING] ...' blockquotes into styled callout divs."""
    lines = md_text.split("\n")
    out = []
    i = 0
    while i < len(lines):
        m = re.match(r"^>\s*\[!(\w+)\]\s*$", lines[i].strip())
        if m:
            alert_type = m.group(1).lower()
            i += 1
            content_lines = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                content_lines.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            inner_html = markdown.markdown("\n".join(content_lines).strip(), extensions=["extra"])
            icon = ALERT_ICONS.get(alert_type, "ℹ️")
            out.append(f'<div class="callout callout-{alert_type}">')
            out.append(f'<div class="callout-title">{icon} {alert_type.upper()}</div>')
            out.append(inner_html)
            out.append("</div>")
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def tag_song_tables(html):
    """Add class="song-table" to the main running-order tables so the
    per-column width rules in CSS can target them without also hitting the
    constraints / vocalist-breakdown / not-selected tables, which have
    completely different column counts and meanings. Identified by their
    first header cell being '#', which only the song tables use."""
    return re.sub(
        r"<table>(\s*<thead>\s*<tr>\s*<th[^>]*>#</th>)",
        r'<table class="song-table">\1',
        html,
    )


def wrap_set_blocks(html):
    """Wrap each 'SET N' / 'ENCORES' / 'GIG SUMMARY' <h2> heading and
    everything up to the next h2 (its table, duration line, and following
    acoustic break, for SET/ENCORES) in a single div so the whole thing
    moves together to a fresh page instead of splitting. GIG SUMMARY gets
    its own forced page too (kept to one page by construction — see
    render_summary_page_lines/render_not_selected_and_archived_lines in
    build_setlist.py — rather than by pagination tricks here). Other h2
    sections (SONGS IN PROGRESS, or the older separate SONGS NOT SELECTED /
    ARCHIVED SONGS format) are left unwrapped so they flow naturally and can
    share a page — forcing every h2 onto its own page (the original,
    pre-fix behavior) left each short trailing section stranded on its own
    mostly-empty page."""
    parts = re.split(r"(<h2[^>]*>.*?</h2>)", html, flags=re.S)
    if len(parts) <= 1:
        return html
    out = [parts[0]]
    for i in range(1, len(parts), 2):
        heading = parts[i]
        content = parts[i + 1] if i + 1 < len(parts) else ""
        heading_text = re.sub(r"<[^>]+>", "", heading).strip()
        if re.match(r"^SET\b", heading_text, re.I) or heading_text.upper() in ("ENCORES", "GIG SUMMARY"):
            out.append(f'<div class="set-block">{heading}{content}</div>')
        else:
            out.append(heading + content)
    return "".join(out)


def find_chrome():
    for p in CHROME_PATHS:
        if os.path.exists(p):
            return p
    print("Error: no Chromium-based browser found for PDF rendering.", file=sys.stderr)
    sys.exit(1)


def render(md_path, pdf_path=None):
    with open(md_path, encoding="utf-8") as f:
        md_text = f.read()

    body_html = markdown.markdown(
        convert_alerts(prevent_setext_headings(md_text)),
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
    )
    body_html = slim_emoji(body_html)
    body_html = tag_song_tables(body_html)
    body_html = wrap_set_blocks(body_html)
    html = f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body_html}</body></html>"

    if pdf_path is None:
        pdf_path = os.path.splitext(md_path)[0] + ".pdf"
    pdf_path = os.path.abspath(pdf_path)

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
        tmp.write(html)
        tmp_path = tmp.name

    chrome = find_chrome()
    try:
        subprocess.run(
            [
                chrome, "--headless", "--disable-gpu", "--no-sandbox",
                "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}",
                f"file://{tmp_path}",
            ],
            check=True, capture_output=True,
        )
    finally:
        os.unlink(tmp_path)

    return pdf_path


def main():
    parser = argparse.ArgumentParser(description="Render a setlist markdown file to a styled PDF")
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
        pdf_path = render(md_path)
        print(f"✅ {pdf_path}")


if __name__ == "__main__":
    main()
