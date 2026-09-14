#!/usr/bin/env python3
"""
scripts/seed_my_club_demo.py

Генератор тестовых данных для проверки личного кабинета игрока («Мой Клуб») в Logovo.bet Mini App.

Что создаёт скрипт:
1. Регистрирует/привязывает пользователя к клубу и дивизиону (с 1 варном для проверки дисциплины).
2. Создает соперников по дивизиону с реальными Telegram-никами.
3. Добавляет активные матчи в текущем туре:
   - Домашний матч с входящим предложением времени от соперника (кнопка «✅ Подтвердить»).
   - Выездной матч без времени (кнопка «🗓 Предложить время»).
4. Добавляет сыгранные матчи клуба (для проверки блока «История игр», формы [В] [Н] [В] и очков в таблице).
5. Заполняет полный состав клуба (15 игроков с позициями ВР, ЦЗ, ЦП, НАП и т.д.).
6. Начисляет голы и ассисты через match_events (для проверки бейджей «Лучший бомбардир» и «Лучший ассистент»).
7. Инициализирует кошелек пользователя с монетами.

Использование:
  python scripts/seed_my_club_demo.py
  python scripts/seed_my_club_demo.py --user-id 1642770076 --team "Реал Мадрид"
  python scripts/seed_my_club_demo.py --clean
"""

import sys
import os
import argparse
from pathlib import Path

# Setup project root import path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import config
import database

DEMO_ROSTER = [
    ("Тибо Куртуа", "ВР"),
    ("Дани Карвахаль", "ПЗ"),
    ("Антонио Рюдигер", "ЦЗ"),
    ("Эдер Милитао", "ЦЗ"),
    ("Ферлан Менди", "ЛЗ"),
    ("Орельен Тчуамени", "ЦОП"),
    ("Федерико Вальверде", "ЦП"),
    ("Эдуардо Камавинга", "ЦП"),
    ("Лука Модрич", "ЦП"),
    ("Джуд Беллингем", "ЦАП"),
    ("Родриго", "ПВ"),
    ("Винисиус Жуниор", "ЛВ"),
    ("Килиан Мбаппе", "НАП"),
    ("Эндрик", "НАП"),
    ("Арда Гюлер", "ЦАП"),
]

DEMO_OPPONENTS = [
    {"team": "Барселона", "user_id": 990101, "username": "barca_coach"},
    {"team": "Манчестер Сити", "user_id": 990102, "username": "city_master"},
    {"team": "Бавария", "user_id": 990103, "username": "bayern_boss"},
    {"team": "Ливерпуль", "user_id": 990104, "username": "klopp_style"},
]


