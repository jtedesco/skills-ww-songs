"""Audit the `youtube_url` column for misattributions.

Scoring picked those URLs, and scoring can be confidently wrong — a live cut
whose title hides it, a latter-day re-recording, a different act with the same
song title. This fetches what each stored URL ACTUALLY points at and compares
it back against the database, then writes a review page with a Listen link per
song so the last check can be done by ear.

    python3 scripts/review_youtube_urls.py                   # terminal summary
    python3 scripts/review_youtube_urls.py --html review.html

Flags raised (none of them mean 'wrong', only 'look at this'):
    artist   the recording is credited to someone else
    version  the title reads as live / karaoke / re-recorded / remixed
    title    the resolved title differs beyond punctuation
    obscure  far fewer plays than the rest — often a re-recording or a
             soundalike rather than the hit everyone knows
"""
import argparse
import csv
import html
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_youtube_urls import (display_artist, norm, artist_sim, base_title,
                                  require_ytmusicapi)

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "songs_metadata.csv")
URL_COL = "youtube_url"
# Below this, a track is suspiciously unplayed for a song a bar crowd knows.
OBSCURE_VIEWS = 250_000
MARKERS = re.compile(
    r"\b(live|karaoke|cover|tribute|remix|instrumental|demo|re-?recorded|sped up)\b", re.I)

FLAG_TEXT = {
    "artist": "credited to a different act",
    "version": "not billed as the studio recording",
    "title": "title differs from the database",
    "obscure": "unusually few plays for this song",
    "missing": "no URL in the database",
    "fetch": "could not be read from YouTube",
}


def collect(csv_path):
    require_ytmusicapi()
    from ytmusicapi import YTMusic
    yt = YTMusic()
    out = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        url = (r.get(URL_COL) or "").strip()
        rec = {"title": r["title"], "artist": display_artist(r["artist"]),
               "year": r.get("release_year", ""), "album": r.get("original_album", ""),
               "url": url, "yt_title": "", "yt_artist": "", "length": "",
               "views": 0, "flags": []}
        if not url:
            rec["flags"].append("missing")
            out.append(rec)
            continue
        try:
            d = yt.get_song(url.rsplit("v=", 1)[-1])["videoDetails"]
        except Exception:
            rec["flags"].append("fetch")
            out.append(rec)
            continue
        rec["yt_title"] = d.get("title", "")
        rec["yt_artist"] = d.get("author", "")
        secs = int(d.get("lengthSeconds") or 0)
        rec["length"] = f"{secs // 60}:{secs % 60:02d}" if secs else ""
        rec["views"] = int(d.get("viewCount") or 0)

        # Judge only the words the catalogue ADDED to the song's own name, so a
        # title like 'Live and Let Die' isn't read as a concert recording.
        extra = re.sub(re.escape(rec["title"].lower()), " ", rec["yt_title"].lower())
        if MARKERS.search(extra):
            rec["flags"].append("version")
        if artist_sim(rec["yt_artist"], rec["artist"]) < 0.6:
            rec["flags"].append("artist")
        if base_title(rec["yt_title"]) != norm(rec["title"]):
            rec["flags"].append("title")
        if rec["views"] and rec["views"] < OBSCURE_VIEWS:
            rec["flags"].append("obscure")
        out.append(rec)
    return out


def commas(n):
    return f"{n:,}" if n else "—"


