"""
services/lab_service.py

🧪 LOGOVO LAB — Isolated Sportsbook Test Environment Service.
Designed for safe manual testing of the Logovo.bet betting engine on synthetic data.
Strict Invariants:
1. Pure preparation & control: Lab NEVER places automated bets.
2. No duplicate engine: delegates settlement to services.settlement_engine.settle_match_predictions,
   live progression to services.live_state_machine and services.live_ingestion,
   and markets to services.odds_engine.
3. Hard data isolation: operates strictly within synthetic season/division/test player namespace.
   Production tournaments, divisions, and user data are strictly untouched.
"""

import json
import random
import logging
from typing import Any, Optional

import database
from services.odds_engine import get_or_create_market, get_or_create_selection, set_odds
from services.settlement_engine import settle_match_predictions
from services.live_state_machine import (
    SCHEDULED,
    LIVE,
    HALFTIME,
    FINISHED,
    transition_live_match,
)
from services.sports_provider import LiveEvent
from services.live_ingestion import ingest_live_event

logger = logging.getLogger("services.lab_service")

# ─── Constants & Metadata ───────────────────────────────────────────────────
TEST_SEASON_NAME = "LOGOVO TEST SEASON 2026"
TEST_DIVISION_CODE = "TEST_LEAGUE"
TEST_DIVISION_NAME = "LOGOVO TEST LEAGUE"

DEFAULT_TEST_USER_ID = 999999999
DEFAULT_TEST_USER_NAME = "Test Player"
INITIAL_TEST_BALANCE = 100000

DEFAULT_SEED = 20260905

SYNTHETIC_TEAMS = [
    "North Wolves",
    "Red Falcons",
    "Iron Lions",
    "Black Eagles",
    "Golden Sharks",
    "Blue Titans",
    "Royal Bears",
    "Storm FC",
    "Silver Foxes",
    "Dark Knights",
    "Phoenix United",
    "Thunder City",
    "Atomic FC",
    "Victory Stars",
    "Capital Dragons",
    "United Kings",
]

PRESET_SCENARIOS = {
    "home_win": {
        "id": "home_win",
        "title": "HOME WIN",
        "badge": "🟢 П1",
        "home_team": "North Wolves",
        "away_team": "Red Falcons",
        "target_outcome": "p1",
        "target_odds": 1.80,
        "expected_score": (2, 0),
        "description": "Победа хозяев поля: ставка на П1 при коэффициенте 1.80, итоговый счет 2:0.",
        "market_key": "1x2",
        "selection_key": "p1",
    },
    "draw": {
        "id": "draw",
        "title": "DRAW",
        "badge": "🟡 Ничья",
        "home_team": "Iron Lions",
        "away_team": "Black Eagles",
        "target_outcome": "x",
        "target_odds": 3.50,
        "expected_score": (1, 1),
        "description": "Ничейный результат: ставка на Х при коэффициенте 3.50, итоговый счет 1:1.",
        "market_key": "1x2",
        "selection_key": "x",
    },
    "away_win": {
        "id": "away_win",
        "title": "AWAY WIN",
        "badge": "🔵 П2",
        "home_team": "Golden Sharks",
        "away_team": "Blue Titans",
        "target_outcome": "p2",
        "target_odds": 2.30,
        "expected_score": (0, 2),
        "description": "Победа гостей: ставка на П2 при коэффициенте 2.30, итоговый счет 0:2.",
        "market_key": "1x2",
        "selection_key": "p2",
    },
    "over": {
        "id": "over",
        "title": "OVER 2.5",
        "badge": "⚽ ТБ 2.5",
        "home_team": "Royal Bears",
        "away_team": "Storm FC",
        "target_outcome": "over_2.5",
        "target_odds": 1.70,
        "expected_score": (3, 1),
        "description": "Тотал больше 2.5 голов: ставка на ТБ 2.5 при коэффициенте 1.70, итоговый счет 3:1.",
        "market_key": "total_goals",
        "selection_key": "over_2.5",
    },
    "under": {
        "id": "under",
        "title": "UNDER 2.5",
        "badge": "🛡️ ТМ 2.5",
        "home_team": "Royal Bears",
        "away_team": "Storm FC",
        "target_outcome": "under_2.5",
        "target_odds": 2.10,
        "expected_score": (1, 0),
        "description": "Тотал меньше 2.5 голов: ставка на ТМ 2.5 при коэффициенте 2.10, итоговый счет 1:0.",
        "market_key": "total_goals",
        "selection_key": "under_2.5",
    },
    "btts": {
        "id": "btts",
        "title": "BTTS YES",
        "badge": "🤝 ОЗ ДА",
        "home_team": "Phoenix United",
        "away_team": "Thunder City",
        "target_outcome": "btts_yes",
        "target_odds": 1.75,
        "expected_score": (2, 1),
        "description": "Обе забьют: ставка на ОЗ Да при коэффициенте 1.75, итоговый счет 2:1.",
        "market_key": "btts",
        "selection_key": "btts_yes",
    },
    "loss": {
        "id": "loss",
        "title": "LOSS TEST",
        "badge": "🔴 Проигрыш",
        "home_team": "Atomic FC",
        "away_team": "Victory Stars",
        "target_outcome": "p1",
        "target_odds": 1.90,
        "expected_score": (0, 2),
        "description": "Проверка проигрыша: вы ставите на П1 (1.90), а матч завершается победой гостей 0:2.",
        "market_key": "1x2",
        "selection_key": "p1",
    },
    "parlay": {
        "id": "parlay",
        "title": "PARLAY (EXPRESS)",
        "badge": "🚂 Экспресс",
        "home_team": "Silver Foxes",
        "away_team": "Dark Knights",
        "target_outcome": "p1",
        "target_odds": 1.75,
        "expected_score": (2, 1),
        "description": "Экспресс-ставка: комбинация исходов матчей Silver Foxes — Dark Knights и Capital Dragons — United Kings.",
        "market_key": "1x2",
        "selection_key": "p1",
    },
    "live": {
        "id": "live",
        "title": "LIVE TEST",
        "badge": "⚡ Live In-Play",
        "home_team": "Capital Dragons",
        "away_team": "United Kings",
        "target_outcome": "p1",
        "target_odds": 1.65,
        "expected_score": (2, 1),
        "description": "Матч подготовлен для живого тестирования Live-событий (62-я минута, счет 1:0).",
        "market_key": "1x2",
        "selection_key": "p1",
    },
    "cashout": {
        "id": "cashout",
        "title": "CASHOUT TEST",
        "badge": "💵 Кэшаут",
        "home_team": "Capital Dragons",
        "away_team": "United Kings",
        "target_outcome": "p1",
        "target_odds": 2.20,
        "expected_score": (1, 0),
        "description": "Тестирование досрочной выплаты: при счете 1:0 в Live котировка кэшаута становится выгодной.",
        "market_key": "1x2",
        "selection_key": "p1",
    },
}

# ─── Internal State Persistence Helpers ─────────────────────────────────────

def _get_lab_config(key: str, default: str = "") -> str:
    """Read a key from system_config table with 'lab_' prefix."""
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM system_config WHERE key = ?", (f"lab_{key}",))
        row = cursor.fetchone()
        return row["value"] if row else default


def _set_lab_config(key: str, value: str) -> None:
    """Save a key to system_config table with 'lab_' prefix."""
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO system_config (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (f"lab_{key}", str(value)))


