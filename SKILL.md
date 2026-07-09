---
name: ww-band-songs
description: Master list of 50 cover band songs cross-referenced with genres, set opener/closer suitability, transitions, vocal roles, and gig substitution rules.
---

# Master Song List & Properties

This skill provides access to the master song database of **Wannabe Weekenders cover songs** (51+ and growing).

The full structured dataset containing song titles, artists, opener/closer roles, transition sequences, key/BPM details, vocal arrangements, playtimes, cleaned intro notes, song ordering rules (segue groupings), Yacht Rock classifications, gig readiness, arrangements (Acoustic / Full / Either), vocalist constraints, date added, archive status, and substitution rules is stored in [songs_metadata.csv](file:///Users/jontedesco/Documents/skills/ww-band-songs/songs_metadata.csv).

## Substitution Policy
When a member is out, `build_setlist.py` applies these band-wide rules automatically:
* **Martin is out**: David covers Martin's lead and backup vocals; rhythm guitar parts are dropped.
* **David is out**: Lauren covers David's lead vocals; keyboard/marimba parts are covered by Jon (piano) or omitted.

Per-song specifics — which songs must be **cut** vs. **survive** without a given member, who covers lead vocals on which title, and who's active on stage (and can therefore step off during an acoustic break) — live entirely in `songs_metadata.csv`'s `substitution_notes` and `can_leave_stage` columns. **Do not duplicate per-song lists here**; a hardcoded copy in this file will drift out of sync with the database as songs get added, archived, or re-arranged (this section previously listed a since-archived song as "Martin-out safe").

`can_leave_stage` is the authoritative source for who's actively performing a given acoustic/either-arrangement song — it's the *complement* of the active set (everyone NOT listed is on stage for that song). A trailing `(Acoustic)` or `(Full Band)` tag disambiguates which arrangement of an "Either" song the list applies to. `build_setlist.py`'s `get_active_performers()` reads this column directly (falling back to lead+backup vocals only if a song hasn't been backfilled yet) — the full band roster it diffs against is the single `BAND_ROSTER` constant near the top of the script, kept in a fixed order so "who's attending" is always computed deterministically rather than depending on Python's per-run hash-randomized set ordering.

## Automated Setlist Builder & Tests
The skill includes an automated setlist building script: `build_setlist.py`.
You can execute it using:
```bash
python3 /Users/jontedesco/Documents/skills/ww-band-songs/scripts/build_setlist.py --gig-type bar --duration 3 --breaks acoustic
```
Refer to the script's help menu (`--help`) for all options.
* **Genre, Era & Mood Filtering**: You can filter the setlist by genre, era, or mood:
  ```bash
  # Generate a setlist containing only rock songs
  python3 scripts/build_setlist.py --genre rock
  
  # Generate a setlist containing only 70s songs
  python3 scripts/build_setlist.py --era 70s

  # Generate a setlist containing only upbeat songs
  python3 scripts/build_setlist.py --mood upbeat
  ```

* **Vocalist Lead Limits**: You can specify minimum and/or maximum lead vocals constraints for each singer (Lauren, Jon, David, Martin) using the following parameters:
  - `--min-david <count>`, `--max-david <count>`
  - `--min-martin <count>`, `--max-martin <count>`
  - `--min-lauren <count>`, `--max-lauren <count>`
  - `--min-jon <count>`, `--max-jon <count>`
  
  Example:
  ```bash
  # Generate a 1.25hr setlist limiting David to <= 2 leads, Martin to <= 1 lead
  python3 scripts/build_setlist.py --duration 1.25 --skip-country-grunge --max-david 2 --max-martin 1
  ```

* **File Output**:
  - The script writes three output files to the `setlists/` subdirectory (created automatically):
    - `<YYYY-MM-DD Location>.md` — the full Rich Metadata Table report.
    - `<YYYY-MM-DD Location>.txt` — the Plaintext Arrow Notation performance script.
    - `<YYYY-MM-DD Location>.pdf` — a styled PDF rendering of the `.md` report, generated automatically (see below). If PDF rendering fails (e.g. no Chromium-based browser installed), the script prints a warning and continues — the `.md`/`.txt` files are still written.
  - Pass `--date` and `--location` to control the filename, e.g. `--date 2026-07-18 --location "Local Bar & Grill"`.
  - If `--date`/`--location` are omitted the files are named `setlist_<timestamp>.md/.txt/.pdf`.

