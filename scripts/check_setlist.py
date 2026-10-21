#!/usr/bin/env python3
"""
check_setlist.py — check a finished setlist .md against the band's rules.

The solver applies most of these while it builds, but a setlist is then
edited by hand and by apply_substitution.py, and nothing re-checked the
result. This reads the printed .md (the source of record) plus
songs_metadata.csv and reports every rule the setlist breaks.

Usage:
    python3 scripts/check_setlist.py "setlists/<gig>/<gig>.md" [more.md ...]

Exit status is 1 if any ERROR was found. WARNINGs are rules the band can
knowingly override — a vocal streak to give someone their only lead, a
not-yet-ready song pulled in with --include-not-ready — and never fail.

build_setlist.py and apply_substitution.py run this on every setlist they
write and print what it finds; test_setlist.py runs it on solver output and
on every committed setlist in the current format. See "Setlist Rules" in
SKILL.md for the rules themselves.
"""
import argparse
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from build_setlist import get_segue_groups, in_season, cut_without, REQUEST_LIST_HEADING
from apply_substitution import (
    parse_md, extract_break_songs, normalize_title, load_song_db,
    has_no_cut_bullet, parse_gig_date, parse_missing_names,
)

DEFAULT_CSV = os.path.join(SCRIPT_DIR, "..", "songs_metadata.csv")
ERROR, WARNING = "ERROR", "WARNING"
MAX_VOCAL_RUN = 3          # same lead, consecutive songs
MIN_TAXING_GAP = 2         # songs between two of Lauren's taxing ones
# A request-list song: a table row ('| 3 | **Brandy** | ...'), or the older
# numbered/bulleted list forms.
REQUEST_ITEM_RE = re.compile(r"^\s*(?:\|\s*\d+\s*\||\d+\.|-)\s*\*\*(.+?)\*\*")


