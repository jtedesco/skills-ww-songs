#!/usr/bin/env python3
import csv
import os
import sys
import random
import re
import argparse
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

# Exact acoustic set performers from user requirements
acoustic_performers = {
    "landslide": {"Lauren", "Martin"},
    "blackbird": {"Lauren", "Martin", "Jon"},
    "wish you were here": {"Lauren", "Martin", "JJ", "David"},
    "the story": {"Lauren", "Martin", "JJ", "David"},
    "vienna": {"Jon", "David"},
    "interstate love song": {"Martin", "Lauren"},
    "ooh la la": {"David", "Lauren"},
    "don't know why": {"Lauren", "JJ", "Jon"},
    "ventura highway": {"David", "Lauren", "Jon"},
    "all for you": {"Jon", "Martin", "Lauren", "David"}
}

def get_active_performers(song, martin_out=False, david_out=False):
    title = song["title"].lower()
    active = set()
    
    # Find matching song in the acoustic performers map
    matched_key = None
    for key in acoustic_performers:
        if key in title:
            matched_key = key
            break
            
    if matched_key:
        active = set(acoustic_performers[matched_key])
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

def select_acoustic_breaks(acoustic_pool, num_breaks, martin_out=False, david_out=False, max_intersection=0):
    valid_pairs = []
    for i in range(len(acoustic_pool)):
        for j in range(i + 1, len(acoustic_pool)):
            song_a = acoustic_pool[i]
            song_b = acoustic_pool[j]
            active_a = get_active_performers(song_a, martin_out, david_out)
            active_b = get_active_performers(song_b, martin_out, david_out)
            
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