PAGE = """<title>Setlist Recording Audit</title>
<style>
  :root {
    --paper:#F4F6F4; --card:#FFFFFF; --ink:#171A19; --dim:#5E6763; --line:#DDE2DE;
    --ok:#0F6E63; --warn:#96590F; --warn-bg:#FBF0DF; --ok-bg:#E4EFEC; --shadow:0 1px 2px rgba(0,0,0,.06);
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --paper:#121614; --card:#191E1C; --ink:#E4E9E5; --dim:#939E98; --line:#2B322F;
      --ok:#5FC3B4; --warn:#E0A354; --warn-bg:#2A2015; --ok-bg:#122A26; --shadow:none;
    }
  }
  :root[data-theme="dark"] {
    --paper:#121614; --card:#191E1C; --ink:#E4E9E5; --dim:#939E98; --line:#2B322F;
    --ok:#5FC3B4; --warn:#E0A354; --warn-bg:#2A2015; --ok-bg:#122A26; --shadow:none;
  }
  * { box-sizing:border-box; }
  body {
    margin:0; background:var(--paper); color:var(--ink);
    font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
    line-height:1.5; padding:2.5rem 1.25rem 4rem;
  }
  .wrap { max-width:70rem; margin:0 auto; display:flex; flex-direction:column; gap:1.75rem; }
  .mono { font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace; }
  h1 { font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
       font-size:1.5rem; font-weight:600; letter-spacing:-.02em; margin:0; text-wrap:balance; }
  .lede { color:var(--dim); margin:.4rem 0 0; max-width:60ch; }
  .counts { display:flex; flex-wrap:wrap; gap:.75rem; }
  .stat { background:var(--card); border:1px solid var(--line); border-radius:6px;
          padding:.7rem 1rem; box-shadow:var(--shadow); min-width:7.5rem; }
  .stat b { display:block; font-family:ui-monospace,"SF Mono",Menlo,monospace;
            font-size:1.5rem; font-variant-numeric:tabular-nums; line-height:1.1; }
  .stat span { font-size:.72rem; text-transform:uppercase; letter-spacing:.07em; color:var(--dim); }
  .stat.flagged b { color:var(--warn); }
  .stat.clean b { color:var(--ok); }
  h2 { font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:.78rem;
       text-transform:uppercase; letter-spacing:.09em; color:var(--dim);
       margin:0 0 .6rem; font-weight:600; }
  .scroll { overflow-x:auto; border:1px solid var(--line); border-radius:6px; background:var(--card); }
  table { border-collapse:collapse; width:100%; min-width:52rem; }
  th { position:sticky; top:0; background:var(--card); text-align:left;
       font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:.7rem;
       text-transform:uppercase; letter-spacing:.07em; color:var(--dim);
       padding:.7rem .9rem; border-bottom:1px solid var(--line); font-weight:600; }
  td { padding:.7rem .9rem; border-bottom:1px solid var(--line); vertical-align:top; font-size:.9rem; }
  tr:last-child td { border-bottom:none; }
  .song { font-weight:600; }
  .by { color:var(--dim); font-size:.82rem; }
  .got { font-size:.86rem; }
  .num { font-family:ui-monospace,"SF Mono",Menlo,monospace;
         font-variant-numeric:tabular-nums; font-size:.82rem; color:var(--dim); white-space:nowrap; }
  .chip { display:inline-block; font-family:ui-monospace,"SF Mono",Menlo,monospace;
          font-size:.68rem; padding:.15rem .45rem; border-radius:3px; margin:0 .25rem .25rem 0;
          background:var(--warn-bg); color:var(--warn); border:1px solid color-mix(in srgb,var(--warn) 30%,transparent); }
  .chip.ok { background:var(--ok-bg); color:var(--ok); border-color:color-mix(in srgb,var(--ok) 28%,transparent); }
  tr.flag td { background:color-mix(in srgb,var(--warn) 5%,transparent); }
  tr.flag td:first-child { box-shadow:inset 3px 0 0 var(--warn); }
  a.listen { font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:.75rem;
             color:var(--ok); text-decoration:none; border-bottom:1px solid color-mix(in srgb,var(--ok) 40%,transparent);
             white-space:nowrap; }
  a.listen:hover, a.listen:focus-visible { border-bottom-color:var(--ok); }
  :focus-visible { outline:2px solid var(--ok); outline-offset:2px; }
  .why { color:var(--dim); font-size:.78rem; margin-top:.15rem; }
  footer { color:var(--dim); font-size:.8rem; border-top:1px solid var(--line); padding-top:1rem; }
  code { font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:.85em;
         background:color-mix(in srgb,var(--ink) 7%,transparent); padding:.1rem .3rem; border-radius:3px; }
</style>
<div class="wrap">
  <header>
    <h1>Setlist Recording Audit</h1>
    <p class="lede">Every song in the library, matched to the recording that will play in the
    band's YouTube Music playlists. Rows needing a second opinion are listed first — open the
    link and check by ear.</p>
  </header>
  <div class="counts">{{COUNTS}}</div>
  {{SECTIONS}}
  <footer>Regenerate with <code>python3 scripts/review_youtube_urls.py --html &lt;file&gt;</code>.
  To correct a match, paste the right URL into the <code>youtube_url</code> column of
  <code>songs_metadata.csv</code> — it is the source of truth and is never re-queried when a
  playlist is built.</footer>
</div>
"""