def cut_window(n):
    """0-based positions a set's emergency cut may occupy: the back half of
    the set, never its closer — the cut is "one of the last songs", dropped
    late when the night is running long. tag_emergency_cuts() searches a
    narrower window for its own pick (it also skips the second-to-last), but
    the band hand-picks the second-to-last song routinely (Respect at Can
    Bar and Empire), so that's allowed here."""
    return range(n // 2, n - 1)


def read_request_list(md_path):
    """Titles on the REQUEST LIST page, or None for a setlist written before
    that page existed."""
    lines = open(md_path, encoding="utf-8").read().split("\n")
    if REQUEST_LIST_HEADING not in lines:
        return None
    tail = lines[lines.index(REQUEST_LIST_HEADING) + 1:]
    return [m.group(1) for m in (REQUEST_ITEM_RE.match(l) for l in tail) if m]


def check_setlist(md_path, csv_path=DEFAULT_CSV):
    """Return [(severity, rule, message), ...] for one setlist."""
    by_title, all_songs = load_song_db(csv_path)
    header_lines, sections, _ = parse_md(md_path)
    gig_date = parse_gig_date(header_lines)
    missing = parse_missing_names(header_lines)
    no_cut = has_no_cut_bullet(header_lines)
    issues = []

    def db(title):
        return by_title.get(normalize_title(title))

    chains = get_segue_groups([s for s in all_songs if s.get("archived") != "Yes"])
    chain_of = {normalize_title(t): [normalize_title(x) for x in c] for c in chains for t in c}
    taxing = {normalize_title(s["title"]) for s in all_songs
              if re.search(r"taxing|gravelly", s.get("vocalist_constraints", ""), re.I)}

    scheduled = []  # every performed title: sets, encores, breaks
    for sec in sections:
        rows = sec["rows"]
        label = sec["heading"].title()
        is_set = sec["heading"].upper().startswith("SET")
        scheduled.extend(r["title"] for r in rows)

        # Emergency cut: at least one per set (a second is fine — Robert's had
        # two), each in the back half, never the closer, never in a segue chain.
        if is_set and not no_cut:
            cuts = [i for i, r in enumerate(rows) if r.get("emergency_cut")]
            if not cuts:
                issues.append((ERROR, "emergency-cut", f"{label} has no emergency cut"))
            for i in cuts:
                where = f"{label} #{i + 1} {rows[i]['title']!r}"
                if i not in cut_window(len(rows)):
                    w = cut_window(len(rows))
                    issues.append((ERROR, "emergency-cut",
                                   f"{where} is outside the cut window #{w.start + 1}–#{w.stop} "
                                   f"(back half of the set, not the closer)"))
                if normalize_title(rows[i]["title"]) in chain_of:
                    issues.append((ERROR, "emergency-cut", f"{where} is part of a segue chain"))

        # Vocal streaks and Lauren's taxing songs.
        run = 1
        for i in range(1, len(rows)):
            run = run + 1 if rows[i]["lead_vocals"] == rows[i - 1]["lead_vocals"] else 1
            if run == MAX_VOCAL_RUN + 1:
                issues.append((WARNING, "vocal-run",
                               f"{label}: {rows[i]['lead_vocals']} leads more than {MAX_VOCAL_RUN} in a row "
                               f"(ending #{i + 1} {rows[i]['title']!r})"))
        hits = [i for i, r in enumerate(rows) if normalize_title(r["title"]) in taxing]
        for a, b in zip(hits, hits[1:]):
            if b - a - 1 < MIN_TAXING_GAP:
                issues.append((WARNING, "vocal-health",
                               f"{label}: taxing songs #{a + 1} {rows[a]['title']!r} and #{b + 1} "
                               f"{rows[b]['title']!r} are fewer than {MIN_TAXING_GAP} songs apart"))

        # Segues: chain members in canonical order; a SEGUE cue needs its source right before it.
        pos = {normalize_title(r["title"]): i for i, r in enumerate(rows)}
        for c in chains:
            present = [pos[normalize_title(t)] for t in c if normalize_title(t) in pos]
            if present != sorted(present):
                issues.append((ERROR, "segue-order", f"{label}: {' → '.join(c)} is out of canonical order"))
        for i, r in enumerate(rows):
            if r.get("intro_notes", "").strip().upper().startswith("SEGUE"):
                chain = chain_of.get(normalize_title(r["title"]), [])
                k = chain.index(normalize_title(r["title"])) if chain else -1
                prev_ok = k > 0 and i > 0 and normalize_title(rows[i - 1]["title"]) == chain[k - 1]
                if not prev_ok:
                    issues.append((ERROR, "segue-cue",
                                   f"{label} #{i + 1} {r['title']!r} prints a SEGUE cue but its "
                                   f"source song isn't directly before it"))

    scheduled.extend(b["title"] for b in extract_break_songs(sections))

    # Every scheduled song: known, once, not archived, ready, in season, playable without who's out.
    seen = set()
    for t in scheduled:
        key = normalize_title(t)
        row = db(t)
        if row is None:
            issues.append((ERROR, "unknown-song", f"{t!r} isn't in songs_metadata.csv"))
            continue
        if key in seen:
            issues.append((ERROR, "duplicate", f"{t!r} is scheduled more than once"))
        seen.add(key)
        if row.get("archived") == "Yes":
            issues.append((ERROR, "archived", f"{t!r} is archived"))
        if row.get("gig_ready") != "Yes":
            issues.append((WARNING, "gig-ready", f"{t!r} isn't gig-ready yet"))
        if not in_season(row, gig_date):
            issues.append((ERROR, "seasonal", f"{t!r} is a {row.get('seasonal')} song; "
                                              f"{gig_date or 'an undated gig'} is out of season"))
        notes = row.get("substitution_notes", "")
        for name in missing:
            if name in notes and "cut song" in notes.lower():
                issues.append((ERROR, "absent-member", f"{t!r} can't be played without {name}: {notes}"))

    # Coverage: the setlist and the request list together are exactly the active repertoire.
    requests = read_request_list(md_path)
    if requests is not None:
        # Active = playable tonight: gig-ready, not archived, in season, and
        # not cut for whoever is out.
        m_out, d_out = "Martin" in missing, "David" in missing
        active = {normalize_title(s["title"]): s["title"] for s in all_songs
                  if s.get("gig_ready") == "Yes" and s.get("archived") != "Yes" and in_season(s, gig_date)
                  and not cut_without(s, m_out, d_out)}
        req = {normalize_title(t): t for t in requests}
        both = seen & set(req)
        if both:
            issues.append((ERROR, "coverage", "on both the setlist and the request list: "
                                              + ", ".join(sorted(req[k] for k in both))))
        uncovered = set(active) - seen - set(req)
        if uncovered:
            issues.append((ERROR, "coverage", "active songs on neither the setlist nor the request list: "
                                              + ", ".join(sorted(active[k] for k in uncovered))))
        stray = set(req) - set(active)
        if stray:
            issues.append((ERROR, "coverage", "on the request list but not playable tonight (not ready, "
                                              "archived, out of season, cut for who's out, or unknown): "
                                              + ", ".join(sorted(req[k] for k in stray))))
        if len(req) != len(requests):
            issues.append((ERROR, "coverage", "the request list repeats a song"))

    return issues


def format_issues(issues):
    return "\n".join(f"  {sev:7} [{rule}] {msg}" for sev, rule, msg in issues)


def report(md_path, csv_path=DEFAULT_CSV, stream=sys.stderr):
    """Check one setlist and print what's wrong; returns the issues. Used by
    build_setlist.py and apply_substitution.py after they write a setlist."""
    issues = check_setlist(md_path, csv_path)
    errors = sum(1 for s, _, _ in issues if s == ERROR)
    if not issues:
        print("✅ Rule check: no issues", file=stream)
    else:
        mark = "❌" if errors else "⚠️ "
        print(f"{mark} Rule check: {errors} error(s), {len(issues) - errors} warning(s)", file=stream)
        print(format_issues(issues), file=stream)
    return issues


def main():
    parser = argparse.ArgumentParser(description="Check setlist .md files against the band's setlist rules")
    parser.add_argument("md_paths", nargs="+")
    parser.add_argument("--csv", default=DEFAULT_CSV)
    args = parser.parse_args()
    failed = False
    for p in args.md_paths:
        print(p)
        issues = report(p, args.csv, stream=sys.stdout)
        failed = failed or any(s == ERROR for s, _, _ in issues)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