def get_active_test_user_id() -> int:
    """Get active test user ID (defaults to DEFAULT_TEST_USER_ID)."""
    val = _get_lab_config("test_user_id", str(DEFAULT_TEST_USER_ID))
    try:
        return int(val)
    except ValueError:
        return DEFAULT_TEST_USER_ID


def set_active_test_user_id(user_id: int) -> None:
    """Set active test user ID."""
    _set_lab_config("test_user_id", str(user_id))


# ─── Division & Season Resolution ───────────────────────────────────────────

def get_test_season() -> Optional[dict]:
    """Fetch synthetic test season record if exists."""
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM seasons WHERE name = ? ORDER BY id DESC LIMIT 1",
            (TEST_SEASON_NAME,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_test_division() -> Optional[dict]:
    """Fetch synthetic test division record if exists."""
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM divisions WHERE code = ? ORDER BY id DESC LIMIT 1",
            (TEST_DIVISION_CODE,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


# ─── Test Environment Initialization ────────────────────────────────────────

def ensure_test_user(user_id: int = DEFAULT_TEST_USER_ID, initial_balance: int = INITIAL_TEST_BALANCE) -> dict:
    """Ensure test player exists in users and user_wallets with initial balance."""
    with database.transaction() as conn:
        cursor = conn.cursor()
        # Ensure in users table
        cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,))
        u = cursor.fetchone()
        if not u:
            cursor.execute("""
                INSERT INTO users (telegram_id, username, team_name, league_name, role)
                VALUES (?, ?, ?, ?, 'user')
            """, (user_id, DEFAULT_TEST_USER_NAME, f"Test Squad {user_id}", TEST_DIVISION_NAME))

        # Ensure in user_wallets
        cursor.execute("SELECT * FROM user_wallets WHERE user_id = ?", (user_id,))
        w = cursor.fetchone()
        if not w:
            cursor.execute("""
                INSERT INTO user_wallets (user_id, balance, total_wagered, total_won, bets_count, bets_won)
                VALUES (?, ?, 0, 0, 0, 0)
            """, (user_id, initial_balance))
            cursor.execute("""
                INSERT INTO coin_transactions (user_id, amount, transaction_type, reference_type, balance_after)
                VALUES (?, ?, 'initial_test_balance', 'lab_init', ?)
            """, (user_id, initial_balance, initial_balance))
            cursor.execute("SELECT * FROM user_wallets WHERE user_id = ?", (user_id,))
            w = cursor.fetchone()

        return dict(w)


