#!/usr/bin/env python3
import csv
import os
import sys
import time
import json
import urllib.request
import urllib.parse
import base64

def clean_artist(artist):
    if ", The" in artist:
        return "The " + artist.replace(", The", "").strip()
    return artist.strip()

def get_spotify_token(client_id, client_secret):
    auth_str = f"{client_id}:{client_secret}"
    auth_b64 = base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')
    
    url = "https://accounts.spotify.com/api/token"
    headers = {
        "Authorization": f"Basic {auth_b64}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode('utf-8')
    
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status == 200:
                result = json.loads(resp.read().decode('utf-8'))
                return result.get("access_token")
    except Exception as e:
        print(f"Error fetching Spotify token: {e}", file=sys.stderr)
        return None

def search_spotify_track(title, artist, token):
    artist_cleaned = clean_artist(artist)
    # Search query: track:"Title" artist:"Artist"
    query = f'track:"{title}" artist:"{artist_cleaned}"'
    url = f"https://api.spotify.com/v1/search?q={urllib.parse.quote(query)}&type=track&limit=1"
    
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode('utf-8'))
                tracks = data.get("tracks", {}).get("items", [])
                if tracks:
                    best_track = tracks[0]
                    return {
                        "popularity": best_track.get("popularity"),
                        "spotify_id": best_track.get("id")
                    }
    except Exception as e:
        print(f"Error searching Spotify for '{title}': {e}", file=sys.stderr)
    return None

def main():
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cred_path = os.path.abspath(os.path.join(script_dir, "..", "..", "credentials.txt"))
    
    if not client_id or not client_secret:
        if os.path.exists(cred_path):
            try:
                with open(cred_path, "r", encoding="utf-8") as cred_f:
                    for line in cred_f:
                        if "=" in line:
                            k, v = line.strip().split("=", 1)
                            if k.strip() == "SPOTIFY_CLIENT_ID":
                                client_id = v.strip()
                            elif k.strip() == "SPOTIFY_CLIENT_SECRET":
                                client_secret = v.strip()
            except Exception as e:
                print(f"Warning: Failed to read credential file at {cred_path}: {e}", file=sys.stderr)
                
    if not client_id or not client_secret:
        print("Error: Please set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET environment variables or create spotify_credentials.txt in the parent directory.", file=sys.stderr)
        print("You can get these from the Spotify Developer Dashboard (https://developer.spotify.com/).", file=sys.stderr)
        sys.exit(1)
        
    csv_path = os.path.abspath(os.path.join(script_dir, "..", "songs_metadata.csv"))
    
    if not os.path.exists(csv_path):
        print(f"Error: CSV file not found at {csv_path}", file=sys.stderr)
        sys.exit(1)
        
    print("Authenticating with Spotify...")
    token = get_spotify_token(client_id, client_secret)
    if not token:
        print("Error: Authentication failed.", file=sys.stderr)
        sys.exit(1)
    print("Authentication successful!")
    
    # Read existing songs
    songs = []
    headers = []
    with open(csv_path, 'r', encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        for row in reader:
            songs.append(dict(row))
            
    # Add new headers if they don't exist
    new_headers = ['spotify_popularity', 'spotify_id']
    for nh in new_headers:
        if nh not in headers:
            headers.append(nh)
            
    # Enrich each song
    print(f"Starting Spotify enrichment of {len(songs)} songs...")
    for idx, song in enumerate(songs):
        title = song['title']
        artist = song['artist']
        
        # If already has popularity tag and not overwriting, we can skip
        if song.get('spotify_popularity'):
            print(f"[{idx+1}/{len(songs)}] Skipping '{title}' (already enriched)")
            continue
            
        print(f"[{idx+1}/{len(songs)}] Querying Spotify for '{title}' by '{artist}'...")
        res = search_spotify_track(title, artist, token)
        if res:
            song['spotify_popularity'] = str(res['popularity'])
            song['spotify_id'] = res['spotify_id']
            print(f"  Result -> Popularity: {song['spotify_popularity']}, ID: {song['spotify_id']}")
        else:
            song['spotify_popularity'] = ""
            song['spotify_id'] = ""
            print("  Result -> Not Found")
            
        time.sleep(0.2) # Rate limit breathing room
        
    # Write back to CSV
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for song in songs:
            row = {k: song.get(k, "") for k in headers}
            writer.writerow(row)
            
    print("Spotify popularity enrichment completed successfully! CSV updated.")

if __name__ == '__main__':
    main()
