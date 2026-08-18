"""Resolve each song in songs_metadata.csv to a canonical YouTube Music URL.

Fills the `youtube_url` column with the original artist's studio recording so
that building a playlist for a gig is a pure CSV lookup — no API call, no
re-matching, and no chance of two runs picking different recordings.

Search runs UNAUTHENTICATED (ytmusicapi only needs credentials to *write*
playlists), so this can be run by anyone with network access.

The band plays covers, so every match must land on the ORIGINAL artist's
recording, not a cover, a live take, or a karaoke backing track. Candidates
are scored rather than taking the top hit — YouTube Music's first result is
routinely a live version, a "feat." re-release, or another song from the same
album. Anything scoring below CONFIDENT is written but flagged for review.

Usage:
    python3 scripts/resolve_youtube_urls.py                  # fill empty cells
    python3 scripts/resolve_youtube_urls.py --force          # re-resolve everything
    python3 scripts/resolve_youtube_urls.py --only "Valerie" # one song (repeatable)
    python3 scripts/resolve_youtube_urls.py --dry-run        # show, write nothing
"""
import argparse
import csv
import os
import re
import sys
import time
import unicodedata
from difflib import SequenceMatcher

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "songs_metadata.csv")
URL_COL = "youtube_url"
WATCH_URL = "https://music.youtube.com/watch?v={}"

# Score at or above which a match is trusted without human review.
CONFIDENT = 70

# Candidates within this many points of the best are treated as a tie and
# settled on play count instead. Scoring can't tell the Escape cut from the
# same recording on a no-name compilation — they are the same track, credited
# identically — but one has 194M plays and the other 3M. The popular pressing
# is the one people mean, and the one whose album art the band will recognise.
TIE_BAND = 12
TIE_MAX = 4

