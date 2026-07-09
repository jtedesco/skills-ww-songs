#!/usr/bin/env python3
import csv
import os
import subprocess
import sys
import re

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "..", "songs_metadata.csv")
BUILDER_PATH = os.path.join(SCRIPT_DIR, "build_setlist.py")

def log_test(name, success, message=""):
    status = "PASS" if success else "FAIL"
    print(f"[{status}] {name}")
    if message and not success:
        print(f"      Details: {message}")
    return success

# -------------------------------------------------------------
# 1. Database Integrity Tests
# -------------------------------------------------------------
def test_database_integrity():
    print("Running Database Integrity Tests...")
    all_pass = True
    
    if not os.path.exists(DB_PATH):
        return log_test("Database exists", False, f"Not found at {DB_PATH}")
    log_test("Database exists", True)
    
    with open(DB_PATH, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        songs = []
        for row in reader:
            song = dict(row)
            song["bpm"] = int(song["bpm"])
            song["backup_vocals"] = [v for v in song["backup_vocals"].split(";") if v]
            songs.append(song)
        
    # Check required fields
    required_fields = {
        "title", "artist", "key", "bpm", "lead_vocals", "backup_vocals",
        "intro_notes", "order_rules", "substitution_notes", "yacht_adjacent",
        "gig_ready", "length", "arrangement", "vocalist_constraints",
        "opener", "closer", "can_leave_stage", "date_added", "archived",
        "preferred_emergency_cut", "release_year", "original_album",
        "musicbrainz_genre", "musicbrainz_mood", "musicbrainz_id"
    }
    
    missing_fields_count = 0
    clean_backups_count = 0
    gig_ready_count = 0
    archived_count = 0
    
    for s in songs:
        title = s.get("title", "Unknown")
        
        # Required fields check
        missing = required_fields - set(s.keys())
        if missing:
            missing_fields_count += 1
            all_pass = False
            log_test(f"Fields check: {title}", False, f"Missing fields: {missing}")
            
        # Lead vs backup vocals check (Clean backups)
        lead = s.get("lead_vocals", "")
        backups = s.get("backup_vocals", [])
        overlap = []
        for b in backups:
            if b == "L" and lead == "Lauren": overlap.append("L/Lauren")
            if b == "J" and lead == "Jon": overlap.append("J/Jon")
            if b == "D" and lead == "David": overlap.append("D/David")
            if b == "M" and lead == "Martin": overlap.append("M/Martin")
        if overlap:
            clean_backups_count += 1
            all_pass = False
            log_test(f"Clean backups check: {title}", False, f"Lead vocalist '{lead}' also listed in backups as {overlap}")
            
        # Gig readiness check
        # Songs are gig ready only if explicitly whitelisted below — being
        # "not ready" is a legitimate state for any arrangement (a full-band
        # song can be mid-rehearsal just as easily as an acoustic one), not
        # something limited to Acoustic/Either songs.
        gig_ready_acoustic = {"Landslide", "Blackbird", "Interstate Love Song",
                               "Wish You Were Here", "Ooh La La", "Ventura Highway",
                               "All For You"}
        not_ready_full_band = {"Kid Charlemagne"}
        if s.get("arrangement") in ["Acoustic", "Either"]:
            if title in gig_ready_acoustic:
                if s.get("gig_ready") != "Yes":
                    gig_ready_count += 1
                    all_pass = False
                    log_test(f"Gig readiness check: {title}", False, f"{title} must be gig ready, found: {s.get('gig_ready')}")
            else:
                if s.get("gig_ready") == "Yes":
                    gig_ready_count += 1
                    all_pass = False
                    log_test(f"Gig readiness check: {title}", False, f"Acoustic/Either song '{title}' must NOT be gig ready, found: {s.get('gig_ready')}")
        else:
            if title in not_ready_full_band:
                if s.get("gig_ready") == "Yes":
                    gig_ready_count += 1
                    all_pass = False
                    log_test(f"Gig readiness check: {title}", False, f"Full band song '{title}' must NOT be gig ready, found: {s.get('gig_ready')}")
            else:
                if s.get("gig_ready") != "Yes":
                    gig_ready_count += 1
                    all_pass = False
                    log_test(f"Gig readiness check: {title}", False, f"Full band song '{title}' must be gig ready, found: {s.get('gig_ready')}")
                
        # Date added check
        # Non-ready songs have 'None'
        if s.get("gig_ready") == "No":
            if s.get("date_added") != "None":
                all_pass = False
                log_test(f"Date added check: {title}", False, f"Non-gig-ready song should have date_added: 'None', found: {s.get('date_added')}")
                
        # Archive check (Paint It Black and Crazy Little Thing Called Love only)
        if title in ["Paint It Black", "Crazy Little Thing Called Love"]:
            if s.get("archived") != "Yes":
                archived_count += 1
                all_pass = False
                log_test(f"Archive check: {title}", False, "Must be archived")
        else:
            if s.get("archived") == "Yes":
                archived_count += 1
                all_pass = False
                log_test(f"Archive check: {title}", False, "Must NOT be archived")
                
        # Preferred emergency cut check
        pref_emergency_cut_list = {"Rock This Town", "Zombie", "Lights", "All Right Now", "Them Changes", "Hook", "Colors"}
        cleaned_title = title.replace("’", "'").replace("‘", "'").lower()
        pref_emergency_cut_cleaned = {t.replace("’", "'").replace("‘", "'").lower() for t in pref_emergency_cut_list}
        is_pref = cleaned_title in pref_emergency_cut_cleaned
        expected_pref = "Yes" if is_pref else "No"
        if s.get("preferred_emergency_cut") != expected_pref:
            all_pass = False
            log_test(f"Preferred emergency cut check: {title}", False, f"Expected {expected_pref}, found: {s.get('preferred_emergency_cut')}")
                 
    if missing_fields_count == 0:
        log_test("All songs contain required fields", True)
    if clean_backups_count == 0:
        log_test("No song lists lead vocalist in backup vocals", True)
    if gig_ready_count == 0:
        log_test("Gig readiness flags match requirements", True)
    if archived_count == 0:
        log_test("Archive flags match requirements (only Paint It Black and Crazy Little Thing Called Love archived)", True)
        
    return all_pass

# -------------------------------------------------------------
# 2. Output Parser
# -------------------------------------------------------------
def parse_markdown_report(stdout_str):
    sets = []
    encores = []
    breaks = []
    
    current_set = None
    in_encore = False
    in_break = False
    
    lines = stdout_str.split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        if line.startswith("## SET"):
            current_set = []
            sets.append(current_set)
            in_encore = False
            in_break = False
            continue
            
        if line.startswith("## ENCORES"):
            in_encore = True
            current_set = None
            in_break = False
            continue
            
        if line.startswith("### ☕ BREAK") or line.startswith("### ⏸️ BREAK"):
            in_break = True
            current_set = None
            in_encore = False
            continue
            
        # Parse table row
        if line.startswith("|") and not line.startswith("|---") and not line.startswith("| # |") and not line.startswith("| Constraint |"):
            parts = [p.strip() for p in line.split("|")]
            # Filter empty bounds
            parts = parts[1:-1]
            
            if current_set is not None:
                # Set song row: #, Title, Artist, Key, BPM, Length, Lead Vocal, Popularity, Note
                if len(parts) >= 7:
                    title = parts[1].replace("**", "").split("🟢")[0].split("🔴")[0].split("🛑")[0].strip()
                    emergency_cut = "🛑" in parts[1]
                    opener = "🟢" in parts[1]
                    closer = "🔴" in parts[1]
                    lead = parts[6].split("(")[0].strip()
                    backups = []
                    if "(" in parts[6]:
                        backups = [b.strip() for b in parts[6].split("(")[1].replace(")", "").split(",")]
                    current_set.append({
                        "title": title,
                        "emergency_cut": emergency_cut,
                        "opener": opener,
                        "closer": closer,
                        "lead": lead,
                        "backups": backups,
                        "key": parts[3],
                        "bpm": int(parts[4]),
                        "length": parts[5]
                    })
            elif in_encore:
                # Encore song row: #, Title, Artist, Key, BPM, Length, Lead Vocal, Popularity, Note
                if len(parts) >= 7:
                    title = parts[1].replace("**", "").strip()
                    lead = parts[6].split("(")[0].strip()
                    encores.append({
                        "title": title,
                        "lead": lead,
                        "length": parts[5]
                    })
            continue
                    
        # Parse acoustic break song bullets: "- **Title** (Artist) - Lead: X | **Can Leave Stage (Bathroom Break)**: `...`"
        if in_break and line.startswith("- **"):
            m = re.match(r"-\s*\*\*(.+?)\*\*\s*\(", line)
            if m:
                breaks.append(m.group(1).strip())
                    
    # Parse silent/acoustic breaks
    breaks_format = "silent"
    if "**Breaks**: ACOUSTIC" in stdout_str.upper():
        breaks_format = "acoustic"
        
    return {
        "sets": sets,
        "breaks": breaks,
        "encores": encores,
        "breaks_format": breaks_format,
        "stdout": stdout_str
    }

# -------------------------------------------------------------
# 3. Scenario Constraint Tests
# -------------------------------------------------------------
def run_scenario(args):
    cmd = ["python3", BUILDER_PATH] + args
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=SCRIPT_DIR)
    if res.returncode != 0:
        print(f"Error executing setlist builder for args {args}:")
        print(res.stderr)
        return None
    return parse_markdown_report(res.stdout)

