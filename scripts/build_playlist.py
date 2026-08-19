"""Build (or refresh) the YouTube Music playlist for a gig's setlist.

Reads the setlist's `.md` — the source of record — and turns it into an
UNLISTED YouTube Music playlist in running order: Set 1, the acoustic break,
Set 2, encores. Track URLs come straight from the `youtube_url` column of
songs_metadata.csv, so this never searches the API and two runs can never
pick different recordings. Populate that column with resolve_youtube_urls.py.

ONE PLAYLIST PER GIG, UPDATED IN PLACE. The playlist id is recorded in
`playlist.json` inside the gig folder; later runs rewrite that same playlist
rather than making a second one, so the link the band already has keeps
working when the setlist is revised.

Usage:
    python3 scripts/build_playlist.py "setlists/2026-08-28 Empire"       # the gig's canonical .md
    python3 scripts/build_playlist.py "setlists/2026-08-28 Empire/2026-08-28 Empire.md"
    python3 scripts/build_playlist.py "setlists/2026-08-28 Empire" --dry-run

Auth (only needed to write; --dry-run needs none). ytmusicapi requires
credentials for playlist writes — set one up once with `ytmusicapi browser`
or `ytmusicapi oauth`, then point WW_YTMUSIC_AUTH at the resulting file:
    export WW_YTMUSIC_AUTH=~/.config/ww-songs/ytmusic_auth.json
That file is credential material: keep it out of this repo.
"""
import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apply_substitution import parse_md, normalize_title, BREAK_BULLET_RE, BREAK_LEAD_RE
from resolve_youtube_urls import require_ytmusicapi

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "songs_metadata.csv")
URL_COL = "youtube_url"
STATE_FILE = "playlist.json"
AUTH_ENV = "WW_YTMUSIC_AUTH"
CLIENT_ID_ENV = "WW_YTMUSIC_CLIENT_ID"
CLIENT_SECRET_ENV = "WW_YTMUSIC_CLIENT_SECRET"
# Browser auth first: OAuth tokens currently authenticate but are rejected by
# every endpoint (see SKILL.md), so a browser file is preferred when both exist.
DEFAULT_AUTH_CANDIDATES = [
    os.path.expanduser("~/.config/ww-songs/ytmusic_browser.json"),
    os.path.expanduser("~/.config/ww-songs/ytmusic_auth.json"),
]
DEFAULT_AUTH = next((p for p in DEFAULT_AUTH_CANDIDATES if os.path.exists(p)),
                    DEFAULT_AUTH_CANDIDATES[0])
# Optional KEY=value file read when the env vars aren't already set, so the
# client id/secret can live in one 0600 file instead of a shell rc. It must sit
# outside the repo: this repo is inside Google Drive, which would sync it.
CONFIG_ENV = os.path.expanduser("~/.config/ww-songs/env")
# Template text shipped in that file. Treated as "not filled in yet" — without
# this the placeholders sail through as real values and fail deep inside
# ytmusicapi with an opaque OAuth error.
PLACEHOLDER = "paste-your"
PLAYLIST_URL = "https://music.youtube.com/playlist?list={}"
# Playlist titles read as "WW - Active Songs" in the app's narrow sidebar.
BAND = "WW"
SEP = " - "


def canonical_setlist(gig_dir):
    """The gig's one .md — named for the folder it sits in (see SKILL.md).
    Falls back to a lone .md under any other name, so a hand-renamed folder
    still resolves."""
    stem = os.path.basename(gig_dir.rstrip("/"))
    named = os.path.join(gig_dir, f"{stem}.md")
    if os.path.exists(named):
        return named
    mds = [n for n in os.listdir(gig_dir) if n.endswith(".md")]
    return os.path.join(gig_dir, mds[0]) if len(mds) == 1 else None


def resolve_setlist(target):
    """Accept either a gig folder or a specific .md and return (md_path, gig_dir)."""
    target = target.rstrip("/")
    if os.path.isdir(target):
        md = canonical_setlist(target)
        if not md:
            print(f"Error: no setlist '<name>.md' found in {target}", file=sys.stderr)
            sys.exit(1)
        return md, target
    if os.path.isfile(target):
        return target, os.path.dirname(target)
    print(f"Error: no such setlist or gig folder: {target}", file=sys.stderr)
    sys.exit(1)


def ordered_titles(md_path):
    """Every song in playing order: each section's table rows, then any
    acoustic-break bullets that follow it. Breaks live in the section's
    preserved trailing text, so walking sections in order yields
    Set 1 -> break -> Set 2 exactly as performed."""
    _header, sections, _target = parse_md(md_path)
    out = []
    for sec in sections:
        for row in sec["rows"]:
            out.append((row["title"], sec["heading"]))
        for line in sec["extra_after"]:
            m = BREAK_BULLET_RE.match(line.strip())
            # A break block can also hold non-song entries, e.g.
            # '- **5 min silent break** (no song — everyone off stage)'.
            # Real song bullets always name a lead vocalist; those don't.
            if m and BREAK_LEAD_RE.search(line):
                out.append((m.group(1).strip(), f"{sec['heading']} / break"))
    return out