def create_test_season(
    season_name: str = TEST_SEASON_NAME,
    division_name: str = TEST_DIVISION_NAME,
    teams_count: int = 16,
    rounds_count: int = 30,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """
    Generate synthetic test season with 16 teams, 30 rounds, 240 matches,
    markets, selections, synthetic odds, and pre-calculated scores.
    """
    teams = SYNTHETIC_TEAMS[:teams_count]
    if len(teams) < 4 or len(teams) % 2 != 0:
        raise ValueError("Количество команд должно быть четным и не менее 4.")

    with database.transaction() as conn:
        cursor = conn.cursor()

        # 1. Create or get season
        cursor.execute("SELECT * FROM seasons WHERE name = ?", (season_name,))
        season_row = cursor.fetchone()
        if season_row:
            season_id = season_row["id"]
            cursor.execute("UPDATE seasons SET status = 'active' WHERE id = ?", (season_id,))
        else:
            cursor.execute("""
                INSERT INTO seasons (name, status, created_at, started_at)
                VALUES (?, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """, (season_name,))
            season_id = cursor.lastrowid

        # 2. Create or get division
        cursor.execute("SELECT * FROM divisions WHERE code = ?", (TEST_DIVISION_CODE,))
        div_row = cursor.fetchone()
        if div_row:
            division_id = div_row["id"]
            cursor.execute("""
                UPDATE divisions
                SET name = ?, season_id = ?, is_active = 1
                WHERE id = ?
            """, (division_name, season_id, division_id))
        else:
            cursor.execute("""
                INSERT INTO divisions (tournament_id, name, code, season_id, is_active, sort_order)
                VALUES (1, ?, ?, ?, 1, 99)
            """, (division_name, TEST_DIVISION_CODE, season_id))
            division_id = cursor.lastrowid

        # 3. Ensure teams in teams table
        for t_name in teams:
            cursor.execute("SELECT id FROM teams WHERE name = ?", (t_name,))
            if not cursor.fetchone():
                cursor.execute("INSERT OR IGNORE INTO teams (name, short_name) VALUES (?, ?)", (t_name, t_name[:3].upper()))

        # 4. Generate Double Round-Robin fixtures (Circle / Berger algorithm)
        round_1_pairs = [
            (teams[0], teams[1]),   # North Wolves vs Red Falcons
            (teams[2], teams[3]),   # Iron Lions vs Black Eagles
            (teams[4], teams[5]),   # Golden Sharks vs Blue Titans
            (teams[6], teams[7]),   # Royal Bears vs Storm FC
            (teams[8], teams[9]),   # Silver Foxes vs Dark Knights
            (teams[10], teams[11]), # Phoenix United vs Thunder City
            (teams[12], teams[13]), # Atomic FC vs Victory Stars
            (teams[14], teams[15]), # Capital Dragons vs United Kings
        ]

        n = len(teams)
        rotating = list(range(1, n))
        schedule: list[list[tuple[str, str]]] = [round_1_pairs]

        rng = random.Random(seed)

        for r_idx in range(1, n - 1):
            round_pairs = []
            team_a = teams[0]
            team_b = teams[rotating[0]]
            if r_idx % 2 == 1:
                round_pairs.append((team_b, team_a))
            else:
                round_pairs.append((team_a, team_b))

            for i in range(1, n // 2):
                h = teams[rotating[i]]
                a = teams[rotating[n - 1 - i]]
                round_pairs.append((h, a))

            schedule.append(round_pairs)
            rotating = [rotating[-1]] + rotating[:-1]

        full_schedule = list(schedule)
        for r_pairs in schedule:
            reversed_pairs = [(away, home) for (home, away) in r_pairs]
            full_schedule.append(reversed_pairs)

        full_schedule = full_schedule[:rounds_count]

        # 5. Clean prior matches and rounds for test division
        cursor.execute("SELECT id FROM matches WHERE division_id = ? AND season_id = ?", (division_id, season_id))
        old_m_ids = [r["id"] for r in cursor.fetchall()]
        if old_m_ids:
            old_placeholders = ",".join("?" for _ in old_m_ids)
            cursor.execute(f"DELETE FROM bet_items WHERE match_id IN ({old_placeholders})", tuple(old_m_ids))
            cursor.execute(f"DELETE FROM markets WHERE match_id IN ({old_placeholders})", tuple(old_m_ids))
            cursor.execute(f"DELETE FROM bet_markets WHERE match_id IN ({old_placeholders})", tuple(old_m_ids))
            cursor.execute(f"DELETE FROM live_events WHERE match_id IN ({old_placeholders})", tuple(old_m_ids))
            cursor.execute(f"DELETE FROM live_match_states WHERE match_id IN ({old_placeholders})", tuple(old_m_ids))
            cursor.execute(f"DELETE FROM matches WHERE id IN ({old_placeholders})", tuple(old_m_ids))

        cursor.execute("DELETE FROM rounds WHERE division_id = ? AND season_id = ?", (division_id, season_id))

        # 6. Insert rounds
        for r_num in range(1, rounds_count + 1):
            is_open = 1 if r_num == 1 else 0
            cursor.execute("""
                INSERT INTO rounds (season_id, division_id, round_number, is_open, deadline)
                VALUES (?, ?, ?, ?, '2026-12-31 23:59:00')
            """, (season_id, division_id, r_num, is_open))

        # 7. Insert matches, markets, selections, and odds
        matches_created = 0
        all_created_matches = []

        for r_idx, r_pairs in enumerate(full_schedule):
            round_num = r_idx + 1
            for m_idx, (home_t, away_t) in enumerate(r_pairs):
                p1_seed = rng.uniform(1.60, 2.80)
                p2_seed = rng.uniform(2.10, 3.60)
                x_seed = rng.uniform(3.10, 3.80)
                tb25_seed = rng.uniform(1.65, 2.15)
                tm25_seed = round(1.0 / (1.05 - (1.0 / tb25_seed)), 2) if tb25_seed > 1.05 else 1.95
                btts_y_seed = rng.uniform(1.60, 1.95)
                btts_n_seed = round(1.0 / (1.05 - (1.0 / btts_y_seed)), 2) if btts_y_seed > 1.05 else 2.05

                exp_h = rng.randint(0, 3)
                exp_a = rng.randint(0, 2)

                if round_num == 1 and m_idx < len(round_1_pairs):
                    if m_idx == 0:  # Home Win (North Wolves vs Red Falcons)
                        p1_seed = 1.80
                        x_seed = 3.40
                        p2_seed = 4.20
                        exp_h, exp_a = (2, 0)
                    elif m_idx == 1:  # Draw (Iron Lions vs Black Eagles)
                        p1_seed = 2.40
                        x_seed = 3.50
                        p2_seed = 2.60
                        exp_h, exp_a = (1, 1)
                    elif m_idx == 2:  # Away Win (Golden Sharks vs Blue Titans)
                        p1_seed = 2.90
                        x_seed = 3.20
                        p2_seed = 2.30
                        exp_h, exp_a = (0, 2)
                    elif m_idx == 3:  # Over 2.5 (Royal Bears vs Storm FC)
                        tb25_seed = 1.70
                        tm25_seed = 2.10
                        exp_h, exp_a = (3, 1)
                    elif m_idx == 4:  # BTTS (Phoenix United vs Thunder City)
                        btts_y_seed = 1.75
                        btts_n_seed = 1.95
                        exp_h, exp_a = (2, 1)
                    elif m_idx == 5:  # Loss (Atomic FC vs Victory Stars)
                        p1_seed = 1.90
                        p2_seed = 3.80
                        exp_h, exp_a = (0, 2)

                p1_val = round(p1_seed, 2)
                x_val = round(x_seed, 2)
                p2_val = round(p2_seed, 2)
                tb25_val = round(tb25_seed, 2)
                tm25_val = round(tm25_seed, 2)
                btts_y_val = round(btts_y_seed, 2)
                btts_n_val = round(btts_n_seed, 2)

                stadium_info = f"Expected:{exp_h}:{exp_a}"

                cursor.execute("""
                    INSERT INTO matches (
                        tournament_id, round_number, player1_team, player2_team,
                        status, division_id, season_id, stadium
                    ) VALUES (1, ?, ?, ?, 'scheduled', ?, ?, ?)
                """, (round_num, home_t, away_t, division_id, season_id, stadium_info))
                match_id = cursor.lastrowid
                matches_created += 1

                # Legacy bet_markets row
                cursor.execute("""
                    INSERT INTO bet_markets (
                        match_id, tour, team1_name, team2_name,
                        odd_p1, odd_x, odd_p2, odd_tb25, odd_tm25, odd_btts_yes, odd_btts_no,
                        is_active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """, (
                    match_id, round_num, home_t, away_t,
                    p1_val, x_val, p2_val, tb25_val, tm25_val, btts_y_val, btts_n_val
                ))

                # Canonical relational markets
                # 1. 1X2 Market
                m_1x2 = get_or_create_market(match_id, "1x2", "Результат матча (1X2)", "main", 1)
                get_or_create_selection(m_1x2["id"], "p1", "П1 (Победа 1)", p1_val)
                get_or_create_selection(m_1x2["id"], "x", "X (Ничья)", x_val)
                get_or_create_selection(m_1x2["id"], "p2", "П2 (Победа 2)", p2_val)

                # 2. Total Goals Market
                m_tot = get_or_create_market(match_id, "total_goals", "Тотал голов (2.5)", "totals", 2)
                get_or_create_selection(m_tot["id"], "over_2.5", "Тотал больше 2.5", tb25_val)
                get_or_create_selection(m_tot["id"], "under_2.5", "Тотал меньше 2.5", tm25_val)

                # 3. BTTS Market
                m_btts = get_or_create_market(match_id, "btts", "Обе забьют", "btts", 3)
                get_or_create_selection(m_btts["id"], "btts_yes", "Обе забьют - Да", btts_y_val)
                get_or_create_selection(m_btts["id"], "btts_no", "Обе забьют - Нет", btts_n_val)

                all_created_matches.append({
                    "id": match_id,
                    "round_number": round_num,
                    "home_team": home_t,
                    "away_team": away_t,
                    "odds": {"p1": p1_val, "x": x_val, "p2": p2_val, "over_2.5": tb25_val, "btts_yes": btts_y_val},
                    "expected_score": f"{exp_h}:{exp_a}",
                })

        # 8. Ensure test player
        ensure_test_user(DEFAULT_TEST_USER_ID, INITIAL_TEST_BALANCE)

        # 9. Update state step
        _set_lab_config("step_season_created", "1")
        _set_lab_config("active_scenario", "home_win")
        _set_lab_config("current_step", "2")

        logger.info(f"🧪 Created synthetic season #{season_id} with {matches_created} matches.")

        return {
            "status": "ok",
            "season_id": season_id,
            "division_id": division_id,
            "season_name": season_name,
            "division_name": division_name,
            "teams_count": len(teams),
            "rounds_count": rounds_count,
            "matches_count": matches_created,
            "test_user_id": DEFAULT_TEST_USER_ID,
            "test_user_balance": INITIAL_TEST_BALANCE,
        }


def reset_test_lab(user_id: Optional[int] = None) -> dict[str, Any]:
    """
    Safely delete all synthetic test data.
    Strictly isolated: leaves all production seasons, divisions, matches, and real users untouched.
    Resets Test Player wallet to 100,000.
    """
    target_uid = user_id or get_active_test_user_id()

    with database.transaction() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM seasons WHERE name = ?", (TEST_SEASON_NAME,))
        s_row = cursor.fetchone()
        season_id = s_row["id"] if s_row else None

        cursor.execute("SELECT id FROM divisions WHERE code = ?", (TEST_DIVISION_CODE,))
        d_row = cursor.fetchone()
        division_id = d_row["id"] if d_row else None

        if division_id or season_id:
            conds = []
            params = []
            if division_id:
                conds.append("division_id = ?")
                params.append(division_id)
            if season_id:
                conds.append("season_id = ?")
                params.append(season_id)

            where_clause = " OR ".join(conds)
            cursor.execute(f"SELECT id FROM matches WHERE {where_clause}", tuple(params))
            match_ids = [r["id"] for r in cursor.fetchall()]

            if match_ids:
                placeholders = ",".join("?" for _ in match_ids)
                cursor.execute(f"DELETE FROM bet_items WHERE match_id IN ({placeholders})", tuple(match_ids))
                cursor.execute(f"""
                    DELETE FROM market_selections WHERE market_id IN (
                        SELECT id FROM markets WHERE match_id IN ({placeholders})
                    )
                """, tuple(match_ids))
                cursor.execute(f"DELETE FROM markets WHERE match_id IN ({placeholders})", tuple(match_ids))
                cursor.execute(f"DELETE FROM bet_markets WHERE match_id IN ({placeholders})", tuple(match_ids))
                cursor.execute(f"DELETE FROM live_events WHERE match_id IN ({placeholders})", tuple(match_ids))
                cursor.execute(f"DELETE FROM live_match_states WHERE match_id IN ({placeholders})", tuple(match_ids))
                cursor.execute(f"DELETE FROM matches WHERE id IN ({placeholders})", tuple(match_ids))

            if division_id:
                cursor.execute("DELETE FROM rounds WHERE division_id = ?", (division_id,))
                cursor.execute("DELETE FROM divisions WHERE id = ?", (division_id,))

            if season_id:
                cursor.execute("DELETE FROM seasons WHERE id = ?", (season_id,))

        # Delete user_bets for test user
        cursor.execute("SELECT id FROM user_bets WHERE user_id = ?", (target_uid,))
        user_bet_ids = [r["id"] for r in cursor.fetchall()]
        if user_bet_ids:
            ub_placeholders = ",".join("?" for _ in user_bet_ids)
            cursor.execute(f"DELETE FROM bet_items WHERE bet_id IN ({ub_placeholders})", tuple(user_bet_ids))
            cursor.execute(f"DELETE FROM user_bets WHERE id IN ({ub_placeholders})", tuple(user_bet_ids))

        # Reset test user wallet & transactions
        cursor.execute("DELETE FROM coin_transactions WHERE user_id = ?", (target_uid,))
        cursor.execute("""
            INSERT INTO user_wallets (user_id, balance, total_wagered, total_won, bets_count, bets_won)
            VALUES (?, ?, 0, 0, 0, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                balance = excluded.balance,
                total_wagered = 0,
                total_won = 0,
                bets_count = 0,
                bets_won = 0
        """, (target_uid, INITIAL_TEST_BALANCE))

        cursor.execute("""
            INSERT INTO coin_transactions (user_id, amount, transaction_type, reference_type, balance_after)
            VALUES (?, ?, 'initial_test_balance', 'lab_reset', ?)
        """, (target_uid, INITIAL_TEST_BALANCE, INITIAL_TEST_BALANCE))

        cursor.execute("DELETE FROM system_config WHERE key LIKE 'lab_%'")

        _set_lab_config("test_user_id", str(target_uid))
        _set_lab_config("current_step", "1")

        logger.info(f"🧪 Lab Reset completed for test user #{target_uid}.")

        return {
            "status": "ok",
            "message": "Синтетические тестовые данные успешно удалены. Баланс Test Player возвращен к 100,000 🪙.",
            "test_user_id": target_uid,
            "test_user_balance": INITIAL_TEST_BALANCE,
        }


# ─── Dashboard Overview & Status ────────────────────────────────────────────

def get_lab_dashboard_status(user_id: Optional[int] = None) -> dict[str, Any]:
    """Compile comprehensive Lab Dashboard overview."""
    target_uid = user_id or get_active_test_user_id()
    season = get_test_season()
    division = get_test_division()

    wallet_bal = INITIAL_TEST_BALANCE
    season_id = season["id"] if season else None
    division_id = division["id"] if division else None

    with database.transaction() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (target_uid,))
        w_row = cursor.fetchone()
        if w_row:
            wallet_bal = w_row["balance"]

        total_matches = 0
        completed_matches = 0
        live_matches = 0
        open_matches = 0

        if division_id and season_id:
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status IN ('confirmed', 'completed', 'finished') THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status IN ('live', 'in_progress') THEN 1 ELSE 0 END) as live_cnt,
                    SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open_cnt
                FROM matches
                WHERE division_id = ? AND season_id = ?
            """, (division_id, season_id))
            m_stats = cursor.fetchone()
            if m_stats:
                total_matches = m_stats["total"] or 0
                completed_matches = m_stats["completed"] or 0
                live_matches = m_stats["live_cnt"] or 0
                open_matches = m_stats["open_cnt"] or 0

        current_round = 1
        total_rounds = 30
        if division_id:
            cursor.execute("""
                SELECT round_number FROM rounds
                WHERE division_id = ? AND is_open = 1
                ORDER BY round_number ASC LIMIT 1
            """, (division_id,))
            r_row = cursor.fetchone()
            if r_row:
                current_round = r_row["round_number"]

        cursor.execute("""
            SELECT 
                COUNT(*) as total_bets,
                SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status IN ('won', 'lost', 'refunded') THEN 1 ELSE 0 END) as settled
            FROM user_bets
            WHERE user_id = ?
        """, (target_uid,))
        b_stats = cursor.fetchone()
        total_bets = b_stats["total_bets"] or 0 if b_stats else 0
        wins = b_stats["wins"] or 0 if b_stats else 0
        losses = b_stats["losses"] or 0 if b_stats else 0
        pending = b_stats["pending"] or 0 if b_stats else 0
        settled = b_stats["settled"] or 0 if b_stats else 0

    step_info = get_step_tracker_status(target_uid)

    return {
        "active_test_environment": {
            "season_name": TEST_SEASON_NAME if season else "Не создан",
            "division_name": TEST_DIVISION_NAME if division else "Не создан",
            "season_id": season_id,
            "division_id": division_id,
            "is_ready": bool(season and division and total_matches > 0),
        },
        "test_user": {
            "id": target_uid,
            "name": DEFAULT_TEST_USER_NAME,
            "balance": wallet_bal,
        },
        "progress": {
            "current_round": current_round,
            "total_rounds": total_rounds,
            "matches_completed": completed_matches,
            "matches_total": total_matches,
            "matches_live": live_matches,
            "matches_open": open_matches,
        },
        "betting_stats": {
            "total_bets": total_bets,
            "coupons": total_bets,
            "settlements": settled,
            "wins": wins,
            "losses": losses,
            "pending": pending,
        },
        "step_tracker": step_info,
        "active_scenario": _get_lab_config("active_scenario", "home_win"),
    }


# ─── Scenarios & Match Preparation ──────────────────────────────────────────

def list_predefined_scenarios() -> list[dict[str, Any]]:
    """Return all 8+ predefined quick test scenarios."""
    return list(PRESET_SCENARIOS.values())


def get_scenario(scenario_id: str) -> Optional[dict[str, Any]]:
    """Get single scenario by ID."""
    return PRESET_SCENARIOS.get(scenario_id)


def find_match_for_scenario(scenario_id: str) -> Optional[dict[str, Any]]:
    """Find the target match in test division corresponding to this scenario."""
    sc = PRESET_SCENARIOS.get(scenario_id)
    if not sc:
        return None

    division = get_test_division()
    season = get_test_season()
    if not division or not season:
        return None

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM matches
            WHERE division_id = ? AND season_id = ?
              AND player1_team = ? AND player2_team = ?
            ORDER BY id ASC LIMIT 1
        """, (division["id"], season["id"], sc["home_team"], sc["away_team"]))
        row = cursor.fetchone()
        return dict(row) if row else None