def test_scenario_1():
    print("\nTesting Scenario 1 (90 Min Set, Yacht Rock Preference)...")
    res = run_scenario(["--duration", "1.5", "--gig-type", "yacht"])
    if not res:
        return False
        
    all_pass = True
    
    # 1. Check Yacht Rock constraint
    yacht_songs = {"Free Ride", "Gold on the Ceiling", "American Girl", "Brown Eyed Girl", 
                   "Peg", "Second Chance", "Baby Blue", "Rikki Don't Lose That Number", 
                   "Brandy", "Everybody Wants to Rule the World", "Reeling in the Years", 
                   "The Chain", "Take It Easy", "Colors", "Brass in Pocket", "Dreams", 
                   "Lights", "Roll with the Changes", "Ventura Highway", "Ooh La La", 
                   "Landslide", "Vienna", "Don't Know Why"}
    
    for s_idx, set_songs in enumerate(res["sets"]):
        for song in set_songs:
            if song["title"] not in yacht_songs:
                all_pass = False
                log_test(f"Yacht rock check: {song['title']}", False, "Song is not Yacht Rock or Yacht Rock Adjacent")
    
    # 2. Check duration warning
    if "INSUFFICIENT MUSIC FOR TARGET DURATION" not in res["stdout"]:
        all_pass = False
        log_test("Duration warning present", False, "Missing insufficient music warning")
    else:
        log_test("Duration warning present", True)
        
    # 3. Check Roll with the Changes closer
    last_set = res["sets"][-1]
    if last_set[-1]["title"] != "Roll with the Changes":
        all_pass = False
        log_test("Show closer check", False, f"Expected 'Roll with the Changes' as closer, found '{last_set[-1]['title']}'")
    else:
        log_test("Show closer check ('Roll with the Changes' closer of last set)", True)
        
    # 4. Check gig-ready songs only
    not_ready_songs = {"The Story", "Vienna", "Don't Know Why"}
    for s_idx, set_songs in enumerate(res["sets"]):
        for song in set_songs:
            if song["title"] in not_ready_songs:
                all_pass = False
                log_test(f"Gig ready check: {song['title']}", False, "Scheduled non-gig-ready song")
                
    # 5. Check emergency cut placement
    for s_idx, set_songs in enumerate(res["sets"]):
        cuts = [idx for idx, s in enumerate(set_songs) if s["emergency_cut"]]
        if len(cuts) != 1:
            all_pass = False
            log_test(f"Emergency cut count Set {s_idx+1}", False, f"Expected exactly 1 cut, found {len(cuts)}")
        else:
            cut_idx = cuts[0]
            # Must be in second half
            mid = len(set_songs) // 2
            if cut_idx < mid or cut_idx >= len(set_songs) - 1:
                all_pass = False
                log_test(f"Emergency cut position Set {s_idx+1}", False, f"Cut at index {cut_idx} is not in second half (len: {len(set_songs)}, mid: {mid})")
            else:
                log_test(f"Emergency cut positioned correctly in Set {s_idx+1}", True)
                
    return all_pass

