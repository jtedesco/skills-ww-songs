#!/usr/bin/env python3
"""
add_song.py — Add a new song to the Wannabe Weekenders repertoire.

Usage:
    python3 scripts/add_song.py "Song Title" "Artist Name"

Steps:
  1. Check for duplicates.
  2. Auto-fetch metadata from MusicBrainz (year, album, genre, mood, MBID).
  3. Auto-fetch ListenBrainz popularity (global 1–10 score).
  4. Interactively prompt for manual fields (key, BPM, vocalist, etc.).
  5. Append the new row to songs_metadata.csv.
  6. Audit the full song list and print a summary.
"""
import csv
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR   = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
CSV_PATH   = os.path.join(REPO_DIR, "songs_metadata.csv")

# ---------------------------------------------------------------------------
# Global popularity anchors (site-wide ListenBrainz scale)
# ---------------------------------------------------------------------------
LOG_MIN   = math.log1p(0)           # 0.0  → score 1.0 (no listens)
LOG_MAX   = math.log1p(5_000_000)   # ≈15.42 → score 10.0 (mega-hit)
LOG_RANGE = LOG_MAX - LOG_MIN

# ---------------------------------------------------------------------------
# Helpers — shared with enrich_metadata.py / fetch_listenbrainz_popularity.py
# ---------------------------------------------------------------------------

def clean_artist(artist: str) -> str:
    if ", The" in artist:
        return "The " + artist.replace(", The", "").strip()
    return artist.strip()

def normalize_title(t: str) -> str:
    t = re.sub(r'\(.*?\)', '', t)
    t = re.sub(r'\[.*?\]', '', t)
    return "".join(c.lower() for c in t if c.isalnum())

def title_matches(rec_title: str, db_title: str) -> bool:
    rc = normalize_title(rec_title)
    dc = normalize_title(db_title)
    return bool(rc and dc and (dc in rc or rc in dc))

def get_listenbrainz_token() -> str | None:
    token = os.environ.get("LISTENBRAINZ_TOKEN")
    if token:
        return token
    cred_path = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "credentials.txt"))
    if os.path.exists(cred_path):
        try:
            with open(cred_path, "r", encoding="utf-8") as f:
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        if k.strip() in ("LISTENBRAINZ_TOKEN", "LISTENBRAINZ_USER_TOKEN"):
                            return v.strip()
        except Exception as e:
            print(f"Warning: Could not read credentials.txt: {e}", file=sys.stderr)
    return None

def mb_request(url: str):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "WannabeWeekendersSetlistBuilder/1.0 ( jon.c.tedesco@gmail.com )"}
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 503:
                time.sleep(2)
                continue
            return None
        except Exception:
            return None
    return None

# ---------------------------------------------------------------------------
# Step 1 — Duplicate check
# ---------------------------------------------------------------------------

