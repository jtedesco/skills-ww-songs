---
name: ww-band-songs
description: Master list of 50 cover band songs cross-referenced with genres, set opener/closer suitability, transitions, vocal roles, and gig substitution rules.
---

# Master Song List & Properties

This skill provides access to the master song database of **Wannabe Weekenders cover songs** (51+ and growing).

The full structured dataset containing song titles, artists, opener/closer roles, transition sequences, key/BPM details, vocal arrangements, playtimes, cleaned intro notes, song ordering rules (segue groupings), Yacht Rock classifications, gig readiness, arrangements (Acoustic / Full / Either), vocalist constraints, date added, archive status, and substitution rules is stored in [songs_metadata.csv](file:///Users/jontedesco/Documents/skills/ww-band-songs/songs_metadata.csv).

## Instrumentation Columns (Who Plays What)

To catch cases where a mid-song instrument switch would collide with a performer's next cue (e.g. someone still swapping guitars when they're supposed to be starting the next song), `songs_metadata.csv` has one column per stage part: `electric_guitar`, `acoustic_guitar` (both semicolon-separated — more than one person can be on the same part), `keys_1`, `keys_2`, `drums`, `bass`, `percussion`, `harmonica`, `accordion`, `sax`. A blank cell means nobody's on that part for that song — either it's unused, or that performer isn't active on the song (e.g. the acoustic/either-arrangement break songs, which only use a small subset of the band).

Fixed, never-switching assignments: Jon is always `keys_1` + vocals, Alex is always `drums`, Debo is always `bass`, JJ is always `electric_guitar`, Lauren is always vocals-only (no instrument column). None of these needs cross-checking against a song's notes — they're constant whenever that person is active on the song.

Martin switches between `electric_guitar` (his default) and `acoustic_guitar` on a fixed per-song list — currently: *Take It Easy*, *Me and Bobby McGee*, *Brown Eyed Girl*, *Baby Blue*, *Crazy Little Thing Called Love*, *The Chain*, *Colors* — plus every Acoustic/Either-arrangement break song where he's active (those are inherently acoustic performances). Check this list (or the per-song `intro_notes`/`substitution_notes`, which sometimes call out "Martin acoustic" explicitly) before adding a new song or changing Martin's part on an existing one — don't just default him to electric.

`keys_2`, `percussion`, `harmonica`, and `accordion` are David's remaining parts (he also covers `electric_guitar`/`acoustic_guitar` and vocals per-song) and `sax` has no assigned player yet — all left blank for now, to be backfilled per-song rather than guessed.

## Energy Arc Columns

`songs_metadata.csv` has `start_energy` and `end_energy` columns (`Low` / `Medium` / `High`) capturing how a song feels at its first and last bar — for building the set's energy arc (e.g. start and end each set high, use a Low→High "build" song to come out of an acoustic break or recover after a mellow mid-set stretch). Most songs hold one energy level throughout (`start_energy` == `end_energy`); only songs that noticeably build or wind down have different start/end values (e.g. *Me and Bobby McGee*: Low→High). This data isn't wired into `build_setlist.py`'s pacing logic yet (which currently paces by BPM only via `make_v_shape()`) — that's a natural next step if arc-aware set-building is wanted.

## Substitution Policy
When a member is out, `build_setlist.py` applies these band-wide rules automatically:
* **Martin is out**: David covers Martin's lead and backup vocals; rhythm guitar parts are dropped.
* **David is out**: Lauren covers David's lead vocals; keyboard/marimba parts are covered by Jon (piano) or omitted.

Per-song specifics — which songs must be **cut** vs. **survive** without a given member, and who covers lead vocals on which title — live entirely in `songs_metadata.csv`'s `substitution_notes` column. **Do not duplicate per-song lists here**; a hardcoded copy in this file will drift out of sync with the database as songs get added, archived, or re-arranged (this section previously listed a since-archived song as "Martin-out safe").

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
  - The script writes output files to the `setlists/` subdirectory (created automatically):
    - `<YYYY-MM-DD Location>.md` — the full Rich Metadata Table report.
    - `<YYYY-MM-DD Location>.pdf` — a styled PDF rendering of the `.md` report, generated automatically (see below). If PDF rendering fails (e.g. no Chromium-based browser installed), the script prints a warning and continues — the `.md` file is still written.
  - Pass `--date` and `--location` to control the filename, e.g. `--date 2026-07-18 --location "Local Bar & Grill"`.
  - If `--date`/`--location` are omitted the files are named `setlist_<timestamp>.md/.pdf`.