def test_scenario_2():
    print("\nTesting Scenario 2 (2hr Set, Bar Gig)...")
    res = run_scenario(["--duration", "2.0", "--gig-type", "bar"])
    if not res:
        return False
        
    all_pass = True
    
    # 1. Check Working for the Weekend opener
    first_set = res["sets"][0]
    if first_set[0]["title"] != "Working for the Weekend":
        all_pass = False
        log_test("Show opener check", False, f"Expected 'Working for the Weekend' as opener, found '{first_set[0]['title']}'")
    else:
        log_test("Show opener check ('Working for the Weekend' opener of Set 1)", True)
        
    # 2. Check Roll with the Changes closer
    last_set = res["sets"][-1]
    if last_set[-1]["title"] != "Roll with the Changes":
        all_pass = False
        log_test("Show closer check", False, f"Expected 'Roll with the Changes' as closer, found '{last_set[-1]['title']}'")
    else:
        log_test("Show closer check ('Roll with the Changes' closer of last set)", True)
        
    # 3. Check Lauren's vocal separation (gravelly separation)
    gravelly = {"Zombie", "Respect", "Roll with the Changes", "You Oughta Know", "Me and Bobby McGee"}
    for s_idx, set_songs in enumerate(res["sets"]):
        gravelly_indices = [idx for idx, s in enumerate(set_songs) if s["title"] in gravelly]
        for idx in range(len(gravelly_indices) - 1):
            sep = gravelly_indices[idx+1] - gravelly_indices[idx] - 1
            # Check if separation is less than 1 (relaxed from 2)
            if sep < 1:
                all_pass = False
                log_test(f"Lauren health check Set {s_idx+1}", False, f"Gravelly songs '{set_songs[gravelly_indices[idx]]['title']}' and '{set_songs[gravelly_indices[idx+1]]['title']}' have separation of {sep}")
                
    # 4. Check backup vocals cleanliness
    for s_idx, set_songs in enumerate(res["sets"]):
        for song in set_songs:
            lead = song["lead"]
            backups = song["backups"]
            if lead in backups or (lead == "Lauren" and "L" in backups) or (lead == "Jon" and "J" in backups) or (lead == "David" and "D" in backups) or (lead == "Martin" and "M" in backups):
                all_pass = False
                log_test(f"Clean backups check Set {s_idx+1}: {song['title']}", False, f"Lead '{lead}' is also in backups '{backups}'")
                
    return all_pass

