#!/usr/bin/env python3
import csv
import os
import sys
import time
import json
import urllib.request
import urllib.parse
import math
import re

def clean_artist(artist):
    # e.g., "Black Keys, The" -> "The Black Keys"
    if ", The" in artist:
        return "The " + artist.replace(", The", "").strip()
    return artist.strip()

def normalize_title(t):
    # Remove text in parentheses/brackets and non-alphanumeric chars
    t = re.sub(r'\(.*?\)', '', t)
    t = re.sub(r'\[.*?\]', '', t)
    return "".join(c.lower() for c in t if c.isalnum())

def title_matches(rec_title, db_title):
    rec_clean = normalize_title(rec_title)
    db_clean = normalize_title(db_title)
    if not rec_clean or not db_clean:
        return False
    return db_clean in rec_clean or rec_clean in db_clean

def get_listenbrainz_token():
    token = os.environ.get("LISTENBRAINZ_TOKEN")
    if token:
        return token
        
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cred_path = os.path.abspath(os.path.join(script_dir, "..", "..", "credentials.txt"))
    
    if os.path.exists(cred_path):
        try:
            with open(cred_path, "r", encoding="utf-8") as cred_f:
                for line in cred_f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        if k.strip() in ("LISTENBRAINZ_TOKEN", "LISTENBRAINZ_USER_TOKEN"):
                            return v.strip()
        except Exception as e:
            print(f"Warning: Failed to read credential file at {cred_path}: {e}", file=sys.stderr)
            
    return None

def fetch_recording_mbids_from_musicbrainz(title, artist):
    artist_cleaned = clean_artist(artist)
    # Search for all recordings by this title and artist
    query = f'recording:"{title}" AND artist:"{artist_cleaned}"'
    url = f"https://musicbrainz.org/ws/2/recording/?query={urllib.parse.quote(query)}&limit=100&fmt=json"
    
    req = urllib.request.Request(url, headers={"User-Agent": "WannabeWeekendersSetlistBuilder/1.0 ( jon.c.tedesco@gmail.com )"})
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode('utf-8'))
                recordings = data.get("recordings", [])
                
                matched_mbids = []
                for rec in recordings:
                    rec_title = rec.get("title", "")
                    if title_matches(rec_title, title):
                        matched_mbids.append(rec["id"])
                return matched_mbids
    except Exception as e:
        print(f"Error fetching recording MBIDs for '{title}' by '{artist}': {e}", file=sys.stderr)
    return []

def fetch_listenbrainz_popularity(mbids, token):
    url = "https://api.listenbrainz.org/1/popularity/recording"
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
        "User-Agent": "WannabeWeekendersSetlistBuilder/1.0 ( jon.c.tedesco@gmail.com )"
    }
    
    chunk_size = 20
    results = {}
    
    for i in range(0, len(mbids), chunk_size):
        chunk = mbids[i:i+chunk_size]
        payload = {"recording_mbids": chunk}
        data = json.dumps(payload).encode('utf-8')
        
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                if resp.status == 200:
                    resp_data = json.loads(resp.read().decode('utf-8'))
                    
                    recordings_list = []
                    if isinstance(resp_data, list):
                        recordings_list = resp_data
                    elif isinstance(resp_data, dict):
                        if "recordings" in resp_data:
                            recordings_list = resp_data["recordings"]
                        elif "payload" in resp_data and isinstance(resp_data["payload"], dict) and "recordings" in resp_data["payload"]:
                            recordings_list = resp_data["payload"]["recordings"]
                            
                    for item in recordings_list:
                        if not isinstance(item, dict):
                            continue
                        mbid = item.get("recording_mbid") or item.get("mbid")
                        listens = item.get("total_listen_count")
                        if listens is None:
                            listens = item.get("listen_count")
                        if listens is None:
                            listens = 0
                            
                        if mbid:
                            results[mbid] = int(listens)
        except Exception as e:
            print(f"Error querying ListenBrainz for chunk: {e}", file=sys.stderr)
            
        time.sleep(0.5) # Polite delay
        
    return results