def prepare_match_for_test(
    match_id: int,
    scenario_id: Optional[str] = None,
    custom_odds: Optional[float] = None,
    custom_score: Optional[tuple[int, int]] = None,
) -> dict[str, Any]:
    """
    Prepare match for safe manual testing:
    - Sets match status to 'open'.
    - Opens the parent round so betting engine accepts slips.
    - Sets canonical odds for target market.
    - Stores expected result for easy subsequent verification.
    - Updates Step Tracker (Step 2 completed -> waiting for manual bet).
    - NEVER places a bet automatically!
    """
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
        m = cursor.fetchone()
        if not m:
            raise ValueError(f"Match #{match_id} not found.")

        div_id = m["division_id"]
        round_num = m["round_number"]

        if div_id and round_num:
            cursor.execute("""
                UPDATE rounds SET is_open = 1
                WHERE division_id = ? AND round_number = ?
            """, (div_id, round_num))

        cursor.execute("UPDATE matches SET status = 'open' WHERE id = ?", (match_id,))

        exp_score_str = ""
        if custom_score:
            exp_score_str = f"{custom_score[0]}:{custom_score[1]}"
            cursor.execute("UPDATE matches SET stadium = ? WHERE id = ?", (f"Expected:{exp_score_str}", match_id))
        elif scenario_id and scenario_id in PRESET_SCENARIOS:
            sc = PRESET_SCENARIOS[scenario_id]
            exp_h, exp_a = sc["expected_score"]
            exp_score_str = f"{exp_h}:{exp_a}"
            cursor.execute("UPDATE matches SET stadium = ? WHERE id = ?", (f"Expected:{exp_score_str}", match_id))

        if scenario_id and scenario_id in PRESET_SCENARIOS:
            sc = PRESET_SCENARIOS[scenario_id]
            target_odd = custom_odds or sc["target_odds"]
            m_key = sc["market_key"]
            s_key = sc["selection_key"]

            cursor.execute("""
                SELECT ms.id, ms.market_id FROM market_selections ms
                JOIN markets mkt ON ms.market_id = mkt.id
                WHERE mkt.match_id = ? AND mkt.market_key = ? AND ms.selection_key = ?
            """, (match_id, m_key, s_key))
            sel_row = cursor.fetchone()
            if sel_row:
                set_odds(sel_row["market_id"], s_key, target_odd, admin_id=0, reason="Lab Scenario Prep")

            col_map = {
                ("1x2", "p1"): "odd_p1",
                ("1x2", "x"): "odd_x",
                ("1x2", "p2"): "odd_p2",
                ("total_goals", "over_2.5"): "odd_tb25",
                ("total_goals", "under_2.5"): "odd_tm25",
                ("btts", "btts_yes"): "odd_btts_yes",
                ("btts", "btts_no"): "odd_btts_no",
            }
            legacy_col = col_map.get((m_key, s_key))
            if legacy_col:
                cursor.execute(f"UPDATE bet_markets SET {legacy_col} = ?, is_active = 1 WHERE match_id = ?", (target_odd, match_id))

    _set_lab_config("prepared_match_id", str(match_id))
    if scenario_id:
        _set_lab_config("active_scenario", scenario_id)
    _set_lab_config("current_step", "3")

    return {
        "status": "ok",
        "match_id": match_id,
        "match_status": "open",
        "home_team": m["player1_team"],
        "away_team": m["player2_team"],
        "round_number": round_num,
        "expected_score": exp_score_str,
        "next_action": "Откройте Logovo.bet и вручную сделайте ставку на этот матч.",
    }


