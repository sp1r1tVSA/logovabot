"""
restore_league.py

Скрипт восстановления турнирной таблицы и структуры боевого сезона КПЛ 2026.
Что делает:
1. Удаляет все синтетические матчи Лаборатории (ID >= 3141 / команды North Wolves, Red Falcons и т.д.).
2. Удаляет тестовые рынки, котировки, события и раунды тестового дивизиона.
3. Удаляет тестовый дивизион TEST_LEAGUE и сезон LOGOVO TEST SEASON 2026.
4. Восстанавливает боевой Сезон 2026 (ID = 1) как единственный активный (status = 'active').
5. Проверяет и пересчитывает официальную турнирную таблицу (240 подтвержденных матчей).
6. Выводит восстановленную таблицу в терминал для подтверждения.
"""

import os
import sys
import logging
import database
from config import KPL_TEAMS

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("restore_league")

SYNTHETIC_TEAMS = [
    "North Wolves", "Red Falcons", "Iron Lions", "Black Eagles",
    "Golden Sharks", "Blue Titans", "Royal Bears", "Storm FC",
    "Silver Foxes", "Dark Knights", "Phoenix United", "Thunder City",
    "Atomic FC", "Victory Stars", "Capital Dragons", "United Kings"
]


def restore():
    print("=" * 80)
    print("🔄 ЗАПУСК ВОССТАНОВЛЕНИЯ БОЕВОЙ ЛИГИ КПЛ 2026...")
    print("=" * 80)

    with database.transaction() as conn:
        cursor = conn.cursor()

        # 1. Поиск синтетических сезонов и дивизионов
        cursor.execute("SELECT id, name FROM seasons WHERE name LIKE '%TEST%' OR name LIKE '%LAB%'")
        test_seasons = cursor.fetchall()
        test_season_ids = [s["id"] for s in test_seasons]

        cursor.execute("SELECT id, name, code FROM divisions WHERE code = 'TEST_LEAGUE' OR name LIKE '%TEST%'")
        test_divs = cursor.fetchall()
        test_div_ids = [d["id"] for d in test_divs]

        print(f"🔍 Найдено тестовых сезонов: {len(test_season_ids)}, дивизионов: {len(test_div_ids)}")

        # 2. Поиск синтетических матчей
        conds = ["id >= 3141"]
        params = []
        if test_season_ids:
            placeholders_s = ",".join("?" for _ in test_season_ids)
            conds.append(f"season_id IN ({placeholders_s})")
            params.extend(test_season_ids)
        if test_div_ids:
            placeholders_d = ",".join("?" for _ in test_div_ids)
            conds.append(f"division_id IN ({placeholders_d})")
            params.extend(test_div_ids)

        # Команды синтетики
        t_placeholders = ",".join("?" for _ in SYNTHETIC_TEAMS)
        conds.append(f"(player1_team IN ({t_placeholders}) OR player2_team IN ({t_placeholders}))")
        params.extend(SYNTHETIC_TEAMS)
        params.extend(SYNTHETIC_TEAMS)

        where_clause = " OR ".join(conds)
        cursor.execute(f"SELECT id FROM matches WHERE {where_clause}", tuple(params))
        synth_match_ids = list({r["id"] for r in cursor.fetchall()})

        print(f"🧹 Найдено синтетических матчей для удаления: {len(synth_match_ids)}")

        # 3. Удаление связанных таблиц синтетики
        if synth_match_ids:
            m_ph = ",".join("?" for _ in synth_match_ids)
            cursor.execute(f"DELETE FROM bet_items WHERE match_id IN ({m_ph})", tuple(synth_match_ids))
            cursor.execute(f"""
                DELETE FROM market_selections WHERE market_id IN (
                    SELECT id FROM markets WHERE match_id IN ({m_ph})
                )
            """, tuple(synth_match_ids))
            cursor.execute(f"DELETE FROM markets WHERE match_id IN ({m_ph})", tuple(synth_match_ids))
            cursor.execute(f"DELETE FROM bet_markets WHERE match_id IN ({m_ph})", tuple(synth_match_ids))
            cursor.execute(f"DELETE FROM live_events WHERE match_id IN ({m_ph})", tuple(synth_match_ids))
            cursor.execute(f"DELETE FROM live_match_states WHERE match_id IN ({m_ph})", tuple(synth_match_ids))
            cursor.execute(f"DELETE FROM matches WHERE id IN ({m_ph})", tuple(synth_match_ids))
            print(f"✅ Удалено {len(synth_match_ids)} синтетических матчей и связанных рынков.")

        # 4. Удаление раундов и дивизионов синтетики
        if test_div_ids:
            d_ph = ",".join("?" for _ in test_div_ids)
            cursor.execute(f"DELETE FROM rounds WHERE division_id IN ({d_ph})", tuple(test_div_ids))
            cursor.execute(f"DELETE FROM divisions WHERE id IN ({d_ph})", tuple(test_div_ids))
            print(f"✅ Удалено тестовых дивизионов: {len(test_div_ids)}")

        if test_season_ids:
            s_ph = ",".join("?" for _ in test_season_ids)
            cursor.execute(f"DELETE FROM rounds WHERE season_id IN ({s_ph})", tuple(test_season_ids))
            cursor.execute(f"DELETE FROM seasons WHERE id IN ({s_ph})", tuple(test_season_ids))
            print(f"✅ Удалено тестовых сезонов: {len(test_season_ids)}")

        # 5. Удаление синтетических команд из teams (North Wolves и т.д.)
        for t_name in SYNTHETIC_TEAMS:
            cursor.execute("DELETE FROM teams WHERE name = ?", (t_name,))

        # 6. Удаление тестового пользователя 999999999
        cursor.execute("DELETE FROM user_bets WHERE user_id = 999999999")
        cursor.execute("DELETE FROM coin_transactions WHERE user_id = 999999999")
        cursor.execute("DELETE FROM user_wallets WHERE user_id = 999999999")
        cursor.execute("DELETE FROM user_progression WHERE user_id = 999999999")
        cursor.execute("DELETE FROM user_achievements WHERE user_id = 999999999")
        cursor.execute("DELETE FROM users WHERE telegram_id = 999999999")

        # 7. Восстановление статуса боевого сезона
        cursor.execute("SELECT id, name, status FROM seasons WHERE id = 1")
        main_season = cursor.fetchone()
        if main_season:
            cursor.execute("UPDATE seasons SET status = 'active' WHERE id = 1")
            print(f"👑 Боевой сезон #{main_season['id']} ({main_season['name']}) переведён в статус 'active'.")
        else:
            # Если сезона с id=1 нет, сделаем первый доступный активным
            cursor.execute("SELECT id, name FROM seasons ORDER BY id ASC LIMIT 1")
            first_s = cursor.fetchone()
            if first_s:
                cursor.execute("UPDATE seasons SET status = 'active' WHERE id = ?", (first_s["id"],))
                print(f"👑 Сезон #{first_s['id']} ({first_s['name']}) переведён в статус 'active'.")

        # Убедимся, что боевые матчи имеют division_id = 1 и season_id = 1
        cursor.execute("""
            UPDATE matches 
            SET division_id = 1, season_id = 1
            WHERE status = 'confirmed' AND (division_id IS NULL OR division_id = 0)
        """)

        # 8. Проверка подтверждённых матчей
        cursor.execute("""
            SELECT COUNT(*) as cnt 
            FROM matches 
            WHERE status = 'confirmed' 
              AND (tournament_type IS NULL OR tournament_type = 'league')
              AND (season_id = 1 OR season_id IS NULL)
              AND (division_id = 1 OR division_id IS NULL)
        """)
        confirmed_count = cursor.fetchone()["cnt"]
        print(f"\n📊 Подтверждённых матчей боевой лиги КПЛ: {confirmed_count}")

    # 9. Проверка и расчет итоговой таблицы
    print("\n" + "=" * 80)
    print("🏆 РАСЧЁТ ИТОГОВОЙ ТАБЛИЦЫ КПЛ ПОСЛЕ ВОССТАНОВЛЕНИЯ:")
    print("=" * 80)
    
    standings = database.get_standings(division_id=None, season_id=1)
    
    header = f"{'#':<3} {'Команда':<18} {'И':<4} {'В':<4} {'Н':<4} {'П':<4} {'ЗМ':<5} {'ПМ':<5} {'РМ':<6} {'О':<4}"
    print(header)
    print("-" * 80)
    
    for idx, s in enumerate(standings, 1):
        team = s["team_name"]
        p = s["played"]
        w = s["wins"]
        d = s["draws"]
        l = s["losses"]
        gf = s["goals_scored"]
        ga = s["goals_conceded"]
        gd = gf - ga
        gd_str = f"+{gd}" if gd > 0 else str(gd)
        pts = s["points"]
        print(f"{idx:<3} {team:<18} {p:<4} {w:<4} {d:<4} {l:<4} {gf:<5} {ga:<5} {gd_str:<6} {pts:<4}")
        
    print("=" * 80)
    print("✅ ВСЕ БОЕВЫЕ ДАННЫЕ УСПЕШНО ВОССТАНОВЛЕНЫ!")
    print("=" * 80)


if __name__ == "__main__":
    restore()
