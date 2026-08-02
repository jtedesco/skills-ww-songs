# ww-band-songs

A Claude skill that builds setlists for the **Wannabe Weekenders** cover band from a structured song database, and keeps that database enriched with external metadata.

This repo is the skill itself: [SKILL.md](SKILL.md) is the instruction set Claude reads, `songs_metadata.csv` is the data, and `scripts/` holds the tools Claude (or you) can run directly.

## What's here

| Path | Purpose |
|---|---|
| [SKILL.md](SKILL.md) | Full instructions for Claude: substitution rules, output formats, programming strategy |
| [songs_metadata.csv](songs_metadata.csv) | Master song database (title, artist, key/BPM, vocalists, genre/mood tags, gig-readiness, etc.) |
| [eval.json](eval.json) | Test scenarios used to evaluate the skill's setlist-building behavior |
| `scripts/build_setlist.py` | Generates a setlist (`.md`, `.pdf`) from the database given constraints |
| `scripts/apply_substitution.py` | Applies targeted song swaps/removals to an *existing* setlist in place, without re-running the solver |
| `scripts/render_pdf.py` | Re-renders a styled PDF from an existing setlist `.md` file |
| `scripts/add_song.py` | Onboards a new song: checks for duplicates, fetches MusicBrainz metadata, prompts for manual fields, appends to the CSV |
| `scripts/enrich_metadata.py` | Backfills missing MusicBrainz metadata (release year, album, genre, mood) for existing songs |
| `scripts/test_setlist.py` | Automated test suite covering database constraints and setlist generation logic |
| `setlists/` | Generated setlist output, one `.md` / `.pdf` pair per gig |

## Quick start

Generate a 3-hour bar-gig setlist with acoustic breaks:

```bash
python3 scripts/build_setlist.py --gig-type bar --duration 3 --breaks acoustic --date 2026-07-18 --location "Local Bar & Grill"
```

This writes `setlists/2026-07-18 Local Bar & Grill.md` (rich metadata table) and `.pdf` (styled render of the `.md`), and best-effort copies the PDF to the band's shared Google Drive folder.

Filter by genre, era, mood, or vocalist lead counts — see `python3 scripts/build_setlist.py --help` for the full option list, or the "Automated Setlist Builder" section of [SKILL.md](SKILL.md).

### Handling a missing member

```bash
python3 scripts/build_setlist.py --duration 2 --martin-out
python3 scripts/build_setlist.py --duration 2 --david-out
```

The script applies the substitution rules documented in [SKILL.md](SKILL.md) — cutting songs that can't survive without that member, and reassigning vocals for the ones that can.

### Revising an existing setlist

Got band feedback naming specific songs to swap or cut on a setlist that's already been shared? Don't re-run `build_setlist.py` — it re-optimizes the whole setlist from scratch and will reshuffle songs nobody asked to change. Use `apply_substitution.py` instead:

```bash
python3 scripts/apply_substitution.py "setlists/2026-07-25 Bear Cave Lake.md" \
    --swap "Rock This Town" "Valerie" \
    --remove "Ooh La La"
```

Edits the `.md` in place (only the named songs change — order and everything else stays untouched), recomputes durations and the EMERGENCY CUT marker, and re-renders `.pdf`. See "Revising an Existing Setlist" in [SKILL.md](SKILL.md) for details.

### Adding a song

```bash
python3 scripts/add_song.py "Song Title" "Artist Name"
```

Fetches metadata automatically, prompts you for the fields it can't infer (key, BPM, arrangement, etc.), and appends the row to `songs_metadata.csv`.

### Keeping metadata fresh

```bash
python3 scripts/enrich_metadata.py                  # backfill missing MusicBrainz fields
```

Note: `enrich_metadata.py` doesn't support `--help` — running `--help` executes the script itself.

### Running tests

```bash
python3 scripts/test_setlist.py
```

## Requirements

- Python 3
- `pip3 install --user markdown` (for PDF rendering)
- A Chromium-based browser installed locally (Chrome, Chromium, or Edge) — used headlessly to print the `.md` report to PDF, no external API required