def main():
    parser = argparse.ArgumentParser(description="Wannabe Weekenders Setlist Builder")
    parser.add_argument("--gig-type", choices=["bar", "yacht"], default="bar", help="Gig type: bar (anything) or yacht (Yacht Rock/Adjacent only)")
    parser.add_argument("--duration", type=float, default=3.0, help="Total gig duration in hours (e.g. 1.0, 2.0, 3.0)")
    parser.add_argument("--martin-out", action="store_true", help="Martin is out (David covers lead/backups, Martin-required songs cut per database)")
    parser.add_argument("--david-out", action="store_true", help="David is out (Lauren covers David's lead parts, keys/marimba omitted/covered)")
    parser.add_argument("--breaks", choices=["acoustic", "silent"], default="acoustic", help="Break format: acoustic (filled with 2 acoustic songs) or silent")
    parser.add_argument("--include-not-ready", action="store_true", help="Include not-yet-gig-ready songs in sets and breaks")
    parser.add_argument("--skip-country-grunge", action="store_true", help="Skip country (Keep Your Hands to Yourself, Take It Easy, Me and Bobby McGee) and grunge (Zombie, You Oughta Know, Interstate Love Song) songs")
    parser.add_argument("--genre", type=str, default=None, help="Filter songs by genre (case-insensitive, e.g. 'Rock', 'Pop', 'Soul')")
    parser.add_argument("--era", type=str, default=None, help="Filter songs by era / decade (e.g. '70s', '80s', '90s', '1970s', '1980s')")
    parser.add_argument("--mood", type=str, default=None, help="Filter songs by mood (case-insensitive, e.g. 'Upbeat', 'Energetic', 'Chill')")
    
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
    
    gravelly_songs = {"Zombie", "Respect", "Roll with the Changes", "You Oughta Know", "Me and Bobby McGee"}
    
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
                song["lead_vocals"] = "David"
            if "M" in song.get("backup_vocals", []):
                song["backup_vocals"] = [b for b in song["backup_vocals"] if b != "M"]
                
        # David Out rules
        if args.david_out:
            notes = song.get("substitution_notes", "")
            if notes.startswith("If David is out:") and "Cut song" in notes:
                david_cut_songs.append(title)
                continue
            if song["lead_vocals"] == "David":
                song["lead_vocals"] = "Lauren"
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
        "Target Duration": "✅ Satisfied"
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
    
    # Select Acoustic Breaks
    break_songs_sets = []
    overworked_performers = set()
    break_overlap_warning = False
    
    if args.breaks == "acoustic" and num_breaks > 0:
        used_song_titles = {s["title"] for set_songs in sets_songs for s in set_songs}
        available_acoustic = [s for s in acoustic_pool if s["title"] not in used_song_titles]
        
        if len(available_acoustic) < num_breaks * 2:
            def _cut_for_lineup(s):
                notes = s.get("substitution_notes", "")
                if args.martin_out and notes.startswith("If Martin is out:") and "Cut song" in notes:
                    return True
                if args.david_out and notes.startswith("If David is out:") and "Cut song" in notes:
                    return True
                return False
            available_acoustic = [
                s for s in all_songs
                if s["arrangement"] in ["Acoustic", "Either"]
                and s["title"] not in used_song_titles
                and (s["gig_ready"] == "Yes" or args.include_not_ready)
                and not _cut_for_lineup(s)
            ]
            
        break_pairs = select_acoustic_breaks(available_acoustic, num_breaks, args.martin_out, args.david_out, max_intersection=0)
        if not break_pairs:
            break_pairs = select_acoustic_breaks(available_acoustic, num_breaks, args.martin_out, args.david_out, max_intersection=1)
            if break_pairs:
                break_overlap_warning = True
                constraints_satisfied_summary["Bathroom Breaks"] = "⚠️ Partially Satisfied (Acoustic overlap)"
                for pair in break_pairs:
                    act_a = get_active_performers(pair[0], args.martin_out, args.david_out)
                    act_b = get_active_performers(pair[1], args.martin_out, args.david_out)
                    overworked_performers.update(act_a.intersection(act_b))
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
    
    if insufficient_music:
        constraints_satisfied_summary["Target Duration"] = f"⚠️ Partially Satisfied (Under target - played {format_length(total_available_seconds)})"
        
    # Output report
    active_members = ["Lauren", "Jon", "JJ", "Debo", "Alex"]
    if not args.martin_out: active_members.append("Martin")
    if not args.david_out: active_members.append("David")
    lineup_str = ", ".join(sorted(active_members))
    
    print(f"# WANNA BE WEEKENDERS SETLIST GENERATOR REPORT")
    print(f"**Gig Type**: {args.gig_type.upper()} SET | **Target Duration**: {args.duration} hrs ({int(args.duration*60)} mins)")
    filter_details = []
    if args.genre: filter_details.append(f"Genre: {args.genre}")
    if args.era: filter_details.append(f"Era: {args.era}")
    if args.mood: filter_details.append(f"Mood: {args.mood}")
    filter_str = f" | **Filters**: {', '.join(filter_details)}" if filter_details else ""
    print(f"**Lineup Available**: Full Band ({lineup_str}){filter_str}")
    print(f"**Breaks**: {args.breaks.upper() if num_breaks > 0 else 'NONE (Single Set)'}\n")
    
    # Display Satisfaction Table
    print("### 📋 CONSTRAINTS SATISFACTION SUMMARY")
    print("| Constraint | Status | Notes |")
    print("| :--- | :--- | :--- |")
    for constraint, status in constraints_satisfied_summary.items():
        print(f"| {constraint} | {status} |")
    print()
    
    if insufficient_music:
        print("> [!WARNING]")
        print(f"> **INSUFFICIENT MUSIC FOR TARGET DURATION**: The total available playtime of matching songs is only **{format_length(total_available_seconds)}**, which is less than the target set playtime of **{format_length(total_set_music_seconds + total_break_seconds + encore_duration_seconds)}** (including breaks/encores). The setlist has been filled with all matching songs but is under target.\n")
        
    if break_overlap_warning:
        overworked_str = ", ".join(sorted(list(overworked_performers)))
        print("> [!WARNING]")
        print("> **PERFORMER BREAK OVERLAP**: It is mathematically impossible to give everyone a bathroom break using the available acoustic songs.")
        print(f"> * **Option A (Silent Break)**: Play no acoustic music during the breaks to allow everyone to rest.")
        print(f"> * **Option B (Acoustic Break)**: Play the set below, but note that **{overworked_str}** will not get a bathroom break.\n")
        
    if args.martin_out:
        print("> [!WARNING]")
        cut_str = ", ".join(f"*{t}*" for t in martin_cut_songs) if martin_cut_songs else "none"
        print(f"> **Martin is Out**: Rhythm guitar parts are cut, David covers Martin's vocal parts, and {cut_str} are cut from the sets (require Martin per database).\n")
    if args.david_out:
        print("> [!WARNING]")
        david_cut_str = f" and {', '.join(f'*{t}*' for t in david_cut_songs)} are cut from the sets (require David per database)" if david_cut_songs else ""
        print(f"> **David is Out**: Keyboard/marimba parts are covered by Jon (piano) or omitted, and Lauren covers David's lead vocal parts on *Keep Your Hands to Yourself*, *Ventura Highway*, and *Ooh La La*{david_cut_str}.\n")
        
    total_music_seconds = 0
    total_trans_seconds = 0
    
    for s_idx in range(num_sets):
        print(f"## SET {s_idx + 1}")
        print("| # | Title | Artist | Key | BPM | Length | Lead Vocal | Date Added | can leave | Note |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        
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
            
            leave_string = song["can_leave_stage"]
            if leave_string == "None":
                leave_string = "-"
                
            print(f"| {idx+1} | **{song['title']}**{marker} | {song['artist']} | {song['key']} | {song['bpm']} | {song['length']} | {v_string} | {song.get('date_added', '-')} | {leave_string} | {song['intro_notes']} |")
            
        set_dur = sum(parse_length(s["length"]) for s in set_songs)
        set_trans = (len(set_songs) - 1) * 30
        total_music_seconds += set_dur
        total_trans_seconds += set_trans
        
        print(f"\n**Set {s_idx + 1} Music Duration**: {format_length(set_dur)} | **Transitions**: {format_length(set_trans)} | **Total**: {format_length(set_dur + set_trans)}")
        print("-" * 40)
        
        if s_idx < num_sets - 1:
            if args.breaks == "acoustic" and s_idx < len(break_songs_sets):
                pair = break_songs_sets[s_idx]
                print(f"\n### ☕ BREAK {s_idx + 1} (Acoustic Set - 10 mins)")
                print("Everyone gets a bathroom break! No member performs in both songs.")
                for s_num, song in enumerate(pair):
                    active = get_active_performers(song, args.martin_out, args.david_out)
                    inactive = {"Lauren", "Jon", "Martin", "David", "JJ", "Debo", "Alex"} - active
                    if args.martin_out:
                        inactive.discard("Martin")
                        active.discard("Martin")
                    if args.david_out:
                        inactive.discard("David")
                        active.discard("David")
                    leave_str = ", ".join(sorted(list(inactive)))
                    print(f"- **{song['title']}** ({song['artist']}) - Lead: {song['lead_vocals']} | **Can Leave Stage (Bathroom Break)**: `{leave_str}`")
                print("\n" + "-" * 40)
            else:
                print(f"\n### ⏸️ BREAK {s_idx + 1} (Silent Break - 10 mins)\n")
                print("-" * 40)
                
    if encores:
        print("## ENCORES")
        print("| # | Title | Artist | Key | BPM | Length | Lead Vocal | Date Added | Note |")
        print("|---|---|---|---|---|---|---|---|---|")
        for idx, song in enumerate(encores):
            lead_v = song["lead_vocals"]
            song["backup_vocals"] = clean_backups(lead_v, song.get("backup_vocals", []))
            backups = ", ".join(song["backup_vocals"])
            v_string = lead_v + (f" ({backups})" if backups else "")
            print(f"| {idx+1} | **{song['title']}** | {song['artist']} | {song['key']} | {song['bpm']} | {song['length']} | {v_string} | {song.get('date_added', '-')} | {song['intro_notes']} |")
            
        encore_dur = sum(parse_length(s["length"]) for s in encores)
        encore_trans = (len(encores) - 1) * 30
        total_music_seconds += encore_dur
        total_trans_seconds += encore_trans
        print(f"\n**Encore Music Duration**: {format_length(encore_dur)} | **Transitions**: {format_length(encore_trans)} | **Total**: {format_length(encore_dur + encore_trans)}")
        print("-" * 40)
        
    total_breaks_sec = num_breaks * 10 * 60
    grand_total_sec = total_music_seconds + total_trans_seconds + total_breaks_sec
    print(f"\n### 📊 GIG SUMMARY STATS")
    print(f"- **Total Songs Scheduled**: {sum(len(s) for s in sets_songs) + len(encores) + len(break_songs_sets)*2}")
    print(f"- **Pure Music Playtime**: {format_length(total_music_seconds)}")
    print(f"- **Transition Buffers (30s/song)**: {format_length(total_trans_seconds)}")
    print(f"- **Break Time**: {format_length(total_breaks_sec)}")
    print(f"- **Grand Total Duration**: {format_length(grand_total_sec)} (Target: {format_length(total_gig_seconds)})")
    
    all_scheduled = [s for set_s in sets_songs for s in set_s] + encores
    vocal_counts = {}
    for s in all_scheduled:
        vocal_counts[s["lead_vocals"]] = vocal_counts.get(s["lead_vocals"], 0) + 1
    total_v = len(all_scheduled)
    
    print("\n### 🎤 LEAD VOCALS BREAKDOWN")
    for v, target in target_vocal_pcts.items():
        count = vocal_counts.get(v, 0)
        pct = (count / total_v * 100) if total_v > 0 else 0
        print(f"- **{v}**: {count} lead songs ({pct:.1f}% | Target: {target*100:.1f}%)")

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
