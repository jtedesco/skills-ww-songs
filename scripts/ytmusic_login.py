"""Mint a YouTube Music OAuth token, working around a ytmusicapi bug.

⚠️  OAUTH DOES NOT CURRENTLY WORK against YouTube Music. Tokens mint and refresh
correctly, but every API call — including `search`, which succeeds
unauthenticated — comes back HTTP 400 "Request contains an invalid argument".
This is server-side (https://github.com/sigma67/ytmusicapi/issues/676) and no
client id, scope or client context avoids it. Use browser auth instead:
    ytmusicapi browser --file ~/.config/ww-songs/ytmusic_browser.json
This script is kept for when upstream restores OAuth support.

`ytmusicapi oauth` crashes with:

    TypeError: __init__() got an unexpected keyword argument 'refresh_token_expires_in'

Google's device-flow response now includes `refresh_token_expires_in` — the countdown on the
7-day refresh-token limit that applies while the OAuth consent screen is in "Testing" — and
ytmusicapi 1.10.3's token dataclass rejects keys it doesn't know. The *stored* file format is
fine; only the constructor is brittle. So this runs the same device flow and writes the token
file directly, keeping just the fields the library expects.

    python3 scripts/ytmusic_login.py

Reads the client id/secret from ~/.config/ww-songs/env (or the environment), and writes the
token to WW_YTMUSIC_AUTH, default ~/.config/ww-songs/ytmusic_auth.json.

Re-run this whenever the token expires; `build_playlist.py` says so explicitly when it does.
The client id and secret never change.
"""
import json
import os
import stat
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_playlist import (AUTH_ENV, CLIENT_ID_ENV, CLIENT_SECRET_ENV, CONFIG_ENV,
                            DEFAULT_AUTH, PLACEHOLDER, load_config_env)
from resolve_youtube_urls import require_ytmusicapi

# Exactly the keys ytmusicapi's Token dataclass accepts. Anything else Google
# sends (today `refresh_token_expires_in`) is dropped — storing it would make
# the file un-loadable for the same reason the CLI crashes.
TOKEN_FIELDS = ("scope", "token_type", "access_token", "refresh_token",
                "expires_at", "expires_in")


def main():
    load_config_env()
    cid = os.environ.get(CLIENT_ID_ENV, "")
    secret = os.environ.get(CLIENT_SECRET_ENV, "")
    if not cid or not secret or PLACEHOLDER in cid or PLACEHOLDER in secret:
        print(f"Error: set {CLIENT_ID_ENV} and {CLIENT_SECRET_ENV} in {CONFIG_ENV} first.",
              file=sys.stderr)
        print("    They come from the Google Cloud OAuth client of type "
              "'TVs and Limited Input devices'.", file=sys.stderr)
        sys.exit(1)

    out = os.path.expanduser(os.environ.get(AUTH_ENV) or DEFAULT_AUTH)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    require_ytmusicapi()
    from ytmusicapi.auth.oauth import OAuthCredentials
    creds = OAuthCredentials(client_id=cid, client_secret=secret)

    code = creds.get_code()
    url = f"{code['verification_url']}?user_code={code['user_code']}"
    print(f"\n  1. Open: {url}")
    print(f"  2. Confirm the code: {code['user_code']}")
    print("  3. The app is unverified, which is expected for a personal client —")
    print("     choose Advanced → 'Go to … (unsafe)' if prompted.")
    try:
        input("\nPress Enter once the browser says you're done (Ctrl-C to abort)... ")
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(1)

    raw = creds.token_from_code(code["device_code"])
    if "refresh_token" not in raw:
        print(f"Error: no refresh token returned. Google said: {raw}", file=sys.stderr)
        print("    Usually means the browser step wasn't completed, or the account "
              "isn't listed\n    under 'Test users' on the OAuth consent screen.",
              file=sys.stderr)
        sys.exit(1)

    token = {k: raw[k] for k in TOKEN_FIELDS if k in raw}
    token["expires_at"] = int(time.time()) + int(raw.get("expires_in", 0))
    dropped = [k for k in raw if k not in TOKEN_FIELDS]

    with open(out, "w", encoding="utf-8") as f:
        json.dump(token, f, indent=2)
        f.write("\n")
    os.chmod(out, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — this is credential material

    print(f"\n✅ Token written to {out} (mode 600)")
    if dropped:
        print(f"   Dropped field(s) ytmusicapi can't accept: {', '.join(dropped)}")
    if "refresh_token_expires_in" in raw:
        days = int(raw["refresh_token_expires_in"]) / 86400.0
        print(f"   Google says this token stops refreshing in ~{days:.1f} days "
              "(consent screen is in Testing).")
        print("   Re-run this script when that happens — nothing else changes.")
    print("\n   Verify with:  python3 scripts/build_playlist.py \"setlists/<gig>\" --dry-run")


if __name__ == "__main__":
    main()