def apply_scenario(scenario_id: str) -> dict[str, Any]:
    """One-click apply for a preset scenario."""
    sc = PRESET_SCENARIOS.get(scenario_id)
    if not sc:
        raise ValueError(f"Unknown scenario '{scenario_id}'.")

    target_match = find_match_for_scenario(scenario_id)
    if not target_match:
        create_test_season()
        target_match = find_match_for_scenario(scenario_id)
        if not target_match:
            raise RuntimeError("Не удалось найти подходящий матч для сценария даже после создания сезона.")

    prep_res = prepare_match_for_test(
        match_id=target_match["id"],
        scenario_id=scenario_id,
        custom_odds=sc["target_odds"],
        custom_score=sc["expected_score"],
    )

    return {
        "status": "ok",
        "scenario": sc,
        "preparation": prep_res,
        "instructions": (
            f"Матч #{target_match['id']} ({sc['home_team']} — {sc['away_team']}) подготовлен!\n"
            f"Откройте обычный Logovo.bet, выберите {sc['title']} ({sc['badge']}) "
            f"с коэффициентом {sc['target_odds']:.2f} и сделайте ставку.\n"
            f"Лаборатория автоматически зафиксирует ваш купон в базе данных."
        ),
    }


# ─── Match Lifecycle & Live Controls ────────────────────────────────────────

def transition_match_lifecycle(match_id: int, target_status: str) -> dict[str, Any]:
    """
    Transition match status safely using existing lifecycle mechanisms:
    SCHEDULED -> OPEN -> LIVE -> FINISHED -> SETTLED.
    """
    target = target_status.lower()

    if target in ("live", "in_progress"):
        transition_live_match(match_id, LIVE, source="lab", force=True)
    elif target in ("halftime", "ht"):
        transition_live_match(match_id, HALFTIME, source="lab", force=True)
    elif target in ("finished", "completed"):
        transition_live_match(match_id, FINISHED, source="lab", force=True)
    else:
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE matches SET status = ? WHERE id = ?", (target, match_id))

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, player1_score, player2_score FROM matches WHERE id = ?", (match_id,))
        updated = cursor.fetchone()

    return {
        "status": "ok",
        "match_id": match_id,
        "new_status": updated["status"] if updated else target,
    }


def send_live_event_action(
    match_id: int,
    action: str,
    side: str = "home",
    minute: Optional[int] = None,
) -> dict[str, Any]:
    """
    Dispatch real-time live event (+goal, cards, halftime) through services.live_ingestion.
    """
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT player1_team, player2_team, live_minute, player1_score, player2_score FROM matches WHERE id = ?", (match_id,))
        m = cursor.fetchone()
        if not m:
            raise ValueError(f"Match #{match_id} not found.")

    curr_min = minute if minute is not None else (m["live_minute"] or 15)
    team_name = m["player1_team"] if side == "home" else m["player2_team"]

    ev_id = f"lab_{match_id}_{action}_{side}_{curr_min}_{random.randint(1000, 9999)}"

    if action == "goal":
        ev = LiveEvent(
            match_id=match_id,
            provider="lab",
            provider_event_id=ev_id,
            event_type="goal",
            minute=curr_min,
            team_name=team_name,
            payload={"side": side},
        )
    elif action == "yellow_card":
        ev = LiveEvent(
            match_id=match_id,
            provider="lab",
            provider_event_id=ev_id,
            event_type="yellow_card",
            minute=curr_min,
            team_name=team_name,
            payload={"side": side},
        )
    elif action == "red_card":
        ev = LiveEvent(
            match_id=match_id,
            provider="lab",
            provider_event_id=ev_id,
            event_type="red_card",
            minute=curr_min,
            team_name=team_name,
            payload={"side": side},
        )
    elif action == "halftime":
        ev = LiveEvent(
            match_id=match_id,
            provider="lab",
            provider_event_id=ev_id,
            event_type="halftime",
            minute=45,
            payload={},
        )
    elif action == "fulltime":
        ev = LiveEvent(
            match_id=match_id,
            provider="lab",
            provider_event_id=ev_id,
            event_type="match_finished",
            minute=90,
            payload={},
        )
    else:
        raise ValueError(f"Unknown live action '{action}'.")

    result = ingest_live_event(ev)

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT player1_score, player2_score, live_minute, status FROM matches WHERE id = ?", (match_id,))
        fresh_m = cursor.fetchone()
        cursor.execute("SELECT status FROM live_match_states WHERE match_id = ?", (match_id,))
        live_s = cursor.fetchone()

    match_st = fresh_m["status"] if fresh_m else "unknown"
    if live_s and live_s["status"] == "HALFTIME":
        match_st = "halftime"
    elif action == "halftime":
        match_st = "halftime"

    return {
        "status": "ok",
        "action": action,
        "match_id": match_id,
        "ingest_result": result,
        "current_score": f"{fresh_m['player1_score'] or 0}:{fresh_m['player2_score'] or 0}",
        "live_minute": fresh_m["live_minute"],
        "match_status": match_st,
    }


