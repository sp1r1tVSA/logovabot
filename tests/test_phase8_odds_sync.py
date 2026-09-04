"""
tests/test_phase8_odds_sync.py

Phase 8 — Provider Odds Synchronization & Validation Test Suite.
Verifies:
1. Strict numerical odds validation:
   - odds > 1.00, finite, non-NaN, non-Inf.
2. Market engine synchronization:
   - Increments odds_version, sets previous_odds, and logs immutable odds_movement.
3. Closed/Finished match protection:
   - Live odds sync refuses to mutate completed or voided matches.
4. Absolute Financial Read-Only Invariant:
   - Zero side effects on user wallets, bets, or settlement records.
"""

import math
import unittest

import database
from services.odds_engine import get_current_odds, get_or_create_market, get_or_create_selection
from services.sports.models import ProviderOdds
from services.sports.odds_sync import sync_provider_odds, validate_odd_value


class TestPhase8OddsSync(unittest.TestCase):
    """Test suite verifying safe ingestion of live provider odds into the market engine."""

    def setUp(self) -> None:
        database.init_db()
        self.match_id = 8901
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM odds_movement WHERE match_id = ?", (self.match_id,))
            cursor.execute("DELETE FROM odds_history WHERE selection_id IN (SELECT id FROM market_selections WHERE market_id IN (SELECT id FROM markets WHERE match_id = ?))", (self.match_id,))
            cursor.execute("DELETE FROM market_selections WHERE market_id IN (SELECT id FROM markets WHERE match_id = ?)", (self.match_id,))
            cursor.execute("DELETE FROM markets WHERE match_id = ?", (self.match_id,))
            cursor.execute("DELETE FROM matches WHERE id = ?", (self.match_id,))

            # Seed match in league
            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, status)
                VALUES (?, 1, 1, 1, 'Liverpool', 'Manchester City', 'active')
            """, (self.match_id,))

    def test_odds_validation_rules(self) -> None:
        """validate_odd_value accepts valid decimals and rejects invalid, NaN, and Inf."""
        self.assertEqual(validate_odd_value(1.95), 1.95)
        self.assertEqual(validate_odd_value("2.10"), 2.10)
        self.assertEqual(validate_odd_value(10.0), 10.0)

        # Rejections
        with self.assertRaises(ValueError):
            validate_odd_value(1.00)  # Must be > 1.00

        with self.assertRaises(ValueError):
            validate_odd_value(0.95)

        with self.assertRaises(ValueError):
            validate_odd_value(float("nan"))

        with self.assertRaises(ValueError):
            validate_odd_value(float("inf"))

        with self.assertRaises(ValueError):
            validate_odd_value(None)

        with self.assertRaises(ValueError):
            validate_odd_value("invalid_odd_string")

    def test_sync_provider_odds_increments_version_and_tracks_movement(self) -> None:
        """Provider odds sync increments version, updates previous_odds, and logs movement."""
        odds_item = ProviderOdds(
            match_id=self.match_id,
            provider="api_sports",
            bookmaker_name="Bet365",
            market_key="match_result",
            market_name="1X2",
            selections=[
                {"selection_key": "home", "name": "П1", "odds": 2.15},
                {"selection_key": "draw", "name": "X", "odds": 3.40},
                {"selection_key": "away", "name": "П2", "odds": 3.10}
            ]
        )

        # 1. Initial Sync
        res1 = sync_provider_odds(self.match_id, [odds_item], provider_name="api_sports")
        self.assertEqual(res1["status"], "ok")
        self.assertEqual(res1["synced_count"], 3)

        # Verify initial values
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ms.odds_value, ms.odds_version, ms.previous_odds
                FROM market_selections ms
                JOIN markets m ON ms.market_id = m.id
                WHERE m.match_id = ? AND ms.selection_key = 'home'
            """, (self.match_id,))
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["odds_value"], 2.15)
            self.assertEqual(row["odds_version"], 1)

        # 2. Update Sync with changed odds
        odds_update = ProviderOdds(
            match_id=self.match_id,
            provider="api_sports",
            bookmaker_name="Bet365",
            market_key="match_result",
            market_name="1X2",
            selections=[
                {"selection_key": "home", "name": "П1", "odds": 1.90}  # Drop from 2.15 to 1.90
            ]
        )
        res2 = sync_provider_odds(self.match_id, [odds_update], provider_name="api_sports")
        self.assertEqual(res2["status"], "ok")
        self.assertEqual(res2["synced_count"], 1)

        # Verify version increment and previous odds
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ms.odds_value, ms.odds_version, ms.previous_odds
                FROM market_selections ms
                JOIN markets m ON ms.market_id = m.id
                WHERE m.match_id = ? AND ms.selection_key = 'home'
            """, (self.match_id,))
            updated_row = cursor.fetchone()
            self.assertEqual(updated_row["odds_value"], 1.90)
            self.assertEqual(updated_row["previous_odds"], 2.15)
            self.assertEqual(updated_row["odds_version"], 2)

            # Verify odds_movement record
            cursor.execute("""
                SELECT * FROM odds_movement
                WHERE match_id = ? ORDER BY id DESC LIMIT 1
            """, (self.match_id,))
            mv = cursor.fetchone()
            self.assertIsNotNone(mv)
            self.assertEqual(mv["direction"], "down")
            self.assertEqual(mv["old_odds"], 2.15)
            self.assertEqual(mv["new_odds"], 1.90)

    def test_sync_refuses_completed_matches(self) -> None:
        """Odds sync strictly skips matches that are completed or confirmed."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE matches SET status = 'completed' WHERE id = ?", (self.match_id,))

        odds_item = ProviderOdds(
            match_id=self.match_id,
            provider="api_sports",
            market_key="match_result",
            selections=[{"selection_key": "home", "odds": 1.50}]
        )

        res = sync_provider_odds(self.match_id, [odds_item])
        self.assertEqual(res["status"], "skipped")
        self.assertEqual(res["synced_count"], 0)

    def test_financial_read_only_invariant(self) -> None:
        """Odds sync must NEVER debit, credit, or alter wallets and bets."""
        user_id = 771122
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_wallets WHERE user_id = ?", (user_id,))
            cursor.execute("INSERT INTO user_wallets (user_id, balance) VALUES (?, 5000)", (user_id,))

        odds_item = ProviderOdds(
            match_id=self.match_id,
            provider="api_sports",
            market_key="match_result",
            selections=[{"selection_key": "home", "odds": 2.20}]
        )

        sync_provider_odds(self.match_id, [odds_item])

        # Verify wallet balance remained completely untouched
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT balance FROM user_wallets WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            self.assertEqual(row["balance"], 5000)
