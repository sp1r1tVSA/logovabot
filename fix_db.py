import sqlite3
import os
from config import DB_PATH

def fix_database():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get all users to map case-insensitive team names to telegram_id
    cursor.execute("SELECT telegram_id, team_name FROM users WHERE team_name IS NOT NULL")
    users = cursor.fetchall()
    
    # Create a mapping of lowercased team_name to telegram_id
    team_to_id = {row[1].lower().strip(): row[0] for row in users}

    # Find matches where player1_id or player2_id is NULL but player1_team/player2_team is known
    cursor.execute("SELECT id, player1_team, player2_team, player1_id, player2_id FROM matches")
    matches = cursor.fetchall()
    
    updates = 0
    for match in matches:
        m_id = match[0]
        p1_team = match[1]
        p2_team = match[2]
        p1_id = match[3]
        p2_id = match[4]
        
        new_p1_id = p1_id
        new_p2_id = p2_id
        
        if p1_team and not p1_id:
            lookup_id = team_to_id.get(p1_team.lower().strip())
            if lookup_id:
                new_p1_id = lookup_id
                
        if p2_team and not p2_id:
            lookup_id = team_to_id.get(p2_team.lower().strip())
            if lookup_id:
                new_p2_id = lookup_id
                
        if new_p1_id != p1_id or new_p2_id != p2_id:
            cursor.execute("""
                UPDATE matches 
                SET player1_id = ?, player2_id = ? 
                WHERE id = ?
            """, (new_p1_id, new_p2_id, m_id))
            updates += 1

    conn.commit()
    print(f"Успешно исправлено матчей: {updates}")
    
    # Check if there are any duplicate matches between the same teams in the same round
    # (Since restore_matches.py might have inserted duplicates if round_number was messed up)
    # But since schedule_matches generates a 30-round schedule, it shouldn't duplicate within the same round.
    
    conn.close()

if __name__ == "__main__":
    fix_database()