def main():
    token = get_listenbrainz_token()
    if not token:
        print("Error: No ListenBrainz token found.", file=sys.stderr)
        print("Please set the LISTENBRAINZ_TOKEN environment variable or add it to credentials.txt.", file=sys.stderr)
        sys.exit(1)
        
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.abspath(os.path.join(script_dir, "..", "songs_metadata.csv"))
    
    if not os.path.exists(csv_path):
        print(f"Error: CSV file not found at {csv_path}", file=sys.stderr)
        sys.exit(1)
        
    # Read existing songs
    songs = []
    headers = []
    with open(csv_path, 'r', encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        for row in reader:
            songs.append(dict(row))
            
    # Ensure new headers exist
    new_headers = ['listenbrainz_listens', 'relative_popularity']
    for nh in new_headers:
        if nh not in headers:
            headers.append(nh)
            
    print(f"Resolving all recording versions from MusicBrainz for {len(songs)} songs...")
    song_to_mbids = {}
    all_mbids = set()
    
    for idx, song in enumerate(songs):
        title = song['title']
        artist = song['artist']
        
        if song.get('archived', 'No') == 'Yes':
            continue
            
        print(f"[{idx+1}/{len(songs)}] Searching versions for '{title}' by '{artist}'...")
        mbids = fetch_recording_mbids_from_musicbrainz(title, artist)
        print(f"  -> Found {len(mbids)} matching versions.")
        song_to_mbids[title] = mbids
        all_mbids.update(mbids)
        
        # Respect MusicBrainz rate limit: 1 request per second
        time.sleep(1.0)
        
    all_mbids_list = list(all_mbids)
    print(f"\nQuerying ListenBrainz play counts for {len(all_mbids_list)} unique recording IDs...")
    mbid_to_listens = fetch_listenbrainz_popularity(all_mbids_list, token)
    
    # Aggregate listen counts for each song
    print("\nAggregating play counts...")
    for song in songs:
        title = song['title']
        if song.get('archived', 'No') == 'Yes':
            song['listenbrainz_listens'] = ""
            song['relative_popularity'] = ""
            continue
            
        mbids = song_to_mbids.get(title, [])
        total_listens = sum(mbid_to_listens.get(mbid, 0) for mbid in mbids)
        song['listenbrainz_listens'] = str(total_listens)
        print(f"  '{title}': {total_listens} total plays (across {len(mbids)} versions)")
        
    # Calculate 1-10 popularity score on a fixed global log-scale.
    # Anchors are derived from real ListenBrainz data (site-wide, not relative to our setlist):
    #   LOG_MIN = log1p(0)           → score 1.0  (obscure / no listens)
    #   LOG_MAX = log1p(5_000_000)   → score 10.0 (global mega-hit, e.g. Bohemian Rhapsody)
    # Scores are clamped to [1.0, 10.0] so even very popular songs don't exceed 10.
    LOG_MIN = math.log1p(0)           # = 0.0
    LOG_MAX = math.log1p(5_000_000)   # ≈ 15.42
    LOG_RANGE = LOG_MAX - LOG_MIN

    print("\nScaling popularity against global ListenBrainz anchors (0 → 1.0, 5M listens → 10.0)...")
    for s in songs:
        if s.get('archived', 'No') != 'Yes':
            try:
                count = int(s.get('listenbrainz_listens', 0))
                raw_score = 1.0 + 9.0 * (math.log1p(count) - LOG_MIN) / LOG_RANGE
                score = max(1.0, min(10.0, raw_score))
                s['relative_popularity'] = f"{score:.2f}"
            except ValueError:
                s['relative_popularity'] = ""
    
    # Write back to CSV
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for song in songs:
            row = {k: song.get(k, "") for k in headers}
            writer.writerow(row)
            
    print("\nAggregation and relative popularity scoring completed successfully! CSV updated.")

if __name__ == '__main__':
    main()
