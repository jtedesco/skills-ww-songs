# ww-band-songs

A Claude skill that builds setlists for the **Wannabe Weekenders** cover band from a structured song database, and keeps that database enriched with external metadata.

This repo is the skill itself: [SKILL.md](SKILL.md) is the instruction set Claude reads, `songs_metadata.csv` is the data, and `scripts/` holds the tools Claude (or you) can run directly.

## What's here

| Path | Purpose |
|---|---|
| [SKILL.md](SKILL.md) | Full instructions for Claude: substitution rules, output formats, programming strategy |
| [songs_metadata.csv](songs_metadata.csv) | Master song database (title, artist, key/BPM, vocalists, genre/mood tags, popularity, gig-readiness, etc.) |
| [eval.json](eval.json) | Test scenarios used to evaluate the skill's setlist-building behavior |
| `scripts/build_setlist.py` | Generates a setlist (`.md`, `.txt`, `.pdf`) from the database given constraints |
| `scripts/render_pdf.py` | Re-renders a styled PDF from an existing setlist `.md` file |
| `scripts/add_song.py` | Onboards a new song: checks for duplicates, fetches MusicBrainz/ListenBrainz data, prompts for manual fields, appends to the CSV |
| `scripts/enrich_metadata.py` | Backfills missing MusicBrainz metadata (release year, album, genre, mood) for existing songs |
| `scripts/fetch_listenbrainz_popularity.py` | Refreshes the global `relative_popularity` score for all songs |
| `scripts/test_setlist.py` | Automated test suite covering database constraints and setlist generation logic |
| `setlists/` | Generated setlist output, one `.md` / `.txt` / `.pdf` triplet per gig |

## Quick start

Generate a 3-hour bar-gig setlist with acoustic breaks:

```bash
python3 scripts/build_setlist.py --gig-type bar --duration 3 --breaks acoustic --date 2026-07-18 --location "Local Bar & Grill"
```

This writes `setlists/2026-07-18 Local Bar & Grill.md` (rich metadata table), `.txt` (plaintext arrow-notation performance script), and `.pdf` (styled render of the `.md`), and best-effort copies the PDF to the band's shared Google Drive folder.

Filter by genre, era, mood, or vocalist lead counts — see `python3 scripts/build_setlist.py --help` for the full option list, or the "Automated Setlist Builder" section of [SKILL.md](SKILL.md).

### Handling a missing member

```bash
python3 scripts/build_setlist.py --duration 2 --martin-out
python3 scripts/build_setlist.py --duration 2 --david-out
```

The script applies the substitution rules documented in [SKILL.md](SKILL.md) — cutting songs that can't survive without that member, and reassigning vocals for the ones that can.

### Adding a song

```bash
python3 scripts/add_song.py "Song Title" "Artist Name"
```

Fetches metadata automatically, prompts you for the fields it can't infer (key, BPM, arrangement, etc.), and appends the row to `songs_metadata.csv`.

### Keeping metadata fresh

```bash
python3 scripts/enrich_metadata.py                  # backfill missing MusicBrainz fields
python3 scripts/fetch_listenbrainz_popularity.py     # refresh popularity scores
```

Note: `enrich_metadata.py` and `fetch_listenbrainz_popularity.py` don't support `--help` — running `--help` executes the script itself.

### Running tests

```bash
python3 scripts/test_setlist.py
```

## Requirements

- Python 3
- `pip3 install --user markdown` (for PDF rendering)
- A Chromium-based browser installed locally (Chrome, Chromium, or Edge) — used headlessly to print the `.md` report to PDF, no external API required