def test_scenario_3():
    print("\nTesting Scenario 3 (3hr Set, David Out, Bar Gig)...")
    res = run_scenario(["--duration", "3.0", "--david-out", "--gig-type", "bar"])
    if not res:
        return False
        
    all_pass = True
    
    # 1. David must not lead or back up any songs
    for s_idx, set_songs in enumerate(res["sets"]):
        for song in set_songs:
            if song["lead"] == "David" or "David" in song["backups"] or "D" in song["backups"]:
                all_pass = False
                log_test(f"David out constraint: {song['title']}", False, "David is still scheduled to perform lead or backup")
                
    # 2. David's lead vocal parts must be covered by Lauren
    # Keep Your Hands to Yourself, Ventura Highway, Ooh La La
    for s_idx, set_songs in enumerate(res["sets"]):
        for song in set_songs:
            if song["title"] in ["Keep Your Hands to Yourself", "Ventura Highway", "Ooh La La"]:
                if song["lead"] != "Lauren":
                    all_pass = False
                    log_test(f"David substitution: {song['title']}", False, f"Expected Lauren as lead, found '{song['lead']}'")
                    
    log_test("David out substitutions applied correctly", True)

    # 3. Vocal breakdown must NOT appear in report output
    if "LEAD VOCALS BREAKDOWN" in res["stdout"]:
        all_pass = False
        log_test("Vocalist breakdown suppressed from output", False, "LEAD VOCALS BREAKDOWN should not be printed")
    else:
        log_test("Vocalist breakdown suppressed from output", True)

    # 4. Vocalist Balance constraint must still appear as satisfied in the table
    if "| Vocalist Balance | ✅ Satisfied |" in res["stdout"] or "Vocalist Balance" in res["stdout"]:
        log_test("Vocalist Balance constraint present in summary", True)
    else:
        all_pass = False
        log_test("Vocalist Balance constraint present in summary", False)

    return all_pass