* **Local Setlist File Storage**:
  - Every generated setlist must be saved to the `setlists/` subdirectory of this skill (i.e., `skills-ww-songs/setlists/`).
  - Save **two files** per setlist, both named `YYYY-MM-DD Location` (e.g., `2026-07-25 Local Bar and Grill Wooddale`):
    - `YYYY-MM-DD Location.md` — the rich metadata table format.
    - `YYYY-MM-DD Location.pdf` — styled PDF of the `.md` report (written automatically by `build_setlist.py`; see PDF Export below).
  - If no venue is known at generation time, use only the date: `YYYY-MM-DD.md` / `.pdf`.
  - Do **not** overwrite an existing file; create a new one or confirm with the user first.

### Revising an Existing Setlist (Band Feedback)
When the band gives feedback on a setlist that's **already been generated and shared** — "swap X for Y", "drop Z", "add W" — do **not** re-run `build_setlist.py`. It's a from-scratch randomized solver: every run re-optimizes the *entire* setlist, so even a two-song request can silently reshuffle unrelated songs, drop others, and change the emergency-cut pick. The band asked for specific changes, not a new setlist — only make the changes they named.

Use `scripts/apply_substitution.py` instead. It edits the existing `.md` in place — only the named songs change, everything else (order, unrelated songs, breaks) stays byte-identical — then re-renders the `.pdf` and re-syncs to Drive, same as `build_setlist.py`:
```bash
python3 scripts/apply_substitution.py "setlists/2026-07-25 Bear Cave Lake.md" \
    --swap "Rock This Town" "Valerie" \
    --swap "Brown Eyed Girl" "Reeling in the Years" \
    --remove "Ooh La La"
```
- `--swap "Old" "New"` replaces a song in place (same slot in the running order); `--remove "Title"` drops a song with no replacement. Both are repeatable and can target a song in any set or the encore.
- It still enforces the same constraints `build_setlist.py` does, even though it isn't re-solving the whole setlist:
  - **EMERGENCY CUT marker**: recomputed after every edit using the same selection logic as `build_setlist.py` (`tag_emergency_cuts`). If a swap/removal takes out the song currently marked as the cut candidate (like replacing an EMERGENCY CUT song with a new one), a new eligible song is picked automatically — the setlist is never left without a cut candidate. Don't hand-pick a replacement cut candidate yourself; let the tool recompute it.
  - **Lineup substitutions**: reads the `Missing:` line already in the file's header, so a brand-new song added via `--swap` gets the same Martin/David-out vocal reassignment already baked into the rest of the setlist — and refuses to add a song that requires a missing member per the database (`substitution_notes` says to cut it).
  - **No duplicates**: refuses to introduce a song that's already scheduled elsewhere in the setlist.
  - **Durations**: per-section and grand-total stats are recomputed from the new song list.
  - **Songs Not Selected**: the trailing section is always fully regenerated from the final scheduled songs (see Format 1 above), so a swap correctly moves songs between "in the setlist" and "not selected."
- If the requested substitutions leave a real ambiguity the tool can't resolve on its own — e.g. the band's feedback doesn't specify which of several plausible songs to cut, or conflicts with an existing constraint in a way that has more than one reasonable fix — ask the band/user rather than guessing.
- Constraints not mentioned above (vocalist balance, pacing flow, acoustic vocalist coverage) are **not** re-validated by this script, since they depend on the whole setlist, not just the edited slots — eyeball the result for anything glaring, but don't run the full solver just to re-check them.

