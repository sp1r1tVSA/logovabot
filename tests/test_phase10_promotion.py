"""
tests/test_phase10_promotion.py

Phase 10: Promotion, Relegation & Qualification Zones Tests.
Strict Invariants:
1. Promotion slots and relegation slots are configurable per division and season.
2. Inactive players (< min_bets) cannot receive promotion.
3. Zones are cleanly tagged: PROMOTION, SAFE, RELEGATION, INACTIVE.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import database
from services.season_progression import SeasonProgressionEngine


@pytest.fixture(autouse=True)
def clean_promotion_data():
    database.init_db()
    with database.transaction() as conn:
        conn.execute("DELETE FROM season_snapshots WHERE season_id = 95")
        conn.execute("DELETE FROM season_player_stats WHERE season_id = 95")
        conn.execute("DELETE FROM season_rules_config WHERE season_id = 95")
        conn.execute("DELETE FROM seasons WHERE id = 95")
        conn.execute("DELETE FROM users WHERE telegram_id BETWEEN 5000 AND 5050")
    yield
    with database.transaction() as conn:
        conn.execute("DELETE FROM season_snapshots WHERE season_id = 95")
        conn.execute("DELETE FROM season_player_stats WHERE season_id = 95")
        conn.execute("DELETE FROM season_rules_config WHERE season_id = 95")
        conn.execute("DELETE FROM seasons WHERE id = 95")
        conn.execute("DELETE FROM users WHERE telegram_id BETWEEN 5000 AND 5050")


def test_01_promotion_and_relegation_zones():
    """
    In a division of 6 players with promotion_slots=2, relegation_slots=2, min_bets=5:
    Ranks 1-2 -> PROMOTION
    Ranks 3-4 -> SAFE
    Ranks 5-6 -> RELEGATION
    """
    database.set_season_rules(season_id=95, division_id=1, promotion_slots=2, relegation_slots=2, min_bets_qualification=5)

    # 6 active players
    for i in range(1, 7):
        uid = 5000 + i
        rating = 1800.0 - (i * 50)
        database.update_season_player_stats(
            user_id=uid, season_id=95, division_id=1,
            rating=rating, settled_bets=10, season_points=100.0 - i, status="ACTIVE"
        )

    standings = SeasonProgressionEngine.get_division_standings(season_id=95, division_id=1)
    assert len(standings) == 6

    assert standings[0]["zone"] == "PROMOTION"
    assert standings[1]["zone"] == "PROMOTION"
    assert standings[2]["zone"] == "SAFE"
    assert standings[3]["zone"] == "SAFE"
    assert standings[4]["zone"] == "RELEGATION"
    assert standings[5]["zone"] == "RELEGATION"


def test_02_inactive_player_excluded_from_promotion():
    """
    Verify a player with high rating but only 1 bet (< 5 min bets)
    is marked INACTIVE and does NOT get PROMOTION zone.
    """
    database.set_season_rules(season_id=95, division_id=1, promotion_slots=2, relegation_slots=2, min_bets_qualification=5)

    # Player 5001 has high rating but 1 bet
    database.update_season_player_stats(
        user_id=5001, season_id=95, division_id=1,
        rating=2000.0, settled_bets=1, season_points=50.0, status="QUALIFYING"
    )
    # Players 5002, 5003 are active with 10 bets
    database.update_season_player_stats(
        user_id=5002, season_id=95, division_id=1,
        rating=1600.0, settled_bets=10, season_points=200.0, status="ACTIVE"
    )
    database.update_season_player_stats(
        user_id=5003, season_id=95, division_id=1,
        rating=1500.0, settled_bets=10, season_points=180.0, status="ACTIVE"
    )

    standings = SeasonProgressionEngine.get_division_standings(season_id=95, division_id=1)

    inactive_player = next(p for p in standings if p["user_id"] == 5001)
    assert inactive_player["zone"] == "INACTIVE"
    assert inactive_player["is_qualified"] is False

    # Active players occupy top promotion slots
    assert standings[0]["user_id"] == 5002
    assert standings[0]["zone"] == "PROMOTION"
    assert standings[1]["user_id"] == 5003
    assert standings[1]["zone"] == "PROMOTION"


def test_03_custom_configurable_rules_per_division():
    """Verify different divisions can configure distinct promotion and relegation slots."""
    database.set_season_rules(season_id=95, division_id=1, promotion_slots=4, relegation_slots=1)
    database.set_season_rules(season_id=95, division_id=2, promotion_slots=1, relegation_slots=4)

    r1 = database.get_season_rules(season_id=95, division_id=1)
    r2 = database.get_season_rules(season_id=95, division_id=2)

    assert r1["promotion_slots"] == 4
    assert r1["relegation_slots"] == 1

    assert r2["promotion_slots"] == 1
    assert r2["relegation_slots"] == 4


def test_04_promotion_status_persists_in_snapshot():
    """Verify promotion status computed in standings matches snapshot records upon finalization."""
    with database.transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO seasons (id, name, status) VALUES (95, 'Promo Season', 'active')")
        conn.execute("INSERT OR IGNORE INTO users (telegram_id, username, role, division_id) VALUES (5010, 'u5010', 'user', 1), (5011, 'u5011', 'user', 1)")

    database.set_season_rules(season_id=95, division_id=1, promotion_slots=1, relegation_slots=1, min_bets_qualification=5)
    database.update_season_player_stats(user_id=5010, season_id=95, division_id=1, rating=1800.0, settled_bets=10, season_points=300.0)
    database.update_season_player_stats(user_id=5011, season_id=95, division_id=1, rating=1200.0, settled_bets=10, season_points=50.0)

    success, msg, res = SeasonProgressionEngine.finalize_season(season_id=95)
    assert success is True

    snaps = database.get_season_snapshots(season_id=95, division_id=1)
    p1 = next(s for s in snaps if s["user_id"] == 5010)
    p2 = next(s for s in snaps if s["user_id"] == 5011)

    assert p1["promotion_status"] == "PROMOTED"
    assert p2["promotion_status"] == "RELEGATED"


def test_05_relegation_boundary_condition():
    """Verify when total players <= promotion_slots + relegation_slots, safe zone size adjusts smoothly."""
    database.set_season_rules(season_id=95, division_id=1, promotion_slots=2, relegation_slots=2, min_bets_qualification=5)
    # Only 3 active players
    for i in range(1, 4):
        database.update_season_player_stats(
            user_id=5020 + i, season_id=95, division_id=1,
            rating=1500.0 - (i * 20), settled_bets=10, season_points=100.0
        )
    standings = SeasonProgressionEngine.get_division_standings(season_id=95, division_id=1)
    assert len(standings) == 3
    # Top 2 are promotion
    assert standings[0]["zone"] == "PROMOTION"
    assert standings[1]["zone"] == "PROMOTION"
