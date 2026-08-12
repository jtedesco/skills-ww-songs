#!/usr/bin/env python3
"""Apply targeted song swaps/removals to an already-generated setlist.

Unlike build_setlist.py (a from-scratch randomized solver that re-optimizes
the entire setlist on every run), this script edits an existing .md
setlist in place: only the named songs change, everything else — song
order, unrelated songs, which acoustic songs fill the breaks — is preserved
exactly. Duration stats and the EMERGENCY CUT marker are recomputed for the
sections that changed, using the same logic build_setlist.py uses, so a
substitution can't silently leave the setlist without a cut candidate or
wrong totals. The trailing "Not Selected / Archived" table is always fully
regenerated from the final scheduled songs, so it never goes stale.

Usage:
    python3 scripts/apply_substitution.py "setlists/2026-07-25 Bear Cave Lake.md" \\
        --swap "Rock This Town" "Valerie" \\
        --swap "Brown Eyed Girl" "Reeling in the Years" \\
        --swap "Lights" "Don't Stop Believing" \\
        --remove "Ooh La La"

--add "Title" "Before Title" inserts a brand-new song immediately before an
existing one (e.g. building out a medley); --set-length "Title" "M:SS"
overrides a song's performed length for this instance only, without
touching songs_metadata.csv (e.g. a trimmed intro when segued into a medley).

Re-renders the .md in place, then the .pdf, and syncs the .pdf to the shared
Drive folder (same as build_setlist.py).
"""
import csv
import os
import re
import sys
import argparse
import unicodedata

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from build_setlist import (
    parse_length, format_length, get_segue_groups, tag_emergency_cuts,
    format_md_row, clean_backups, parse_covering_vocalist,
    render_in_progress_lines, render_summary_page_lines, SUMMARY_PAGE_HEADING,
    check_absent_member_notes, format_absent_member_status,
    check_energy_flow, format_pacing_flow_status,
    TABLE_HEADER, TABLE_DIVIDER,
)

# Any of these starting a line marks the beginning of the always-regenerated
# tail — old-format files (pre-dating the combined summary page) used the
# first three as separate top-level headings; new ones use just the last two.
TRAILING_HEADINGS = {"## SONGS NOT SELECTED", "## ARCHIVED SONGS", SUMMARY_PAGE_HEADING, "## SONGS IN PROGRESS"}
GIG_STATS_HEADING = "### 📊 GIG SUMMARY STATS"
BREAK_BULLET_RE = re.compile(r"^-\s*\*\*(.+?)\*\*\s*\(")
BREAK_LEAD_RE = re.compile(r"Lead:\s*(\w+)")


def normalize_title(t):
    t = unicodedata.normalize("NFKC", t)
    t = t.replace("’", "'").replace("‘", "'")
    return t.strip().lower()


def load_song_db(csv_path):
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        by_title = {}
        all_songs = []
        for row in reader:
            song = dict(row)
            # Blank bpm is a legitimate state (see "NEVER guess a key or BPM"
            # in SKILL.md) — keep it as None instead of failing to parse.
            song["bpm"] = int(song["bpm"]) if song["bpm"].strip() else None
            song["backup_vocals"] = [v for v in song["backup_vocals"].split(";") if v]
            all_songs.append(song)
            by_title[normalize_title(song["title"])] = song
    return by_title, all_songs


def _cell(cells, col, name, default=""):
    """Read one table cell by column header name, tolerating a missing
    column (older setlist written before that column existed)."""
    i = col.get(name)
    return cells[i] if i is not None and i < len(cells) else default


def strip_row_title(cell):
    """'**Lights** 🛑 **[EMERGENCY CUT]**' -> 'Lights'"""
    start = cell.find("**")
    end = cell.find("**", start + 2)
    if start == -1 or end == -1:
        return cell.strip()
    return cell[start + 2:end].strip()


def parse_energy_cell(cell):
    """'Low' -> ('Low', 'Low'); 'Low→High' -> ('Low', 'High')."""
    cell = cell.strip()
    if "→" in cell:
        start, end = cell.split("→", 1)
        return start.strip(), end.strip()
    return cell, cell


