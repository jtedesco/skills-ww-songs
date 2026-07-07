---
name: ww-band-songs
description: Master list of 50 cover band songs cross-referenced with genres, set opener/closer suitability, transitions, vocal roles, and gig substitution rules.
---

# Master Song List & Properties

This skill provides access to the master song database of **50 cover band songs** for the Wannabe Weekenders.

The full structured dataset containing song titles, artists, opener/closer roles, transition sequences, key/BPM details, vocal arrangements, playtimes, cleaned intro notes, song ordering rules (segue groupings), Yacht Rock classifications, gig readiness, arrangements (Acoustic / Full / Either), vocalist constraints, date added, archive status, and substitution rules is stored in [songs_metadata.csv](file:///Users/jontedesco/Documents/skills/ww-band-songs/songs_metadata.csv).

## High-Level Substitution Rules
* **Martin is Out (Rhythm Guitar / Vocals)**:
  * David covers all of Martin's lead and backup vocals.
  * Songs requiring Martin's acoustic guitar (*Colors*, *The Chain*, *Landslide*, and *Blackbird* — see `substitution_notes` in songs_metadata.csv) must be cut from the setlist. *Take It Easy*, *Me and Bobby McGee*, *Crazy Little Thing Called Love*, and *Ventura Highway* remain.
* **David is Out (Aux Percussion / Keys / Vocals)**:
  * Lauren covers David's lead vocals on *Keep Your Hands to Yourself*, *Ventura Highway*, and *Ooh La La*.
  * Keyboard / marimba sections are omitted or covered. Jon covers main piano parts (e.g. *Piano Man*), and accordion/marimba elements are omitted.

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
  python3 /Users/jontedesco/Documents/skills/ww-band-songs/scripts/build_setlist.py --genre rock
  
  # Generate a setlist containing only 70s songs
  python3 /Users/jontedesco/Documents/skills/ww-band-songs/scripts/build_setlist.py --era 70s

  # Generate a setlist containing only upbeat songs
  python3 /Users/jontedesco/Documents/skills/ww-band-songs/scripts/build_setlist.py --mood upbeat
  ```

### Database Enrichment (MusicBrainz API)
To update or enrich the song database metadata with the latest details (original release year, album, genre, and recording ID) from the MusicBrainz API, run the enrichment script:
```bash
python3 /Users/jontedesco/Documents/skills/ww-band-songs/scripts/enrich_metadata.py
```

### Verification and Evaluation
An evaluation definition is configured in [eval.json](file:///Users/jontedesco/Documents/skills/ww-band-songs/eval.json).
A comprehensive automated validation suite is provided in [test_setlist.py](file:///Users/jontedesco/Documents/skills/ww-band-songs/scripts/test_setlist.py). To run the test suite and verify database constraints and setlist generation logic:
```bash
python3 /Users/jontedesco/Documents/skills/ww-band-songs/scripts/test_setlist.py
```

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
  - Rotate lead vocalists (Lauren, Jon, Martin, David) to prevent any one singer from leading more than 3 songs in a row.
  - Separate vocally taxing/gravelly songs (such as *Zombie* or *Respect* for Lauren) by at least 2 non-taxing songs to allow recovery.

### 4. Acoustic Bathroom Breaks (Mid-Gig Downtime)
* **Goal**: Give band members restroom breaks without stopping the music.
* **Approach**:
  - Schedule 10-minute "Acoustic Sets" between main sets where only 2 members perform (e.g. Lauren & Martin on *Landslide* or Jon & David on *Vienna*).
  - Ensure the performing members do not overlap between the two songs, allowing everyone else to leave the stage.
