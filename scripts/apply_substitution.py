#!/usr/bin/env python3
"""Apply targeted song swaps/removals to an already-generated setlist.

Unlike build_setlist.py (a from-scratch randomized solver that re-optimizes
the entire setlist on every run), this script edits an existing .md/.txt
setlist in place: only the named songs change, everything else — song
order, unrelated songs, breaks — is preserved exactly. Duration stats and
the EMERGENCY CUT marker are recomputed for the sections that changed, using
the same logic build_setlist.py uses, so a substitution can't silently leave
the setlist without a cut candidate or wrong totals.

Usage:
    python3 scripts/apply_substitution.py "setlists/2026-07-25 Bear Cave Lake.md" \\
        --swap "Rock This Town" "Valerie" \\
        --swap "Brown Eyed Girl" "Reeling in the Years" \\
        --swap "Lights" "Don't Stop Believing" \\
        --remove "Ooh La La"

Re-renders the .md and .txt in place, then the .pdf/.rtf, and syncs the
.pdf/.rtf to the shared Drive folder (same as build_setlist.py).
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
    format_md_row, format_txt_line, clean_backups, parse_covering_vocalist,
)


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
            song["bpm"] = int(song["bpm"])
            song["backup_vocals"] = [v for v in song["backup_vocals"].split(";") if v]
            all_songs.append(song)
            by_title[normalize_title(song["title"])] = song
    return by_title, all_songs


def strip_row_title(cell):
    """'**Lights** 🛑 **[EMERGENCY CUT]**' -> 'Lights'"""
    start = cell.find("**")
    end = cell.find("**", start + 2)
    if start == -1 or end == -1:
        return cell.strip()
    return cell[start + 2:end].strip()


VOCAL_CELL_RE = re.compile(r"^([A-Za-z]+)(?:\s*\(([^)]*)\))?$")


def parse_vocal_cell(cell):
    """'Jon (L, D)' -> ('Jon', ['L', 'D']); 'Lauren' -> ('Lauren', [])."""
    m = VOCAL_CELL_RE.match(cell.strip())
    if not m:
        return cell.strip(), []
    backups = [b.strip() for b in m.group(2).split(",")] if m.group(2) else []
    return m.group(1), backups


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
        for j, l in enumerate(block):
            if l.startswith("| #"):
                in_table = True
                continue
            if l.startswith("|---"):
                continue
            if in_table:
                if l.startswith("|"):
                    cells = [c.strip() for c in l.split("|")[1:-1]]
                    # | # | Title | Artist | Key | BPM | Length | Lead Vocal | Popularity | Note |
                    title = strip_row_title(cells[1])
                    lead, backups = parse_vocal_cell(cells[6])
                    rows.append({
                        "title": title, "artist": cells[2], "key": cells[3],
                        "bpm": int(cells[4]), "length": cells[5],
                        "lead_vocals": lead, "backup_vocals": backups,
                        "relative_popularity": None if cells[7] == "-" else cells[7],
                        "intro_notes": cells[8],
                    })
                else:
                    table_end = j
                    break

        trailing = block[table_end:]
        sep_idx = next((j for j, l in enumerate(trailing) if l.startswith("----")), None)
        extra_after = trailing[sep_idx + 1:] if sep_idx is not None else []

        sections.append({"heading": heading, "rows": rows, "extra_after": extra_after})

    return header_lines, sections


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
    if david_out and song["lead_vocals"] == "David":
        song["lead_vocals"] = parse_covering_vocalist(notes, "Lauren")
    if martin_out:
        song["backup_vocals"] = [b for b in song.get("backup_vocals", []) if b != "M"]
    if david_out:
        song["backup_vocals"] = [b for b in song.get("backup_vocals", []) if b != "D"]
    song["backup_vocals"] = clean_backups(song["lead_vocals"], song.get("backup_vocals", []))
    return song


def apply_ops(sections, swaps, removes, by_title, martin_out, david_out):
    """Mutate each section's row list in place per --swap/--remove.
    Existing (untouched) rows keep whatever lead/backup vocals are already
    printed (already lineup-correct); a --swap's replacement is looked up
    fresh from the database and lineup-substituted. Errors out if a
    requested title isn't found anywhere in the setlist."""
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
    """Merge in the lineup-independent fields (opener/closer/preferred_emergency_cut)
    that the .md table doesn't print but tag_emergency_cuts() and the row/tag
    formatters need — without touching lead_vocals/backup_vocals, which must
    stay whatever they already are (existing rows keep their already-applied
    lineup substitution; new rows already got it via build_new_song)."""
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