VOCAL_CELL_RE = re.compile(r"^([A-Za-z]+)(?:\s*\(for\s+([A-Za-z]+)\))?(?:\s*\(([^)]*)\))?$")


def parse_vocal_cell(cell):
    """'Jon (L, D)' -> ('Jon', None, ['L', 'D']); 'Lauren' -> ('Lauren', None, []);
    'Lauren (for David) (J)' -> ('Lauren', 'David', ['J']) — the '(for X)'
    marker (see vocal_display_string in build_setlist.py) records that this
    row's lead was reassigned away from an absent member."""
    m = VOCAL_CELL_RE.match(cell.strip())
    if not m:
        return cell.strip(), None, []
    backups = [b.strip() for b in m.group(3).split(",")] if m.group(3) else []
    return m.group(1), m.group(2), backups


def parse_missing(header_lines):
    """Read the '- **Missing:** Martin, Debo' header line so a brand-new
    song introduced via --swap gets the same lineup substitution already
    baked into every other song already in the setlist."""
    martin_out = david_out = False
    for l in header_lines:
        s = l.strip()
        if s.startswith("- **Missing:**"):
            names = [n.strip() for n in s.split("**Missing:**", 1)[1].split(",")]
            martin_out = "Martin" in names
            david_out = "David" in names
    return martin_out, david_out


def parse_missing_names(header_lines):
    """Read the '- **Missing:** David, Debo (Paul subbing on bass)' header
    line into a clean name list (['David', 'Debo']) — same source as
    parse_missing() but keeps every absent name (not just Martin/David) and
    strips trailing '(...)' asides, for the absent-member note check."""
    for l in header_lines:
        s = l.strip()
        if s.startswith("- **Missing:**"):
            raw = s.split("**Missing:**", 1)[1].strip()
            if raw == "None":
                return []
            return [part.split("(")[0].strip() for part in raw.split(",") if part.split("(")[0].strip()]
    return []


CONSTRAINT_ROW_RE = re.compile(r"^\|\s*(.+?)\s*\|.*\|$")


def upsert_constraint_rows(header_lines, updates):
    """Replace (or, for older-format files that predate a given check,
    append) rows in the CONSTRAINTS SATISFACTION SUMMARY table by
    constraint name, in the same '| Name | Status |' shape build_setlist.py
    writes. `updates` is {constraint_name: status_text}."""
    heading_idx = next((i for i, l in enumerate(header_lines)
                         if l.strip() == "### 📋 CONSTRAINTS SATISFACTION SUMMARY"), None)
    if heading_idx is None:
        return header_lines
    rows_start = heading_idx + 3  # heading, "| Constraint | Status | Notes |", "|:---|...|"
    rows_end = rows_start
    while rows_end < len(header_lines) and header_lines[rows_end].startswith("|"):
        rows_end += 1

    remaining = dict(updates)
    for i in range(rows_start, rows_end):
        m = CONSTRAINT_ROW_RE.match(header_lines[i])
        if m and m.group(1) in remaining:
            header_lines[i] = f"| {m.group(1)} | {remaining.pop(m.group(1))} |"

    insert_at = rows_end
    for name, status in remaining.items():
        header_lines.insert(insert_at, f"| {name} | {status} |")
        insert_at += 1
    return header_lines


