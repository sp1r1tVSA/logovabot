import sqlite3
from config import DB_PATH

def restore():
    print(f"Подключение к БД: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    from config import KPL_TEAMS
    
    # Берем команды из конфига
    teams = set(KPL_TEAMS)
    missing_team = "Ривер Плейт"
    
    print(f"Всего команд: {len(teams)}")
    
    restored_count = 0
    # Проходим по всем 30 турам
    for round_num in range(1, 31):
        cursor.execute("""
            SELECT COALESCE(m.player1_team, u1.team_name), COALESCE(m.player2_team, u2.team_name)
            FROM matches m
            LEFT JOIN users u1 ON m.player1_id = u1.telegram_id
            LEFT JOIN users u2 ON m.player2_id = u2.telegram_id
            WHERE m.round_number = ? AND (m.tournament_type IS NULL OR m.tournament_type = 'league')
        """, (round_num,))
        matches = cursor.fetchall()
        
        round_teams = set()
        for m in matches:
            if m[0]: round_teams.add(m[0])
            if m[1]: round_teams.add(m[1])
            
        missing_in_round = teams - round_teams
        if len(missing_in_round) == 2 and missing_team in missing_in_round:
            # Нашли соперника, с которым Ривер Плейт должен был играть в этом туре
            opponent = list(missing_in_round - {missing_team})[0]
            
            # Чередуем дома/в гостях для баланса
            if round_num % 2 == 0:
                p1, p2 = missing_team, opponent
            else:
                p1, p2 = opponent, missing_team
                
            print(f"Тур {round_num}: Восстанавливаем матч {p1} - {p2}")
            cursor.execute("""
                INSERT INTO matches (round_number, player1_team, player2_team, status, tournament_type)
                VALUES (?, ?, ?, 'pending', 'league')
            """, (round_num, p1, p2))
            restored_count += 1
            
    conn.commit()
    conn.close()
    print(f"Успешно восстановлено матчей: {restored_count}")

if __name__ == '__main__':
    restore()
