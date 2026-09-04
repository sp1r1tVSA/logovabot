"""
tests/test_phase9_odds.py

Phase 9 — Odds Validation & Movement Engine Test Suite.
Verifies numerical integrity, boundary protection (NaN, Inf, negatives, extremes),
odds movement classification, and automated anomaly RiskAlert emission.
"""

import math
import os
import tempfile
import unittest
import database
from services.odds_movers import classify_movement, record_odds_movement
from services.sports.odds_sync import validate_odd_value, sync_provider_odds
from services.sports.models import ProviderOdds


class TestPhase9Odds(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        database.DB_PATH = self._tmp.name
        database.init_db()
        database.ensure_canonical_divisions()

        self.match_id = 940001
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO seasons (name, status) VALUES ('Odds Season', 'active')")
            self.season_id = cursor.lastrowid
            cursor.execute("INSERT INTO rounds (division_id, round_number, season_id, is_open) VALUES (1, 1, ?, 1)", (self.season_id,))
            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, status)
                VALUES (?, 1, ?, 1, 'Arsenal', 'Chelsea', 'open')
            """, (self.match_id, self.season_id))

            cursor.execute("INSERT INTO markets (match_id, market_key, market_name, status) VALUES (?, 'match_result', 'Match Winner', 'open')", (self.match_id,))
            self.market_id = cursor.lastrowid
            cursor.execute("""
                INSERT INTO market_selections (market_id, selection_key, selection_name, odds_value, status, odds_version)
                VALUES (?, 'home', 'Arsenal', 2.00, 'active', 1)
            """, (self.market_id,))
            self.selection_id = cursor.lastrowid

    def tearDown(self):
        try:
            os.remove(self._tmp.name)
        except OSError:
            pass

    def test_p9_odds_01_valid_odds_update(self):
        """P9-ODDS-01: Ingesting valid odds increments odds_version and records movement."""
        self.assertEqual(validate_odd_value(2.25), 2.25)
        self.assertEqual(validate_odd_value("1.85"), 1.85)

        odds_item = ProviderOdds(
            match_id=self.match_id,
            provider="test_feed",
            market_key="match_result",
            selections=[{"selection_key": "home", "name": "Arsenal", "odds": 2.40}]
        )
        res = sync_provider_odds(self.match_id, [odds_item], provider_name="test_feed")
        self.assertEqual(res["synced_count"], 1)

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT odds_value, odds_version FROM market_selections WHERE id = ?", (self.selection_id,))
            row = cursor.fetchone()
            self.assertEqual(row["odds_value"], 2.40)
            self.assertGreater(row["odds_version"], 1)

    def test_p9_odds_02_rejection_of_nan_inf_and_negative(self):
        """P9-ODDS-02: validate_odd_value rejects NaN, Inf, zero, and negative values."""
        with self.assertRaises(ValueError):
            validate_odd_value(float("nan"))

        with self.assertRaises(ValueError):
            validate_odd_value(float("inf"))

        with self.assertRaises(ValueError):
            validate_odd_value(float("-inf"))

        with self.assertRaises(ValueError):
            validate_odd_value(-1.50)

        with self.assertRaises(ValueError):
            validate_odd_value(0.0)

        with self.assertRaises(ValueError):
            validate_odd_value(1.00)  # Must be strictly > 1.00

    def test_p9_odds_03_extreme_odds_protection(self):
        """P9-ODDS-03: Extreme odds (>1000 or <= 1.00) are rejected."""
        with self.assertRaises(ValueError):
            validate_odd_value(1001.00)

        with self.assertRaises(ValueError):
            validate_odd_value(50000.0)

        # 999.00 is allowed
        self.assertEqual(validate_odd_value(999.00), 999.00)

    def test_p9_odds_04_movement_classification(self):
        """P9-ODDS-04: classify_movement categorizes shifts into STABLE, MOVING, FAST_MOVE, ANOMALY."""
        self.assertEqual(classify_movement(pct_change=0.5, velocity=0.01), "STABLE")
        self.assertEqual(classify_movement(pct_change=3.0, velocity=0.05), "MOVING")
        self.assertEqual(classify_movement(pct_change=9.0, velocity=0.1), "FAST_MOVE")
        self.assertEqual(classify_movement(pct_change=2.0, velocity=0.25), "FAST_MOVE")
        self.assertEqual(classify_movement(pct_change=20.0, velocity=0.1), "ANOMALY")
        self.assertEqual(classify_movement(pct_change=5.0, velocity=0.6), "ANOMALY")

    def test_p9_odds_05_rapid_odds_anomaly_triggers_risk_alert(self):
        """P9-ODDS-05: Significant odds anomaly emits a deduplicated RiskAlert."""
        # Record massive shift: 2.00 -> 1.20 (40% drop)
        mov_id = record_odds_movement(
            selection_id=self.selection_id,
            market_id=self.market_id,
            match_id=self.match_id,
            old_odds=2.00,
            new_odds=1.20,
            reason="Sharp action",
            source="provider"
        )
        self.assertIsNotNone(mov_id)

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM risk_alerts WHERE alert_type = 'ODDS_ANOMALY'")
            alerts = cursor.fetchall()
            self.assertGreaterEqual(len(alerts), 1)
            alert = alerts[0]
            self.assertEqual(alert["match_id"], self.match_id)
            self.assertEqual(alert["severity"], "high")


if __name__ == "__main__":
    unittest.main()
