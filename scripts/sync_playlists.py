"""Keep three canonical YouTube Music playlists in sync with the song database.

The band's library, not any one gig:

    Active       gig-ready and not archived — the current repertoire
    In Progress  being learned, not yet gig-ready
    Archived     retired from the set

Membership comes from `gig_ready` and `archived` in songs_metadata.csv, and the
recordings from its `youtube_url` column, so a sync makes no search calls and
can't drift between runs. Archived wins over gig-ready: a song that was
gig-ready and has since been retired belongs in Archived only.

Each playlist is created once and thereafter **diffed**, not rebuilt — only the
added and removed songs are touched, so re-running is cheap and the link the
band already has keeps working. Ids live in playlists.json at the repo root;
commit it, or a later sync will make duplicates.

    python3 scripts/sync_playlists.py --dry-run     # show the diff, change nothing
    python3 scripts/sync_playlists.py               # apply
    python3 scripts/sync_playlists.py --only active
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_playlist import BAND, SEP, PLAYLIST_URL, connect

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "songs_metadata.csv")
STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "playlists.json")
URL_COL = "youtube_url"

PLAYLISTS = [
    ("active", "Active Songs",
     "The band's current repertoire — gig-ready and in rotation."),
    ("in_progress", "In Progress",
     "Songs being learned. Not yet gig-ready."),
    ("archived", "Archived",
     "Retired from the set. Kept for reference."),
]


# YouTube rejects large add requests with "HTTP 409: Conflict" — 46 at once
# fails where 20 succeeds. Chunk, and halve on conflict before giving up.
ADD_BATCH = 20


def add_in_batches(yt, playlist_id, video_ids, batch=ADD_BATCH):
    """Add tracks in chunks, shrinking the chunk on a 409 rather than failing.
    Returns the number added."""
    added, i = 0, 0
    while i < len(video_ids):
        size = min(batch, len(video_ids) - i)
        while True:
            try:
                yt.add_playlist_items(playlist_id, video_ids[i:i + size], duplicates=False)
                added += size
                i += size
                break
            except Exception as e:
                if "409" in str(e) and size > 1:
                    size = max(1, size // 2)   # too big for the server; try smaller
                    continue
                raise
        if i < len(video_ids):
            time.sleep(1)   # the API is eventually consistent; don't hammer it
    return added


def yes(v):
    return (v or "").strip().lower() == "yes"


def category(row):
    """Archived first: a retired song is archived even if still flagged gig-ready."""
    if yes(row.get("archived")):
        return "archived"
    return "active" if yes(row.get("gig_ready")) else "in_progress"


def load_songs(csv_path):
    buckets = {key: [] for key, _, _ in PLAYLISTS}
    missing = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            url = (row.get(URL_COL) or "").strip()
            if not url:
                missing.append(row["title"])
                continue
            buckets[category(row)].append({
                "title": row["title"], "artist": row["artist"],
                "video_id": url.rsplit("v=", 1)[-1],
            })
    for key in buckets:
        buckets[key].sort(key=lambda s: s["title"].lower())
    return buckets, missing


def read_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def write_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def sync_one(yt, key, title, blurb, songs, state, dry_run, privacy):
    full_title = f"{BAND}{SEP}{title}"
    entry = state.get(key, {})
    playlist_id = entry.get("playlist_id")
    want = [s["video_id"] for s in songs]
    desc = f"{blurb} {len(songs)} songs. Synced {datetime.now():%Y-%m-%d} from songs_metadata.csv."

    existing = []
    if playlist_id:
        try:
            existing = yt.get_playlist(playlist_id, limit=None).get("tracks", []) if yt else []
        except Exception as e:
            print(f"  ! can't read existing playlist {playlist_id}: {e}", file=sys.stderr)
            print("    Remove its entry from playlists.json to create a fresh one.", file=sys.stderr)
            return False

    have = {t.get("videoId") for t in existing if t.get("videoId")}
    to_add = [v for v in want if v not in have]
    # Remove by setVideoId, which is the per-playlist row handle, not the song id.
    to_remove = [t for t in existing if t.get("videoId") and t["videoId"] not in set(want)
                 and t.get("setVideoId")]

    print(f"\n{full_title}: {len(songs)} song(s)")
    if not playlist_id:
        print(f"  will CREATE with {len(want)} track(s)")
    else:
        print(f"  {len(have)} already there, +{len(to_add)} to add, -{len(to_remove)} to remove")
    for s in songs:
        if s["video_id"] in to_add and playlist_id:
            print(f"    + {s['title']} — {s['artist']}")
    for t in to_remove:
        print(f"    - {t.get('title', t['videoId'])}")

    if dry_run:
        return False
    if not playlist_id:
        playlist_id = yt.create_playlist(full_title, desc, privacy_status=privacy)
        if not isinstance(playlist_id, str):
            # YouTube answers with a channel-creation form instead of a playlist
            # id when the Google account has never created a YouTube channel —
            # playlists belong to a channel, so there is nowhere to put one.
            if isinstance(playlist_id, dict) and "channelCreationForm" in json.dumps(playlist_id):
                raise SystemExit(
                    "\nError: this Google account has no YouTube channel, so playlists "
                    "can't be created.\n"
                    "    YouTube returned a channel-creation form instead of a playlist id.\n"
                    "    Fix: sign in at https://www.youtube.com, open any 'Create' or "
                    "channel prompt and\n    create the channel (a personal one is fine), "
                    "then re-run this script.\n"
                    "    Nothing has been created or changed.")
            print(f"  ! unexpected create_playlist response: {playlist_id!r}", file=sys.stderr)
            return False
        # Record the id NOW: if adding tracks fails partway, a re-run must
        # reuse this playlist rather than create a second empty one.
        state[key] = {"playlist_id": playlist_id, "url": PLAYLIST_URL.format(playlist_id),
                      "title": full_title, "track_count": 0, "synced": "creating"}
        write_state(state)
        n = add_in_batches(yt, playlist_id, want)
        print(f"  created with {n} track(s)")
    else:
        if to_remove:
            yt.remove_playlist_items(playlist_id, to_remove)
        if to_add:
            add_in_batches(yt, playlist_id, to_add)
        yt.edit_playlist(playlist_id, title=full_title, description=desc)
        if not to_add and not to_remove:
            print("  already in sync")

    state[key] = {"playlist_id": playlist_id, "url": PLAYLIST_URL.format(playlist_id),
                  "title": full_title, "track_count": len(want),
                  "synced": datetime.now().strftime("%Y-%m-%d %H:%M")}
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would change; needs no credentials.")
    ap.add_argument("--only", choices=[k for k, _, _ in PLAYLISTS],
                    help="Sync just one playlist.")
    ap.add_argument("--privacy", default="UNLISTED", choices=["UNLISTED", "PRIVATE", "PUBLIC"])
    ap.add_argument("--csv", default=CSV_PATH)
    args = ap.parse_args()

    buckets, missing = load_songs(args.csv)
    if missing:
        print(f"⚠️  {len(missing)} song(s) have no {URL_COL} and are left out: "
              f"{', '.join(missing)}", file=sys.stderr)
        print("    Fix with: python3 scripts/resolve_youtube_urls.py", file=sys.stderr)

    state = read_state()
    yt = None if args.dry_run else connect()

    changed = False
    for key, title, blurb in PLAYLISTS:
        if args.only and key != args.only:
            continue
        changed |= sync_one(yt, key, title, blurb, buckets[key], state,
                            args.dry_run, args.privacy)

    if args.dry_run:
        print("\nDRY RUN — nothing created or modified.")
        return
    if changed:
        write_state(state)
    if not state:
        print("\nNo playlists exist yet — nothing was recorded.", file=sys.stderr)
        return
    print()
    for key, _, _ in PLAYLISTS:
        if key in state:
            print(f"  {state[key]['title']:44} {state[key]['url']}")
    if changed:
        print(f"\nIds recorded in {os.path.relpath(STATE_PATH)} — commit it, or the next sync "
              "creates duplicates.")


if __name__ == "__main__":
    main()
