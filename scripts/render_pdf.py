#!/usr/bin/env python3
"""Render a setlist markdown file (Format 1 — Rich Metadata Table) to a styled PDF.

Converts GitHub-style alert blockquotes (> [!WARNING]) into colored callout
boxes, then prints the resulting HTML to PDF via headless Chrome — no paid
API or third-party PDF service required.
"""
import argparse
import glob
import os
import re
import shutil
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

# Non-macOS fallbacks, checked only after the paths above — so a Mac keeps
# using its installed Chrome/Chromium/Edge exactly as before. These let the
# renderer also work on a Linux box or a cloud session (where the browser
# lives on PATH or under a Playwright browser dir).
CHROME_COMMANDS = [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "microsoft-edge", "microsoft-edge-stable",
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
  /* Keep a set on as few printed pages as possible: the # and Dance columns
     only ever hold a number and a ✓, so they get the minimum and Intro (the
     column that wraps) keeps the slack. Without this the wider table pushes
     each set onto another page and the band turns a page mid-set.
     Scoped to .song-table (see tag_song_tables) — applying these widths to
     every table squeezes the constraints table's first column to 1.6em and
     wraps each constraint name into a tower.

     nth-child() is positional, so these indices track build_setlist.py's
     TABLE_COLUMNS: # is 1, Genre is 4, Decade is 5, Dance is 11, Intro is 12.
     Insert or reorder a column there and these selectors have to move with
     it, or the widths land on the wrong columns. */
  /* Fixed layout with every column's share spelled out. Under auto layout
     the columns size to their content and Intro — the one column that is
     meant to wrap — gets whatever is left, which after Genre and Decade were
     added was a one-word strip. The shares below sum to 100%; the narrow
     ones are sized to their header word ("Decade", "Length", "Dance"). */
  .song-table { table-layout: fixed; }
  .song-table th:nth-child(1), .song-table td:nth-child(1) { width: 3.4%; }
  .song-table th:nth-child(2), .song-table td:nth-child(2) { width: 13.5%; }
  .song-table th:nth-child(3), .song-table td:nth-child(3) { width: 12%; }
  .song-table th:nth-child(4), .song-table td:nth-child(4) { width: 13.5%; }
  .song-table th:nth-child(5), .song-table td:nth-child(5) { width: 7%; white-space: nowrap; }
  .song-table th:nth-child(6), .song-table td:nth-child(6) { width: 4.5%; }
  .song-table th:nth-child(7), .song-table td:nth-child(7) { width: 5.2%; }
  .song-table th:nth-child(8), .song-table td:nth-child(8) { width: 7%; }
  .song-table th:nth-child(9), .song-table td:nth-child(9) { width: 6.8%; }
  .song-table th:nth-child(10), .song-table td:nth-child(10) { width: 8%; }
  .song-table th:nth-child(11), .song-table td:nth-child(11) { width: 6%; text-align: center; padding-left: 2px; padding-right: 2px; }
  .song-table th:nth-child(12), .song-table td:nth-child(12) { width: 13.1%; }
  /* Genre ("Yacht Rock / Jazz Rock") may break only after its ' / ' — see
     keep_genre_parts_together — and Energy only after its arrow. */
  .genre-part { white-space: nowrap; }
  th { background: #f2f2f2; font-weight: 600; }
  tr:nth-child(even) td { background: #fafafa; }
  strong { font-weight: 600; }
  code { background: #f2f2f2; padding: 1px 4px; border-radius: 3px; font-size: 0.9em; }
  .icon { width: 0.95em; height: 0.95em; vertical-align: -0.12em; margin-right: 2px; }
  /* FLOOR SHEET — one page, read from standing height, so every rule here is
     in service of font size. Two columns because a single column of 37 songs
     at this size would run three pages; paragraphs (not a table) because a
     table can't break across a column. The three tiers come straight from
     the markdown render_floor_sheet_lines writes. */
  .floor-sheet { column-count: 2; column-gap: 0.3in; }
  .floor-sheet h2 { column-span: all; font-size: 15pt; margin: 0 0 6px; }
  .floor-sheet h3 { font-size: 13pt; margin: 8px 0 5px; padding-bottom: 2px;
                    border-bottom: 1.5px solid #222; break-after: avoid; break-inside: avoid; }
  .floor-sheet h3:first-of-type { margin-top: 0; }
  .floor-sheet p { margin: 0 0 2px; font-size: 10.5pt; line-height: 1.25;
                   break-inside: avoid; orphans: 2; widows: 2; }
  /* Title on its own line: display:block rather than a <br> in the markdown,
     so the generated .md carries no trailing-whitespace line breaks (fragile,
     and invisible in a diff). A title that still wraps after this is genuinely
     too long for the column and wants an abbreviation. */
  /* Direct child only: the title. A bold tag inside the cue (e.g. [Vamp]) is
     nested in the <em>, so it must not pick up display:block / 15.5pt. */
  .floor-sheet p > strong { display: block; font-size: 15.5pt; font-weight: 700;
                            line-height: 1.12; }
  .floor-sheet p em { font-style: normal; color: #444; font-size: 10pt; }
  .floor-sheet p em strong { display: inline; font-size: inherit; font-weight: 700;
                             color: #1a1a1a; }
  /* SHADED BLOCKS — a run of songs the band reads as one unit (a themed
     stretch, a medley, a dance run), marked in the .md with an invisible
     <!--shade:LABEL--> comment on each song. Deliberately a flat wash plus a
     left rule rather than a border box: a box around N rows can't survive a
     page or column break, a background can. Must out-specify the
     tr:nth-child(even) zebra above, which it does on class count. */
  .song-table tr.shaded td { background: #f4efe6; }
  .song-table tr.shaded td:first-child { border-left: 3px solid #c8922a; }
  .song-table tr.shaded-start td { border-top: 1.5px solid #c8922a; }
  .song-table tr.shaded-end td { border-bottom: 1.5px solid #c8922a; }
  /* Floor sheet: same wash, but the padding is kept to 1px vertical — this
     page is one-page-or-bust, and each shaded entry that grows costs a slot. */
  .floor-sheet p.shaded { background: #f4efe6; border-left: 3px solid #c8922a;
                          padding: 1px 4px; margin-left: -4px; }
  /* GIG SUMMARY — the last page, in two columns: stats and the vocalist
     breakdown at the top of the left column, then the repertoire table
     flowing down the left column and on into the right. Chrome fragments a
     table row-by-row across columns and repeats its <thead> at the top of
     the second one. */
  .summary-page { column-count: 2; column-gap: 0.3in; }
  .summary-page h2 { column-span: all; margin-top: 0; }
  .summary-page h3 { break-after: avoid; }
  .summary-page h3:first-of-type { margin-top: 0; }
  .summary-page ul { break-inside: avoid; }
  .summary-page table { font-size: 8pt; margin-top: 2px; }
  .summary-page th, .summary-page td { padding: 1px 4px; }
  .summary-page tr { break-inside: avoid; }
  /* Song gets the width; Genre may wrap after its ' / '; Decade and Energy
     are short and stay on one line. */
  .repertoire-table th:nth-child(1), .repertoire-table td:nth-child(1) { width: 46%; }
  .repertoire-table td:nth-child(3), .repertoire-table td:nth-child(4) { white-space: nowrap; }
  .repertoire-table tr.group-row td { background: #e8e8e8; font-size: 8.5pt; padding-top: 3px;
                                      border-top: 1.5px solid #888; break-after: avoid; }
  /* A white gap above every group heading after the first, so "In Progress"
     and "Archived" stand out from the rows above them rather than reading as
     one more song. A wide collapsed border wins over the neighbouring 1px
     rules, so this opens a real break in the table. */
  .repertoire-table tr.group-row:not(:first-child) td { border-top: 12px solid #fff; }
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


def style_repertoire_table(html):
    """Class the GIG SUMMARY's repertoire table and turn each row carrying
    build_setlist.py's <!--group--> marker into a single full-width heading
    cell ("Active — Not Selected (27)"). The .md keeps those rows as plain
    four-cell rows with three empty cells, so it stays a valid table; the
    colspan only exists in print."""
    def do_table(m):
        table = m.group(0)
        if "<!--group-->" not in table:
            return table
        table = table.replace("<table>", '<table class="repertoire-table">', 1)
        def do_row(r):
            row = r.group(0)
            if "<!--group-->" not in row:
                return row
            first = re.search(r"<td[^>]*>(.*?)</td>", row, flags=re.S).group(1).replace("<!--group-->", "")
            ncols = len(re.findall(r"<td", row))
            return f'<tr class="group-row"><td colspan="{ncols}">{first}</td></tr>'
        return re.sub(r"<tr>.*?</tr>", do_row, table, flags=re.S)
    return re.sub(r"<table>.*?</table>", do_table, html, flags=re.S)


def keep_genre_parts_together(html):
    """Let a merged Genre cell ('Yacht Rock / Jazz Rock') wrap only between
    its two halves, never inside one. Wraps each half in a nowrap span, in
    the song tables' Genre column (4th) and the repertoire table's (2nd).
    Positional, like the width rules in CSS — build_setlist.py's
    TABLE_COLUMNS and REPERTOIRE_COLUMNS are what these indices track."""
    def spans(cell_html):
        # A one-part genre ('Alternative Rock') is left free to wrap: it has
        # no ' / ' to break at, and held to one line it overruns the column.
        if " / " not in cell_html:
            return cell_html
        # &nbsp; before the slash so a wrap lands after it: 'Classic Rock /'
        # then 'Soft Rock', never 'Classic Rock' then '/ Soft Rock'.
        return "&nbsp;/ ".join(f'<span class="genre-part">{p}</span>' for p in cell_html.split(" / "))

    def do_table(m, col):
        def do_row(r):
            cells = re.findall(r"(<td[^>]*>)(.*?)(</td>)", r.group(0), flags=re.S)
            if len(cells) < col:
                return r.group(0)
            row = r.group(0)
            open_tag, body, close = cells[col - 1]
            # Rebuild by position: split the row on its cells and swap one.
            parts = re.split(r"(<td[^>]*>.*?</td>)", row, flags=re.S)
            td_idx = [i for i, p in enumerate(parts) if p.startswith("<td")]
            parts[td_idx[col - 1]] = open_tag + spans(body) + close
            return "".join(parts)
        return re.sub(r"<tr[^>]*>.*?</tr>", do_row, m.group(0), flags=re.S)

    html = re.sub(r'<table class="song-table">.*?</table>', lambda m: do_table(m, 4), html, flags=re.S)
    # Energy ('Medium→High') may break after its arrow, for the same reason:
    # unbroken it is the second-widest fixed cell and Intro pays for it.
    html = re.sub(r'<table class="song-table">.*?</table>',
                  lambda m: m.group(0).replace("→", "→<wbr>"), html, flags=re.S)
    return re.sub(r'<table class="repertoire-table">.*?</table>', lambda m: do_table(m, 2), html, flags=re.S)


SHADE_RE = re.compile(r"<!--shade:(.*?)-->")


def apply_shading(html):
    """Turn the <!--shade:LABEL--> markers build_setlist.py writes into shaded
    table rows and floor-sheet paragraphs, then strip them.

    One marker serves both surfaces, so this is the only place that knows what
    shading looks like. Rows also get shaded-start / shaded-end when the run
    begins or ends, which is what draws the block's top and bottom rules —
    computed here from adjacency rather than trusted from the markdown, so a
    reorder can't leave a rule stranded mid-block."""
    def do_rows(m):
        rows = re.findall(r"<tr>.*?</tr>", m.group(0), flags=re.S)
        if not rows:
            return m.group(0)
        labels = [(SHADE_RE.search(r).group(1) if SHADE_RE.search(r) else None) for r in rows]
        out = []
        for i, (row, label) in enumerate(zip(rows, labels)):
            row = SHADE_RE.sub("", row)
            if label:
                classes = ["shaded"]
                if i == 0 or labels[i - 1] != label:
                    classes.append("shaded-start")
                if i == len(rows) - 1 or labels[i + 1] != label:
                    classes.append("shaded-end")
                row = row.replace("<tr>", f'<tr class="{" ".join(classes)}">', 1)
            out.append(row)
        return m.group(0)[:m.group(0).index(rows[0])] + "".join(out) + "</tbody>"

    html = re.sub(r"<tbody>.*?</tbody>", do_rows, html, flags=re.S)

    def do_para(m):
        para = m.group(0)
        if not SHADE_RE.search(para):
            return para
        return SHADE_RE.sub("", para).replace("<p>", '<p class="shaded">', 1)

    html = re.sub(r"<p>.*?</p>", do_para, html, flags=re.S)
    # Any marker left (e.g. in a heading or a cell shape not matched above)
    # must not reach the page as a literal comment in printed text.
    return SHADE_RE.sub("", html)


def wrap_set_blocks(html):
    """Wrap each 'SET N' / 'ENCORES' / 'GIG SUMMARY' <h2> heading and
    everything up to the next h2 (its table, duration line, and following
    acoustic break, for SET/ENCORES) in a single div so the whole thing
    moves together to a fresh page instead of splitting. FLOOR SHEET and
    GIG SUMMARY get the same forced page break plus a class that lays each
    out in two columns (see CSS): large type for the floor sheet, and for
    the summary a long repertoire table that wraps from the left column into
    the right. Other h2 sections (SONGS IN PROGRESS, or the older separate
    SONGS NOT SELECTED / ARCHIVED SONGS format, on setlists written before
    those moved into the summary's table) are left unwrapped so they flow
    naturally and can share a page — forcing every h2 onto its own page (the
    original, pre-fix behavior) left each short trailing section stranded on
    its own mostly-empty page."""
    parts = re.split(r"(<h2[^>]*>.*?</h2>)", html, flags=re.S)
    if len(parts) <= 1:
        return html
    out = [parts[0]]
    for i in range(1, len(parts), 2):
        heading = parts[i]
        content = parts[i + 1] if i + 1 < len(parts) else ""
        heading_text = re.sub(r"<[^>]+>", "", heading).strip()
        if heading_text.upper() == "FLOOR SHEET":
            out.append(f'<div class="set-block floor-sheet">{heading}{content}</div>')
        elif heading_text.upper() == "GIG SUMMARY":
            out.append(f'<div class="set-block summary-page">{heading}{content}</div>')
        elif re.match(r"^SET\b", heading_text, re.I) or heading_text.upper() == "ENCORES":
            out.append(f'<div class="set-block">{heading}{content}</div>')
        else:
            out.append(heading + content)
    return "".join(out)


def find_chrome():
    override = os.environ.get("CHROME_PATH")
    if override and os.path.exists(override):
        return override
    for p in CHROME_PATHS:
        if os.path.exists(p):
            return p
    for cmd in CHROME_COMMANDS:
        found = shutil.which(cmd)
        if found:
            return found
    for pattern in ("/opt/pw-browsers/chromium*/chrome-linux/chrome",
                    os.path.expanduser("~/.cache/ms-playwright/chromium*/chrome-linux/chrome")):
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]
    print("Error: no Chromium-based browser found for PDF rendering.", file=sys.stderr)
    print("       Set CHROME_PATH to a Chromium-based browser executable to override.", file=sys.stderr)
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
    body_html = apply_shading(body_html)
    # After apply_shading: that pass rebuilds each <tbody> from its bare <tr>
    # rows, so a row that already carries a class would be dropped.
    body_html = style_repertoire_table(body_html)
    body_html = keep_genre_parts_together(body_html)
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
    parser.add_argument("--all", action="store_true",
                        help="Render every .md file under setlists/ (recursively)")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    setlists_dir = os.path.join(script_dir, "..", "setlists")

    if args.all:
        # Recursive: setlists are filed under a per-gig subfolder, so a
        # flat listdir() would find nothing.
        md_files = sorted(glob.glob(os.path.join(setlists_dir, "**", "*.md"), recursive=True))
    elif args.md_file:
        md_files = [args.md_file]
    else:
        parser.error("Provide a .md file path or use --all")

    for md_path in md_files:
        pdf_path = render(md_path)
        print(f"✅ {pdf_path}")


if __name__ == "__main__":
    main()
