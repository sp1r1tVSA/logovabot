import sqlite3
import os

DB_PATH = os.getenv("LEAGUE_SQLITE_PATH", "league.db")

RESULTS = [
    {
        "round_number": 1,
        "team1": "Ривер Плейт",
        "team2": "АЕК",
        "score1": 3,
        "score2": 3,
        "events": [
            ("Ривер Плейт", "Driussi", "goal", 3),
            ("Ривер Плейт", "Lucas Paquetá", "assist", 2),
            ("Ривер Плейт", "Martínez", "assist", 1),
            ("АЕК", "Rani Khedira", "goal", 1),
            ("АЕК", "Evander", "goal", 2),
            ("АЕК", "Orbelín Pineda", "assist", 1),
            ("АЕК", "Roony Bardghji", "assist", 1),
        ]
    },
    {
        "round_number": 2,
        "team1": "Копенгаген",
        "team2": "Ривер Плейт",
        "score1": 2,
        "score2": 3,
        "events": [
            ("Копенгаген", "Ruben Vargas", "goal", 1),
            ("Копенгаген", "Dodi Lukébakio", "goal", 1),
            ("Копенгаген", "Mohamed Elyounoussi", "assist", 2),
            ("Ривер Плейт", "Quintero", "goal", 1),
            ("Ривер Плейт", "Martínez", "goal", 1),
            ("Ривер Плейт", "Driussi", "goal", 1),
            ("Ривер Плейт", "Quintero", "assist", 1),
            ("Ривер Плейт", "Driussi", "assist", 1),
        ]
    },
    {
        "round_number": 3,
        "team1": "Ривер Плейт",
        "team2": "Бока Хуниорс",
        "score1": 4,
        "score2": 0,
        "events": [
            ("Ривер Плейт", "Galoppo", "goal", 2),
            ("Ривер Плейт", "Quintero", "goal", 2),
            ("Ривер Плейт", "Driussi", "assist", 4),
        ]
    },
    {
        "round_number": 4,
        "team1": "Рейнджерс",
        "team2": "Ривер Плейт",
        "score1": 0,
        "score2": 2,
        "events": [
            ("Ривер Плейт", "Galoppo", "goal", 1),
            ("Ривер Плейт", "Martínez", "goal", 1),
            ("Ривер Плейт", "Driussi", "assist", 2),
        ]
    },
    {
        "round_number": 6,
        "team1": "Будë Глимт",
        "team2": "Ривер Плейт",
        "score1": 1,
        "score2": 2,
        "events": [
            ("Будë Глимт", "Kasper Høgh", "goal", 1),
            ("Будë Глимт", "Artem Dovbyk", "assist", 1),
            ("Ривер Плейт", "Martínez", "goal", 1),
            ("Ривер Плейт", "Driussi", "goal", 1),
            ("Ривер Плейт", "Quintero", "assist", 1),
            ("Ривер Плейт", "Driussi", "assist", 1),
        ]
    },
    {
        "round_number": 7,
        "team1": "Ривер Плейт",
        "team2": "Фейеноорд",
        "score1": 2,
        "score2": 3,
        "events": [
            ("Ривер Плейт", "Quintero", "goal", 1),
            ("Ривер Плейт", "Martínez", "goal", 1),
            ("Ривер Плейт", "Quintero", "assist", 1),
            ("Ривер Плейт", "Driussi", "assist", 1),
            ("Фейеноорд", "Andrey Santos", "goal", 2),
            ("Фейеноорд", "Sem Steijn", "goal", 1),
            ("Фейеноорд", "Serhou Guirassy", "assist", 2),
        ]
    },
    {
        "round_number": 8,
        "team1": "ПСВ",
        "team2": "Ривер Плейт",
        "score1": 2,
        "score2": 0,
        "events": [
            ("ПСВ", "Rio Ngumoha", "goal", 2),
            ("ПСВ", "Mike", "assist", 2),
        ]
    },
    {
        "round_number": 9,
        "team1": "Ривер Плейт",
        "team2": "Спортинг",
        "score1": 7,
        "score2": 0,
        "events": [
            ("Ривер Плейт", "Driussi", "goal", 4),
            ("Ривер Плейт", "Lucas Paquetá", "goal", 1),
            ("Ривер Плейт", "Martínez", "goal", 2),
            ("Ривер Плейт", "Driussi", "assist", 3),
            ("Ривер Плейт", "Lucas Paquetá", "assist", 2),
            ("Ривер Плейт", "Martínez", "assist", 1),
            ("Ривер Плейт", "Quintero", "assist", 1),
        ]
    }
]

def restore():
    print(f"Connecting to {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    for res in RESULTS:
        rnd = res["round_number"]
        t1 = res["team1"]
        t2 = res["team2"]
        
        # Find the match
        cursor.execute("""
            SELECT id, player1_team, player2_team FROM matches 
            WHERE round_number = ? AND tournament_type = 'league' 
            AND ((player1_team = ? AND player2_team = ?) OR (player1_team = ? AND player2_team = ?))
        """, (rnd, t1, t2, t2, t1))
        
        match = cursor.fetchone()
        if not match:
            print(f"Match not found: Round {rnd}, {t1} vs {t2}")
            continue
            
        m_id = match[0]
        db_t1 = match[1]
        
        if db_t1 == t1:
            s1 = res["score1"]
            s2 = res["score2"]
        else:
            s1 = res["score2"]
            s2 = res["score1"]
            
        # Update match scores and status
        cursor.execute("UPDATE matches SET player1_score = ?, player2_score = ?, status = 'confirmed' WHERE id = ?", (s1, s2, m_id))
        
        # Clean up existing events just in case
        cursor.execute("DELETE FROM match_events WHERE match_id = ?", (m_id,))
        
        # Insert events
        for team, player, evt_type, count in res["events"]:
            cursor.execute("""
                INSERT INTO match_events (match_id, team_name, player_name, event_type, count)
                VALUES (?, ?, ?, ?, ?)
            """, (m_id, team, player, evt_type, count))
            
            cursor.execute("INSERT OR IGNORE INTO squad_players (team_name, player_name) VALUES (?, ?)", (team, player))
            
        print(f"Restored Round {rnd}: {t1} {res['score1']}:{res['score2']} {t2} (Events: {len(res['events'])})")
        
    conn.commit()
    conn.close()
    print("Done!")

if __name__ == '__main__':
    restore()