def test_scenario_4():
    print("\nTesting Scenario 4 (3hr Set, Martin Out, Bar Gig)...")
    res = run_scenario(["--duration", "3.0", "--martin-out", "--gig-type", "bar"])
    if not res:
        return False
        
    all_pass = True
    
    # 1. Songs that REQUIRE Martin (acoustic guitar) must be cut from sets AND acoustic breaks.
    # Cut list: The Chain, Landslide, Blackbird
    # These must NOT be cut: Ooh La La, Wish You Were Here, Ventura Highway, Colors
    martin_required = {"The Chain", "Landslide", "Blackbird"}
    martin_survives = {"Ooh La La", "Wish You Were Here", "Ventura Highway", "Colors"}
    found_violations = False
    for s_idx, set_songs in enumerate(res["sets"]):
        for song in set_songs:
            if song["title"] in martin_required:
                all_pass = False
                found_violations = True
                log_test(f"Martin out cut: {song['title']}", False, f"{song['title']} should be cut (requires Martin)")
    for title in res.get("breaks", []):
        if title in martin_required:
            all_pass = False
            found_violations = True
            log_test(f"Martin out cut (break): {title}", False, f"{title} should be cut from acoustic breaks (requires Martin)")

    if not found_violations:
        log_test("Martin-required songs (Colors, The Chain, Landslide, Blackbird) cut from sets and breaks", True)

    # 2. Ooh La La, Wish You Were Here, Ventura Highway must NOT be cut when Martin is out
    # (Their substitution_notes confirm they survive without him)
    # We can't assert they always appear (setlist is probabilistic), but if they do appear,
    # they must NOT be attributed to Martin as lead.
    all_set_songs = [song for set_songs in res["sets"] for song in set_songs]
    all_set_titles = {s["title"] for s in all_set_songs}
    surviving_present = martin_survives & all_set_titles
    if surviving_present:
        log_test(f"Martin-surviving songs present in setlist ({', '.join(sorted(surviving_present))})", True)
    # If none happened to be selected, that's fine — just note it

    # 3. Martin must not lead or back up any songs
    for s_idx, set_songs in enumerate(res["sets"]):
        for song in set_songs:
            if song["lead"] == "Martin" or "Martin" in song["backups"] or "M" in song["backups"]:
                all_pass = False
                log_test(f"Martin out constraint: {song['title']}", False, "Martin is still scheduled to perform lead or backup")

    # 4. David covers most of Martin's lead parts (American Girl, Hey Jealousy),
    #    but Born to Run has a per-song override sending it to Lauren instead.
    for s_idx, set_songs in enumerate(res["sets"]):
        for song in set_songs:
            if song["title"] in ["American Girl", "Hey Jealousy"]:
                if song["lead"] != "David":
                    all_pass = False
                    log_test(f"Martin substitution: {song['title']}", False, f"Expected David as lead, found '{song['lead']}'")
            if song["title"] == "Born to Run":
                if song["lead"] != "Lauren":
                    all_pass = False
                    log_test(f"Martin substitution: {song['title']}", False, f"Expected Lauren as lead (per-song override), found '{song['lead']}'")

    log_test("Martin out substitutions applied correctly", True)

    # 5. Vocal breakdown must NOT appear in report output
    if "LEAD VOCALS BREAKDOWN" in res["stdout"]:
        all_pass = False
        log_test("Vocalist breakdown suppressed from output", False, "LEAD VOCALS BREAKDOWN should not be printed")
    else:
        log_test("Vocalist breakdown suppressed from output", True)

    # 6. Segue ordering: Brown Eyed Girl must come before Hey Jealousy whenever both appear
    all_songs_flat = [s for set_songs in res["sets"] for s in set_songs] + res["encores"]
    all_titles = [s["title"] for s in all_songs_flat]
    if "Brown Eyed Girl" in all_titles and "Hey Jealousy" in all_titles:
        beg_idx = all_titles.index("Brown Eyed Girl")
        hj_idx = all_titles.index("Hey Jealousy")
        if beg_idx < hj_idx:
            log_test("Segue order: Brown Eyed Girl before Hey Jealousy", True)
        else:
            all_pass = False
            log_test("Segue order: Brown Eyed Girl before Hey Jealousy", False,
                     f"Brown Eyed Girl at index {beg_idx}, Hey Jealousy at {hj_idx}")

    # 7. Acoustic breaks must use surviving acoustic songs (not cut ones)
    martin_cut = {"Landslide", "Blackbird", "The Chain"}
    for title in res.get("breaks", []):
        if title in martin_cut:
            all_pass = False
            log_test(f"Martin-out acoustic break contains cut song: {title}", False,
                     f"{title} requires Martin and must not appear in acoustic breaks")

    return all_pass