def row_html(r):
    flags = r["flags"]
    chips = "".join(f'<span class="chip">{html.escape(f)}</span>' for f in flags) \
        or '<span class="chip ok">ok</span>'
    why = "; ".join(FLAG_TEXT.get(f, f) for f in flags)
    listen = (f'<a class="listen" href="{html.escape(r["url"])}" target="_blank" '
              f'rel="noopener">Listen &rarr;</a>') if r["url"] else "—"
    got = html.escape(r["yt_title"] or "—")
    got_by = html.escape(r["yt_artist"] or "")
    return f"""<tr class="{'flag' if flags else ''}">
  <td><div class="song">{html.escape(r['title'])}</div>
      <div class="by">{html.escape(r['artist'])}{(' &middot; ' + html.escape(r['year'])) if r['year'] else ''}</div></td>
  <td><div class="got">{got}</div><div class="by">{got_by}</div></td>
  <td class="num">{r['length'] or '—'}</td>
  <td class="num">{commas(r['views'])}</td>
  <td>{chips}{f'<div class="why">{html.escape(why)}</div>' if why else ''}</td>
  <td>{listen}</td>
</tr>"""


def table(rows, heading):
    if not rows:
        return ""
    body = "\n".join(row_html(r) for r in rows)
    return f"""<section><h2>{html.escape(heading)}</h2><div class="scroll"><table>
<thead><tr><th>In the database</th><th>Resolved recording</th><th>Length</th>
<th>Plays</th><th>Flags</th><th></th></tr></thead>
<tbody>{body}</tbody></table></div></section>"""


def write_html(recs, path):
    flagged = [r for r in recs if r["flags"]]
    clean = [r for r in recs if not r["flags"]]
    counts = "".join([
        f'<div class="stat"><b>{len(recs)}</b><span>songs</span></div>',
        f'<div class="stat clean"><b>{len(clean)}</b><span>look right</span></div>',
        f'<div class="stat flagged"><b>{len(flagged)}</b><span>worth a listen</span></div>',
    ])
    sections = table(sorted(flagged, key=lambda r: r["title"]), "Check these") + \
        table(sorted(clean, key=lambda r: r["title"]), "Everything else")
    page = PAGE.replace("{{COUNTS}}", counts).replace("{{SECTIONS}}", sections)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return len(flagged)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--html", help="Write a clickable review page here.")
    ap.add_argument("--csv", default=CSV_PATH)
    args = ap.parse_args()

    recs = collect(args.csv)
    flagged = [r for r in recs if r["flags"]]
    for r in sorted(flagged, key=lambda r: r["title"]):
        print(f"  [{','.join(r['flags'])}] {r['title']} — {r['artist']}")
        print(f"      resolved to: {r['yt_title'] or '(none)'} | {r['yt_artist']}")
        print(f"      {r['url']}")
    print(f"\n{len(recs)} song(s): {len(recs) - len(flagged)} look right, "
          f"{len(flagged)} worth a listen.")
    if args.html:
        write_html(recs, args.html)
        print(f"Review page: {args.html}")


if __name__ == "__main__":
    main()
