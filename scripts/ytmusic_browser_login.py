"""Set up YouTube Music browser auth, correctly, from pasted request headers.

`ytmusicapi browser` leaves two traps that produce a file which looks fine and
then fails at load time:

1. It validates only `cookie` and `x-goog-authuser`, but `YTMusic()` classifies
   a file as browser auth **solely** by an `authorization` header containing
   `SAPISIDHASH`. Without it the file is misread as an OAuth token and raises
   "oauth JSON provided ... but oauth_credentials not provided".
2. The value doesn't have to come from the browser — for browser auth
   ytmusicapi recomputes it from the cookie on every request. So it can be
   derived, and this script derives it.

Paste the request headers of any authenticated music.youtube.com request. The
only line that truly matters is `cookie:` (it must carry `__Secure-3PAPISID`);
`x-goog-authuser:` is expected by the underlying parser, and defaults to 0.

    python3 scripts/ytmusic_browser_login.py            # paste, then Ctrl-D
    python3 scripts/ytmusic_browser_login.py --from headers.txt
    python3 scripts/ytmusic_browser_login.py --repair   # fix an existing file

Writes to WW_YTMUSIC_AUTH, default ~/.config/ww-songs/ytmusic_browser.json,
mode 600. Keep it out of the repo — this repo lives in Google Drive.
"""
import argparse
import json
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_youtube_urls import require_ytmusicapi

DEFAULT_BROWSER_AUTH = os.path.expanduser("~/.config/ww-songs/ytmusic_browser.json")
AUTH_ENV = "WW_YTMUSIC_AUTH"
ORIGIN = "https://music.youtube.com"


def add_authorization(headers):
    """Give the header dict the SAPISIDHASH `authorization` that marks it as
    browser auth, derived from the cookie exactly as ytmusicapi does per request."""
    from ytmusicapi.helpers import sapisid_from_cookie, get_authorization
    if "cookie" not in {k.lower() for k in headers}:
        raise SystemExit("Error: no 'cookie' header found. Paste the headers of an "
                         "authenticated\n       music.youtube.com request (filter Network on "
                         "'browse').")
    cookie = next(v for k, v in headers.items() if k.lower() == "cookie")
    try:
        sapisid = sapisid_from_cookie(cookie)
    except KeyError:
        raise SystemExit("Error: the cookie has no __Secure-3PAPISID value, so it isn't a "
                         "signed-in\n       session. Sign in to music.youtube.com and copy "
                         "the headers again.")
    headers.setdefault("origin", ORIGIN)
    headers.setdefault("x-goog-authuser", "0")
    headers["authorization"] = get_authorization(sapisid + " " + headers["origin"])
    return headers


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", help="Read pasted headers from this file.")
    ap.add_argument("--repair", action="store_true",
                    help="Fix the existing auth file in place (raw headers, or missing "
                         "authorization).")
    ap.add_argument("--out", default=os.environ.get(AUTH_ENV) or DEFAULT_BROWSER_AUTH)
    args = ap.parse_args()
    require_ytmusicapi()
    out = os.path.expanduser(args.out)

    if args.repair:
        raw = open(out, encoding="utf-8").read()
    elif args.src:
        raw = open(os.path.expanduser(args.src), encoding="utf-8").read()
    else:
        print("Paste the request headers, then press Ctrl-D:")
        raw = sys.stdin.read()

    if raw.lstrip().startswith("{"):
        headers = json.load(open(out, encoding="utf-8")) if args.repair else json.loads(raw)
    else:
        from ytmusicapi.auth.browser import setup_browser
        headers = json.loads(setup_browser(headers_raw=raw.strip()))

    headers = add_authorization(headers)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(headers, f, indent=4, sort_keys=True)
        f.write("\n")
    os.chmod(out, stat.S_IRUSR | stat.S_IWUSR)

    from ytmusicapi.auth.auth_parse import determine_auth_type
    from ytmusicapi.auth.types import AuthType
    from requests.structures import CaseInsensitiveDict
    kind = determine_auth_type(CaseInsensitiveDict(headers))
    # AuthType is an IntEnum: f-string formatting renders it as its integer on
    # Python 3.9, so report .name and compare against the member itself.
    print(f"\n✅ Wrote {out} (mode 600) — detected as {kind.name}")
    if kind != AuthType.BROWSER:
        raise SystemExit(f"Error: expected BROWSER auth, got {kind.name}. The cookie is "
                         "probably not from a signed-in session.")

    from ytmusicapi import YTMusic
    try:
        n = len(YTMusic(out).get_library_playlists(limit=1))
        print(f"   Verified against your account (read {n} playlist).")
    except Exception as e:
        raise SystemExit(f"Error: credentials written but rejected: {e}")
    print(f"\n   export {AUTH_ENV}={out}")


if __name__ == "__main__":
    main()