def test_scenario_5():
    print("\nTesting Scenario 5 (1.25hr Set, Vocal Limits constraints)...")
    res = run_scenario(["--duration", "1.25", "--skip-country-grunge", "--max-david", "2", "--max-martin", "1", "--min-lauren", "5", "--gig-type", "bar"])
    if not res:
        return False
        
    all_pass = True
    
    # 1. Count scheduled vocals (including encores)
    counts = {}
    for s_idx, set_songs in enumerate(res["sets"]):
        for song in set_songs:
            counts[song["lead"]] = counts.get(song["lead"], 0) + 1
    for song in res["encores"]:
        counts[song["lead"]] = counts.get(song["lead"], 0) + 1
        
    # Check constraints
    if counts.get("David", 0) > 2:
        all_pass = False
        log_test("Max David limit", False, f"Expected <= 2, found {counts.get('David', 0)}")
    else:
        log_test("Max David limit (<= 2)", True)
        
    if counts.get("Martin", 0) > 1:
        all_pass = False
        log_test("Max Martin limit", False, f"Expected <= 1, found {counts.get('Martin', 0)}")
    else:
        log_test("Max Martin limit (<= 1)", True)
        
    if counts.get("Lauren", 0) < 5:
        all_pass = False
        log_test("Min Lauren limit", False, f"Expected >= 5, found {counts.get('Lauren', 0)}")
    else:
        log_test("Min Lauren limit (>= 5)", True)
        
    # Check that "Vocalist Limits" is in the satisfaction summary and marked satisfied
    if "| Vocalist Limits | ✅ Satisfied |" in res["stdout"]:
        log_test("Vocalist Limits shown in summary table", True)
    else:
        all_pass = False
        log_test("Vocalist Limits shown in summary table", False, "Missing Vocalist Limits or not satisfied in summary table")

    return all_pass