def set_match_result_and_settle(
    match_id: int,
    score1: int,
    score2: int,
    confirm_and_settle: bool = True,
) -> dict[str, Any]:
    """
    Save final score and execute official settlement via services.settlement_engine.settle_match_predictions.
    Strictly uses existing betting & payout engine without direct wallet mutations.
    """
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE matches
            SET player1_score = ?, player2_score = ?, status = 'finished'
            WHERE id = ?
        """, (score1, score2, match_id))

    settle_notifications = []
    if confirm_and_settle:
        settle_notifications = settle_match_predictions(
            match_id=match_id,
            score1=score1,
            score2=score2,
            match_status="finished"
        )
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE matches SET status = 'completed' WHERE id = ?", (match_id,))

    _set_lab_config("current_step", "7")

    return {
        "status": "ok",
        "match_id": match_id,
        "score": f"{score1}:{score2}",
        "settled": confirm_and_settle,
        "payouts_count": len(settle_notifications),
        "notifications": settle_notifications,
    }


# ─── Manual Bet Detection & Step Tracker ─────────────────────────────────────

def check_manual_bet(match_id: Optional[int] = None, user_id: Optional[int] = None) -> dict[str, Any]:
    """
    Detect whether the tester placed a real manual bet from Logovo.bet UI.
    Inspects user_bets and bet_items in database.
    """
    target_uid = user_id or get_active_test_user_id()
    target_mid = match_id
    if target_mid is None:
        raw_mid = _get_lab_config("prepared_match_id", "")
        if raw_mid.isdigit():
            target_mid = int(raw_mid)

    with database.transaction() as conn:
        cursor = conn.cursor()

        query = """
            SELECT ub.*, bi.match_id, bi.outcome_type, bi.odd, bi.status as item_status,
                   m.player1_team, m.player2_team, m.round_number
            FROM user_bets ub
            JOIN bet_items bi ON ub.id = bi.bet_id
            JOIN matches m ON bi.match_id = m.id
            WHERE ub.user_id = ?
        """
        params = [target_uid]

        if target_mid is not None:
            query += " AND bi.match_id = ?"
            params.append(target_mid)

        query += " ORDER BY ub.id DESC LIMIT 1"
        cursor.execute(query, params)
        row = cursor.fetchone()

        if row:
            bet_dict = dict(row)
            return {
                "detected": True,
                "bet_id": bet_dict["id"],
                "match_id": bet_dict["match_id"],
                "match": f"{bet_dict['player1_team']} — {bet_dict['player2_team']}",
                "round": bet_dict["round_number"],
                "selection": bet_dict["outcome_type"],
                "odds": float(bet_dict["odd"]),
                "stake": bet_dict["amount"],
                "potential_payout": bet_dict["potential_win"],
                "actual_payout": bet_dict["actual_payout"],
                "status": bet_dict["status"],
                "created_at": bet_dict["created_at"],
            }

        return {"detected": False, "message": "Ставка пока не обнаружена."}


def get_step_tracker_status(user_id: Optional[int] = None) -> dict[str, Any]:
    """
    Step-by-step interactive testing guide status.
    """
    target_uid = user_id or get_active_test_user_id()
    season = get_test_season()
    prepared_mid_str = _get_lab_config("prepared_match_id", "")
    prepared_mid = int(prepared_mid_str) if prepared_mid_str.isdigit() else None

    step_season = bool(season)
    step_match_prep = False
    step_bet_placed = False
    step_match_started = False
    step_result_set = False
    step_settled = False
    step_verified = False

    active_match_dict = None
    detected_bet = None

    if prepared_mid:
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM matches WHERE id = ?", (prepared_mid,))
            m_r = cursor.fetchone()
            if m_r:
                active_match_dict = dict(m_r)
                step_match_prep = True
                m_status = m_r["status"]
                if m_status in ("live", "in_progress", "halftime", "finished", "completed"):
                    step_match_started = True
                if m_r["player1_score"] is not None and m_r["player2_score"] is not None:
                    step_result_set = True
                if m_status in ("completed", "settled") or (m_status == "finished" and not step_result_set):
                    step_settled = True

        bet_check = check_manual_bet(prepared_mid, target_uid)
        if bet_check["detected"]:
            step_bet_placed = True
            detected_bet = bet_check
            if bet_check["status"] in ("won", "lost", "refunded"):
                step_settled = True
                step_verified = True

    if not step_season:
        curr_step = 1
        curr_instruction = "Нажмите «Создать тестовый сезон», чтобы сгенерировать 16 команд, 30 туров и 240 матчей."
    elif not step_match_prep:
        curr_step = 2
        curr_instruction = "Выберите быстрый сценарий (например, «HOME WIN») или нажмите «Подготовить к тесту» у любого матча."
    elif not step_bet_placed:
        curr_step = 3
        team1 = active_match_dict['player1_team'] if active_match_dict else "Команда 1"
        team2 = active_match_dict['player2_team'] if active_match_dict else "Команда 2"
        curr_instruction = (
            f"Откройте обычный Logovo.bet, перейдите к матчу #{prepared_mid} "
            f"({team1} — {team2}) "
            f"и сделайте ставку руками. Лаборатория сама заметит её в БД."
        )
    elif not step_match_started:
        curr_step = 4
        curr_instruction = (
            f"Ставка обнаружена! (Купон #{detected_bet['bet_id']}, ставка {detected_bet['stake']} 🪙). "
            f"Нажмите «Начать Live» или сразу перейдите к установке счета."
        )
    elif not step_result_set:
        curr_step = 5
        curr_instruction = f"Укажите итоговый счет матча #{prepared_mid} и нажмите «Сохранить результат»."
    elif not step_settled:
        curr_step = 6
        curr_instruction = "Нажмите «Подтвердить и рассчитать (Settle)», чтобы запустить существующий букмекерский расчет."
    else:
        curr_step = 7
        curr_instruction = "Матч рассчитан! Перейдите во вкладку «Финансовый аудит» и проверьте формулу баланса."

    return {
        "current_step": curr_step,
        "instruction": curr_instruction,
        "steps": [
            {"num": 1, "title": "Сезон создан", "completed": step_season},
            {"num": 2, "title": "Матч подготовлен", "completed": step_match_prep, "match_id": prepared_mid},
            {"num": 3, "title": "Ручная ставка игрока", "completed": step_bet_placed, "bet": detected_bet},
            {"num": 4, "title": "Матч запущен (Live)", "completed": step_match_started},
            {"num": 5, "title": "Результат установлен", "completed": step_result_set},
            {"num": 6, "title": "Расчет (Settlement)", "completed": step_settled},
            {"num": 7, "title": "Проверка баланса", "completed": step_verified},
        ],
        "prepared_match": active_match_dict,
        "detected_bet": detected_bet,
    }


# ─── Financial Reconciliation ───────────────────────────────────────────────

def get_financial_reconciliation(user_id: Optional[int] = None) -> dict[str, Any]:
    """
    Mathematical balance audit for test user:
    Formula:
        Initial Balance (100,000)
      - Stakes
      + Payouts
      + Refunds
      + Cashout
      = Expected Balance
    Check:
        Expected Balance == Actual Balance
    """
    target_uid = user_id or get_active_test_user_id()

    with database.transaction() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (target_uid,))
        w_row = cursor.fetchone()
        actual_balance = w_row["balance"] if w_row else INITIAL_TEST_BALANCE

        cursor.execute("""
            SELECT amount FROM coin_transactions
            WHERE user_id = ? AND transaction_type IN ('initial_test_balance', 'welcome_bonus')
            ORDER BY id ASC LIMIT 1
        """, (target_uid,))
        init_row = cursor.fetchone()
        initial_balance = init_row["amount"] if init_row else INITIAL_TEST_BALANCE

        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) as stakes
            FROM coin_transactions
            WHERE user_id = ? AND transaction_type IN ('bet_placed', 'bet')
        """, (target_uid,))
        total_stakes = abs(cursor.fetchone()["stakes"])

        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) as payouts
            FROM coin_transactions
            WHERE user_id = ? AND transaction_type IN ('bet_won', 'payout')
        """, (target_uid,))
        total_payouts = cursor.fetchone()["payouts"]

        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) as refunds
            FROM coin_transactions
            WHERE user_id = ? AND transaction_type IN ('refund', 'bet_refund')
        """, (target_uid,))
        total_refunds = cursor.fetchone()["refunds"]

        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) as cashouts
            FROM coin_transactions
            WHERE user_id = ? AND transaction_type IN ('cashout', 'bet_cashout')
        """, (target_uid,))
        total_cashouts = cursor.fetchone()["cashouts"]

        expected_balance = initial_balance - total_stakes + total_payouts + total_refunds + total_cashouts
        difference = actual_balance - expected_balance

        cursor.execute("""
            SELECT * FROM coin_transactions
            WHERE user_id = ?
            ORDER BY id DESC LIMIT 50
        """, (target_uid,))
        txs = [dict(r) for r in cursor.fetchall()]

    is_ok = (difference == 0)
    badge = "🟢 BALANCE OK" if is_ok else "🔴 FINANCIAL MISMATCH"

    return {
        "status": "ok" if is_ok else "mismatch",
        "badge": badge,
        "initial_balance": initial_balance,
        "total_stakes": total_stakes,
        "total_payouts": total_payouts,
        "total_refunds": total_refunds,
        "total_cashouts": total_cashouts,
        "expected_balance": expected_balance,
        "actual_balance": actual_balance,
        "difference": difference,
        "net_profit_loss": (total_payouts + total_cashouts + total_refunds) - total_stakes,
        "transactions_count": len(txs),
        "recent_transactions": txs,
    }


# ─── Teams Standings Table ───────────────────────────────────────────────────

def get_teams_standings() -> list[dict[str, Any]]:
    """
    Calculate dynamic standings for the 16 synthetic teams based on completed matches.
    Table fields: Team | Played | Wins | Draws | Losses | GF | GA | GD | Points.
    """
    division = get_test_division()
    season = get_test_season()
    if not division or not season:
        return []

    div_id = division["id"]
    s_id = season["id"]

    table: dict[str, dict[str, Any]] = {}
    for t_name in SYNTHETIC_TEAMS:
        table[t_name] = {
            "team": t_name,
            "played": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "gf": 0,
            "ga": 0,
            "gd": 0,
            "points": 0,
        }

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT player1_team, player2_team, player1_score, player2_score
            FROM matches
            WHERE division_id = ? AND season_id = ?
              AND status IN ('confirmed', 'completed', 'finished')
              AND player1_score IS NOT NULL AND player2_score IS NOT NULL
        """, (div_id, s_id))
        completed_matches = cursor.fetchall()

        for m in completed_matches:
            h = m["player1_team"]
            a = m["player2_team"]
            s1 = m["player1_score"]
            s2 = m["player2_score"]

            if h not in table:
                table[h] = {"team": h, "played": 0, "wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0, "gd": 0, "points": 0}
            if a not in table:
                table[a] = {"team": a, "played": 0, "wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0, "gd": 0, "points": 0}

            table[h]["played"] += 1
            table[a]["played"] += 1
            table[h]["gf"] += s1
            table[h]["ga"] += s2
            table[a]["gf"] += s2
            table[a]["ga"] += s1

            if s1 > s2:
                table[h]["wins"] += 1
                table[h]["points"] += 3
                table[a]["losses"] += 1
            elif s1 < s2:
                table[a]["wins"] += 1
                table[a]["points"] += 3
                table[h]["losses"] += 1
            else:
                table[h]["draws"] += 1
                table[a]["draws"] += 1
                table[h]["points"] += 1
                table[a]["points"] += 1

    standings = list(table.values())
    for row in standings:
        row["gd"] = row["gf"] - row["ga"]

    standings.sort(key=lambda x: (-x["points"], -x["gd"], -x["gf"], x["team"]))
    for rank, row in enumerate(standings, 1):
        row["rank"] = rank

    return standings


# ─── Season Control & Rounds Management ──────────────────────────────────────

def get_season_control_overview() -> dict[str, Any]:
    """Provide detailed status of all 30 rounds in test season."""
    division = get_test_division()
    season = get_test_season()
    if not division or not season:
        return {"status": "not_created", "rounds": []}

    div_id = division["id"]
    s_id = season["id"]

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.round_number, r.is_open, r.deadline,
                   COUNT(m.id) as matches_count,
                   SUM(CASE WHEN m.status IN ('completed', 'confirmed', 'finished') THEN 1 ELSE 0 END) as completed,
                   SUM(CASE WHEN m.status IN ('live', 'in_progress', 'halftime') THEN 1 ELSE 0 END) as live,
                   SUM(CASE WHEN m.status = 'open' THEN 1 ELSE 0 END) as open_cnt,
                   SUM(CASE WHEN m.status = 'scheduled' THEN 1 ELSE 0 END) as scheduled
            FROM rounds r
            LEFT JOIN matches m ON r.division_id = m.division_id AND r.season_id = m.season_id AND r.round_number = m.round_number
            WHERE r.division_id = ? AND r.season_id = ?
            GROUP BY r.round_number, r.is_open, r.deadline
            ORDER BY r.round_number ASC
        """, (div_id, s_id))
        rows = cursor.fetchall()

    rounds_data = []
    for r in rows:
        rounds_data.append({
            "round_number": r["round_number"],
            "is_open": bool(r["is_open"]),
            "deadline": r["deadline"],
            "matches_count": r["matches_count"] or 0,
            "completed": r["completed"] or 0,
            "live": r["live"] or 0,
            "open": r["open_cnt"] or 0,
            "scheduled": r["scheduled"] or 0,
        })

    return {
        "status": "ok",
        "season_name": TEST_SEASON_NAME,
        "division_name": TEST_DIVISION_NAME,
        "total_rounds": len(rounds_data),
        "rounds": rounds_data,
    }


def manage_round_action(round_number: int, action: str) -> dict[str, Any]:
    """Execute lifecycle action on a test round (open, close, complete)."""
    division = get_test_division()
    season = get_test_season()
    if not division or not season:
        raise ValueError("Test season not initialized.")

    div_id = division["id"]
    s_id = season["id"]

    with database.transaction() as conn:
        cursor = conn.cursor()

        if action == "open":
            cursor.execute("""
                UPDATE rounds SET is_open = 1
                WHERE division_id = ? AND season_id = ? AND round_number = ?
            """, (div_id, s_id, round_number))
            cursor.execute("""
                UPDATE matches SET status = 'open'
                WHERE division_id = ? AND season_id = ? AND round_number = ? AND status = 'scheduled'
            """, (div_id, s_id, round_number))
            cursor.execute("""
                UPDATE bet_markets SET is_active = 1
                WHERE match_id IN (
                    SELECT id FROM matches WHERE division_id = ? AND season_id = ? AND round_number = ?
                )
            """, (div_id, s_id, round_number))

        elif action == "close":
            cursor.execute("""
                UPDATE rounds SET is_open = 0
                WHERE division_id = ? AND season_id = ? AND round_number = ?
            """, (div_id, s_id, round_number))
            cursor.execute("""
                UPDATE bet_markets SET is_active = 0
                WHERE match_id IN (
                    SELECT id FROM matches WHERE division_id = ? AND season_id = ? AND round_number = ?
                )
            """, (div_id, s_id, round_number))

        elif action == "complete":
            cursor.execute("""
                SELECT id, stadium FROM matches
                WHERE division_id = ? AND season_id = ? AND round_number = ?
                  AND status NOT IN ('completed', 'confirmed')
            """, (div_id, s_id, round_number))
            uncompleted = cursor.fetchall()

            for m in uncompleted:
                m_id = m["id"]
                stad = m["stadium"] or ""
                score1, score2 = 1, 0
                if "Expected:" in stad:
                    try:
                        raw_sc = stad.split("Expected:")[1].split()[0]
                        s1_str, s2_str = raw_sc.split(":")
                        score1, score2 = int(s1_str), int(s2_str)
                    except Exception:
                        pass
                settle_match_predictions(m_id, score1, score2, match_status="finished")
                cursor.execute("UPDATE matches SET status = 'completed' WHERE id = ?", (m_id,))

            cursor.execute("""
                UPDATE rounds SET is_open = 0
                WHERE division_id = ? AND season_id = ? AND round_number = ?
            """, (div_id, s_id, round_number))

        else:
            raise ValueError(f"Unknown round action '{action}'.")

    return {
        "status": "ok",
        "round_number": round_number,
        "action": action,
    }


# ─── Match Details & Filtering ──────────────────────────────────────────────

def get_lab_matches(
    round_number: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Retrieve matches from test division with rich markets & odds info."""
    division = get_test_division()
    season = get_test_season()
    if not division or not season:
        return []

    div_id = division["id"]
    s_id = season["id"]

    with database.transaction() as conn:
        cursor = conn.cursor()
        query = """
            SELECT m.*, 
                   COALESCE(m.player1_team, 'Хозяева') as team1_name,
                   COALESCE(m.player2_team, 'Гости') as team2_name,
                   bm.odd_p1, bm.odd_x, bm.odd_p2,
                   bm.odd_tb25, bm.odd_tm25, bm.odd_btts_yes, bm.odd_btts_no
            FROM matches m
            LEFT JOIN bet_markets bm ON m.id = bm.match_id
            WHERE m.division_id = ? AND m.season_id = ?
        """
        params = [div_id, s_id]

        if round_number:
            query += " AND m.round_number = ?"
            params.append(round_number)

        if status:
            query += " AND m.status = ?"
            params.append(status)

        query += " ORDER BY m.round_number ASC, m.id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        rows = cursor.fetchall()
        matches = []

        for r in rows:
            m_dict = dict(r)
            m_id = m_dict["id"]

            cursor.execute("""
                SELECT ms.selection_key, ms.odds_value, mkt.market_key
                FROM market_selections ms
                JOIN markets mkt ON ms.market_id = mkt.id
                WHERE mkt.match_id = ? AND ms.status = 'active'
            """, (m_id,))
            selections = cursor.fetchall()
            odds_map = {}
            for sel in selections:
                odds_map[sel["selection_key"]] = float(sel["odds_value"])

            m_dict["odds"] = {
                "p1": odds_map.get("p1") or m_dict.get("odd_p1") or 2.00,
                "x": odds_map.get("x") or m_dict.get("odd_x") or 3.20,
                "p2": odds_map.get("p2") or m_dict.get("odd_p2") or 2.50,
                "over_2.5": odds_map.get("over_2.5") or m_dict.get("odd_tb25") or 1.85,
                "under_2.5": odds_map.get("under_2.5") or m_dict.get("odd_tm25") or 1.95,
                "btts_yes": odds_map.get("btts_yes") or m_dict.get("odd_btts_yes") or 1.75,
                "btts_no": odds_map.get("btts_no") or m_dict.get("odd_btts_no") or 2.05,
            }

            stadium_text = m_dict.get("stadium") or ""
            exp_str = ""
            if "Expected:" in stadium_text:
                exp_str = stadium_text.split("Expected:")[1].split()[0]
            m_dict["expected_score"] = exp_str

            matches.append(m_dict)

        return matches


def get_lab_match_detail(match_id: int) -> Optional[dict[str, Any]]:
    """Fetch single match full detail with relational markets & live events."""
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
        m_row = cursor.fetchone()
        if not m_row:
            return None

        m_dict = dict(m_row)

        cursor.execute("SELECT * FROM markets WHERE match_id = ? ORDER BY sort_order ASC", (match_id,))
        markets = [dict(m) for m in cursor.fetchall()]
        for mkt in markets:
            cursor.execute("SELECT * FROM market_selections WHERE market_id = ?", (mkt["id"],))
            mkt["selections"] = [dict(s) for s in cursor.fetchall()]

        m_dict["markets"] = markets

        cursor.execute("SELECT * FROM live_match_states WHERE match_id = ?", (match_id,))
        live_st = cursor.fetchone()
        m_dict["live_state"] = dict(live_st) if live_st else None

        cursor.execute("SELECT * FROM live_events WHERE match_id = ? ORDER BY minute ASC, id ASC", (match_id,))
        m_dict["live_events"] = [dict(e) for e in cursor.fetchall()]

        target_uid = get_active_test_user_id()
        cursor.execute("""
            SELECT ub.*, bi.outcome_type, bi.odd, bi.status as item_status
            FROM user_bets ub
            JOIN bet_items bi ON ub.id = bi.bet_id
            WHERE bi.match_id = ? AND ub.user_id = ?
            ORDER BY ub.id DESC
        """, (match_id, target_uid))
        m_dict["my_bets"] = [dict(b) for b in cursor.fetchall()]

        return m_dict


def get_test_player_bets(user_id: Optional[int] = None, limit: int = 50) -> list[dict[str, Any]]:
    """Retrieve slips placed by test player."""
    target_uid = user_id or get_active_test_user_id()
    return database.get_user_bets(target_uid, limit=limit)
