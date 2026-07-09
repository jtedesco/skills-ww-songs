#!/usr/bin/env python3
import csv
import os
import sys
import random
import re
import argparse
import io
import shutil
from datetime import datetime
from math import ceil

def parse_length(length_str):
    if not length_str:
        return 0
    clean = re.sub(r'[^\d:]', '', length_str)
    if ":" in clean:
        parts = clean.split(":")
        try:
            return int(parts[0]) * 60 + int(parts[1])
        except ValueError:
            return 0
    else:
        try:
            return int(clean) * 60
        except ValueError:
            return 0

def format_length(seconds):
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"

# Single source of truth for "who's in the band" — a fixed, deterministic
# order (not a set) so downstream membership math never depends on Python's
# per-process hash-randomized set iteration order.
BAND_ROSTER = ["Lauren", "Jon", "Martin", "David", "JJ", "Debo", "Alex"]

def parse_can_leave_stage(value):
    """Parse the `can_leave_stage` CSV column into a set of member names.

    Values look like "Debo, Alex, Jon (Acoustic)" — a trailing "(Acoustic)"
    or "(Full Band)" tag disambiguates which arrangement of an "Either" song
    the list applies to. Returns None (not an empty set) when the column has
    no data yet, so callers can distinguish "nobody can leave" from "not
    filled in" and fall back accordingly.
    """
    if not value or value == "None":
        return None
    cleaned = re.sub(r"\s*\((Acoustic|Full Band)\)\s*$", "", value.strip())
    names = {n.strip() for n in cleaned.split(",") if n.strip()}
    return names or None

def parse_covering_vocalist(notes, default):
    """Extract the per-song covering lead vocalist from substitution_notes.

    Notes like "If Martin is out: Lauren sings lead vocals." let a specific
    song override the band's default covering singer (e.g. Born to Run goes
    to Lauren instead of the usual David-covers-Martin rule) without any
    per-title logic in this script — just an edit to the CSV.
    """
    m = re.search(r"(\w+) sings lead vocals", notes)
    return m.group(1) if m else default

def get_active_performers(song, martin_out=False, david_out=False):
    """Determine which band members are active (on stage) for a song.

    Prefers the curated `can_leave_stage` column in songs_metadata.csv — the
    complement of the active set — so this data lives in the database, not
    hardcoded in the skill. Falls back to lead + backup vocals for any song
    that hasn't been backfilled with can_leave_stage data yet.
    """
    leaving = parse_can_leave_stage(song.get("can_leave_stage", ""))
    if leaving is not None:
        active = set(BAND_ROSTER) - leaving
    else:
        active = {song["lead_vocals"]}
        for backup in song.get("backup_vocals", []):
            if backup == "L": active.add("Lauren")
            elif backup == "J": active.add("Jon")
            elif backup == "D": active.add("David")
            elif backup == "M": active.add("Martin")

    # Apply substitutions / member out rules
    if martin_out and "Martin" in active:
        active.remove("Martin")
    if david_out and "David" in active:
        active.remove("David")

    return active

def get_segue_groups(songs):
    groups = []
    seen_rules = set()
    for s in songs:
        rules = s.get("order_rules", "None")
        if rules and rules != "None" and rules not in seen_rules:
            seen_rules.add(rules)
            parts = [p.strip() for p in rules.split("->")]
            group = []
            for p in parts:
                if p.startswith("SEGUE "):
                    p = p[6:]
                group.append(p)
            groups.append(group)
    return groups

class Item:
    def __init__(self, songs_list):
        self.songs = songs_list
        self.titles = [s["title"] for s in songs_list]
        
        total_seconds = sum(parse_length(s["length"]) for s in songs_list)
        if len(songs_list) > 1:
            total_seconds += (len(songs_list) - 1) * 30
        self.duration_seconds = total_seconds
        
        self.vocals_sequence = [s["lead_vocals"] for s in songs_list]
        self.opener = songs_list[0]["opener"]
        self.closer = songs_list[-1]["closer"]
        self.bpm = sum(s["bpm"] for s in songs_list) / len(songs_list)
        
        prio_map = {"2026-05": 2, "2026-03": 1, "2026-01": 0}
        self.priority = max(prio_map.get(s.get("date_added", "2026-01"), 0) for s in songs_list)
        
    def __repr__(self):
        return f"Item({self.titles})"

def make_v_shape(items):
    sorted_items = sorted(items, key=lambda it: it.bpm, reverse=True)
    left = []
    right = []
    for idx, item in enumerate(sorted_items):
        if idx % 2 == 0:
            left.append(item)
        else:
            right.append(item)
    return left + list(reversed(right))

def select_acoustic_breaks(acoustic_pool, num_breaks, martin_out=False, david_out=False, max_intersection=0, always_on=None):
    """Find num_breaks pairs of acoustic songs for bathroom breaks.

    'always_on' is a set of performer names to EXCLUDE from the overlap check —
    typically Lauren when Martin is out, since she appears in every acoustic song.
    The constraint is still useful: we want at least some band members to rotate.
    """
    if always_on is None:
        always_on = set()
    valid_pairs = []
    for i in range(len(acoustic_pool)):
        for j in range(i + 1, len(acoustic_pool)):
            song_a = acoustic_pool[i]
            song_b = acoustic_pool[j]
            active_a = get_active_performers(song_a, martin_out, david_out) - always_on
            active_b = get_active_performers(song_b, martin_out, david_out) - always_on

            if len(active_a.intersection(active_b)) <= max_intersection:
                valid_pairs.append((song_a, song_b))

    def find_unique_combination(pairs, count, chosen=[]):
        if len(chosen) == count:
            return chosen
        for p in pairs:
            used_songs = {s["title"] for pair in chosen for s in pair}
            if p[0]["title"] not in used_songs and p[1]["title"] not in used_songs:
                res = find_unique_combination(pairs, count, chosen + [p])
                if res is not None:
                    return res
        return None

    return find_unique_combination(valid_pairs, num_breaks)


def clean_backups(lead_vocals, backups_list):
    cleaned = []
    for b in backups_list:
        if b == "L" and lead_vocals == "Lauren": continue
        if b == "J" and lead_vocals == "Jon": continue
        if b == "D" and lead_vocals == "David": continue
        if b == "M" and lead_vocals == "Martin": continue
        cleaned.append(b)
    return cleaned