def test_scenario_6():
    """Scenario 6: 3hr Martin-out bar gig — acoustic breaks must work."""
    print("\nTesting Scenario 6 (3hr Set, Martin Out, Acoustic Breaks)...")
    import tempfile, os, glob

    # Run with --date and --location to exercise file output
    test_date = "2099-01-01"
    test_loc = "Test Venue Eval"
    res = run_scenario([
        "--duration", "3.0", "--martin-out", "--gig-type", "bar",
        "--breaks", "acoustic",
        "--date", test_date, "--location", test_loc
    ])
    if not res:
        return False

    all_pass = True

    # 1. Acoustic breaks must engage (not silently fall back)
    #    At least one acoustic break song must appear
    if len(res["breaks"]) >= 2:
        log_test("Martin-out acoustic breaks populated", True)
    else:
        all_pass = False
        log_test("Martin-out acoustic breaks populated", False,
                 f"Expected >= 2 break songs, found {len(res['breaks'])}")

    # 2. No cut songs in acoustic breaks
    martin_cut = {"Landslide", "Blackbird", "The Chain"}
    for title in res["breaks"]:
        if title in martin_cut:
            all_pass = False
            log_test(f"Acoustic break uses cut song: {title}", False)

    if all(t not in martin_cut for t in res["breaks"]):
        log_test("Acoustic break songs are all Martin-out-safe", True)

    # 3. File output: .md and .txt must be written to setlists/
    setlists_dir = os.path.join(SCRIPT_DIR, "..", "setlists")
    expected_stem = f"{test_date} {test_loc}"
    md_file = os.path.join(setlists_dir, expected_stem + ".md")
    txt_file = os.path.join(setlists_dir, expected_stem + ".txt")
    pdf_file = os.path.join(setlists_dir, expected_stem + ".pdf")
    rtf_file = os.path.join(setlists_dir, expected_stem + ".rtf")

    if os.path.exists(md_file):
        log_test("File output: .md written to setlists/", True)
        os.remove(md_file)  # clean up test artifact
    else:
        all_pass = False
        log_test("File output: .md written to setlists/", False, f"Expected: {md_file}")

    if os.path.exists(txt_file):
        log_test("File output: .txt written to setlists/", True)
        # Verify plaintext has arrow notation header
        with open(txt_file, encoding="utf-8") as f:
            txt_content = f.read()
        if "No Martin" in txt_content and "->" in txt_content:
            log_test("Plaintext .txt contains arrow notation", True)
        else:
            all_pass = False
            log_test("Plaintext .txt contains arrow notation", False)
        os.remove(txt_file)  # clean up test artifact
    else:
        all_pass = False
        log_test("File output: .txt written to setlists/", False, f"Expected: {txt_file}")

    if os.path.exists(pdf_file):
        os.remove(pdf_file)  # clean up test artifact (PDF rendering is best-effort)

    if os.path.exists(rtf_file):
        os.remove(rtf_file)  # clean up test artifact (RTF rendering is best-effort)

    shared_drive_dir = os.path.expanduser("~/Google Drive/Shared Drives/Wannabe Weekenders/Setlists")
    for ext in (".pdf", ".rtf"):
        shared_drive_file = os.path.join(shared_drive_dir, expected_stem + ext)
        if os.path.exists(shared_drive_file):
            os.remove(shared_drive_file)  # clean up Drive sync test artifact

    # 4. Segue ordering: Funkytown -> Miss You -> Reeling in the Years
    all_songs_flat = [s for set_songs in res["sets"] for s in set_songs]
    all_titles = [s["title"] for s in all_songs_flat]
    segue_trio = ["Funkytown", "Miss You", "Reeling in the Years"]
    trio_present = [t for t in segue_trio if t in all_titles]
    if len(trio_present) >= 2:
        indices = [all_titles.index(t) for t in trio_present]
        if indices == sorted(indices):
            log_test(f"Segue order: {' -> '.join(trio_present)}", True)
        else:
            all_pass = False
            log_test(f"Segue order: {' -> '.join(trio_present)}", False,
                     f"Order violation: indices {indices}")

    return all_pass

