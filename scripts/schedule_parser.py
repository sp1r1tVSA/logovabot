import re
import sys
from pathlib import Path

# Add project root to sys.path for standalone execution
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database

def normalize_team(name: str) -> str:
    return name.lower().strip().replace('ё', 'е').replace('ë', 'е')

def parse_schedule_text(text: str) -> tuple[dict[int, list[tuple[str, str]]], list[str]]:
    """
    Parses raw text schedule into structured rounds and matches.
    Returns tuple: (rounds_dict, errors_list).
    rounds_dict format: { round_number: [ (home_team_name, away_team_name), ... ] }
    """
    lines = text.strip().splitlines()
    rounds: dict[int, list[tuple[str, str]]] = {}
    errors: list[str] = []
    
    current_round = None
    
    # Pre-fetch registered users and map lowercase team names to canonical team names
    users = [dict(u) for u in database.list_users()]
    teams_map = {normalize_team(u['team_name']): u['team_name'] for u in users if u.get('team_name')}
    
    for line_no, line in enumerate(lines, 1):
        line_clean = line.strip()
        if not line_clean:
            continue
            
        # Check for round header (e.g. "1 Тур", "Тур 1", "Round 1", "1тур", "1-й Тур")
        round_match = re.search(r'(?:тур|round)\s*(\d+)|\b(\d+)\s*(?:тур|round|-й тур|-йтур)', line_clean, re.IGNORECASE)
        if round_match:
            r_num = int(round_match.group(1) or round_match.group(2))
            current_round = r_num
            if current_round not in rounds:
                rounds[current_round] = []
            continue
            
        # Parse match pairing (e.g. "Спортинг - Ривер Плейт", "Спортинг 🆚 Ривер Плейт", "Спортинг vs Ривер Плейт")
        if current_round is None:
            current_round = 1
            rounds[current_round] = []
            
        parts = re.split(r'\s*(?:-|—|–|🆚|vs|v\.?)\s*', line_clean)
        if len(parts) == 2:
            team1_raw, team2_raw = parts[0].strip(), parts[1].strip()
            
            t1 = teams_map.get(normalize_team(team1_raw), team1_raw)
            t2 = teams_map.get(normalize_team(team2_raw), team2_raw)
            
            rounds[current_round].append((t1, t2))
        else:
            errors.append(f"Строка {line_no}: Не удалось распознать пару команд -> '{line_clean}'")
            
    return rounds, errors

def create_matches_from_parsed_schedule(rounds_data: dict[int, list[tuple[str, str]]]) -> tuple[int, int, list[str]]:
    """
    Creates rounds and matches in DB from parsed schedule data.
    Clears all existing rounds and matches first!
    Returns tuple: (num_rounds_created, num_matches_created, list_of_unmatched_teams).
    """
    database.clear_all_rounds_and_matches()
    
    users = [dict(u) for u in database.list_users()]
    team_to_user_id = {normalize_team(u['team_name']): u['telegram_id'] for u in users if u.get('team_name')}
    
    unmatched_teams = set()
    total_matches = 0
    total_rounds = len(rounds_data)
    
    for r_num, pairs in rounds_data.items():
        # Ensure round exists in DB
        database.create_round(r_num)
        
        for t1, t2 in pairs:
            u1_id = team_to_user_id.get(normalize_team(t1))
            u2_id = team_to_user_id.get(normalize_team(t2))
            
            if not u1_id:
                unmatched_teams.add(t1)
            if not u2_id:
                unmatched_teams.add(t2)
                
            if u1_id and u2_id:
                database.create_match(r_num, u1_id, u2_id)
                total_matches += 1
                
    return total_rounds, total_matches, list(unmatched_teams)
