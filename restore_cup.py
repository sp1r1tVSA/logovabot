import sqlite3
import os
from config import DB_PATH

def restore_cup():
    if not os.path.exists(DB_PATH):
        print(f"Database {DB_PATH} not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get all users to map case-insensitive team names to telegram_id
    cursor.execute("SELECT telegram_id, team_name FROM users WHERE team_name IS NOT NULL")
    users = cursor.fetchall()
    team_to_id = {row[1].lower().strip(): row[0] for row in users}

    # Fetch all active cup series
    cursor.execute("SELECT id, stage, team1_name, team2_name, team1_wins, team2_wins FROM cup_series WHERE status = 'active'")
    series_list = cursor.fetchall()

    restored = 0
    for series in series_list:
        s_id = series[0]
        stage = series[1]
        t1_name = series[2]
        t2_name = series[3]
        t1_wins = series[4] or 0
        t2_wins = series[5] or 0
        
        # Check if there is any pending match for this series
        cursor.execute("SELECT COUNT(*) FROM matches WHERE cup_series_id = ? AND status = 'pending'", (s_id,))
        pending_count = cursor.fetchone()[0]
        
        if pending_count == 0:
            next_game = t1_wins + t2_wins + 1
            
            p1_id = team_to_id.get(t1_name.lower().strip())
            p2_id = team_to_id.get(t2_name.lower().strip())
            
            # Insert the missing cup match
            cursor.execute("""
                INSERT INTO matches (
                    round_number, player1_id, player2_id, player1_team, player2_team, 
                    status, tournament_type, cup_stage, cup_series_id, game_num_in_series
                ) VALUES (-1, ?, ?, ?, ?, 'pending', 'cup', ?, ?, ?)
            """, (p1_id, p2_id, t1_name, t2_name, stage, s_id, next_game))
            restored += 1
            print(f"Восстановлен кубковый матч: {t1_name} vs {t2_name} (Игра {next_game})")

    conn.commit()
    conn.close()
    
    print(f"Всего восстановлено кубковых матчей: {restored}")

if __name__ == "__main__":
    restore_cup()