* **Local Setlist File Storage**:
  - Every generated setlist must be saved to the `setlists/` subdirectory of this skill (i.e., `skills-ww-songs/setlists/`).
  - Save **three files** per setlist, all named `YYYY-MM-DD Location` (e.g., `2026-07-25 Local Bar and Grill Wooddale`):
    - `YYYY-MM-DD Location.md` — the rich metadata table format (Format 1).
    - `YYYY-MM-DD Location.txt` — the plaintext arrow notation format (Format 2).
    - `YYYY-MM-DD Location.pdf` — styled PDF of the `.md` report (written automatically by `build_setlist.py`; see PDF Export below).
  - If no venue is known at generation time, use only the date: `YYYY-MM-DD.md` / `.txt` / `.pdf`.
  - Do **not** overwrite an existing file; create a new one or confirm with the user first.

### PDF Export
`build_setlist.py` automatically renders the `.md` report to a styled `.pdf` in the same call — no manual conversion needed. It shells out to a local headless Chromium-based browser (Google Chrome / Chromium / Edge, whichever is found first) to print styled HTML to PDF, so no paid API or internet-dependent service is involved.

To (re-)render a PDF for an existing setlist `.md` file without regenerating the setlist itself:
```bash
python3 scripts/render_pdf.py "setlists/2026-07-25 Bear Cave Lake.md"

# Re-render every .md file in setlists/
python3 scripts/render_pdf.py --all
```
Requires the `markdown` Python package (`pip3 install --user markdown`) and a Chromium-based browser installed locally.

### Syncing PDFs to Shared Google Drive
After rendering, `build_setlist.py` also copies the `.pdf` (best-effort — failures just print a warning) to the local Google Drive Desktop mount for the band's shared drive:
```
~/Google Drive/Shared Drives/Wannabe Weekenders/Setlists/
```
This is a plain filesystem copy (`shutil.copy2`) into the folder synced by the Google Drive Desktop app — **do not** use the Google Drive MCP connector for this. That connector was tried and ruled out: it has no chunked/resumable upload, so pushing a several-hundred-KB PDF through it requires base64-encoding the whole file into a single tool call, which blows past any single-call token budget (a ~300KB PDF is ~400K base64 characters ≈ ~400K tokens). It also has no permission-write tool, so "anyone with the link" sharing can't be automated either way. The local-copy approach sidesteps both problems entirely.

To manually re-sync an existing PDF: `cp "setlists/<file>.pdf" ~/Google\ Drive/Shared\ Drives/Wannabe\ Weekenders/Setlists/`.

### Adding a New Song
To add a new song to the repertoire, run the onboarding script:
```bash
python3 scripts/add_song.py "Song Title" "Artist Name"
```
This script will automatically:
1. **Check for duplicates** — exits early if the song already exists.
2. **Fetch MusicBrainz metadata** — release year, original album, genre tags, mood tags, and recording MBID.
3. **Fetch ListenBrainz popularity** — aggregates listens across all recording versions and computes a global 1–10 score.
4. **Prompt for manual fields** — key, BPM, length, lead vocalist, backup vocals, arrangement, gig readiness, opener/closer, intro notes, substitution notes, etc.
5. **Append the new row** to `songs_metadata.csv` with `date_added` set to the current month.
6. **Audit the full repertoire** and print a summary of: missing critical fields, not-gig-ready songs, songs without ListenBrainz data, and archived songs.

### Database Enrichment (MusicBrainz API)
To update or enrich the song database metadata with the latest details (original release year, album, genre, and recording ID) from the MusicBrainz API, run the enrichment script:
```bash
python3 scripts/enrich_metadata.py
```

### ListenBrainz Popularity
To refresh the `relative_popularity` scores for all songs, run:
```bash
python3 scripts/fetch_listenbrainz_popularity.py
```
Popularity uses a **global log-scale** anchored to site-wide ListenBrainz data (not relative to our setlist):
- Score **1.0** = 0 listens (unknown/obscure)
- Score **~5–6** = ~10K–50K listens (known track)
- Score **~8–9** = ~500K listens (popular classic)
- Score **10.0** = 5,000,000+ listens (global mega-hit)