# Substrings in a candidate title that mean it is not the canonical studio
# recording. Weight is subtracted from the score.
TITLE_PENALTIES = [
    (("karaoke", "made famous by", "in the style of", "backing track", "instrumental"), 60),
    (("cover", "tribute", "as made popular"), 50),
    ((r"\blive\b", "bbc session", "radio 1", "live lounge", "unplugged",
      "concert", "at the forum", "session)"), 40),
    (("sped up", "slowed", "reverb", "8 bit", "8-bit", "lofi", "lo-fi", "remix",
      "mashup", "loop", "extended mix"), 45),
    (("demo", "rehearsal", "outtake", "alternate take", "re-recorded", "rerecorded",
      "taylor's version"), 30),
    (("radio edit", "single version", "edit)", "short version"), 8),
    # A remaster is usually the canonical catalogue entry now, so this is only
    # a tiebreaker against an identical non-remastered cut.
    (("remaster", "remastered"), 3),
]


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s):
    """Loose comparison form: no accents, case, punctuation or leading 'the'."""
    s = strip_accents(s or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^the ", "", s)
    return s


def base_title(s):
    """Title with trailing parenthetical/bracketed qualifiers removed, so
    'The Chain (2004 Remaster)' compares equal to 'The Chain'."""
    s = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", s or "")
    s = re.sub(r"\s+-\s+.*$", "", s)
    return norm(s)


def display_artist(csv_artist):
    """songs_metadata.csv stores artists library-sorted ('Black Keys, The').
    Turn that back into natural order for searching."""
    a = (csv_artist or "").strip()
    m = re.match(r"^(.*),\s*(The|A|An)$", a, re.I)
    if m:
        return f"{m.group(2)} {m.group(1)}"
    return a


def parse_len(s):
    """'3:30' -> 210 seconds. Returns None if unparseable."""
    m = re.match(r"^\s*(\d+):(\d{1,2})\s*$", s or "")
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def token_set_ratio(a, b):
    """Similarity that ignores word order, so 'Jenny (867-5309)' matches
    '867-5309 / Jenny'. Compares the sorted token sets of the full titles."""
    ta, tb = sorted(set(norm(a).split())), sorted(set(norm(b).split()))
    if not ta or not tb:
        return 0.0
    return similarity(" ".join(ta), " ".join(tb))


def artist_sim(cand_artist, want_artist):
    """Similarity that understands backing-band suffixes: the database says
    'Tom Petty' where the catalogue says 'Tom Petty And The Heartbreakers',
    and likewise for Springsteen/E Street and Joan Jett/Blackhearts. Plain
    string similarity scores those around 0.55 and would reject the correct
    studio recording, so treat one name containing the other as a match."""
    # norm() drops a LEADING 'the'; artists are also stored library-sorted
    # ('Black Keys, The'), so drop a trailing one too and let both orderings
    # converge on the same string.
    a, b = (re.sub(r"\s+the$", "", norm(x)) for x in (cand_artist, want_artist))
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 4 and long_.startswith(short + " "):
        extra = long_[len(short):].strip()
        # Only a BACKING BAND counts as the same act. A bare second name is a
        # collaboration — 'Badfinger & Matthew Sweet' and 'Tommy Tutone & No
        # Resolve' are latter-day re-recordings, not the original hit, and
        # accepting them silently is how a tribute cut ends up on the playlist.
        if re.match(r"^(and\s+|with\s+)?(the|his|her)\s+\w", extra):
            return 0.95
        return min(similarity(a, b), 0.55)
    if len(short) >= 4 and long_.endswith(" " + short):
        return 0.95
    return similarity(a, b)


def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


def score_candidate(cand, want_title, want_artist, want_album, want_secs):
    """Rate how well a YT Music song result matches the database row.
    Returns (score, [reasons]) — score is roughly 0-100."""
    reasons = []
    c_title = cand.get("title") or ""
    c_artists = [a.get("name", "") for a in (cand.get("artists") or [])]
    # A track credited to one artist "(feat. Other)" still counts as a match on
    # Other — e.g. Valerie is catalogued under Mark Ronson feat. Amy Winehouse,
    # but the database lists it under Amy Winehouse.
    # Anchored to an opening bracket: a bare 'with' would swallow ordinary
    # title words — 'Roll (with) the Changes', 'Hit Me (with) Your Best Shot'
    # — and invent an artist out of the song's own name.
    c_artists += re.findall(r"[(\[]\s*(?:feat\.|featuring|ft\.|with)\s+([^)\]]+)",
                            c_title, re.I)
    c_album = ((cand.get("album") or {}) or {}).get("name") or ""

    # --- title ---
    # Compare two ways and keep the better: straight sequence similarity on the
    # de-qualified titles, and a token-set match on the full titles so a
    # reordered name still lines up — the database says 'Jenny (867-5309)'
    # where the catalogue says '867-5309 / Jenny'.
    t_sim = max(similarity(base_title(c_title), norm(want_title)),
                token_set_ratio(c_title, want_title))
    score = 55 * t_sim
    if base_title(c_title) == norm(want_title):
        score += 10
        reasons.append("exact title")
    elif t_sim < 0.7:
        reasons.append(f"weak title match ({t_sim:.2f})")

    # --- artist: any credited artist matching is enough ('feat.' splits them) ---
    a_sim = max([artist_sim(a, want_artist) for a in c_artists] or [0])
    score += 35 * a_sim
    if a_sim >= 0.95:
        reasons.append("artist match")
        # Extra credited acts alongside our artist mean a re-recording or
        # collaboration ('Tommy Tutone, No Resolve'), not the original hit.
        # Only when OUR artist holds the primary credit, though: on 'Mark
        # Ronson (feat. Amy Winehouse)' the extra name is the primary act and
        # that genuinely is the canonical recording of Valerie.
        primary = c_artists[0] if c_artists else ""
        if artist_sim(primary, want_artist) >= 0.95:
            strangers = [a for a in c_artists[1:]
                         if artist_sim(a, want_artist) < 0.6]
            if strangers:
                score -= 35
                reasons.append(f"-35 extra credit ({', '.join(strangers)})")
    elif a_sim < 0.6:
        # Hard penalty, not a nudge. The band plays covers, so a right-title/
        # wrong-artist hit is the single most likely failure and must never
        # clear CONFIDENT on title similarity alone.
        score -= 50
        reasons.append(f"ARTIST MISMATCH (got {', '.join(c_artists) or '?'})")

    # --- album agreement is a strong signal for 'this is the studio cut' ---
    if want_album and c_album:
        if norm(c_album) == norm(want_album):
            score += 8
            reasons.append("album match")

    # --- non-canonical version penalties ---
    # Square brackets are used interchangeably with parens by the catalogue
    # ('Landslide ... [Live 2006]'), so flatten them before matching.
    low = c_title.lower().replace("[", "(").replace("]", ")")
    # Only judge the qualifiers the catalogue ADDED to the song's real name.
    # Checking the whole title would penalise songs whose actual name contains
    # a marker word — 'Live and Let Die', 'Cover Me', 'Demo' etc.
    want_low = (want_title or "").lower()
    extra = low.replace(want_low, " ") if want_low and want_low in low else low
    for needles, weight in TITLE_PENALTIES:
        if any(re.search(n, extra) if n.startswith("\\b") else n in extra for n in needles):
            score -= weight
            reasons.append(f"-{weight} version penalty")
            break

    # The album gives away live recordings whose track title hides it — the
    # plain-titled 'Landslide' on 'The Best of Rock and Roll Hall of Fame +
    # Museum Live' is a concert take, indistinguishable by title alone.
    alb = c_album.lower()
    if alb and not (want_album and norm(c_album) == norm(want_album)):
        if re.search(r"\blive\b|\bin concert\b|\bunplugged\b|\bkaraoke\b", alb):
            score -= 30
            reasons.append(f"-30 live album ({c_album})")

    # --- duration sanity against the arrangement length in the DB ---
    c_secs = cand.get("duration_seconds")
    if want_secs and c_secs:
        delta = abs(c_secs - want_secs)
        # Band arrangements differ from the record, so only flag big gaps.
        ratio = c_secs / float(want_secs)
        if ratio >= 2.0 or ratio <= 0.45:
            # The DB length is the BAND's arrangement, not the original record
            # (Funkytown is a 2:00 closer here against a 7:51 album cut), so
            # this stays a nudge rather than a veto — only a gross mismatch
            # counts, and even then it can't sink an otherwise perfect match.
            score -= 20
            reasons.append(f"-20 length {c_secs}s vs {want_secs}s")
        elif delta > 150:
            score -= 12
            reasons.append(f"duration off by {delta}s")

    return score, reasons


def resolve(yt, row, limit, verbose):
    title = row["title"]
    artist = display_artist(row["artist"])
    album = row.get("original_album", "")
    want_secs = parse_len(row.get("length", ""))

    query = f"{title} {artist}"
    try:
        results = yt.search(query, filter="songs", limit=limit)
    except Exception as e:
        return None, 0, [f"search failed: {e}"]

    scored = []
    for cand in results:
        if not cand.get("videoId"):
            continue
        s, why = score_candidate(cand, title, artist, album, want_secs)
        scored.append((s, cand, why))
    if not scored:
        return None, 0, ["no results"]

    scored.sort(key=lambda x: -x[0])

    # Break near-ties by popularity. Only a handful of extra lookups, and only
    # when the score genuinely can't separate the candidates.
    contenders = [c for c in scored[:TIE_MAX] if c[0] >= scored[0][0] - TIE_BAND]
    if len(contenders) > 1:
        rated = []
        for sc, cand, why in contenders:
            try:
                views = int(yt.get_song(cand["videoId"])["videoDetails"].get("viewCount") or 0)
            except Exception:
                views = 0
            rated.append((views, sc, cand, why))
        rated.sort(key=lambda x: -x[0])
        if rated[0][0]:
            top_views, sc, cand, why = rated[0]
            why = why + [f"most played of {len(rated)} tied ({top_views:,})"]
            scored = [(sc, cand, why)] + [x for x in scored if x[1] is not cand]

    if verbose:
        for s, cand, why in scored[:5]:
            arts = ", ".join(a.get("name", "") for a in (cand.get("artists") or []))
            print(f"      {s:6.1f}  {cand.get('title')!r} | {arts} | {'; '.join(why)}")
    best_score, best, why = scored[0]
    return best, best_score, why


def require_ytmusicapi():
    """Import ytmusicapi, or explain how to run this with an interpreter that
    has it. The `python3` first on PATH is often not the one the project's
    dependencies were installed into (Homebrew's Python vs. the system one),
    and the bare ModuleNotFoundError gives no hint which to use."""
    try:
        import ytmusicapi  # noqa: F401
        return
    except ImportError:
        pass
    import subprocess
    script = os.path.relpath(sys.argv[0]) if sys.argv and sys.argv[0] else "<script>"
    print(f"Error: ytmusicapi is not installed for {sys.executable}", file=sys.stderr)
    for candidate in ("/usr/bin/python3", "/opt/homebrew/bin/python3", "python3"):
        if candidate == sys.executable:
            continue
        try:
            ok = subprocess.run([candidate, "-c", "import ytmusicapi"],
                                capture_output=True, timeout=30).returncode == 0
        except (OSError, subprocess.SubprocessError):
            continue
        if ok:
            print(f"\n    It IS installed for {candidate}. Run:", file=sys.stderr)
            print(f"        {candidate} {script} {' '.join(sys.argv[1:])}".rstrip(),
                  file=sys.stderr)
            sys.exit(1)
    print("\n    Install it with:  /usr/bin/pip3 install --user -r requirements.txt",
          file=sys.stderr)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append", default=[],
                    help="Only resolve this song title (repeatable).")
    ap.add_argument("--force", action="store_true",
                    help="Re-resolve songs that already have a URL.")
    ap.add_argument("--dry-run", action="store_true", help="Print results, write nothing.")
    ap.add_argument("--verbose", action="store_true", help="Show all scored candidates.")
    ap.add_argument("--limit", type=int, default=8, help="Candidates to score per song.")
    ap.add_argument("--csv", default=CSV_PATH)
    args = ap.parse_args()

    require_ytmusicapi()
    from ytmusicapi import YTMusic

    with open(args.csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames)
        rows = list(reader)

    if URL_COL not in headers:
        headers.append(URL_COL)
        for r in rows:
            r.setdefault(URL_COL, "")
        print(f"{'Would add' if args.dry_run else 'Added'} '{URL_COL}' column.")

    wanted = {norm(t) for t in args.only}
    yt = YTMusic()  # unauthenticated: search only

    resolved = skipped = failed = 0
    review = []
    overwritten = []
    for row in rows:
        for h in headers:
            row.setdefault(h, "")
        if wanted and norm(row["title"]) not in wanted:
            continue
        if row.get(URL_COL) and not args.force:
            skipped += 1
            continue

        print(f"  {row['title']} — {display_artist(row['artist'])}")
        best, score, why = resolve(yt, row, args.limit, args.verbose)
        if not best:
            failed += 1
            print(f"    ✗ no match ({'; '.join(why)})")
            review.append((row["title"], 0, "; ".join(why), ""))
            continue

        url = WATCH_URL.format(best["videoId"])
        arts = ", ".join(a.get("name", "") for a in (best.get("artists") or []))
        flag = "✓" if score >= CONFIDENT else "?"
        print(f"    {flag} [{score:.0f}] {best.get('title')!r} | {arts}")
        print(f"      {url}")
        if score < CONFIDENT:
            review.append((row["title"], score, "; ".join(why), url))
        previous = row.get(URL_COL, "").strip()
        if previous and previous != url:
            # --force re-resolves from scratch, so a URL a human corrected by
            # hand can get overwritten. Never do that silently.
            overwritten.append((row["title"], previous, url))
            print(f"      ⚠️  replaced previous URL {previous}")
        if not args.dry_run:
            row[URL_COL] = url
        resolved += 1
        time.sleep(0.3)  # be polite to the endpoint

    if not args.dry_run:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

    print(f"\nResolved {resolved}, skipped {skipped} (already had a URL), failed {failed}.")
    if args.dry_run:
        print("DRY RUN — nothing written.")
    if overwritten:
        print(f"\n⚠️  {len(overwritten)} existing URL(s) were REPLACED — if any of these "
              "were hand-picked, restore them (git diff songs_metadata.csv):")
        for title, prev, now in overwritten:
            print(f"  {title}\n      was {prev}\n      now {now}")
    if review:
        print(f"\n⚠️  {len(review)} match(es) below the confidence threshold — verify by hand:")
        for title, score, why, url in sorted(review, key=lambda x: x[1]):
            print(f"  [{score:.0f}] {title}: {why}")
            if url:
                print(f"        {url}")
        print("\nTo correct one, paste the right URL into the CSV's "
              f"'{URL_COL}' cell, or re-run with --only \"<title>\" --verbose "
              "to see the other candidates.")


if __name__ == "__main__":
    main()
