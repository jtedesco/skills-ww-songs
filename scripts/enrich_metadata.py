#!/usr/bin/env python3
import csv
import os
import sys
import time
import json
import urllib.request
import urllib.parse
import re

def clean_artist(artist):
    # e.g., "Black Keys, The" -> "The Black Keys"
    if ", The" in artist:
        return "The " + artist.replace(", The", "").strip()
    return artist.strip()

def clean_title(title):
    # Remove extra annotations like " (Live)" or similar if they might mess up lookup
    return title.strip()

def query_musicbrainz(url):
    req = urllib.request.Request(
        url,
        headers={'User-Agent': 'WannabeWeekendersSetlistBuilder/1.0 ( jon.c.tedesco@gmail.com )'}
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code == 503:
                time.sleep(2)
                continue
            elif e.code == 404:
                return None
            else:
                print(f"HTTP Error {e.code} for URL: {url}", file=sys.stderr)
                return None
        except Exception as e:
            print(f"Error for URL: {url} - {e}", file=sys.stderr)
            return None
    return None

def fetch_song_attributes(title, artist):
    artist_cleaned = clean_artist(artist)
    title_cleaned = clean_title(title)
    
    # Predefined set of common mood keywords to match against MusicBrainz tags
    mood_words = {
        'energetic', 'happy', 'sad', 'chill', 'uplifting', 'melancholy', 'dark', 
        'aggressive', 'relaxed', 'emotional', 'fun', 'angry', 'romantic', 'hype', 
        'dance', 'party', 'smooth', 'laidback', 'mysterious', 'intense', 'somber', 
        'playful', 'dreamy', 'nostalgic', 'epic', 'upbeat', 'slow', 'fast', 'intense',
        'heavy', 'light', 'calm', 'dramatic', 'melancholic', 'peaceful', 'cheerful',
        'quirky', 'sensual', 'warm', 'cool', 'triumphant', 'sombre', 'funky', 'groovy',
        'ballad', 'soulful', 'rhythmic', 'driving', 'atmospheric', 'bouncy', 'gentle',
        'melodic'
    }
    stop_words = {
        'uk', 'british', 'usa', 'american', 'band', 'group', 'vocalist', 'singer', 
        'composer', 'producer', 'rock', 'pop', 'jazz', 'blues', 'soul', 'metal', 
        'country', 'alternative', 'indie', 'classic rock', 'hard rock', 'folk', 
        'disco', 'reggae', 'funk', 'electronic', 'dance-pop', 'new wave'
    }
    
    # 1. Query release-group to get earliest release date and original album/single title
    rg_query = f'releasegroup:"{title_cleaned}" AND artist:"{artist_cleaned}"'
    rg_url = f'https://musicbrainz.org/ws/2/release-group/?query={urllib.parse.quote(rg_query)}&fmt=json'
    
    rg_data = query_musicbrainz(rg_url)
    time.sleep(1.0) # Respect rate limits
    
    earliest_year = ""
    album_title = ""
    
    if rg_data and rg_data.get('release-groups'):
        rgs = rg_data['release-groups']
        rgs_sorted = sorted(rgs, key=lambda x: (
            0 if x.get('primary-type') == 'Album' else
            1 if x.get('primary-type') == 'Single' else
            2 if x.get('primary-type') == 'EP' else 3,
            -int(x.get('score', 0))
        ))
        best_rg = rgs_sorted[0]
        album_title = best_rg.get('title', '')
        first_date = best_rg.get('first-release-date', '')
        if first_date:
            match = re.search(r'\d{4}', first_date)
            if match:
                earliest_year = match.group(0)

    # 2. Query recording to get recording ID, genres, and mood tags
    rec_query = f'recording:"{title_cleaned}" AND artist:"{artist_cleaned}"'
    rec_url = f'https://musicbrainz.org/ws/2/recording/?query={urllib.parse.quote(rec_query)}&fmt=json'
    
    rec_data = query_musicbrainz(rec_url)
    time.sleep(1.0) # Respect rate limits
    
    recording_id = ""
    rec_genres = []
    rec_moods = []
    
    if rec_data and rec_data.get('recordings'):
        best_rec = rec_data['recordings'][0]
        recording_id = best_rec.get('id', '')
        
        # Pull tags/genres
        tags = best_rec.get('tags', [])
        all_tags_sorted = sorted(tags, key=lambda x: x.get('count', 0), reverse=True)
        
        for t in all_tags_sorted:
            t_name = t.get('name', '').lower()
            if not t_name:
                continue
            if t_name in mood_words:
                rec_moods.append(t_name)
            elif t_name not in stop_words and not re.match(r'^\d{2}s$', t_name):
                rec_genres.append(t_name)
        
        # Fallback to artist tags if recording has no tags/genres/moods
        if not rec_genres or not rec_moods:
            artist_credit = best_rec.get('artist-credit', [])
            if artist_credit:
                artist_id = artist_credit[0].get('artist', {}).get('id')
                if artist_id:
                    art_url = f'https://musicbrainz.org/ws/2/artist/{artist_id}?inc=tags&fmt=json'
                    art_data = query_musicbrainz(art_url)
                    time.sleep(1.0) # Respect rate limits
                    if art_data:
                        art_tags = art_data.get('tags', [])
                        art_tags_sorted = sorted(art_tags, key=lambda x: x.get('count', 0), reverse=True)
                        for t in art_tags_sorted:
                            t_name = t.get('name', '').lower()
                            if t_name:
                                if t_name in mood_words:
                                    if t_name not in rec_moods:
                                        rec_moods.append(t_name)
                                elif t_name not in stop_words and not re.match(r'^\d{2}s$', t_name):
                                    if t_name not in rec_genres:
                                        rec_genres.append(t_name)

    # Clean genres: limit to top 3 and title case them
    cleaned_genres = []
    for g in rec_genres[:3]:
        cleaned_genres.append(g.title())
    genre_str = ";".join(cleaned_genres) if cleaned_genres else "Rock"
    
    # Clean moods: limit to top 3 and title case them
    cleaned_moods = []
    for m in rec_moods[:3]:
        cleaned_moods.append(m.title())
    mood_str = ";".join(cleaned_moods) if cleaned_moods else "Upbeat" # Default to Upbeat if none found
    
    return {
        'release_year': earliest_year,
        'original_album': album_title,
        'musicbrainz_genre': genre_str,
        'musicbrainz_mood': mood_str,
        'musicbrainz_id': recording_id
    }

def main():
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
            
    # Add new headers if they don't exist
    new_headers = ['release_year', 'original_album', 'musicbrainz_genre', 'musicbrainz_mood', 'musicbrainz_id']
    for nh in new_headers:
        if nh not in headers:
            headers.append(nh)
            
    # Enrich each song
    print(f"Starting enrichment of {len(songs)} songs...")
    for idx, song in enumerate(songs):
        title = song['title']
        artist = song['artist']
        
        # If already has mood tag, we can skip
        if song.get('musicbrainz_mood'):
            print(f"[{idx+1}/{len(songs)}] Skipping '{title}' (already has mood tag)")
            continue
            
        print(f"[{idx+1}/{len(songs)}] Querying MusicBrainz for '{title}' by '{artist}'...")
        try:
            attrs = fetch_song_attributes(title, artist)
            song['release_year'] = attrs['release_year'] or song.get('release_year') or ""
            song['original_album'] = attrs['original_album'] or song.get('original_album') or ""
            song['musicbrainz_genre'] = attrs['musicbrainz_genre'] or song.get('musicbrainz_genre') or ""
            song['musicbrainz_mood'] = attrs['musicbrainz_mood'] or song.get('musicbrainz_mood') or ""
            song['musicbrainz_id'] = attrs['musicbrainz_id'] or song.get('musicbrainz_id') or ""
            print(f"  Result -> Year: {song['release_year']}, Album: {song['original_album']}, Genre: {song['musicbrainz_genre']}, Mood: {song['musicbrainz_mood']}, ID: {song['musicbrainz_id']}")
        except Exception as e:
            print(f"  Failed to enrich '{title}': {e}", file=sys.stderr)
            
    # Write back to CSV
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for song in songs:
            # Clean dictionary keys to only include headers
            row = {k: song.get(k, "") for k in headers}
            writer.writerow(row)
            
    print("Enrichment completed successfully! CSV updated with mood tags.")

if __name__ == '__main__':
    main()