def simulate_all_scheduled(sets_songs, available_songs, num_sets, breaks_opt, num_breaks, acoustic_pool, martin_out, david_out):
    break_songs_sets = []
    if breaks_opt == "acoustic" and num_breaks > 0:
        used_song_titles = {s["title"] for set_songs in sets_songs for s in set_songs}
        available_acoustic = [s for s in acoustic_pool if s["title"] not in used_song_titles]
        
        if len(available_acoustic) < num_breaks * 2:
            def _cut_for_lineup(s):
                notes = s.get("substitution_notes", "")
                if martin_out and notes.startswith("If Martin is out:") and "Cut song" in notes:
                    return True
                if david_out and notes.startswith("If David is out:") and "Cut song" in notes:
                    return True
                return False
            available_acoustic = [
                s for s in available_songs
                if s["arrangement"] in ["Acoustic", "Either"]
                and s["title"] not in used_song_titles
                and not _cut_for_lineup(s)
            ]
            
        break_pairs = select_acoustic_breaks(available_acoustic, num_breaks, martin_out, david_out, max_intersection=0)
        if not break_pairs:
            break_pairs = select_acoustic_breaks(available_acoustic, num_breaks, martin_out, david_out, max_intersection=1)
        if break_pairs:
            break_songs_sets = break_pairs
            
    encores = []
    used_song_titles = {s["title"] for set_songs in sets_songs for s in set_songs}
    if break_songs_sets:
        for pair in break_songs_sets:
            used_song_titles.add(pair[0]["title"])
            used_song_titles.add(pair[1]["title"])
            
    remaining_songs = [s for s in available_songs if s["title"] not in used_song_titles]
    encore_options = ["All Right Now", "Crazy Little Thing Called Love"]
    for opt in encore_options:
        match = next((s for s in remaining_songs if s["title"].lower() == opt.lower()), None)
        if match:
            encores.append(match)
            remaining_songs.remove(match)
            
    while len(encores) < 2 and remaining_songs:
        remaining_songs.sort(key=lambda s: s["bpm"], reverse=True)
        encores.append(remaining_songs.pop(0))
        
    return [s for set_s in sets_songs for s in set_s] + encores