def count_break_songs(sections):
    """Break-pair songs live in each section's preserved extra_after text as
    '- **Title** (Artist) - Lead: X | **Can Leave Stage ...** bullets — count
    them so the summary's Total Songs Scheduled still matches (breaks aren't
    touched by this script). Matched on the "Can Leave Stage" phrase, unique
    to break bullets, since extra_after for the final section also picks up
    the trailing GIG SUMMARY STATS block (no heading follows it to bound it)."""
    count = 0
    for sec in sections:
        for line in sec["extra_after"]:
            if "**Can Leave Stage" in line:
                count += 1
    return count


def render_md(header_lines, sections, songs_by_section, break_song_count=0):
    out = list(header_lines)
    for sec, songs in zip(sections, songs_by_section):
        out.append(f"## {sec['heading']}")
        out.append("| # | Title | Artist | Key | BPM | Length | Lead Vocal | Popularity | Note |")
        out.append("|---|---|---|---|---|---|---|---|---|")

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

    # Recompute the grand summary stats block at the end of the file, if present
    total_songs = sum(len(s) for s in songs_by_section) + break_song_count
    total_music = sum(parse_length(sg["length"]) for songs in songs_by_section for sg in songs)
    total_trans = sum((len(songs) - 1) * 30 if len(songs) > 1 else 0 for songs in songs_by_section)

    rebuilt = []
    skip_old_summary = False
    for line in out:
        if line.startswith("### 📊 GIG SUMMARY STATS"):
            skip_old_summary = True
            rebuilt.append(line)
            continue
        if skip_old_summary:
            if line.startswith("- **Total Songs Scheduled**"):
                rebuilt.append(f"- **Total Songs Scheduled**: {total_songs}")
                continue
            if line.startswith("- **Pure Music Playtime**"):
                rebuilt.append(f"- **Pure Music Playtime**: {format_length(total_music)}")
                continue
            if line.startswith("- **Transition Buffers"):
                rebuilt.append(f"- **Transition Buffers (30s/song)**: {format_length(total_trans)}")
                continue
            if line.startswith("- **Break Time**"):
                rebuilt.append(line)  # untouched by this script — breaks aren't modified
                # Break time isn't recomputed; extract it to fold into grand total below
                continue
            if line.startswith("- **Grand Total Duration**"):
                break_seconds = 0
                for l2 in out:
                    if l2.startswith("- **Break Time**"):
                        h, m = l2.split("**Break Time**:")[1].strip().split(":")
                        break_seconds = int(h) * 60 + int(m)
                        break
                target = line.split("(Target:")[1].rstrip(")").strip() if "(Target:" in line else None
                grand = total_music + total_trans + break_seconds
                target_str = f" (Target: {target})" if target else ""
                rebuilt.append(f"- **Grand Total Duration**: {format_length(grand)}{target_str}")
                skip_old_summary = False
                continue
        rebuilt.append(line)

    return "\n".join(rebuilt).rstrip("\n") + "\n"