def seed_cabinet_demo(user_id: int, team_name: str = "Реал Мадрид", division_id: int = 1, clean: bool = False):
    print(f"\n🚀 Запуск генерации тестовых данных для «Мой Клуб»...")
    print(f"   👤 Telegram ID игрока: {user_id}")
    print(f"   🛡 Игровой клуб: {team_name}")
    print(f"   🏆 Дивизион ID: {division_id}")

    database.init_db()
    database.ensure_canonical_divisions()

    with database.transaction() as conn:
        cursor = conn.cursor()

        # 1. Очистка старых демо-данных если запрошено
        if clean:
            print("   🧹 Очистка предыдущих тестовых матчей и состава...")
            cursor.execute("DELETE FROM squad_players WHERE LOWER(team_name) = LOWER(?)", (team_name,))
            cursor.execute("DELETE FROM match_events WHERE LOWER(team_name) = LOWER(?)", (team_name,))
            cursor.execute(
                "DELETE FROM matches WHERE LOWER(player1_team) = LOWER(?) OR LOWER(player2_team) = LOWER(?)",
                (team_name, team_name)
            )

        # 2. Убеждаемся в наличии активного сезона и дивизиона
        cursor.execute("SELECT id FROM seasons WHERE status = 'active' LIMIT 1")
        s_row = cursor.fetchone()
        season_id = s_row["id"] if s_row else 1

        cursor.execute("SELECT id, name FROM divisions WHERE id = ?", (division_id,))
        div_row = cursor.fetchone()
        div_name = div_row["name"] if div_row else f"Дивизион {division_id}"

        # 3. Регистрация основного пользователя
        cursor.execute("SELECT username FROM users WHERE telegram_id = ?", (user_id,))
        existing_u = cursor.fetchone()
        username = existing_u["username"] if existing_u and existing_u["username"] else f"coach_{user_id}"

        cursor.execute("""
            INSERT INTO users (telegram_id, username, team_name, division_id, warn_count, role)
            VALUES (?, ?, ?, ?, 1, 'player')
            ON CONFLICT(telegram_id) DO UPDATE SET
                team_name = excluded.team_name,
                division_id = excluded.division_id,
                warn_count = 1
        """, (user_id, username, team_name, division_id))

        # 4. Регистрация соперников
        for opp in DEMO_OPPONENTS:
            cursor.execute("""
                INSERT INTO users (telegram_id, username, team_name, division_id, warn_count, role)
                VALUES (?, ?, ?, ?, 0, 'player')
                ON CONFLICT(telegram_id) DO UPDATE SET
                    team_name = excluded.team_name,
                    division_id = excluded.division_id
            """, (opp["user_id"], opp["username"], opp["team"], division_id))

        # 5. Создание туров
        cursor.execute("""
            INSERT INTO rounds (season_id, division_id, round_number, is_open, deadline, bets_open)
            VALUES (?, ?, 1, 1, 'Сегодня, 23:59', 0)
            ON CONFLICT(season_id, division_id, round_number) DO UPDATE SET
                is_open = 1, deadline = 'Сегодня, 23:59'
        """, (season_id, division_id))

        cursor.execute("""
            INSERT INTO rounds (season_id, division_id, round_number, is_open, deadline, bets_open)
            VALUES (?, ?, 2, 0, '18.09.2026 21:00', 0)
            ON CONFLICT(season_id, division_id, round_number) DO UPDATE SET
                deadline = '18.09.2026 21:00'
        """, (season_id, division_id))

        # 6. Состав клуба (squad_players)
        cursor.execute("DELETE FROM squad_players WHERE LOWER(team_name) = LOWER(?)", (team_name,))
        for player_name, pos in DEMO_ROSTER:
            cursor.execute("""
                INSERT OR REPLACE INTO squad_players (team_name, player_name, position)
                VALUES (?, ?, ?)
            """, (team_name, player_name, pos))

        # 7. Активные матчи (Тур 1)
        # Матч 1: Дома против Барселоны. Соперник уже предложил время!
        opp_barca = DEMO_OPPONENTS[0]
        cursor.execute("""
            INSERT INTO matches (
                season_id, division_id, round_number, tournament_type,
                player1_id, player2_id, player1_team, player2_team,
                status, proposed_time, proposed_by, time_status
            ) VALUES (?, ?, 1, 'league', ?, ?, ?, ?, 'pending', 'Сегодня, 21:30', ?, 'proposed')
        """, (season_id, division_id, user_id, opp_barca["user_id"], team_name, opp_barca["team"], opp_barca["user_id"]))
        active_m1_id = cursor.lastrowid

        # Матч 2: В гостях у Манчестер Сити. Время еще не согласовано.
        opp_city = DEMO_OPPONENTS[1]
        cursor.execute("""
            INSERT INTO matches (
                season_id, division_id, round_number, tournament_type,
                player1_id, player2_id, player1_team, player2_team,
                status, proposed_time, proposed_by, time_status
            ) VALUES (?, ?, 1, 'league', ?, ?, ?, ?, 'pending', NULL, NULL, 'none')
        """, (season_id, division_id, opp_city["user_id"], user_id, opp_city["team"], team_name))
        active_m2_id = cursor.lastrowid

        # 8. Сыгранные матчи (История игр + форма команды + статистика игроков)
        # Игра 1: Реал Мадрид 3 : 1 Бавария (Победа)
        opp_bayern = DEMO_OPPONENTS[2]
        cursor.execute("""
            INSERT INTO matches (
                season_id, division_id, round_number, tournament_type,
                player1_id, player2_id, player1_team, player2_team,
                player1_score, player2_score, status, played_at
            ) VALUES (?, ?, 1, 'league', ?, ?, ?, ?, 3, 1, 'confirmed', datetime('now', '-2 days'))
        """, (season_id, division_id, user_id, opp_bayern["user_id"], team_name, opp_bayern["team"]))
        m_hist1 = cursor.lastrowid

        # Игра 2: Ливерпуль 2 : 2 Реал Мадрид (Ничья)
        opp_liv = DEMO_OPPONENTS[3]
        cursor.execute("""
            INSERT INTO matches (
                season_id, division_id, round_number, tournament_type,
                player1_id, player2_id, player1_team, player2_team,
                player1_score, player2_score, status, played_at
            ) VALUES (?, ?, 1, 'league', ?, ?, ?, ?, 2, 2, 'confirmed', datetime('now', '-1 day'))
        """, (season_id, division_id, opp_liv["user_id"], user_id, opp_liv["team"], team_name))
        m_hist2 = cursor.lastrowid

        # Игра 3: Реал Мадрид 2 : 0 Барселона (Победа)
        cursor.execute("""
            INSERT INTO matches (
                season_id, division_id, round_number, tournament_type,
                player1_id, player2_id, player1_team, player2_team,
                player1_score, player2_score, status, played_at
            ) VALUES (?, ?, 1, 'league', ?, ?, ?, ?, 2, 0, 'confirmed', datetime('now', '-3 hours'))
        """, (season_id, division_id, user_id, opp_barca["user_id"], team_name, opp_barca["team"]))
        m_hist3 = cursor.lastrowid

        # 9. События матчей (голы и ассисты)
        events = [
            # В матче против Баварии (3:1): Винисиус 2 гола, Мбаппе 1 гол, Родриго 1 пас, Модрич 1 пас
            (m_hist1, team_name, "Винисиус Жуниор", "goal", 2),
            (m_hist1, team_name, "Килиан Мбаппе", "goal", 1),
            (m_hist1, team_name, "Родриго", "assist", 1),
            (m_hist1, team_name, "Лука Модрич", "assist", 1),
            (m_hist1, opp_bayern["team"], "Гарри Кейн", "goal", 1),

            # В матче против Ливерпуля (2:2): Винисиус 1 гол, Беллингем 1 гол, Родриго 1 пас, Вальверде 1 пас
            (m_hist2, team_name, "Винисиус Жуниор", "goal", 1),
            (m_hist2, team_name, "Джуд Беллингем", "goal", 1),
            (m_hist2, team_name, "Родриго", "assist", 1),
            (m_hist2, team_name, "Федерико Вальверде", "assist", 1),
            (m_hist2, opp_liv["team"], "Мохамед Салах", "goal", 2),

            # В матче против Барселоны (2:0): Мбаппе 1 гол, Винисиус 1 гол, Карвахаль 1 пас, Родриго 1 пас
            (m_hist3, team_name, "Килиан Мбаппе", "goal", 1),
            (m_hist3, team_name, "Винисиус Жуниор", "goal", 1),
            (m_hist3, team_name, "Дани Карвахаль", "assist", 1),
            (m_hist3, team_name, "Родриго", "assist", 1),
        ]

        for m_id, t_name, p_name, ev_type, count in events:
            cursor.execute("""
                INSERT INTO match_events (match_id, team_name, player_name, event_type, count)
                VALUES (?, ?, ?, ?, ?)
            """, (m_id, t_name, p_name, ev_type, count))

        # 10. Кошелек пользователя
        cursor.execute("""
            INSERT INTO user_wallets (user_id, balance, bets_count, bets_won)
            VALUES (?, 1000, 5, 3)
            ON CONFLICT(user_id) DO UPDATE SET balance = MAX(balance, 1000)
        """, (user_id,))

    print("✅ Тестовые данные успешно созданы в базе!")
    print("\n📋 Что теперь отображается во вкладке «Мой Клуб»:")
    print(f"   • Баннер клуба: герб {team_name}, {div_name}, форма: [В] [Н] [В], 7 очков")
    print(f"   • Дисциплина: 1 / {config.MAX_WARNS_LIMIT} ⚠️")
    print("   • Вкладка «Мои матчи»:")
    print(f"     1) {team_name} vs Барселона (Тур 1, Дома) — «Соперник предлагает: Сегодня, 21:30» + кнопка «✅ Подтвердить»")
    print(f"     2) Манчестер Сити vs {team_name} (Тур 1, В гостях) — «Ожидает игры» + кнопка «🗓 Предложить время»")
    print("   • Вкладка «Состав клуба»:")
    print("     15 игроков с позициями. Лидеры: Винисиус Жуниор (4 ⚽) и Родриго (3 👟)")
    print("   • Вкладка «История игр»:")
    print("     3 сыгранных матча с результатами (3:1 Бавария, 2:2 Ливерпуль, 2:0 Барселона)")
    print("\n💡 Для проверки:")
    print("   1. Перезапустите бота (python main.py), если он ещё не был перезапущен.")
    print("   2. Откройте в Telegram Mini App и перейдите во вкладку «Мой Клуб» (🛡).")


def main():
    parser = argparse.ArgumentParser(description="Seed demo data for Logovo.bet 'My Club' tab")
    default_uid = config.ADMIN_IDS[0] if config.ADMIN_IDS else 1642770076
    parser.add_argument("--user-id", type=int, default=default_uid, help=f"Telegram ID пользователя (default: {default_uid})")
    parser.add_argument("--team", type=str, default="Реал Мадрид", help="Название клуба (default: Реал Мадрид)")
    parser.add_argument("--division-id", type=int, default=1, help="ID дивизиона (default: 1)")
    parser.add_argument("--clean", action="store_true", help="Очистить предыдущие демо-данные перед генерацией")

    args = parser.parse_args()
    seed_cabinet_demo(
        user_id=args.user_id,
        team_name=args.team,
        division_id=args.division_id,
        clean=args.clean
    )


if __name__ == "__main__":
    main()