def load_urls(csv_path):
    by_title = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_title[normalize_title(row["title"])] = row
    return by_title


def gig_label(md_path, gig_dir):
    """'2026-08-28 Empire', taken from the file itself."""
    return os.path.basename(md_path)[:-3]


def read_state(gig_dir):
    path = os.path.join(gig_dir, STATE_FILE)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def write_state(gig_dir, state):
    with open(os.path.join(gig_dir, STATE_FILE), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def load_config_env(path=CONFIG_ENV):
    """Read KEY=value lines from the config file into the environment, without
    overriding anything already exported. Silent when the file is absent."""
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and val and not os.environ.get(key):
                    os.environ[key] = val
    except OSError as e:
        print(f"⚠️  couldn't read {path}: {e}", file=sys.stderr)


def connect():
    load_config_env()
    auth = os.path.expanduser(os.environ.get(AUTH_ENV) or DEFAULT_AUTH)
    if not os.path.exists(auth):
        print(f"Error: no YouTube Music credentials at {auth}", file=sys.stderr)
        print(f"    Create them with:  ytmusicapi browser --file {auth}", file=sys.stderr)
        print("    (browser auth, not oauth — see SKILL.md: OAuth tokens are currently "
              "rejected by\n     YouTube Music with HTTP 400 regardless of client id.)",
              file=sys.stderr)
        print(f"    then point {AUTH_ENV} at the file. Use --dry-run to preview "
              "the playlist without credentials.", file=sys.stderr)
        sys.exit(1)

    require_ytmusicapi()
    from ytmusicapi import YTMusic
    try:
        with open(auth, encoding="utf-8") as f:
            blob = json.load(f)
    except (OSError, ValueError):
        blob = {}

    # An OAuth token file holds only the tokens — the client id/secret that
    # minted them are NOT stored there, and ytmusicapi refuses to load the file
    # without them. Browser-header files need no such thing, so only OAuth
    # takes this path.
    if "refresh_token" in blob:
        from ytmusicapi import OAuthCredentials
        cid = os.environ.get(CLIENT_ID_ENV, "")
        secret = os.environ.get(CLIENT_SECRET_ENV, "")
        if PLACEHOLDER in cid or PLACEHOLDER in secret:
            print(f"Error: {CONFIG_ENV} still has the template placeholders in it.",
                  file=sys.stderr)
            print("    Replace them with the real client id and secret from the "
                  "Google Cloud OAuth client.", file=sys.stderr)
            sys.exit(1)
        if not (cid and secret):
            print(f"Error: {auth} is an OAuth token, so the Google client that "
                  "created it is needed too.", file=sys.stderr)
            print(f"    Put them in {CONFIG_ENV} (chmod 600):", file=sys.stderr)
            print(f"        {CLIENT_ID_ENV}=<client id>", file=sys.stderr)
            print(f"        {CLIENT_SECRET_ENV}=<client secret>", file=sys.stderr)
            print("    These are the same values entered during `ytmusicapi oauth` — "
                  "from the Google Cloud OAuth client of type "
                  "'TVs and Limited Input devices'.", file=sys.stderr)
            sys.exit(1)
        return YTMusic(auth, oauth_credentials=OAuthCredentials(
            client_id=cid, client_secret=secret))
    return YTMusic(auth)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="Gig folder, or a specific setlist .md")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the track list; touch no playlist and need no credentials.")
    ap.add_argument("--strict", action="store_true",
                    help="Abort if any song lacks a URL (default: skip it and warn).")
    ap.add_argument("--privacy", default="UNLISTED",
                    choices=["UNLISTED", "PRIVATE", "PUBLIC"])
    ap.add_argument("--csv", default=CSV_PATH)
    args = ap.parse_args()

    md_path, gig_dir = resolve_setlist(args.target)
    name = gig_label(md_path, gig_dir)
    songs = ordered_titles(md_path)
    by_title = load_urls(args.csv)

    tracks, missing = [], []
    for title, section in songs:
        row = by_title.get(normalize_title(title))
        url = (row or {}).get(URL_COL, "").strip()
        if not row:
            missing.append((title, section, "not in songs_metadata.csv"))
        elif not url:
            missing.append((title, section, f"empty {URL_COL}"))
        else:
            tracks.append({"title": title, "artist": row["artist"],
                           "section": section, "video_id": url.rsplit("v=", 1)[-1]})

    print(f"Setlist : {md_path}")
    print(f"Playlist: {BAND}{SEP}{name}  [{args.privacy.lower()}]")
    last = None
    for i, t in enumerate(tracks, 1):
        if t["section"] != last:
            print(f"  -- {t['section']}")
            last = t["section"]
        print(f"  {i:2}. {t['title']} — {t['artist']}")
    print(f"\n{len(tracks)} track(s) resolved.")

    if missing:
        print(f"\n⚠️  {len(missing)} song(s) with no URL — they will be LEFT OUT:")
        for title, section, why in missing:
            print(f"  {title} ({section}): {why}")
        print("  Fix with: python3 scripts/resolve_youtube_urls.py "
              "--only \"<title>\"")
        if args.strict:
            print("\nAborting (--strict).", file=sys.stderr)
            sys.exit(1)
    if not tracks:
        print("Nothing to publish.", file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        print("\nDRY RUN — no playlist created or modified.")
        return

    yt = connect()
    state = read_state(gig_dir)
    playlist_id = state.get("playlist_id")
    desc = (f"{BAND}{SEP}{name}. Setlist order, including the acoustic "
            f"break. {len(tracks)} tracks. Updated {datetime.now():%Y-%m-%d}.")

    try:
        _publish(yt, gig_dir, state, playlist_id, name, desc, tracks, args)
    except Exception as e:
        from ytmusicapi.auth.oauth.exceptions import BadOAuthClient, UnauthorizedOAuthClient
        # A Google project left in "Testing" expires its refresh tokens after 7
        # days. The library surfaces that as a generic server error carrying
        # 'invalid_grant', which reads like a bug rather than a routine re-auth.
        if "invalid_grant" in str(e):
            auth = os.path.expanduser(os.environ.get(AUTH_ENV) or DEFAULT_AUTH)
            print("\nError: the saved YouTube Music token has expired or been revoked.",
                  file=sys.stderr)
            print(f"    Mint a fresh one:  ytmusicapi oauth --file {auth}", file=sys.stderr)
            print("    Google expires refresh tokens after 7 days while the OAuth consent "
                  "screen is\n    in 'Testing'. Nothing else needs redoing — the client id "
                  "and secret stay the same.", file=sys.stderr)
            sys.exit(1)
        if "HTTP 400" in str(e) and "refresh_token" in open(
                os.path.expanduser(os.environ.get(AUTH_ENV) or DEFAULT_AUTH), encoding="utf-8").read():
            print("\nError: YouTube Music rejected the request (HTTP 400).", file=sys.stderr)
            print("    This is the known OAuth breakage: tokens authenticate fine but every "
                  "endpoint\n    returns 400 — see https://github.com/sigma67/ytmusicapi/issues/676."
                  "\n    Switch to browser auth:", file=sys.stderr)
            print(f"        ytmusicapi browser --file {os.path.expanduser('~/.config/ww-songs/ytmusic_browser.json')}",
                  file=sys.stderr)
            print(f"        # then point {AUTH_ENV} at that file", file=sys.stderr)
            sys.exit(1)
        if isinstance(e, (BadOAuthClient, UnauthorizedOAuthClient)):
            print(f"\nError: YouTube Music rejected the credentials — {e}", file=sys.stderr)
            print(f"    Check {CONFIG_ENV}: the client id and secret must be from the SAME "
                  "OAuth client that\n    created the token file, that client must be type "
                  "'TVs and Limited Input devices',\n    and YouTube Data API v3 must be "
                  "enabled on its project.", file=sys.stderr)
            sys.exit(1)
        raise


def _publish(yt, gig_dir, state, playlist_id, name, desc, tracks, args):
    video_ids = [t["video_id"] for t in tracks]
    if playlist_id:
        # Update in place so the link already shared with the band keeps working:
        # clear the current contents, then re-add in the new running order.
        try:
            existing = yt.get_playlist(playlist_id, limit=None).get("tracks", [])
        except Exception as e:
            print(f"Error: can't read playlist {playlist_id}: {e}", file=sys.stderr)
            print("    Delete playlist.json to create a fresh playlist instead.", file=sys.stderr)
            sys.exit(1)
        removable = [t for t in existing if t.get("setVideoId")]
        if removable:
            yt.remove_playlist_items(playlist_id, removable)
        yt.edit_playlist(playlist_id, title=f"{BAND}{SEP}{name}", description=desc)
        print(f"Updated existing playlist ({len(removable)} track(s) cleared).")
    else:
        playlist_id = yt.create_playlist(f"{BAND}{SEP}{name}", desc,
                                         privacy_status=args.privacy)
        if not isinstance(playlist_id, str):
            print(f"Error: unexpected create_playlist response: {playlist_id!r}", file=sys.stderr)
            sys.exit(1)
        print("Created new playlist.")

    # duplicates=True: a setlist legitimately never repeats a song, but the
    # same recording can back two entries, and silent de-duping would
    # desynchronise the playlist from the running order.
    yt.add_playlist_items(playlist_id, video_ids, duplicates=True)

    url = PLAYLIST_URL.format(playlist_id)
    write_state(gig_dir, {"playlist_id": playlist_id, "url": url,
                          "gig": name,
                          "track_count": len(tracks),
                          "updated": datetime.now().strftime("%Y-%m-%d %H:%M")})
    print(f"\n✅ {len(video_ids)} track(s) — {url}")
    print(f"   Recorded in {os.path.join(gig_dir, STATE_FILE)}")


if __name__ == "__main__":
    main()
