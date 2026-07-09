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
  h3 { font-size: 11.5pt; margin: 16px 0 6px; }
  ul { margin: 4px 0 14px; padding-left: 20px; }
  li { margin: 2px 0; }
  hr { border: none; border-top: 1px solid #ddd; margin: 16px 0; }
  table { border-collapse: collapse; width: 100%; margin: 8px 0 16px; font-size: 9.5pt; }
  th, td { border: 1px solid #ddd; padding: 5px 8px; text-align: left; vertical-align: top; }
  th { background: #f2f2f2; font-weight: 600; }
  tr:nth-child(even) td { background: #fafafa; }
  strong { font-weight: 600; }
  code { background: #f2f2f2; padding: 1px 4px; border-radius: 3px; font-size: 0.9em; }
  .callout { border-left: 4px solid #d4a017; background: #fff8e6; padding: 10px 14px; margin: 12px 0; border-radius: 3px; }
  .callout-title { font-weight: 700; margin-bottom: 4px; }
  .callout p { margin: 4px 0; }
  .callout ul { margin: 4px 0; }
  .callout-note { border-left-color: #0969da; background: #eff6ff; }
  .callout-tip { border-left-color: #1a7f37; background: #edfdf3; }
  .callout-important { border-left-color: #8250df; background: #f6f0ff; }
  .callout-caution { border-left-color: #cf222e; background: #fff0f0; }
"""


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
        convert_alerts(md_text),
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
    )
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