This means a score is **stable** — adding a new song won't shift every other song's score.

### Verification and Evaluation
An evaluation definition is configured in [eval.json](file:///Users/jontedesco/Documents/skills/ww-band-songs/eval.json).
A comprehensive automated validation suite is provided in [test_setlist.py](file:///Users/jontedesco/Documents/skills/ww-band-songs/scripts/test_setlist.py). To run the test suite and verify database constraints and setlist generation logic:
```bash
python3 scripts/test_setlist.py
```

## Setlist Output Format

**Every generated setlist must be presented in TWO formats, in this order:**

---

### Format 1 — Rich Metadata Table

A full structured report including all song metadata, popularity scores, and constraint satisfaction.

**Title**: `# YYYY-MM-DD - Location` — matches the file naming convention exactly (date, then venue). If venue or date is unknown, use whichever is known; fall back to `# Setlist` if neither is known.

**Header block** (bullet list directly under the title, no table — keeps it compact/token-light):
```
- **Gig Type:** Bar / Yacht
- **Duration:** {N} min  — or —  {N} sets (~{M} min each), {H} hrs total
- **Missing:** None / Martin / David
- **Filters:** Genre: X, Era: Y, No Grunge, No Country  (omit this line entirely if no filters)
- **Breaks:** None / Acoustic ({N} × 10 min)
```
All "who's missing" and event-detail info lives here — do not restate it in the title or duplicate a full member roster elsewhere in the document. Detailed substitution effects (which songs get cut, who covers which vocal parts) still belong in a `[!WARNING]` callout below the header, since that's unique actionable info beyond just *who's* missing.

- **Constraints satisfaction table**: One row per constraint (✅/❌), with pass/fail
- **Song table** with columns: `#`, `Song`, `Artist`, `Lead`, `Backups`, `Key`, `BPM`, `Length`, `Popularity`, `Notes`
- **Duration summary**: Music time, transitions, breaks, grand total

> **Note**: Vocalist target percentages are used internally by the solver but are **not** published in the report.

---

### Format 2 — Plaintext Arrow Notation

A performance-script-style view used in rehearsal notes and Google Docs. Rules:

**Header line:**
```
{N}x {duration} sets ({who's missing, or "None Missing"})
```
Example: `1x 75 Min set (None Missing)` or `3x 1hr sets (No David)`

**Per-song lines** (one per song, separated by blank lines):
```
[Starter instruction] [Song Title] ([Lead Vocalist] +[BackupInitials]) [Key] [tags]
```

- First song has **no** `->` prefix. Every subsequent song starts with `-> `.
- **Segues** (no gap between songs) use `-> SEGUE [transition note] [Song Title] ...` on its own line.
- **Segue song order is canonical**: songs within a segue group MUST appear in the order defined by `order_rules` in `songs_metadata.csv`. This applies in sets, encores, and breaks.
  - ⚠️ **Critical**: A song whose `intro_notes` begins with `SEGUE` is always the **destination** (it comes *second*). The source song has a plain intro note and appears *first*. Never invert this.
  - Full canonical segue order reference (all groups):

    | Source → Destination | Source intro | Destination intro |
    |---|---|---|
    | **Superstition** → **Valerie** | `Alex starts, Lauren welcomes` | `SEGUE Bass sets tempo` |
    | **Brown Eyed Girl** → **Hey Jealousy** | `Jon starts` | `SEGUE Jon piano, Martin to electric` |
    | **Peg** → **Second Chance** | `Alex counts us in` | `SEGUE (Cmaj7 to Cm) Jon` |
    | **Funkytown** → **Miss You** → **Reeling in the Years** | `Jon starts` | `SEGUE Jon` → `SEGUE (E7 Resolve) JJ starts` |
- **Set breaks** are indicated with a blank line followed by `(break)` on its own line.
- **Encore** is indicated with `(encore)` on its own line.
- `[Emergency Cut #N]` tag appended at the end of a line for emergency-cut songs.

**Starter instructions** come directly from the `intro_notes` column in `songs_metadata.csv`. Rules:

- If `intro_notes` begins with `SEGUE`, render the song line as `-> SEGUE [rest of intro_notes] [Song Title] ...` — indicating a direct musical bridge with no gap between songs.
- If `intro_notes` is a plain instruction (e.g. `JJ starts`, `Alex counts us in`), render it verbatim at the start of the song line.
- If `intro_notes` is `TBD` or empty, omit the starter instruction entirely and just render `-> [Song Title] ...` (do **not** invent one).
- **Never invent or infer segue transitions** — only use what is explicitly in `intro_notes`.

**Stars / highlights**: There is no star field in the data. Do **not** add `*` markers to any song line unless a `starred` or `highlight` column is added to `songs_metadata.csv` in the future.

**Backup vocal notation** `+[initials]`:
- Initials: `L` = Lauren, `J` = Jon, `D` = David, `M` = Martin
- `+3` = all three backup vocalists present
- Example: `(Lauren +JD)` = Lauren leads, Jon & David harmonize

**Example:**
```
1x 75 Min set (None Missing)

Alex counts us in Working for the Weekend (Lauren +JD) [Bm] [OPENER]

-> JJ starts Rock This Town (Lauren) [D]

-> Jon starts Jenny (867-5309) (Jon +LM) [F#m]

-> SEGUE Jon stays on keys Brandy (Jon +L) [E]

-> Jon starts Rikki Don't Lose That Number (Jon +LD) [E]

* -> Alex counts us in, start together Superstition (Lauren) [E] *

-> SEGUE Bass sets tempo Valerie (Lauren) [E]

-> Jon starts Roll with the Changes (Lauren +JD) [C] [CLOSER]

(encore)

-> JJ starts All Right Now (Lauren +JD) [A]

-> Martin to dropped D, starts The Middle (Martin +3) [D]
```

---

## Setlist Programming Strategies

When generating setlists, consider the following programming strategies to optimize the gig's energy flow and crowd response:

### 1. The Energy Bell Curve (BPM V-Shape)
* **Goal**: Maximize impact at key moments of the set.
* **Approach**: 
  - Start the set with a high-energy, recognizable opener (e.g. *Working for the Weekend*).
  - Transition into mid-tempo songs in the middle of the set to allow the crowd (and singers) to breathe.
  - Ramp energy back up for a dramatic finish, ending on the highest-energy, crowd-pleasing show closers (e.g. *Roll with the Changes*).
* **Implementation**: The `build_setlist.py` script applies a V-shape pacing order automatically to the middle items of a set using BPM.

### 2. The Dance Peak (Late-Set Momentum)
* **Goal**: Build a sustained dance floor during the second half of the set.
* **Approach**:
  - Group dancing-friendly songs back-to-back (e.g., *Valerie*, *Superstition*, *Funkytown*, *Pink Pony Club*) towards the end of the set (just before the closer).
  - Minimize transitions (keep the 30-second transition buffer tight or segue them where possible) to maintain momentum.

### 3. Vocalist Vibe & Health Rotations
* **Goal**: Keep the band's stage presence dynamic while protecting vocal cords.
* **Approach**:
  - **Every vocalist present at the gig must have at least one lead song. This rule supersedes the 3-in-a-row rotation rule** — if giving a vocalist their required lead creates a streak, the streak is acceptable. Absent vocalists (e.g. "No David") are exempt.
  - Rotate lead vocalists (Lauren, Jon, Martin, David) to prevent any one singer from leading more than 3 songs in a row (secondary to the rule above).
  - Separate vocally taxing/gravelly songs (such as *Zombie* or *Respect* for Lauren) by at least 2 non-taxing songs to allow recovery.

### 4. Acoustic Bathroom Breaks (Mid-Gig Downtime)
* **Goal**: Give band members restroom breaks without stopping the music.
* **Approach**:
  - Schedule 10-minute "Acoustic Sets" between main sets where only a subset of members perform.
  - The two songs in the acoustic break must use non-overlapping performer sets (max 1 overlap allowed as fallback), so that different members rotate off stage.
  - When Martin is out, Lauren and David will appear in nearly every available acoustic song — Lauren because she's the primary vocalist, David because he's covering Martin's parts. This is expected. The break overlap check excludes both of them in that case and focuses on giving the *rest of the band* a rotation opportunity.
  - Songs that require Martin's acoustic guitar (*Landslide*, *Blackbird*) are cut from the acoustic break pool when Martin is out. Songs with a Martin-out substitution (*Wish You Were Here*, *Ventura Highway*, *Ooh La La*, *All For You*) remain in the pool.