def parse_md(md_path):
    """Split an existing setlist .md into a header block plus an ordered
    list of sections. Each section keeps its table rows (parsed back into
    song dicts using the *already-printed* data — preserving whatever
    lineup substitutions were already applied) and everything after the
    table (the duration line, the '----' separator, and any BREAK block)
    as opaque trailing lines to be re-attached untouched after regeneration.
    """
    with open(md_path, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")

    # Capture the original target duration (e.g. "180:00") before truncating
    # anything, so the regenerated stats block can keep it.
    target_duration = None
    for l in lines:
        m = re.search(r"\(Target:\s*([\d:]+)\)", l)
        if m:
            target_duration = m.group(1)
            break

    # The whole tail (gig stats, vocalist breakdown, not-selected, archived,
    # in-progress) depends on the final scheduled songs, not just the edited
    # slots, so it's always fully regenerated — drop whatever's there so it
    # isn't parsed as a normal SET/ENCORES section or duplicated. Older
    # documents nested the gig-stats block inside the last section instead
    # of giving it its own heading, so also cut at that line if a real
    # trailing heading isn't found first.
    cut_idx = next(
        (i for i, l in enumerate(lines) if l.strip() in TRAILING_HEADINGS or l.strip() == GIG_STATS_HEADING),
        None,
    )
    if cut_idx is not None:
        lines = lines[:cut_idx]

    heading_idxs = [i for i, l in enumerate(lines) if l.startswith("## ")]
    if not heading_idxs:
        print(f"Error: no '## SET N' / '## ENCORES' sections found in {md_path}", file=sys.stderr)
        sys.exit(1)

    header_lines = lines[:heading_idxs[0]]
    sections = []
    for k, h_idx in enumerate(heading_idxs):
        end_idx = heading_idxs[k + 1] if k + 1 < len(heading_idxs) else len(lines)
        heading = lines[h_idx][3:].strip()
        block = lines[h_idx + 1:end_idx]

        rows = []
        table_end = len(block)
        in_table = False
        col = {}
        for j, l in enumerate(block):
            if l.startswith("| #"):
                in_table = True
                # Index cells by header NAME, not fixed position: setlists
                # written before the Dance column existed have 9 columns and
                # current ones have 10, so a positional read would silently
                # pull Intro text out of the Dance slot on older files.
                col = {name.strip(): i for i, name in enumerate(l.split("|")[1:-1])}
                continue
            if l.startswith("|---"):
                continue
            if in_table:
                if l.startswith("|"):
                    cells = [c.strip() for c in l.split("|")[1:-1]]
                    title_cell = _cell(cells, col, "Title")
                    lead, covering_for, backups = parse_vocal_cell(_cell(cells, col, "Lead Vocal"))
                    start_energy, end_energy = parse_energy_cell(_cell(cells, col, "Energy"))
                    rows.append({
                        "title": strip_row_title(title_cell),
                        "artist": _cell(cells, col, "Artist"),
                        "key": _cell(cells, col, "Key"),
                        "bpm": int(_cell(cells, col, "BPM")),
                        "length": _cell(cells, col, "Length"),
                        "lead_vocals": lead, "backup_vocals": backups,
                        "covering_for": covering_for,
                        "start_energy": start_energy, "end_energy": end_energy,
                        "intro_notes": _cell(cells, col, "Intro"),
                        "emergency_cut": "EMERGENCY CUT" in title_cell,
                    })
                else:
                    table_end = j
                    break

        trailing = block[table_end:]
        sep_idx = next((j for j, l in enumerate(trailing) if l.startswith("----")), None)
        extra_after = trailing[sep_idx + 1:] if sep_idx is not None else []

        sections.append({"heading": heading, "rows": rows, "extra_after": extra_after})

    return header_lines, sections, target_duration


def build_new_song(by_title, title, martin_out, david_out):
    """Look up a brand-new song (introduced via --swap) from the database
    and apply the same Martin/David-out substitution rules build_setlist.py
    applies, so it matches the lineup already baked into the rest of the
    setlist. Errors out if the song can't be played with this lineup."""
    key = normalize_title(title)
    if key not in by_title:
        print(f"Error: song not found in database: {title!r}", file=sys.stderr)
        sys.exit(1)
    song = dict(by_title[key])
    notes = song.get("substitution_notes", "")
    if martin_out and notes.startswith("If Martin is out:") and "Cut song" in notes:
        print(f"Error: {title!r} requires Martin per the database and can't be added — Martin is out for this gig.", file=sys.stderr)
        sys.exit(1)
    if david_out and notes.startswith("If David is out:") and "Cut song" in notes:
        print(f"Error: {title!r} requires David per the database and can't be added — David is out for this gig.", file=sys.stderr)
        sys.exit(1)
    if martin_out and song["lead_vocals"] == "Martin":
        song["lead_vocals"] = parse_covering_vocalist(notes, "David")
        song["covering_for"] = "Martin"
    if david_out and song["lead_vocals"] == "David":
        song["lead_vocals"] = parse_covering_vocalist(notes, "Lauren")
        song["covering_for"] = "David"
    if martin_out:
        song["backup_vocals"] = [b for b in song.get("backup_vocals", []) if b != "M"]
    if david_out:
        song["backup_vocals"] = [b for b in song.get("backup_vocals", []) if b != "D"]
    song["backup_vocals"] = clean_backups(song["lead_vocals"], song.get("backup_vocals", []))
    return song


def apply_ops(sections, swaps, removes, adds, by_title, martin_out, david_out):
    """Mutate each section's row list in place per --swap/--remove/--add.
    Existing (untouched) rows keep whatever lead/backup vocals are already
    printed (already lineup-correct); a --swap's replacement and a --add
    are looked up fresh from the database and lineup-substituted. Errors
    out if a requested title isn't found anywhere in the setlist."""
    # Removes run before swaps so "move a song" (--remove it from its old
    # slot, --swap it into a new one) works in a single invocation instead
    # of the swap's fresh lookup colliding with the song's still-present
    # original occurrence and tripping the duplicate check.
    for title in removes:
        found = False
        for sec in sections:
            for i, row in enumerate(sec["rows"]):
                if normalize_title(row["title"]) == normalize_title(title):
                    del sec["rows"][i]
                    found = True
                    break
            if found:
                break
        if not found:
            print(f"Error: --remove song not found in setlist: {title!r}", file=sys.stderr)
            sys.exit(1)

    for old, new in swaps:
        found = False
        for sec in sections:
            for i, row in enumerate(sec["rows"]):
                if normalize_title(row["title"]) == normalize_title(old):
                    sec["rows"][i] = build_new_song(by_title, new, martin_out, david_out)
                    found = True
                    break
            if found:
                break
        if not found:
            print(f"Error: --swap song not found in setlist: {old!r}", file=sys.stderr)
            sys.exit(1)

    for title, before in adds:
        found = False
        for sec in sections:
            for i, row in enumerate(sec["rows"]):
                if normalize_title(row["title"]) == normalize_title(before):
                    sec["rows"].insert(i, build_new_song(by_title, title, martin_out, david_out))
                    found = True
                    break
            if found:
                break
        if not found:
            print(f"Error: --add anchor song not found in setlist: {before!r}", file=sys.stderr)
            sys.exit(1)


def apply_length_overrides(sections, overrides):
    """Override a song's *performed* length for this instance only (e.g. a
    medley where a song is trimmed to a shorter segue-in). Doesn't touch
    songs_metadata.csv — this is specific to how this gig plays the song."""
    for title, length in overrides:
        if not re.match(r"^\d{1,2}:\d{2}$", length.strip()):
            print(f"Error: --set-length value {length!r} isn't M:SS / MM:SS format", file=sys.stderr)
            sys.exit(1)
        found = False
        for sec in sections:
            for row in sec["rows"]:
                if normalize_title(row["title"]) == normalize_title(title):
                    row["length"] = length
                    found = True
                    break
            if found:
                break
        if not found:
            print(f"Error: --set-length song not found in setlist: {title!r}", file=sys.stderr)
            sys.exit(1)


def refresh_intro_notes(sections, by_title):
    """Overwrite each printed Intro cell with the current songs_metadata.csv
    value. Deliberately opt-in (--refresh-intro-notes), because a printed
    note can carry gig-specific staging the database has no place for —
    'last song of first set', 'stage banter thank yous' — and a blanket
    refresh would silently drop it. Every overwrite is printed so those
    cases are visible and can be re-added by hand. A blank CSV value is
    skipped rather than wiping a printed note."""
    changed = 0
    for sec in sections:
        for row in sec["rows"]:
            csv_row = by_title.get(normalize_title(row["title"]))
            if not csv_row:
                continue
            new = (csv_row.get("intro_notes") or "").strip()
            if new and new != row["intro_notes"]:
                print(f"   {row['title']}: {row['intro_notes']!r} -> {new!r}", file=sys.stderr)
                row["intro_notes"] = new
                changed += 1
    print(f"✅ Refreshed {changed} intro note(s) from songs_metadata.csv", file=sys.stderr)


def check_no_duplicates(sections):
    """Error out if any title now appears more than once across the setlist
    — most likely a --swap target that was already scheduled elsewhere."""
    seen = {}
    for sec in sections:
        for row in sec["rows"]:
            key = normalize_title(row["title"])
            seen.setdefault(key, []).append(row["title"])
    dupes = [titles[0] for titles in seen.values() if len(titles) > 1]
    if dupes:
        print(f"Error: song(s) now scheduled more than once: {dupes}. "
              f"Pick a different replacement or remove the existing occurrence too.", file=sys.stderr)
        sys.exit(1)


def enrich_static_fields(sections, by_title):
    """Merge in the lineup-independent fields (opener/closer/preferred_emergency_cut,
    start_energy/end_energy) that tag_emergency_cuts() and the row/tag formatters
    need — without touching lead_vocals/backup_vocals, which must stay whatever
    they already are (existing rows keep their already-applied lineup
    substitution; new rows already got it via build_new_song). Unlike vocals,
    energy is always refreshed from the database rather than preserved from
    the printed cell, since it's a static per-song fact, not a per-instance
    lineup customization."""
    for sec in sections:
        for row in sec["rows"]:
            key = normalize_title(row["title"])
            if key not in by_title:
                print(f"Error: song not found in database: {row['title']!r}", file=sys.stderr)
                sys.exit(1)
            csv_row = by_title[key]
            row["opener"] = csv_row["opener"]
            row["closer"] = csv_row["closer"]
            row["preferred_emergency_cut"] = csv_row.get("preferred_emergency_cut")
            row["start_energy"] = csv_row.get("start_energy", "")
            row["end_energy"] = csv_row.get("end_energy", "")
            row["danceable"] = csv_row.get("danceable", "")


def extract_break_songs(sections):
    """Break-pair songs live in each section's preserved extra_after text as
    '- **Title** (Artist) - Lead: X' bullets (breaks aren't touched by this
    script, so this just reads back what's already there) — used to keep
    the summary stats/vocalist-breakdown/'Not Selected / Archived' table
    accurate. Matched on the bold-title-then-open-paren shape, which GIG
    SUMMARY STATS bullets ('- **Label**: value') don't have, since
    extra_after for the final section can also pick up trailing content
    with no heading following it. Returns [{"title":..., "lead_vocals":...}]."""
    songs = []
    for sec in sections:
        for line in sec["extra_after"]:
            m = BREAK_BULLET_RE.match(line.strip())
            if m:
                lead_m = BREAK_LEAD_RE.search(line)
                songs.append({"title": m.group(1).strip(), "lead_vocals": lead_m.group(1) if lead_m else ""})
    return songs


def render_md(header_lines, sections, songs_by_section, all_songs, by_title, break_songs=None, target_duration=None):
    break_songs = break_songs or []
    out = list(header_lines)
    for sec, songs in zip(sections, songs_by_section):
        out.append(f"## {sec['heading']}")
        out.append(TABLE_HEADER)
        out.append(TABLE_DIVIDER)

        is_main_set = sec["heading"].upper().startswith("SET")
        for idx, song in enumerate(songs):
            marker = ""
            if is_main_set:
                if song.get("emergency_cut", False):
                    marker = " 🛑 **[EMERGENCY CUT]**"
                elif song["opener"] == "Yes" and idx == 0:
                    marker = " 🟢 *[Opener]*"
                elif song["closer"] == "Yes" and idx == len(songs) - 1:
                    marker = " 🔴 *[Closer]*"
            out.append(format_md_row(song, idx, marker))

        dur = sum(parse_length(s["length"]) for s in songs)
        trans = (len(songs) - 1) * 30 if len(songs) > 1 else 0
        label = "Set" if is_main_set else "Encore"
        set_num = "".join(c for c in sec["heading"] if c.isdigit())
        label_str = f"Set {set_num}" if is_main_set else "Encore"
        out.append("")
        out.append(f"**{label_str} Music Duration**: {format_length(dur)} | **Transitions**: {format_length(trans)} | **Total**: {format_length(dur + trans)}")
        out.append("-" * 40)
        out.extend(sec["extra_after"])

    # Gig-wide stats + the combined summary page are always fully
    # regenerated (not patched in place) — total_music/total_trans cover the
    # main sets/encores (songs_by_section); break duration is looked up
    # fresh from the database via each break song's title, since breaks
    # aren't structured data here, just preserved bullet text.
    total_music = sum(parse_length(sg["length"]) for songs in songs_by_section for sg in songs)
    total_trans = sum((len(songs) - 1) * 30 if len(songs) > 1 else 0 for songs in songs_by_section)

    break_seconds = 0
    for bs in break_songs:
        key = normalize_title(bs["title"])
        if key in by_title:
            break_seconds += parse_length(by_title[key]["length"])
    if len(break_songs) > 1:
        break_seconds += (len(break_songs) - 1) * 30

    total_songs = sum(len(s) for s in songs_by_section) + len(break_songs)
    grand_total = total_music + total_trans + break_seconds
    target_str = f" (Target: {target_duration})" if target_duration else ""

    stats_lines = [
        f"- **Total Songs Scheduled**: {total_songs}",
        f"- **Pure Music Playtime**: {format_length(total_music)}",
        f"- **Transition Buffers (30s/song)**: {format_length(total_trans)}",
        f"- **Break Time**: {format_length(break_seconds)}",
        f"- **Grand Total Duration**: {format_length(grand_total)}{target_str}",
    ]

    scheduled_titles = {s["title"] for songs in songs_by_section for s in songs}
    scheduled_titles |= {bs["title"] for bs in break_songs}
    all_scheduled = [s for songs in songs_by_section for s in songs] + break_songs

    # The final section's preserved extra_after ends with whatever blank
    # lines trailed the last '----' separator. Without trimming them first,
    # the append below stacks one more on every run and the gap before GIG
    # SUMMARY grows without bound (observed at 10 blank lines after repeated
    # edits). Normalize to exactly one.
    while out and not out[-1].strip():
        out.pop()

    out.append("")
    out.extend(render_summary_page_lines(stats_lines, all_scheduled, all_songs, scheduled_titles))
    out.append("")
    out.extend(render_in_progress_lines(all_songs))

    return "\n".join(out).rstrip("\n") + "\n"


def main():
    parser = argparse.ArgumentParser(description="Apply targeted song swaps/removals to an existing setlist")
    parser.add_argument("md_path", help="Path to the existing setlist .md file")
    parser.add_argument("--swap", nargs=2, action="append", default=[], metavar=("OLD", "NEW"),
                         help="Replace OLD with NEW in place (repeatable)")
    parser.add_argument("--remove", action="append", default=[],
                         help="Remove a song with no replacement (repeatable)")
    parser.add_argument("--add", nargs=2, action="append", default=[], metavar=("TITLE", "BEFORE"),
                         help="Insert TITLE immediately before an existing song BEFORE (repeatable)")
    parser.add_argument("--set-length", nargs=2, action="append", default=[], metavar=("TITLE", "LENGTH"),
                         help="Override a song's performed length (M:SS) for this instance only, e.g. a trimmed medley segue-in (repeatable)")
    parser.add_argument("--refresh-intro-notes", action="store_true",
                         help="Overwrite every printed Intro cell with the current songs_metadata.csv value. "
                              "Off by default: printed notes can carry gig-specific staging the database lacks "
                              "(e.g. 'last song of first set'), so each overwrite is printed for review.")
    args = parser.parse_args()

    if not args.swap and not args.remove and not args.add and not args.refresh_intro_notes:
        print("Error: nothing to do — pass at least one --swap, --remove, --add, or --refresh-intro-notes", file=sys.stderr)
        sys.exit(1)

    md_path = args.md_path
    csv_path = os.path.join(SCRIPT_DIR, "..", "songs_metadata.csv")

    by_title, all_songs = load_song_db(csv_path)
    header_lines, sections, target_duration = parse_md(md_path)
    martin_out, david_out = parse_missing(header_lines)

    apply_ops(sections, [tuple(s) for s in args.swap], args.remove, [tuple(a) for a in args.add],
              by_title, martin_out, david_out)
    check_no_duplicates(sections)
    enrich_static_fields(sections, by_title)
    if args.refresh_intro_notes:
        refresh_intro_notes(sections, by_title)
    apply_length_overrides(sections, [tuple(s) for s in args.set_length])

    songs_by_section = [sec["rows"] for sec in sections]

    # EMERGENCY CUT selection is independent of reordering: a --swap/--remove/
    # --add that doesn't touch the currently-marked song (including a plain
    # reorder via --remove + --add of some other title) must leave the mark
    # exactly where it is — including a hand-picked override in the source
    # .md that doesn't match what tag_emergency_cuts() would have picked.
    # Only recompute (same selection logic build_setlist.py uses, over the
    # whole non-archived song database's segue chains) when the previously
    # marked song is no longer present post-edit — swapped out, removed, or
    # this is an older file that never had a mark — so the setlist is never
    # left without a cut candidate. Checked per SET, not globally: each set
    # carries its own independent cut candidate, so one set losing its mark
    # must not force a recompute (and thus overwrite a hand-picked override)
    # in an unrelated set that still has one.
    main_set_indices = [i for i, sec in enumerate(sections) if sec["heading"].upper().startswith("SET")]
    main_sets_songs = [songs_by_section[i] for i in main_set_indices]
    sets_needing_recompute = [
        local_idx for local_idx, songs in enumerate(main_sets_songs)
        if not any(song.get("emergency_cut", False) for song in songs)
    ]
    if sets_needing_recompute:
        non_archived = [s for s in all_songs if s.get("archived", "No") != "Yes"]
        segue_groups = get_segue_groups(non_archived)
        tagged = tag_emergency_cuts(main_sets_songs, segue_groups)
        for local_idx in sets_needing_recompute:
            songs_by_section[main_set_indices[local_idx]] = tagged[local_idx]

    missing_names = parse_missing_names(header_lines)
    labeled_sections = [(sections[i]["heading"], songs_by_section[i]) for i in range(len(sections))]
    energy_drops = check_energy_flow(labeled_sections)
    absent_flags = check_absent_member_notes(
        [s for songs in songs_by_section for s in songs], missing_names)
    header_lines = upsert_constraint_rows(header_lines, {
        "Pacing Flow": format_pacing_flow_status(energy_drops),
        "Absent-Member Note Check": format_absent_member_status(absent_flags),
    })

    break_songs = extract_break_songs(sections)
    md_content = render_md(header_lines, sections, songs_by_section, all_songs, by_title, break_songs, target_duration)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"✅ Saved markdown  → {os.path.abspath(md_path)}", file=sys.stderr)

    pdf_path = None
    try:
        import render_pdf
        pdf_path = render_pdf.render(md_path)
        print(f"✅ Saved PDF       → {pdf_path}", file=sys.stderr)
    except Exception as e:
        print(f"⚠️  PDF generation skipped ({e})", file=sys.stderr)

    if pdf_path:
        import shutil
        shared_drive_dir = os.path.expanduser("~/Google Drive/Shared Drives/Wannabe Weekenders/Setlists")
        try:
            shutil.copy2(pdf_path, shared_drive_dir)
            print(f"✅ Synced to Drive → {os.path.join(shared_drive_dir, os.path.basename(pdf_path))}", file=sys.stderr)
        except Exception as e:
            print(f"⚠️  Drive sync skipped for {os.path.basename(pdf_path)} ({e})", file=sys.stderr)


if __name__ == "__main__":
    main()