def load_songs() -> tuple[list, list]:
    if not os.path.exists(CSV_PATH):
        print(f"Error: CSV not found at {CSV_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames)
        songs = [dict(row) for row in reader]
    return headers, songs

def check_duplicate(songs: list, title: str, artist: str) -> bool:
    for s in songs:
        if normalize_title(s["title"]) == normalize_title(title):
            print(f"\n⚠️  '{title}' by '{s['artist']}' already exists in the repertoire.")
            return True
    return False

# ---------------------------------------------------------------------------
# Step 2 — MusicBrainz metadata
# ---------------------------------------------------------------------------

MOOD_WORDS = {
    'energetic','happy','sad','chill','uplifting','melancholy','dark','aggressive',
    'relaxed','emotional','fun','angry','romantic','hype','dance','party','smooth',
    'laidback','mysterious','intense','somber','playful','dreamy','nostalgic','epic',
    'upbeat','slow','fast','heavy','light','calm','dramatic','melancholic','peaceful',
    'cheerful','quirky','sensual','warm','cool','triumphant','sombre','funky','groovy',
    'ballad','soulful','rhythmic','driving','atmospheric','bouncy','gentle','melodic'
}
STOP_WORDS = {
    'uk','british','usa','american','band','group','vocalist','singer','composer',
    'producer','rock','pop','jazz','blues','soul','metal','country','alternative',
    'indie','classic rock','hard rock','folk','disco','reggae','funk','electronic',
    'dance-pop','new wave'
}

def fetch_musicbrainz_metadata(title: str, artist: str) -> dict:
    artist_c = clean_artist(artist)
    result = {
        "release_year": "", "original_album": "",
        "musicbrainz_genre": "", "musicbrainz_mood": "", "musicbrainz_id": ""
    }

    print(f"\n🔍 Querying MusicBrainz for '{title}' by '{artist_c}'...")

    # Release group → year + album
    rg_query = f'releasegroup:"{title}" AND artist:"{artist_c}"'
    rg_url = (
        "https://musicbrainz.org/ws/2/release-group/"
        f"?query={urllib.parse.quote(rg_query)}"
        "&fmt=json"
    )
    rg_data = mb_request(rg_url)
    time.sleep(1.0)

    if rg_data and rg_data.get("release-groups"):
        rgs = sorted(
            rg_data["release-groups"],
            key=lambda x: (
                0 if x.get("primary-type") == "Album" else
                1 if x.get("primary-type") == "Single" else
                2 if x.get("primary-type") == "EP" else 3,
                -int(x.get("score", 0))
            )
        )
        best = rgs[0]
        result["original_album"] = best.get("title", "")
        fd = best.get("first-release-date", "")
        m = re.search(r'\d{4}', fd)
        if m:
            result["release_year"] = m.group(0)

    # Recording → MBID, genres, moods
    rec_query = f'recording:"{title}" AND artist:"{artist_c}"'
    rec_url = (
        "https://musicbrainz.org/ws/2/recording/"
        f"?query={urllib.parse.quote(rec_query)}"
        "&fmt=json"
    )
    rec_data = mb_request(rec_url)
    time.sleep(1.0)

    genres, moods = [], []
    artist_id = None

    if rec_data and rec_data.get("recordings"):
        best_rec = rec_data["recordings"][0]
        result["musicbrainz_id"] = best_rec.get("id", "")
        tags = sorted(best_rec.get("tags", []), key=lambda x: x.get("count", 0), reverse=True)
        for t in tags:
            name = t.get("name", "").lower()
            if name in MOOD_WORDS:
                moods.append(name)
            elif name not in STOP_WORDS and not re.match(r'^\d{2}s$', name):
                genres.append(name)
        ac = best_rec.get("artist-credit", [])
        if ac:
            artist_id = ac[0].get("artist", {}).get("id")

    # Fallback: artist tags
    if artist_id and (not genres or not moods):
        art_url = f"https://musicbrainz.org/ws/2/artist/{artist_id}?inc=tags&fmt=json"
        art_data = mb_request(art_url)
        time.sleep(1.0)
        if art_data:
            for t in sorted(art_data.get("tags", []), key=lambda x: x.get("count", 0), reverse=True):
                name = t.get("name", "").lower()
                if name in MOOD_WORDS and name not in moods:
                    moods.append(name)
                elif name not in STOP_WORDS and not re.match(r'^\d{2}s$', name) and name not in genres:
                    genres.append(name)

    result["musicbrainz_genre"] = ";".join(g.title() for g in genres[:3]) or "Rock"
    result["musicbrainz_mood"]  = ";".join(m.title() for m in moods[:3]) or "Upbeat"

    print(f"   Year: {result['release_year'] or '?'} | Album: {result['original_album'] or '?'}")
    print(f"   Genre: {result['musicbrainz_genre']} | Mood: {result['musicbrainz_mood']}")
    print(f"   MBID: {result['musicbrainz_id'] or 'not found'}")
    return result

# ---------------------------------------------------------------------------
# Step 3 — ListenBrainz popularity
# ---------------------------------------------------------------------------

def fetch_recording_mbids(title: str, artist: str) -> list[str]:
    artist_c = clean_artist(artist)
    query = f'recording:"{title}" AND artist:"{artist_c}"'
    url = f"https://musicbrainz.org/ws/2/recording/?query={urllib.parse.quote(query)}&limit=100&fmt=json"
    data = mb_request(url)
    time.sleep(1.0)
    if not data:
        return []
    return [
        rec["id"]
        for rec in data.get("recordings", [])
        if title_matches(rec.get("title", ""), title)
    ]

def fetch_lb_listens(mbids: list[str], token: str) -> int:
    if not mbids:
        return 0
    url = "https://api.listenbrainz.org/1/popularity/recording"
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
        "User-Agent": "WannabeWeekendersSetlistBuilder/1.0 ( jon.c.tedesco@gmail.com )"
    }
    total = 0
    for i in range(0, len(mbids), 20):
        chunk = mbids[i:i+20]
        payload = json.dumps({"recording_mbids": chunk}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                rd = json.loads(resp.read().decode("utf-8"))
                recordings = (
                    rd if isinstance(rd, list)
                    else rd.get("recordings", rd.get("payload", {}).get("recordings", []))
                )
                for item in recordings:
                    if isinstance(item, dict):
                        lc = item.get("total_listen_count") or item.get("listen_count") or 0
                        total += int(lc)
        except Exception as e:
            print(f"   Warning: ListenBrainz request failed: {e}", file=sys.stderr)
        time.sleep(0.5)
    return total

def compute_global_score(listens: int) -> str:
    raw = 1.0 + 9.0 * (math.log1p(listens) - LOG_MIN) / LOG_RANGE
    return f"{max(1.0, min(10.0, raw)):.2f}"

def fetch_popularity(title: str, artist: str, token: str | None) -> tuple[int, str]:
    if not token:
        print("   ⚠️  No ListenBrainz token — skipping popularity fetch.")
        return 0, ""

    print(f"\n🎧 Fetching ListenBrainz listens for '{title}'...")
    mbids = fetch_recording_mbids(title, artist)
    print(f"   Found {len(mbids)} matching recording versions.")
    listens = fetch_lb_listens(mbids, token)
    score   = compute_global_score(listens)
    print(f"   Total listens: {listens:,}  →  Popularity score: {score}/10")
    return listens, score

# ---------------------------------------------------------------------------
# Step 4 — Interactive prompts for manual fields
# ---------------------------------------------------------------------------

def prompt(label: str, default: str = "", choices: list[str] | None = None) -> str:
    choice_hint = f" [{'/'.join(choices)}]" if choices else ""
    default_hint = f" (default: {default})" if default else ""
    while True:
        raw = input(f"  {label}{choice_hint}{default_hint}: ").strip()
        if not raw and default:
            return default
        if choices and raw not in choices:
            print(f"    → Please enter one of: {', '.join(choices)}")
            continue
        return raw

def prompt_optional(label: str, default: str = "None") -> str:
    raw = input(f"  {label} (press Enter to skip, default '{default}'): ").strip()
    return raw if raw else default

def gather_manual_fields(title: str, artist: str) -> dict:
    print(f"\n📝 Manual fields for '{title}' by '{artist}'")
    print("   (Press Enter to accept defaults where shown)\n")

    key         = prompt("Musical key (e.g. G, Am, Bb)")
    bpm         = prompt("BPM (e.g. 120)")
    length      = prompt("Song length (mm:ss, e.g. 3:45)")
    lead        = prompt("Lead vocalist", choices=["Lauren", "Jon", "David", "Martin"])
    backup      = prompt_optional("Backup vocals (semicolon-separated initials, e.g. L;J;D)", default="None")
    arrangement = prompt("Arrangement", choices=["Full Band", "Acoustic", "Either"])
    gig_ready   = prompt("Gig ready?", choices=["Yes", "No"], default="No")
    opener      = prompt("Can open a set?", choices=["Yes", "No"], default="No")
    closer      = prompt("Can close a set?", choices=["Yes", "No"], default="No")
    yacht       = prompt("Yacht Rock adjacent?", choices=["Yes", "No", "Adjacent"], default="No")
    intro_notes = prompt_optional("Intro notes (who starts, e.g. 'Jon starts')")
    order_rules = prompt_optional("Order/segue rules")
    sub_notes   = prompt_optional("Substitution notes (if a member is out)")
    constraints = prompt_optional("Vocalist constraints")
    can_leave   = prompt_optional("Can leave stage notes")
    emergency   = prompt("Preferred emergency cut?", choices=["Yes", "No"], default="No")

    return {
        "key": key, "bpm": bpm, "length": length,
        "lead_vocals": lead, "backup_vocals": backup,
        "arrangement": arrangement, "gig_ready": gig_ready,
        "opener": opener, "closer": closer,
        "yacht_adjacent": yacht,
        "intro_notes": intro_notes, "order_rules": order_rules,
        "substitution_notes": sub_notes, "vocalist_constraints": constraints,
        "can_leave_stage": can_leave, "preferred_emergency_cut": emergency,
    }

# ---------------------------------------------------------------------------
# Step 5 — Append to CSV
# ---------------------------------------------------------------------------

def append_song(headers: list, songs: list, new_song: dict):
    # Ensure all headers exist
    for h in headers:
        if h not in new_song:
            new_song[h] = ""

    songs.append(new_song)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for s in songs:
            writer.writerow({k: s.get(k, "") for k in headers})

    print(f"\n✅ '{new_song['title']}' has been added to songs_metadata.csv!")

# ---------------------------------------------------------------------------
# Step 6 — Audit
# ---------------------------------------------------------------------------

CRITICAL_FIELDS = ["key", "bpm", "lead_vocals", "length", "gig_ready", "arrangement"]

def audit_song_list(songs: list):
    active = [s for s in songs if s.get("archived", "No") != "Yes"]
    archived = [s for s in songs if s.get("archived", "No") == "Yes"]

    print("\n" + "=" * 60)
    print("📋 SONG LIST AUDIT")
    print("=" * 60)
    print(f"Total songs: {len(songs)}  |  Active: {len(active)}  |  Archived: {len(archived)}")

    # --- Not gig-ready ---
    not_ready = [s for s in active if s.get("gig_ready", "").strip() != "Yes"]
    print(f"\n🚫 NOT GIG-READY ({len(not_ready)} songs):")
    if not_ready:
        for s in not_ready:
            print(f"   • {s['title']} — {s['artist']}  [gig_ready={s.get('gig_ready','')}]")
    else:
        print("   None — all active songs are gig-ready! ✓")

    # --- Missing critical fields ---
    missing = []
    for s in active:
        gaps = [f for f in CRITICAL_FIELDS if not s.get(f, "").strip()]
        if gaps:
            missing.append((s["title"], s["artist"], gaps))

    print(f"\n⚠️  MISSING CRITICAL FIELDS ({len(missing)} songs):")
    if missing:
        for title, artist, gaps in missing:
            print(f"   • {title} — {artist}  missing: {', '.join(gaps)}")
    else:
        print("   None — all critical fields populated! ✓")

    # --- No popularity data ---
    no_pop = [s for s in active if not s.get("listenbrainz_listens", "").strip()]
    print(f"\n📊 NO LISTENBRAINZ DATA ({len(no_pop)} songs):")
    if no_pop:
        for s in no_pop:
            print(f"   • {s['title']} — {s['artist']}")
    else:
        print("   All songs have ListenBrainz data! ✓")

    # --- Archived songs list ---
    print(f"\n🗄️  ARCHIVED SONGS ({len(archived)}):")
    if archived:
        for s in archived:
            print(f"   • {s['title']} — {s['artist']}")
    else:
        print("   None archived.")

    # --- Summary ---
    issues = len(not_ready) + len(missing) + len(no_pop)
    print(f"\n{'=' * 60}")
    if issues == 0:
        print("🎉 Audit complete — no issues found!")
    else:
        print(f"⚡ Audit complete — {issues} issue(s) found across the active repertoire.")
    print("=" * 60)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 scripts/add_song.py \"Song Title\" \"Artist Name\"")
        sys.exit(1)

    title  = sys.argv[1].strip()
    artist = sys.argv[2].strip()

    print(f"\n🎵 Adding: '{title}' by '{artist}'")
    print("-" * 50)

    # Load existing data
    headers, songs = load_songs()

    # 1. Duplicate check
    if check_duplicate(songs, title, artist):
        print("   Exiting without changes.")
        sys.exit(0)

    # 2. MusicBrainz metadata
    mb_meta = fetch_musicbrainz_metadata(title, artist)

    # 3. ListenBrainz popularity
    token = get_listenbrainz_token()
    listens, score = fetch_popularity(title, artist, token)

    # 4. Manual fields
    manual = gather_manual_fields(title, artist)

    # 5. Assemble row
    # Non-gig-ready songs use the literal "None" for date_added, matching
    # the rest of the database (date_added tracks when a song went live).
    date_added = datetime.now().strftime("%Y-%m") if manual["gig_ready"] == "Yes" else "None"
    new_song = {
        "title":                title,
        "artist":               artist,
        "date_added":           date_added,
        "archived":             "No",
        "release_year":         mb_meta["release_year"],
        "original_album":       mb_meta["original_album"],
        "musicbrainz_genre":    mb_meta["musicbrainz_genre"],
        "musicbrainz_mood":     mb_meta["musicbrainz_mood"],
        "musicbrainz_id":       mb_meta["musicbrainz_id"],
        "listenbrainz_listens": str(listens),
        "relative_popularity":  score,
        **manual
    }

    # 6. Append
    append_song(headers, songs, new_song)

    # 7. Audit
    _, all_songs = load_songs()
    audit_song_list(all_songs)

if __name__ == "__main__":
    main()