### PDF Export
`build_setlist.py` automatically renders the `.md` report to a styled `.pdf` in the same call — no manual conversion needed. It shells out to a local headless Chromium-based browser (Google Chrome / Chromium / Edge, whichever is found first) to print styled HTML to PDF, so no paid API or internet-dependent service is involved.

**One printable page per set**: each `## SET N` (and `## ENCORES`) heading starts a fresh page, with that section's table, duration line, and following acoustic break all kept together on it — so a printed copy never has one set's tail bleeding onto the page that starts the next set. A set with enough songs to overflow a single page (e.g. an 18-song set with no intermission) still spans multiple pages as needed; the browser repeats the table header row on the continuation page. This relies on a `prevent_setext_headings()` preprocessing step in `render_pdf.py`: CommonMark treats a line of `----` immediately following non-blank text (no blank line between) as *Setext heading* syntax, which was silently turning the `**Set N Music Duration**...` line into its own `<h2>` and confusing the per-set pagination — the same trap would also make that line render as a giant heading on GitHub or any other CommonMark viewer, so it's worth knowing about beyond just the PDF path.

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
This is a plain filesystem copy (`shutil.copy2`) into the folder synced by the Google Drive Desktop app — **never use the Google Drive MCP connector for this, for any file type, even small ones.** This was tried and explicitly ruled out by the user ("forget the MCP approach altogether"), for good reason: the connector has no chunked/resumable upload, so pushing even a moderate-size binary (like a PDF) through it means base64-encoding the whole thing into a single tool call, which blows past any single-call token budget (a ~300KB PDF is ~400K base64 characters ≈ ~400K tokens). It also has no permission-write tool, so "anyone with the link" sharing can't be automated either way. The local-copy approach sidesteps all of this.

To manually re-sync an existing file: `cp "setlists/<file>.pdf" ~/Google\ Drive/Shared\ Drives/Wannabe\ Weekenders/Setlists/`.

### Adding a New Song
To add a new song to the repertoire, run the onboarding script:
```bash
python3 scripts/add_song.py "Song Title" "Artist Name"
```
This script will automatically:
1. **Check for duplicates** — exits early if the song already exists.
2. **Fetch MusicBrainz metadata** — release year, original album, genre tags, mood tags, and recording MBID.
3. **Fetch ListenBrainz popularity** — aggregates listens across all recording versions and computes a global 1–10 score.
4. **Prompt for manual fields** — key, BPM, length, lead vocalist, backup vocals, arrangement, gig readiness, opener/closer, intro notes, substitution notes, instrumentation (electric/acoustic guitar, keys, drums, bass, percussion, harmonica, accordion, sax — defaults are pre-filled per the Instrumentation Columns rules above, e.g. Jon always on `keys_1`, Martin defaulting to acoustic on the fixed song list), and `start_energy`/`end_energy`, etc.
5. **Append the new row** to `songs_metadata.csv` with `date_added` set to the current month.
6. **Audit the full repertoire** and print a summary of: missing critical fields, not-gig-ready songs, songs without ListenBrainz data, and archived songs.

**One field the script can't decide for you**: lead vocalist is a real editorial call, not something to infer from genre/style — ask the band/user rather than guessing.