def render_txt(txt_path, sections, songs_by_section):
    with open(txt_path, "r", encoding="utf-8") as f:
        blocks = f.read().split("\n\n")
    # Trailing split artifact from a final newline
    while blocks and blocks[-1] == "":
        blocks.pop()

    header = blocks[0]
    main_set_songs = [songs for sec, songs in zip(sections, songs_by_section) if sec["heading"].upper().startswith("SET")]
    encore_songs = next((songs for sec, songs in zip(sections, songs_by_section) if sec["heading"].upper() == "ENCORES"), None)

    # Walk the original blocks, classifying runs of song-lines between the
    # literal "(break)" / "(encore)" markers. Regenerate "regular set" runs
    # and the "(encore)" run; preserve "(break)" runs verbatim untouched.
    out_blocks = [header]
    set_cursor = 0
    i = 1
    n = len(blocks)
    while i < n:
        b = blocks[i]
        if b.strip() == "(break)":
            # Preserve the marker and the following (untouched) break run verbatim
            out_blocks.append(b)
            i += 1
            while i < n and blocks[i].strip() not in ("(break)", "(encore)"):
                out_blocks.append(blocks[i])
                i += 1
            continue
        if b.strip() == "(encore)":
            out_blocks.append(b)
            i += 1
            if encore_songs is not None:
                # Encore songs always get the "-> " arrow prefix, even the
                # first one — only the show's true opener omits it.
                for song in encore_songs:
                    out_blocks.append(format_txt_line(song, is_first=False))
            # Skip past the original encore run
            while i < n and blocks[i].strip() not in ("(break)", "(encore)"):
                i += 1
            continue
        # A regular main-set run: consume until the next marker, regenerate from set_cursor
        while i < n and blocks[i].strip() not in ("(break)", "(encore)"):
            i += 1
        if set_cursor < len(main_set_songs):
            for idx, song in enumerate(main_set_songs[set_cursor]):
                out_blocks.append(format_txt_line(song, is_first=(idx == 0)))
            set_cursor += 1

    return "\n\n".join(out_blocks) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Apply targeted song swaps/removals to an existing setlist")
    parser.add_argument("md_path", help="Path to the existing setlist .md file")
    parser.add_argument("--swap", nargs=2, action="append", default=[], metavar=("OLD", "NEW"),
                         help="Replace OLD with NEW in place (repeatable)")
    parser.add_argument("--remove", action="append", default=[],
                         help="Remove a song with no replacement (repeatable)")
    args = parser.parse_args()

    if not args.swap and not args.remove:
        print("Error: nothing to do — pass at least one --swap or --remove", file=sys.stderr)
        sys.exit(1)

    md_path = args.md_path
    txt_path = os.path.splitext(md_path)[0] + ".txt"
    csv_path = os.path.join(SCRIPT_DIR, "..", "songs_metadata.csv")

    by_title, all_songs = load_song_db(csv_path)
    header_lines, sections = parse_md(md_path)
    martin_out, david_out = parse_missing(header_lines)

    apply_ops(sections, [tuple(s) for s in args.swap], args.remove, by_title, martin_out, david_out)
    check_no_duplicates(sections)
    enrich_static_fields(sections, by_title)

    songs_by_section = [sec["rows"] for sec in sections]

    # Recompute the EMERGENCY CUT marker for main sets only (encores never
    # carry it), using the same selection logic build_setlist.py uses, over
    # the whole (non-archived) song database's segue chains.
    non_archived = [s for s in all_songs if s.get("archived", "No") != "Yes"]
    segue_groups = get_segue_groups(non_archived)

    main_set_indices = [i for i, sec in enumerate(sections) if sec["heading"].upper().startswith("SET")]
    main_sets_songs = [songs_by_section[i] for i in main_set_indices]
    tagged = tag_emergency_cuts(main_sets_songs, segue_groups)
    for i, tagged_songs in zip(main_set_indices, tagged):
        songs_by_section[i] = tagged_songs

    break_song_count = count_break_songs(sections)
    md_content = render_md(header_lines, sections, songs_by_section, break_song_count)
    txt_content = render_txt(txt_path, sections, songs_by_section)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(txt_content)
    print(f"✅ Saved markdown  → {os.path.abspath(md_path)}", file=sys.stderr)
    print(f"✅ Saved plaintext → {os.path.abspath(txt_path)}", file=sys.stderr)

    pdf_path = None
    try:
        import render_pdf
        pdf_path = render_pdf.render(md_path)
        print(f"✅ Saved PDF       → {pdf_path}", file=sys.stderr)
    except Exception as e:
        print(f"⚠️  PDF generation skipped ({e})", file=sys.stderr)

    rtf_path = None
    try:
        import render_rtf
        rtf_path = render_rtf.render(md_path)
        print(f"✅ Saved RTF       → {rtf_path}", file=sys.stderr)
    except Exception as e:
        print(f"⚠️  RTF generation skipped ({e})", file=sys.stderr)

    if pdf_path or rtf_path:
        import shutil
        shared_drive_dir = os.path.expanduser("~/Google Drive/Shared Drives/Wannabe Weekenders/Setlists")
        for path in (pdf_path, rtf_path):
            if not path:
                continue
            try:
                shutil.copy2(path, shared_drive_dir)
                print(f"✅ Synced to Drive → {os.path.join(shared_drive_dir, os.path.basename(path))}", file=sys.stderr)
            except Exception as e:
                print(f"⚠️  Drive sync skipped for {os.path.basename(path)} ({e})", file=sys.stderr)


if __name__ == "__main__":
    main()