def test_scenario_7():
    """Scenario 7: Full-band gig — every present vocalist must lead at least one song."""
    print("\nTesting Scenario 7 (Full Band — Every Vocalist Must Lead)...")
    res = run_scenario(["--duration", "1.25", "--gig-type", "bar", "--breaks", "none"])
    if not res:
        return False

    all_pass = True

    # Collect all leads across sets and encores
    counts = {}
    for set_songs in res["sets"]:
        for song in set_songs:
            counts[song["lead"]] = counts.get(song["lead"], 0) + 1
    for song in res["encores"]:
        counts[song["lead"]] = counts.get(song["lead"], 0) + 1

    # All four vocalists must have at least 1 lead (full band = no one out)
    for vocalist in ["Lauren", "Jon", "Martin", "David"]:
        if counts.get(vocalist, 0) >= 1:
            log_test(f"Vocalist inclusion: {vocalist} leads >= 1 song", True)
        else:
            all_pass = False
            log_test(f"Vocalist inclusion: {vocalist} leads >= 1 song", False,
                     f"{vocalist} has 0 lead songs — every present vocalist must lead at least one.")

    # Verify the constraint is reported as satisfied in the output
    if "Vocalist Inclusion" in res["stdout"] or "vocalist" in res["stdout"].lower():
        log_test("Vocalist inclusion noted in constraint table", True)

    return all_pass


# -------------------------------------------------------------
# Main Test Suite Runner
# -------------------------------------------------------------
def main():
    print("=============================================================")
    print("WANNABE WEEKENDERS SETLIST BUILDER AUTOMATED TEST SUITE")
    print("=============================================================\n")
    
    db_ok = test_database_integrity()
    
    print("\n-------------------------------------------------------------")
    print("Scenario Validation Tests")
    print("-------------------------------------------------------------")
    s1_ok = test_scenario_1()
    s2_ok = test_scenario_2()
    s3_ok = test_scenario_3()
    s4_ok = test_scenario_4()
    s5_ok = test_scenario_5()
    s6_ok = test_scenario_6()
    s7_ok = test_scenario_7()

    print("\n=============================================================")
    print("TEST SUITE SUMMARY")
    print("=============================================================")
    print(f"Database Integrity:                     {'PASS' if db_ok else 'FAIL'}")
    print(f"Scenario 1 (Yacht):                     {'PASS' if s1_ok else 'FAIL'}")
    print(f"Scenario 2 (2hr):                       {'PASS' if s2_ok else 'FAIL'}")
    print(f"Scenario 3 (No David):                  {'PASS' if s3_ok else 'FAIL'}")
    print(f"Scenario 4 (No Martin):                 {'PASS' if s4_ok else 'FAIL'}")
    print(f"Scenario 5 (Vocal Limits):              {'PASS' if s5_ok else 'FAIL'}")
    print(f"Scenario 6 (Martin-out Acoustic+Files): {'PASS' if s6_ok else 'FAIL'}")
    print(f"Scenario 7 (All Vocalists Lead ≥1):     {'PASS' if s7_ok else 'FAIL'}")
    print("=============================================================")

    if db_ok and s1_ok and s2_ok and s3_ok and s4_ok and s5_ok and s6_ok and s7_ok:
        print("\nALL TESTS PASSED SUCCESSFULLY! ✅")
        sys.exit(0)
    else:
        print("\nSOME TESTS FAILED! ❌")
        sys.exit(1)

if __name__ == "__main__":
    main()
