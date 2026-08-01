import sqlite3

def fix_missing_teams():
    print("Подключение к БД league.db...")
    conn = sqlite3.connect('league.db')
    cursor = conn.cursor()
    
    # Заполняем player1_team для старых матчей, где это поле пустое, 
    # беря название команды из таблицы users по старому player1_id
    cursor.execute("""
        UPDATE matches
        SET player1_team = (SELECT team_name FROM users WHERE users.telegram_id = matches.player1_id)
        WHERE (player1_team IS NULL OR player1_team = '') AND player1_id IS NOT NULL
    """)
    p1_updated = cursor.rowcount
    
    # То же самое для player2_team
    cursor.execute("""
        UPDATE matches
        SET player2_team = (SELECT team_name FROM users WHERE users.telegram_id = matches.player2_id)
        WHERE (player2_team IS NULL OR player2_team = '') AND player2_id IS NOT NULL
    """)
    p2_updated = cursor.rowcount
    
    conn.commit()
    conn.close()
    
    print(f"Обновлено команд хозяев (player1_team): {p1_updated}")
    print(f"Обновлено команд гостей (player2_team): {p2_updated}")
    print("Готово! Теперь матчи Порту и остальных команд должны вернуться.")

if __name__ == '__main__':
    fix_missing_teams()