def main():
    parser = argparse.ArgumentParser(description="Wannabe Weekenders Setlist Builder")
    parser.add_argument("--gig-type", choices=["bar", "yacht"], default="bar", help="Gig type: bar (anything) or yacht (Yacht Rock/Adjacent only)")
    parser.add_argument("--duration", type=float, default=3.0, help="Total gig duration in hours (e.g. 1.0, 2.0, 3.0)")
    parser.add_argument("--martin-out", action="store_true", help="Martin is out (David covers lead/backups, Martin-required songs cut per database)")
    parser.add_argument("--david-out", action="store_true", help="David is out (Lauren covers David's lead parts, keys/marimba omitted/covered)")
    parser.add_argument("--breaks", choices=["acoustic", "silent", "none"], default="acoustic", help="Break format: acoustic (filled with 2 acoustic songs), silent, or none")
    parser.add_argument("--include-not-ready", action="store_true", help="Include not-yet-gig-ready songs in sets and breaks")
    parser.add_argument("--skip-country-grunge", action="store_true", help="Skip country (Keep Your Hands to Yourself, Take It Easy, Me and Bobby McGee) and grunge (Zombie, You Oughta Know, Interstate Love Song) songs")
    parser.add_argument("--genre", type=str, default=None, help="Filter songs by genre (case-insensitive, e.g. 'Rock', 'Pop', 'Soul')")
    parser.add_argument("--era", type=str, default=None, help="Filter songs by era / decade (e.g. '70s', '80s', '90s', '1970s', '1980s')")
    parser.add_argument("--mood", type=str, default=None, help="Filter songs by mood (case-insensitive, e.g. 'Upbeat', 'Energetic', 'Chill')")
    parser.add_argument("--max-david", type=int, default=None, help="Maximum number of lead songs for David")
    parser.add_argument("--max-martin", type=int, default=None, help="Maximum number of lead songs for Martin")
    parser.add_argument("--max-lauren", type=int, default=None, help="Maximum number of lead songs for Lauren")
    parser.add_argument("--max-jon", type=int, default=None, help="Maximum number of lead songs for Jon")
    parser.add_argument("--min-david", type=int, default=None, help="Minimum number of lead songs for David")
    parser.add_argument("--min-martin", type=int, default=None, help="Minimum number of lead songs for Martin")
    parser.add_argument("--min-lauren", type=int, default=None, help="Minimum number of lead songs for Lauren")
    parser.add_argument("--min-jon", type=int, default=None, help="Minimum number of lead songs for Jon")
    parser.add_argument("--date", type=str, default=None, help="Gig date (YYYY-MM-DD) used for output filename")
    parser.add_argument("--location", type=str, default=None, help="Venue/location name used for output filename")
    
    args = parser.parse_args()
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(script_dir, "..", "songs_metadata.csv")
    
    if not os.path.exists(db_path):
        print(f"Error: Database file not found at {db_path}", file=sys.stderr)
        sys.exit(1)
        
    with open(db_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        all_songs = []
        for row in reader:
            song = dict(row)
            song["bpm"] = int(song["bpm"])
            song["backup_vocals"] = [v for v in song["backup_vocals"].split(";") if v]
            all_songs.append(song)
        
    # Apply substitutions and filter pool
    available_songs = []
    acoustic_pool = []
    martin_cut_songs = []
    david_cut_songs = []
    
    # Vocally-taxing songs are flagged per-song in the database (vocalist_constraints),
    # not hardcoded here, so adding/removing one only requires an edit to the CSV.
    gravelly_songs = {
        s["title"] for s in all_songs
        if "gravelly" in s.get("vocalist_constraints", "").lower()
        or "taxing" in s.get("vocalist_constraints", "").lower()
    }
    
    for s in all_songs:
        song = dict(s)
        title = song["title"]
        
        # Archive Filter
        if song.get("archived", "No") == "Yes":
            continue
            
        # Genre Filter
        if args.genre:
            song_genres = [g.strip().lower() for g in song.get("musicbrainz_genre", "").split(";") if g.strip()]
            req_genre = args.genre.lower()
            if not any(req_genre in g for g in song_genres):
                continue
                
        # Mood Filter
        if args.mood:
            song_moods = [m.strip().lower() for m in song.get("musicbrainz_mood", "").split(";") if m.strip()]
            req_mood = args.mood.lower()
            if not any(req_mood in m for m in song_moods):
                continue
                
        # Era Filter
        if args.era:
            era_str = args.era.strip().lower()
            year_str = song.get("release_year", "")
            if not year_str:
                continue
            try:
                year = int(year_str)
                decade = None
                if era_str in ['70s', '1970s']: decade = 1970
                elif era_str in ['80s', '1980s']: decade = 1980
                elif era_str in ['90s', '1990s']: decade = 1990
                elif era_str in ['00s', '2000s', '00']: decade = 2000
                elif era_str in ['10s', '2010s']: decade = 2010
                elif era_str in ['20s', '2020s']: decade = 2020
                
                if decade is not None:
                    if not (decade <= year <= decade + 9):
                        continue
                else:
                    if year_str != era_str:
                        continue
            except ValueError:
                continue
            
        # Skip Country and Grunge if requested
        country_grunge = {
            "keep your hands to yourself", "take it easy", "me and bobby mcgee",
            "zombie", "you oughta know", "interstate love song"
        }
        if args.skip_country_grunge and title.lower() in country_grunge:
            continue
            
        # Gig readiness check
        if song["gig_ready"] != "Yes" and not args.include_not_ready:
            continue
            
        # Martin Out rules: any song whose substitution_notes says it must be
        # cut without Martin (e.g. requires his acoustic guitar) is dropped
        # from the pool entirely, driven by the database rather than a
        # hardcoded title list.
        if args.martin_out:
            notes = song.get("substitution_notes", "")
            if notes.startswith("If Martin is out:") and "Cut song" in notes:
                martin_cut_songs.append(title)
                continue
            if song["lead_vocals"] == "Martin":
                song["lead_vocals"] = parse_covering_vocalist(notes, "David")
            if "M" in song.get("backup_vocals", []):
                song["backup_vocals"] = [b for b in song["backup_vocals"] if b != "M"]

        # David Out rules
        if args.david_out:
            notes = song.get("substitution_notes", "")
            if notes.startswith("If David is out:") and "Cut song" in notes:
                david_cut_songs.append(title)
                continue
            if song["lead_vocals"] == "David":
                song["lead_vocals"] = parse_covering_vocalist(notes, "Lauren")
            if "D" in song.get("backup_vocals", []):
                song["backup_vocals"] = [b for b in song["backup_vocals"] if b != "D"]
                
        # Clean backups to ensure lead is never backup
        song["backup_vocals"] = clean_backups(song["lead_vocals"], song.get("backup_vocals", []))
        
        # Yacht rock filter
        if args.gig_type == "yacht":
            if song["yacht_adjacent"] not in ["Yes", "Adjacent"]:
                continue
                
        # Separate into pools
        if song["arrangement"] == "Acoustic":
            acoustic_pool.append(song)
        elif song["arrangement"] == "Either":
            acoustic_pool.append(song)
            available_songs.append(song)
        else:
            available_songs.append(song)

    # Calculate set structures

    # Keep 2 hours or less as a single set (unless specified otherwise)
    if args.duration <= 2.0:
        num_sets = 1
    else:
        num_sets = max(1, int(ceil(args.duration)))
    num_breaks = num_sets - 1

    # ---------------------------------------------------------------
    # Pre-reserve acoustic break songs (before the main set solver)
    # so they can't be consumed by the main sets.
    # Priority: Acoustic-only songs first (they're never in available_songs
    # anyway), then "Either" songs as fallback (removing from available_songs
    # to ensure they are held back).
    # ---------------------------------------------------------------
    pre_reserved_break_titles = set()
    if args.breaks == "acoustic" and num_breaks > 0:
        def _is_hard_cut(s):
            notes = s.get("substitution_notes", "")
            if args.martin_out and "If Martin is out:" in notes and "Cut song" in notes:
                return True
            if args.david_out and "If David is out:" in notes and "Cut song" in notes:
                return True
            return False

        candidate_acoustics = [
            s for s in acoustic_pool
            if (s["gig_ready"] == "Yes" or args.include_not_ready)
            and not _is_hard_cut(s)
        ]

        # Try to find valid pairs among Acoustic-only songs first
        acoustic_only = [s for s in candidate_acoustics if s["arrangement"] == "Acoustic"]
        either_acoustics = [s for s in candidate_acoustics if s["arrangement"] == "Either"]

        # When Martin is out Lauren appears in every acoustic song, so exclude
        # her from the overlap check (she'll always be on stage during breaks).
        always_on = {"Lauren", "David"} if args.martin_out else set()

        pre_pairs = select_acoustic_breaks(acoustic_only, num_breaks, args.martin_out, args.david_out, max_intersection=0, always_on=always_on)
        if not pre_pairs:
            pre_pairs = select_acoustic_breaks(candidate_acoustics, num_breaks, args.martin_out, args.david_out, max_intersection=0, always_on=always_on)
        if not pre_pairs:
            pre_pairs = select_acoustic_breaks(candidate_acoustics, num_breaks, args.martin_out, args.david_out, max_intersection=1, always_on=always_on)

        if pre_pairs:
            for pair in pre_pairs:
                for s in pair:
                    pre_reserved_break_titles.add(s["title"])
            # Remove "Either" songs that are reserved from the main set pool
            available_songs = [s for s in available_songs if s["title"] not in pre_reserved_break_titles]


    total_gig_seconds = args.duration * 3600
    break_duration_seconds = 10 * 60 if num_breaks > 0 else 0
    encore_duration_seconds = 8 * 60 if num_sets > 1 else 0
    
    total_break_seconds = num_breaks * break_duration_seconds
    total_set_music_seconds = total_gig_seconds - total_break_seconds - (encore_duration_seconds if num_sets > 1 else 0)
    
    # Calculate total duration of available songs matching filters to verify if we have enough music
    total_available_seconds = sum(parse_length(s["length"]) for s in available_songs)
    if len(available_songs) > 1:
        total_available_seconds += (len(available_songs) - 1) * 30
        
    insufficient_music = False
    if total_available_seconds < total_set_music_seconds:
        insufficient_music = True
        total_set_music_seconds = total_available_seconds
        
    target_set_seconds = total_set_music_seconds / num_sets
    
    # Segue group building
    segue_raw_groups = get_segue_groups(available_songs)
    
    used_in_segue = set()
    segue_items = []
    
    for group in segue_raw_groups:
        group_songs = []
        valid = True
        for title in group:
            match = next((s for s in available_songs if s["title"].lower() == title.lower()), None)
            if not match:
                valid = False
                break
            group_songs.append(match)
        if valid:
            item = Item(group_songs)
            segue_items.append(item)
            for title in group:
                used_in_segue.add(title.lower())
                
    single_items = []
    for s in available_songs:
        if s["title"].lower() not in used_in_segue:
            single_items.append(Item([s]))
            
    all_items = segue_items + single_items
    
    # Target lead vocals percentages (proportional distribution if out)
    base_vocal_pcts = {"Lauren": 0.5, "Jon": 0.3, "David": 0.1, "Martin": 0.1}
    remaining_vocalists = ["Lauren", "Jon"]
    if not args.martin_out: remaining_vocalists.append("Martin")
    if not args.david_out: remaining_vocalists.append("David")

    # Vocalist Inclusion only applies to present vocalists who have at least one
    # eligible lead song in the filtered pool (e.g. Martin has no Yacht Rock songs,
    # so we cannot require him to lead in a Yacht Rock set).
    eligible_leads = {s["lead_vocals"] for s in available_songs}
    inclusion_required_vocalists = [v for v in remaining_vocalists if v in eligible_leads]

    
    sum_rem_targets = sum(base_vocal_pcts[v] for v in remaining_vocalists)
    target_vocal_pcts = {}
    for v in ["Lauren", "Jon", "David", "Martin"]:
        if v in remaining_vocalists:
            target_vocal_pcts[v] = base_vocal_pcts[v] / sum_rem_targets
        else:
            target_vocal_pcts[v] = 0.0
            
    # Solver loop
    best_candidate = None
    best_score = float('inf')
    
    max_consecutive_vocals = 3
    min_gravelly_separation = 2
    duration_tolerance = 180
    
    constraints_satisfied_summary = {
        "Show Opener (Working for the Weekend)": "❌ Not Satisfied (Not in pool/not ready)",
        "Show Closer (Roll with the Changes)": "❌ Not Satisfied (Not in pool/not ready)",
        "Set Openers & Closers": "✅ Satisfied",
        "Vocalist Balance": "✅ Satisfied",
        "Lauren Vocal Health": "✅ Satisfied",
        "Pacing Flow": "✅ Satisfied",
        "Bathroom Breaks": "✅ Satisfied",
        "Target Duration": "✅ Satisfied",
        "Vocalist Inclusion": "✅ Satisfied"
    }
    
    for relaxation in range(5):
        if relaxation == 1:
            min_gravelly_separation = 1
            constraints_satisfied_summary["Lauren Vocal Health"] = "⚠️ Partially Satisfied (Relaxed to 1 song gap)"
        elif relaxation == 2:
            max_consecutive_vocals = 4
            constraints_satisfied_summary["Vocalist Balance"] = "⚠️ Partially Satisfied (Relaxed to max 4 consecutive)"
        elif relaxation == 3:
            duration_tolerance = 360
            constraints_satisfied_summary["Target Duration"] = "⚠️ Partially Satisfied (Set lengths relaxed to +/- 6 mins)"
        elif relaxation == 4:
            min_gravelly_separation = 0
            max_consecutive_vocals = 5
            duration_tolerance = 600
            constraints_satisfied_summary["Lauren Vocal Health"] = "❌ Not Satisfied (Gravelly back-to-back allowed)"
            constraints_satisfied_summary["Vocalist Balance"] = "❌ Not Satisfied (Relaxed to max 5 consecutive)"
            constraints_satisfied_summary["Target Duration"] = "❌ Not Satisfied (Set lengths highly relaxed)"
            
        attempts = 5000
        for attempt in range(attempts):
            high_prio = [it for it in all_items if it.priority == 2]
            med_prio = [it for it in all_items if it.priority == 1]
            low_prio = [it for it in all_items if it.priority == 0]
            
            random.shuffle(high_prio)
            random.shuffle(med_prio)
            random.shuffle(low_prio)
            
            prioritized_pool = high_prio + med_prio + low_prio
            
            sets_items = [[] for _ in range(num_sets)]
            used_item_indices = set()
            
            # 1. Assign openers
            # Set 1 opener (prefer WFTW)
            wftw_idx = next((idx for idx, it in enumerate(prioritized_pool) if "Working for the Weekend" in it.titles), None)
            if wftw_idx is not None:
                sets_items[0].append(prioritized_pool[wftw_idx])
                used_item_indices.add(wftw_idx)
                constraints_satisfied_summary["Show Opener (Working for the Weekend)"] = "✅ Satisfied"
            else:
                opener_candidates = [(idx, it) for idx, it in enumerate(prioritized_pool) if it.opener in ["Yes", "Maybe"] and idx not in used_item_indices]
                if not opener_candidates: continue
                max_prio = max(c[1].priority for c in opener_candidates)
                best = [c for c in opener_candidates if c[1].priority == max_prio]
                yes_c = [c for c in best if c[1].opener == "Yes"]
                chosen_idx, chosen_item = random.choice(yes_c if yes_c else best)
                sets_items[0].append(chosen_item)
                used_item_indices.add(chosen_idx)
                
            # Other sets openers
            openers_found = True
            for s_idx in range(1, num_sets):
                opener_candidates = [(idx, it) for idx, it in enumerate(prioritized_pool) if it.opener in ["Yes", "Maybe"] and idx not in used_item_indices]
                if not opener_candidates:
                    openers_found = False
                    break
                max_prio = max(c[1].priority for c in opener_candidates)
                best = [c for c in opener_candidates if c[1].priority == max_prio]
                yes_c = [c for c in best if c[1].opener == "Yes"]
                chosen_idx, chosen_item = random.choice(yes_c if yes_c else best)
                sets_items[s_idx].append(chosen_item)
                used_item_indices.add(chosen_idx)
                
            if not openers_found:
                continue
                
            # 2. Assign closers
            # Set N closer (prefer RWTC)
            rwtc_idx = next((idx for idx, it in enumerate(prioritized_pool) if "Roll with the Changes" in it.titles), None)
            if rwtc_idx is not None and rwtc_idx not in used_item_indices:
                sets_items[num_sets-1].append(prioritized_pool[rwtc_idx])
                used_item_indices.add(rwtc_idx)
                constraints_satisfied_summary["Show Closer (Roll with the Changes)"] = "✅ Satisfied"
            else:
                closer_candidates = [(idx, it) for idx, it in enumerate(prioritized_pool) if it.closer in ["Yes", "Maybe"] and idx not in used_item_indices]
                if not closer_candidates: continue
                max_prio = max(c[1].priority for c in closer_candidates)
                best = [c for c in closer_candidates if c[1].priority == max_prio]
                yes_c = [c for c in best if c[1].closer == "Yes"]
                chosen_idx, chosen_item = random.choice(yes_c if yes_c else best)
                sets_items[num_sets-1].append(chosen_item)
                used_item_indices.add(chosen_idx)
                
            # Other sets closers
            closers_found = True
            for s_idx in range(num_sets - 1):
                closer_candidates = [(idx, it) for idx, it in enumerate(prioritized_pool) if it.closer in ["Yes", "Maybe"] and idx not in used_item_indices]
                if not closer_candidates:
                    closers_found = False
                    break
                max_prio = max(c[1].priority for c in closer_candidates)
                best = [c for c in closer_candidates if c[1].priority == max_prio]
                yes_c = [c for c in best if c[1].closer == "Yes"]
                chosen_idx, chosen_item = random.choice(yes_c if yes_c else best)
                sets_items[s_idx].append(chosen_item)
                used_item_indices.add(chosen_idx)
                
            if not closers_found:
                continue
                
            # Fill the middle
            fill_success = True
            for s_idx in range(num_sets):
                current_opener = sets_items[s_idx][0]
                current_closer = sets_items[s_idx][1]
                
                middle_items = []
                current_dur = current_opener.duration_seconds + current_closer.duration_seconds + 30
                
                remaining_candidates = [
                    (idx, it) for idx, it in enumerate(prioritized_pool)
                    if idx not in used_item_indices
                ]
                remaining_candidates.sort(key=lambda x: x[1].priority, reverse=True)
                
                high_rem = [c for c in remaining_candidates if c[1].priority == 2]
                med_rem = [c for c in remaining_candidates if c[1].priority == 1]
                low_rem = [c for c in remaining_candidates if c[1].priority == 0]
                
                random.shuffle(high_rem)
                random.shuffle(med_rem)
                random.shuffle(low_rem)
                
                sorted_rem = high_rem + med_rem + low_rem
                
                for idx, it in sorted_rem:
                    potential_dur = current_dur + it.duration_seconds + 30
                    if potential_dur <= target_set_seconds + duration_tolerance:
                        middle_items.append(it)
                        used_item_indices.add(idx)
                        current_dur = potential_dur
                        if abs(current_dur - target_set_seconds) <= duration_tolerance:
                            break
                            
                if abs(current_dur - target_set_seconds) > duration_tolerance and not insufficient_music:
                    fill_success = False
                    break
                
                paced_middle = make_v_shape(middle_items)
                sets_items[s_idx] = [current_opener] + paced_middle + [current_closer]
                
            if not fill_success:
                continue
                
            candidate_sets_songs = []
            valid_setlist = True
            all_songs_in_sets = []
            
            for s_idx in range(num_sets):
                set_songs = []
                for it in sets_items[s_idx]:
                    set_songs.extend(it.songs)
                
                consec_count = 0
                prev_vocalist = None
                for song in set_songs:
                    vocalist = song["lead_vocals"]
                    if vocalist == prev_vocalist:
                        consec_count += 1
                        if consec_count > max_consecutive_vocals:
                            valid_setlist = False
                            break
                    else:
                        consec_count = 1
                        prev_vocalist = vocalist
                        
                if not valid_setlist:
                    break
                    
                gravelly_indices = [idx for idx, song in enumerate(set_songs) if song["title"] in gravelly_songs]
                for g_idx in range(len(gravelly_indices) - 1):
                    sep = gravelly_indices[g_idx+1] - gravelly_indices[g_idx] - 1
                    if sep < min_gravelly_separation:
                        valid_setlist = False
                        break
                        
                if not valid_setlist:
                    break
                    
                candidate_sets_songs.append(set_songs)
                all_songs_in_sets.extend(set_songs)
                
            if not valid_setlist:
                continue
                
            # Simulated Scheduled Songs (including encores) for vocalist constraints validation
            sim_scheduled = simulate_all_scheduled(
                candidate_sets_songs, available_songs, num_sets,
                args.breaks, num_breaks, acoustic_pool,
                args.martin_out, args.david_out
            )
            sim_counts = {}
            for s in sim_scheduled:
                sim_counts[s["lead_vocals"]] = sim_counts.get(s["lead_vocals"], 0) + 1
                
            # Validate max limits
            if args.max_david is not None and sim_counts.get("David", 0) > args.max_david:
                valid_setlist = False
            if args.max_martin is not None and sim_counts.get("Martin", 0) > args.max_martin:
                valid_setlist = False
            if args.max_lauren is not None and sim_counts.get("Lauren", 0) > args.max_lauren:
                valid_setlist = False
            if args.max_jon is not None and sim_counts.get("Jon", 0) > args.max_jon:
                valid_setlist = False
                
            # Validate min limits
            if args.min_david is not None and sim_counts.get("David", 0) < args.min_david:
                valid_setlist = False
            if args.min_martin is not None and sim_counts.get("Martin", 0) < args.min_martin:
                valid_setlist = False
            if args.min_lauren is not None and sim_counts.get("Lauren", 0) < args.min_lauren:
                valid_setlist = False
            if args.min_jon is not None and sim_counts.get("Jon", 0) < args.min_jon:
                valid_setlist = False
                
            # Validate Vocalist Inclusion (every present vocalist who has eligible songs must lead at least 1)
            for v in inclusion_required_vocalists:
                if sim_counts.get(v, 0) < 1:
                    valid_setlist = False
                    break

            if not valid_setlist:
                continue
                
            score = get_vocalist_score(all_songs_in_sets, target_vocal_pcts)
            if score < best_score:
                best_score = score
                best_candidate = (candidate_sets_songs, used_item_indices)
                
        if best_candidate is not None:
            break
            
    if best_candidate is None:
        print("Error: Could not generate a valid setlist satisfying the constraints.", file=sys.stderr)
        sys.exit(1)
        
    sets_songs, used_indices = best_candidate
    
    if any(x is not None for x in [args.min_david, args.min_martin, args.min_lauren, args.min_jon, args.max_david, args.max_martin, args.max_lauren, args.max_jon]):
        constraints_satisfied_summary["Vocalist Limits"] = "✅ Satisfied"
    
    # Select Acoustic Breaks
    break_songs_sets = []
    overworked_performers = set()
    break_overlap_warning = False
    
    if args.breaks == "acoustic" and num_breaks > 0:
        used_song_titles = {s["title"] for set_songs in sets_songs for s in set_songs}
        available_acoustic = [s for s in acoustic_pool if s["title"] not in used_song_titles]

        # Fallback: pull all acoustic/either songs that aren't hard-cut for this lineup.
        # A song is only hard-cut if its substitution_notes explicitly say
        # "If Martin is out: Cut song" or "If David is out: Cut song".  Songs
        # that say "Martin-out OK" or give alternative arrangements are fine.
        if len(available_acoustic) < num_breaks * 2:
            def _cut_for_lineup(s):
                notes = s.get("substitution_notes", "")
                if args.martin_out and "If Martin is out:" in notes and "Cut song" in notes:
                    return True
                if args.david_out and "If David is out:" in notes and "Cut song" in notes:
                    return True
                return False
            available_acoustic = [
                s for s in all_songs
                if s["arrangement"] in ["Acoustic", "Either"]
                and s["title"] not in used_song_titles
                and (s["gig_ready"] == "Yes" or args.include_not_ready)
                and not _cut_for_lineup(s)
            ]
            
        always_on_break = {"Lauren", "David"} if args.martin_out else set()
        break_pairs = select_acoustic_breaks(available_acoustic, num_breaks, args.martin_out, args.david_out, max_intersection=0, always_on=always_on_break)
        if not break_pairs:
            break_pairs = select_acoustic_breaks(available_acoustic, num_breaks, args.martin_out, args.david_out, max_intersection=1, always_on=always_on_break)
            if break_pairs:
                break_overlap_warning = True
                constraints_satisfied_summary["Bathroom Breaks"] = "⚠️ Partially Satisfied (Acoustic overlap)"
                for pair in break_pairs:
                    act_a = get_active_performers(pair[0], args.martin_out, args.david_out)
                    act_b = get_active_performers(pair[1], args.martin_out, args.david_out)
                    overworked_performers.update(act_a.intersection(act_b) - always_on_break)
            else:
                constraints_satisfied_summary["Bathroom Breaks"] = "❌ Not Satisfied (Acoustic failed - silent breaks used)"
                args.breaks = "silent"
        else:
            constraints_satisfied_summary["Bathroom Breaks"] = "✅ Satisfied (All members get breaks)"
            
        if break_pairs:
            break_songs_sets = break_pairs
            
    # Select Encores
    encores = []
    used_song_titles = {s["title"] for set_songs in sets_songs for s in set_songs}
    if break_songs_sets:
        for pair in break_songs_sets:
            used_song_titles.add(pair[0]["title"])
            used_song_titles.add(pair[1]["title"])
            
    remaining_songs = [s for s in available_songs if s["title"] not in used_song_titles]
    encore_options = ["All Right Now", "Crazy Little Thing Called Love"]
    for opt in encore_options:
        match = next((s for s in remaining_songs if s["title"].lower() == opt.lower()), None)
        if match:
            encores.append(match)
            remaining_songs.remove(match)
            
    while len(encores) < 2 and remaining_songs:
        remaining_songs.sort(key=lambda s: s["bpm"], reverse=True)
        encores.append(remaining_songs.pop(0))
        
    sets_songs = tag_emergency_cuts(sets_songs, segue_raw_groups)

    # Re-order encores so that segue-linked songs appear in their canonical
    # order (e.g. Brown Eyed Girl must come before Hey Jealousy).
    def _restore_segue_order(song_list, groups):
        """Sort song_list so any segue group's songs appear in group order."""
        # Build a position map: title -> (group_idx, position_in_group)
        seg_pos = {}
        for g_idx, grp in enumerate(groups):
            for pos, title in enumerate(grp):
                seg_pos[title.lower()] = (g_idx, pos)

        result = list(song_list)
        # Stable insertion-sort on segue groups: for each group that appears
        # in the list, collect those songs, sort by position, and put them
        # back in-place (preserving non-segue song positions).
        groups_present = {}
        for i, s in enumerate(result):
            key = s["title"].lower()
            if key in seg_pos:
                g_idx, pos = seg_pos[key]
                groups_present.setdefault(g_idx, []).append((i, pos, s))

        for g_idx, entries in groups_present.items():
            if len(entries) < 2:
                continue
            indices = [e[0] for e in entries]
            sorted_songs = [e[2] for e in sorted(entries, key=lambda x: x[1])]
            for idx, song in zip(sorted(indices), sorted_songs):
                result[idx] = song
        return result

    encores = _restore_segue_order(encores, segue_raw_groups)
    
    if insufficient_music:
        constraints_satisfied_summary["Target Duration"] = f"⚠️ Partially Satisfied (Under target - played {format_length(total_available_seconds)})"
        
    # ---------------------------------------------------------------
    # Build output filename stem
    # ---------------------------------------------------------------
    script_dir = os.path.dirname(os.path.abspath(__file__))
    setlists_dir = os.path.join(script_dir, "..", "setlists")
    os.makedirs(setlists_dir, exist_ok=True)

    if args.date and args.location:
        file_stem = f"{args.date} {args.location}"
    elif args.date:
        file_stem = args.date
    elif args.location:
        file_stem = args.location
    else:
        file_stem = f"setlist_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    md_path  = os.path.join(setlists_dir, file_stem + ".md")
    txt_path = os.path.join(setlists_dir, file_stem + ".txt")

    # ---------------------------------------------------------------
    # Output report
    # ---------------------------------------------------------------
    # Capture markdown output into a buffer so we can write to file + stdout
    md_buf = io.StringIO()

    def md(*a, **kw):
        """Print to both stdout and the markdown buffer."""
        print(*a, **kw)
        print(*a, **kw, file=md_buf)

    # ---------------------------------------------------------------
    # Title (matches file naming convention) + compact header block
    # ---------------------------------------------------------------
    if args.date and args.location:
        title_str = f"{args.date} - {args.location}"
    elif args.date:
        title_str = args.date
    elif args.location:
        title_str = args.location
    else:
        title_str = "Setlist"
    md(f"# {title_str}\n")

    if num_sets == 1:
        duration_field = f"{int(args.duration * 60)} min"
    else:
        duration_field = f"{num_sets} sets (~{int(target_set_seconds / 60)} min each), {args.duration:g} hrs total"

    missing_field = "Martin" if args.martin_out else ("David" if args.david_out else "None")

    filter_details = []
    if args.genre: filter_details.append(f"Genre: {args.genre}")
    if args.era: filter_details.append(f"Era: {args.era}")
    if args.mood: filter_details.append(f"Mood: {args.mood}")
    if args.skip_country_grunge: filter_details.append("No Grunge, No Country")

    if num_breaks > 0:
        breaks_field = f"{args.breaks.capitalize()} ({num_breaks} × 10 min)"
    else:
        breaks_field = "None"

    md(f"- **Gig Type:** {args.gig_type.capitalize()}")
    md(f"- **Duration:** {duration_field}")
    md(f"- **Missing:** {missing_field}")
    if filter_details:
        md(f"- **Filters:** {', '.join(filter_details)}")
    md(f"- **Breaks:** {breaks_field}")
    md()

    # Display Satisfaction Table
    md("### 📋 CONSTRAINTS SATISFACTION SUMMARY")
    md("| Constraint | Status | Notes |")
    md("| :--- | :--- | :--- |")
    for constraint, status in constraints_satisfied_summary.items():
        md(f"| {constraint} | {status} |")
    md()

    if insufficient_music:
        md("> [!WARNING]")
        md(f"> **INSUFFICIENT MUSIC FOR TARGET DURATION**: The total available playtime of matching songs is only **{format_length(total_available_seconds)}**, which is less than the target set playtime of **{format_length(total_set_music_seconds + total_break_seconds + encore_duration_seconds)}** (including breaks/encores). The setlist has been filled with all matching songs but is under target.\n")

    if break_overlap_warning:
        overworked_str = ", ".join(sorted(list(overworked_performers)))
        md("> [!WARNING]")
        md("> **PERFORMER BREAK OVERLAP**: It is mathematically impossible to give everyone a bathroom break using the available acoustic songs.")
        md(f"> * **Option A (Silent Break)**: Play no acoustic music during the breaks to allow everyone to rest.")
        md(f"> * **Option B (Acoustic Break)**: Play the set below, but note that **{overworked_str}** will not get a bathroom break.\n")

    if args.martin_out:
        md("> [!WARNING]")
        cut_str = ", ".join(f"*{t}*" for t in martin_cut_songs) if martin_cut_songs else "none"
        md(f"> **Substitutions**: Rhythm guitar parts are cut, David covers Martin's vocal parts, and {cut_str} are cut from the sets (require Martin per database).\n")
    if args.david_out:
        md("> [!WARNING]")
        david_cut_str = f" and {', '.join(f'*{t}*' for t in david_cut_songs)} are cut from the sets (require David per database)" if david_cut_songs else ""
        md(f"> **Substitutions**: Keyboard/marimba parts are covered by Jon (piano) or omitted, and Lauren covers David's lead vocal parts on *Keep Your Hands to Yourself*, *Ventura Highway*, and *Ooh La La*{david_cut_str}.\n")
        
    total_music_seconds = 0
    total_trans_seconds = 0
    
    for s_idx in range(num_sets):
        md(f"## SET {s_idx + 1}")
        md("| # | Title | Artist | Key | BPM | Length | Lead Vocal | Popularity | Note |")
        md("|---|---|---|---|---|---|---|---|---|")
        
        set_songs = sets_songs[s_idx]
        for idx, song in enumerate(set_songs):
            marker = ""
            if song.get("emergency_cut", False):
                marker = " 🛑 **[EMERGENCY CUT]**"
            elif song["opener"] == "Yes" and idx == 0:
                marker = " 🟢 *[Opener]*"
            elif song["closer"] == "Yes" and idx == len(set_songs) - 1:
                marker = " 🔴 *[Closer]*"
                
            lead_v = song["lead_vocals"]
            song["backup_vocals"] = clean_backups(lead_v, song.get("backup_vocals", []))
            backups = ", ".join(song["backup_vocals"])
            v_string = lead_v + (f" ({backups})" if backups else "")

            pop_score = song.get("relative_popularity", "") or "-"
                
            md(f"| {idx+1} | **{song['title']}**{marker} | {song['artist']} | {song['key']} | {song['bpm']} | {song['length']} | {v_string} | {pop_score} | {song['intro_notes']} |")
            
        set_dur = sum(parse_length(s["length"]) for s in set_songs)
        set_trans = (len(set_songs) - 1) * 30
        total_music_seconds += set_dur
        total_trans_seconds += set_trans
        
        md(f"\n**Set {s_idx + 1} Music Duration**: {format_length(set_dur)} | **Transitions**: {format_length(set_trans)} | **Total**: {format_length(set_dur + set_trans)}")
        md("-" * 40)
        
        if s_idx < num_sets - 1:
            if args.breaks == "acoustic" and s_idx < len(break_songs_sets):
                pair = break_songs_sets[s_idx]
                md(f"\n### ☕ BREAK {s_idx + 1} (Acoustic Set - 10 mins)")
                md("Everyone gets a bathroom break! No member performs in both songs.")
                for s_num, song in enumerate(pair):
                    active = get_active_performers(song, args.martin_out, args.david_out)
                    inactive = set(BAND_ROSTER) - active
                    if args.martin_out:
                        inactive.discard("Martin")
                        active.discard("Martin")
                    if args.david_out:
                        inactive.discard("David")
                        active.discard("David")
                    leave_str = ", ".join(sorted(list(inactive)))
                    md(f"- **{song['title']}** ({song['artist']}) - Lead: {song['lead_vocals']} | **Can Leave Stage (Bathroom Break)**: `{leave_str}`")
                md("\n" + "-" * 40)
            else:
                md(f"\n### ⏸️ BREAK {s_idx + 1} (Silent Break - 10 mins)\n")
                md("-" * 40)
                
    if encores:
        md("## ENCORES")
        md("| # | Title | Artist | Key | BPM | Length | Lead Vocal | Popularity | Note |")
        md("|---|---|---|---|---|---|---|---|---|")
        for idx, song in enumerate(encores):
            lead_v = song["lead_vocals"]
            song["backup_vocals"] = clean_backups(lead_v, song.get("backup_vocals", []))
            backups = ", ".join(song["backup_vocals"])
            v_string = lead_v + (f" ({backups})" if backups else "")
            pop_score = song.get("relative_popularity", "") or "-"
            md(f"| {idx+1} | **{song['title']}** | {song['artist']} | {song['key']} | {song['bpm']} | {song['length']} | {v_string} | {pop_score} | {song['intro_notes']} |")
            
        encore_dur = sum(parse_length(s["length"]) for s in encores)
        encore_trans = (len(encores) - 1) * 30
        total_music_seconds += encore_dur
        total_trans_seconds += encore_trans
        md(f"\n**Encore Music Duration**: {format_length(encore_dur)} | **Transitions**: {format_length(encore_trans)} | **Total**: {format_length(encore_dur + encore_trans)}")
        md("-" * 40)
        
    total_breaks_sec = num_breaks * 10 * 60
    grand_total_sec = total_music_seconds + total_trans_seconds + total_breaks_sec
    md(f"\n### 📊 GIG SUMMARY STATS")
    md(f"- **Total Songs Scheduled**: {sum(len(s) for s in sets_songs) + len(encores) + len(break_songs_sets)*2}")
    md(f"- **Pure Music Playtime**: {format_length(total_music_seconds)}")
    md(f"- **Transition Buffers (30s/song)**: {format_length(total_trans_seconds)}")
    md(f"- **Break Time**: {format_length(total_breaks_sec)}")
    md(f"- **Grand Total Duration**: {format_length(grand_total_sec)} (Target: {format_length(total_gig_seconds)})")
    
    all_scheduled = [s for set_s in sets_songs for s in set_s] + encores
    vocal_counts = {}
    for s in all_scheduled:
        vocal_counts[s["lead_vocals"]] = vocal_counts.get(s["lead_vocals"], 0) + 1
    total_v = len(all_scheduled)
    # (Vocalist breakdown used internally by solver; not published in report)

    # ---------------------------------------------------------------
    # Build plaintext arrow-notation (Format 2)
    # ---------------------------------------------------------------
    def backup_initials(song):
        """Return +XYZ initials string for backup vocals (after martin/david substitution)."""
        backups = clean_backups(song["lead_vocals"], song.get("backup_vocals", []))
        mapping = {"L": "L", "J": "J", "D": "D", "M": "M"}
        initials = "".join(mapping[b] for b in backups if b in mapping)
        if len(initials) == 3:
            return "+3"
        return ("+" + initials) if initials else ""

    def song_line(song, is_first=False):
        """Format a single song line in plaintext arrow notation."""
        lead = song["lead_vocals"]
        bi = backup_initials(song)
        vocal_str = f"({lead} {bi})".strip() if bi else f"({lead})"
        key = song["key"]
        tags = []
        if song["opener"] == "Yes" and is_first:
            tags.append("[OPENER]")
        if song["closer"] == "Yes":
            tags.append("[CLOSER]")
        if song.get("emergency_cut", False):
            tags.append("[EMERGENCY CUT]")
        tag_str = (" " + " ".join(tags)) if tags else ""
        intro = song.get("intro_notes", "").strip()
        if intro and intro != "TBD":
            if intro.upper().startswith("SEGUE"):
                return (True, intro[5:].strip(), f"{song['title']} {vocal_str} [{key}]{tag_str}")
            else:
                return (False, intro, f"{song['title']} {vocal_str} [{key}]{tag_str}")
        return (False, None, f"{song['title']} {vocal_str} [{key}]{tag_str}")

    missing_str = "No Martin" if args.martin_out else ("No David" if args.david_out else "None Missing")
    num_sets_label = f"{num_sets}x {int(args.duration / num_sets * 60)} Min set" if num_sets == 1 else f"{num_sets}x Sets"
    txt_lines = [f"{num_sets_label} ({missing_str})", ""]

    for s_idx, set_songs in enumerate(sets_songs):
        for i, song in enumerate(set_songs):
            is_segue, extra, body = song_line(song, is_first=(i == 0))
            if i == 0:
                if extra:
                    txt_lines.append(f"{extra} {body}")
                else:
                    txt_lines.append(body)
            else:
                if is_segue:
                    txt_lines.append(f"-> SEGUE {extra} {body}")
                elif extra:
                    txt_lines.append(f"-> {extra} {body}")
                else:
                    txt_lines.append(f"-> {body}")
            txt_lines.append("")

        if s_idx < num_sets - 1:
            if args.breaks == "acoustic" and s_idx < len(break_songs_sets):
                pair = break_songs_sets[s_idx]
                txt_lines.append("(break)")
                txt_lines.append("")
                for b_idx, bsong in enumerate(pair):
                    b_is_segue, b_extra, b_body = song_line(bsong, is_first=(b_idx == 0))
                    if b_idx == 0:
                        line = f"{b_extra} {b_body}".strip() if b_extra else b_body
                    elif b_is_segue:
                        line = f"-> SEGUE {b_extra} {b_body}"
                    elif b_extra:
                        line = f"-> {b_extra} {b_body}"
                    else:
                        line = f"-> {b_body}"
                    txt_lines.append(line)
                    txt_lines.append("")
            else:
                txt_lines.append("(break)")
                txt_lines.append("")

    if encores:
        txt_lines.append("(encore)")
        txt_lines.append("")
        for i, song in enumerate(encores):
            is_segue, extra, body = song_line(song)
            if is_segue:
                txt_lines.append(f"-> SEGUE {extra} {body}")
            elif extra:
                txt_lines.append(f"-> {extra} {body}")
            else:
                txt_lines.append(f"-> {body}")
            txt_lines.append("")

    txt_content = "\n".join(txt_lines)

    # Print plaintext to stdout
    print("\n" + "=" * 60)
    print("PLAINTEXT ARROW NOTATION")
    print("=" * 60)
    print(txt_content)

    # ---------------------------------------------------------------
    # Write files
    # ---------------------------------------------------------------
    md_content = md_buf.getvalue()

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(txt_content)

    print(f"\n✅ Saved markdown  → {os.path.abspath(md_path)}", file=sys.stderr)
    print(f"✅ Saved plaintext → {os.path.abspath(txt_path)}", file=sys.stderr)

    pdf_path = None
    try:
        import render_pdf
        pdf_path = render_pdf.render(md_path)
        print(f"✅ Saved PDF       → {pdf_path}", file=sys.stderr)
    except Exception as e:
        print(f"⚠️  PDF generation skipped ({e})", file=sys.stderr)

    # Only sync real gigs (named via --date/--location) to the shared Drive folder —
    # ad-hoc/test runs fall back to a setlist_<timestamp> stem and shouldn't clutter it.
    if pdf_path and (args.date or args.location):
        shared_drive_dir = os.path.expanduser("~/Google Drive/Shared Drives/Wannabe Weekenders/Setlists")
        try:
            shutil.copy2(pdf_path, shared_drive_dir)
            print(f"✅ Synced to Drive → {os.path.join(shared_drive_dir, os.path.basename(pdf_path))}", file=sys.stderr)
        except Exception as e:
            print(f"⚠️  Drive sync skipped ({e})", file=sys.stderr)

def tag_emergency_cuts(sets_songs, segue_groups):
    segue_titles = {title.lower() for group in segue_groups for title in group}
    updated_sets = []
    
    for s_idx, set_songs in enumerate(sets_songs):
        eligible_idx = None
        start_search = len(set_songs) - 3
        end_search = len(set_songs) // 2
        
        # Pass 1a: Strict constraints & preferred emergency cut
        for idx in range(start_search, end_search - 1, -1):
            if 0 < idx < len(set_songs) - 1:
                song = set_songs[idx]
                if song["title"].lower() not in segue_titles and song["opener"] == "No" and song["closer"] == "No":
                    if song.get("preferred_emergency_cut") == "Yes":
                        eligible_idx = idx
                        break
                        
        # Pass 1b: Strict constraints & any
        if eligible_idx is None:
            for idx in range(start_search, end_search - 1, -1):
                if 0 < idx < len(set_songs) - 1:
                    song = set_songs[idx]
                    if song["title"].lower() not in segue_titles and song["opener"] == "No" and song["closer"] == "No":
                        eligible_idx = idx
                        break
                        
        # Pass 2a: Relax opener/closer constraints & preferred emergency cut
        if eligible_idx is None:
            for idx in range(start_search, end_search - 1, -1):
                if 0 < idx < len(set_songs) - 1:
                    song = set_songs[idx]
                    if song["title"].lower() not in segue_titles:
                        if song.get("preferred_emergency_cut") == "Yes":
                            eligible_idx = idx
                            break
                            
        # Pass 2b: Relax opener/closer constraints & any
        if eligible_idx is None:
            for idx in range(start_search, end_search - 1, -1):
                if 0 < idx < len(set_songs) - 1:
                    song = set_songs[idx]
                    if song["title"].lower() not in segue_titles:
                        eligible_idx = idx
                        break
                        
        # Pass 3a: Relax segue constraint & preferred emergency cut
        if eligible_idx is None:
            for idx in range(start_search, end_search - 1, -1):
                if 0 < idx < len(set_songs) - 1:
                    song = set_songs[idx]
                    if song.get("preferred_emergency_cut") == "Yes":
                        eligible_idx = idx
                        break
                        
        # Pass 3b: Relax segue constraint & any
        if eligible_idx is None:
            for idx in range(start_search, end_search - 1, -1):
                if 0 < idx < len(set_songs) - 1:
                    eligible_idx = idx
                    break
                    
        updated_set = []
        for idx, song in enumerate(set_songs):
            song_copy = dict(song)
            song_copy["emergency_cut"] = (idx == eligible_idx)
            updated_set.append(song_copy)
        updated_sets.append(updated_set)
        
    return updated_sets

def get_vocalist_score(songs_list, targets):
    counts = {}
    total = len(songs_list)
    if total == 0:
        return float('inf')
    for s in songs_list:
        counts[s["lead_vocals"]] = counts.get(s["lead_vocals"], 0) + 1
    score = 0.0
    for v, target in targets.items():
        actual = counts.get(v, 0) / total
        score += (actual - target) ** 2
    return score

if __name__ == "__main__":
    main()