**Test suite maintenance — do this every time, not just when it's convenient**: `test_setlist.py`'s `test_database_integrity()` has two hardcoded whitelists that don't derive from the CSV automatically and silently start failing if you forget them:
- New song is **Acoustic/Either** and marked `gig_ready: Yes` → add its title to `gig_ready_acoustic`.
- New song is **Full Band** and marked `gig_ready: No` (the script's own default!) → add its title to `not_ready_full_band`, or the integrity check will fail expecting every full-band song to be ready.

Run `python3 scripts/test_setlist.py` after adding a song and before considering it done — this is the mechanical "did I miss a step" check.

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

### Rich Metadata Table

A full structured report including all song metadata, energy arc, and constraint satisfaction.

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
- **Song table** with columns: `#`, `Song`, `Artist`, `Lead`, `Backups`, `Key`, `BPM`, `Length`, `Energy`, `Intro`
- **Duration summary**: Music time, transitions, breaks, grand total
- **Gig Summary page**: A single trailing `## GIG SUMMARY` section (one PDF page, separate from the main sets — see PDF Export below) built by `render_summary_page_lines()` in `build_setlist.py`, combining four subsections:
  - `### 📊 Stats` — the same duration/song-count bullets that used to stand alone.
  - `### Lead Vocalist Breakdown` — a table of each vocalist's led-song count and % of the night (sets + breaks + encores), via `render_vocal_breakdown_lines()`.
  - `### Songs Not Selected` — gig-ready/non-archived songs that didn't make this setlist (any set, break, or encore), split into **Full Band** / **Acoustic** (which also covers `Either`-arrangement songs) bold labels, via `render_not_selected_lines()`.
  - `### Archived Songs` — everything with `archived: Yes`, via `render_archived_lines()`.

  Every `apply_substitution.py` run fully regenerates this whole page from the final scheduled songs (dropping whatever was there before — including old-format documents that had these as separate top-level sections, or nested the stats inside the last set/encore), so it's never stale after a swap/remove/add, unlike the acoustic breaks (which that script leaves untouched).
- **Songs In Progress**: its own separate trailing page, `## SONGS IN PROGRESS` (everything with `gig_ready` not `Yes` and not archived), via `render_in_progress_lines()`. Kept separate from the Gig Summary page since it's gig-independent repertoire status (always the same regardless of which gig this is), not this gig's bookkeeping.

> **Note**: Vocalist target percentages are used internally by the solver but are **not** published in the report.

---

### Segue Handling

Segue groups (songs performed with no gap between them) are defined by the `order_rules` column in `songs_metadata.csv` and enforced by `get_segue_groups()` / `tag_emergency_cuts()` in `build_setlist.py` (e.g. keeping segue-linked songs together, never picking one as the emergency cut). This matters when reading or editing a song's `intro_notes`, not just when generating a setlist:

- **Segue song order is canonical**: songs within a segue group MUST appear in the order defined by `order_rules`. This applies in sets, encores, and breaks.
- ⚠️ **Critical**: A song whose `intro_notes` begins with `SEGUE` is always the **destination** (it comes *second*). The source song has a plain intro note and appears *first*. Never invert this.
- Full canonical segue order reference (all groups):

  | Source → Destination | Source intro | Destination intro |
  |---|---|---|
  | **Superstition** → **Valerie** | `Alex starts, Lauren welcomes` | `SEGUE Bass sets tempo` |
  | **Brown Eyed Girl** → **Hey Jealousy** | `Jon starts` | `SEGUE Jon piano, Martin to electric` |
  | **Peg** → **Second Chance** | `Alex counts us in` | `SEGUE (Cmaj7 to Cm) Jon` |
  | **Funkytown** → **Miss You** → **Reeling in the Years** | `Jon starts` | `SEGUE Jon` → `SEGUE (E7 Resolve) JJ starts` |
- **Never invent or infer segue transitions** — only use what is explicitly in `intro_notes`.

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

### 4. Acoustic Breaks (Mid-Gig Downtime)
* **Goal**: Give the full band a musical breather between main sets, with vocal variety.
* **Approach**:
  - Schedule 10-minute "Acoustic Sets" between main sets, filled with acoustic/either-arrangement songs.
  - **Acoustic Vocalist Coverage**: `select_acoustic_pool_songs()` in `build_setlist.py` tries to have every present vocalist (with at least one eligible acoustic-pool song) lead at least one acoustic-break song, picking the highest-`relative_popularity` option per vocalist first, then filling any remaining slots by popularity. This is a best-effort goal, not a hard constraint — reported in the report's constraints table as "Acoustic Vocalist Coverage" (✅ full coverage / ⚠️ partial, naming who missed out / ❌ no acoustic songs available at all, silent breaks used instead).
  - Songs that require Martin's acoustic guitar (*Landslide*, *Blackbird*) are cut from the acoustic break pool when Martin is out. Songs with a Martin-out substitution (*Wish You Were Here*, *Ventura Highway*, *Ooh La La*, *All For You*) remain in the pool.
