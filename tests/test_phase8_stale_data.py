"""
tests/test_phase8_stale_data.py

Phase 8 — Stale Data Protection & Freshness Policy Test Suite.
Verifies:
1. Freshness evaluation thresholds (FRESH, STALE, EXPIRED, UNAVAILABLE).
2. Badge strings (🟢 LIVE DATA FRESH, 🟡 DATA DELAYED, 🔴 LIVE DATA UNAVAILABLE).
3. Degradation / discounting of AI confidence on stale and expired feeds.
4. Counting stale matches in SQLite database.
"""

import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import database
from services.sports.freshness import (
    evaluate_match_freshness,
    FRESH,
    STALE,
    EXPIRED,
    UNAVAILABLE
)


class TestPhase8StaleData(unittest.TestCase):
    """Verification of stale data policies and confidence discounts."""

    def setUp(self) -> None:
        database.init_db()

    def test_fresh_data_evaluation(self) -> None:
        """Feed updated 30 seconds ago is FRESH with full confidence (1.0)."""
        now = datetime.now(timezone.utc)
        recent_update = (now - timedelta(seconds=30)).isoformat()

        res = evaluate_match_freshness(recent_update, stale_threshold=120, expired_threshold=300)
        self.assertEqual(res["status"], FRESH)
        self.assertEqual(res["badge"], "🟢 LIVE DATA FRESH")
        self.assertEqual(res["confidence_multiplier"], 1.0)
        self.assertFalse(res["is_stale"])
        self.assertFalse(res["is_expired"])
        self.assertAlmostEqual(res["age_seconds"], 30.0, delta=2.0)

    def test_stale_data_evaluation(self) -> None:
        """Feed updated 180 seconds ago is STALE with 0.85 confidence multiplier."""
        now = datetime.now(timezone.utc)
        stale_update = (now - timedelta(seconds=180)).isoformat()

        res = evaluate_match_freshness(stale_update, stale_threshold=120, expired_threshold=300)
        self.assertEqual(res["status"], STALE)
        self.assertEqual(res["badge"], "🟡 DATA DELAYED")
        self.assertEqual(res["confidence_multiplier"], 0.70)
        self.assertTrue(res["is_stale"])
        self.assertFalse(res["is_expired"])
        self.assertAlmostEqual(res["age_seconds"], 180.0, delta=2.0)

    def test_expired_data_evaluation(self) -> None:
        """Feed updated 450 seconds ago is EXPIRED with 0.40 confidence multiplier."""
        now = datetime.now(timezone.utc)
        expired_update = (now - timedelta(seconds=450)).isoformat()

        res = evaluate_match_freshness(expired_update, stale_threshold=120, expired_threshold=300)
        self.assertEqual(res["status"], EXPIRED)
        self.assertEqual(res["badge"], "🔴 LIVE DATA UNAVAILABLE")
        self.assertEqual(res["confidence_multiplier"], 0.40)

        self.assertTrue(res["is_stale"])
        self.assertTrue(res["is_expired"])

    def test_missing_timestamp_unavailable(self) -> None:
        """None timestamp is marked as UNAVAILABLE with 0.0 confidence multiplier."""
        res = evaluate_match_freshness(None)
        self.assertEqual(res["status"], UNAVAILABLE)
        self.assertEqual(res["badge"], "🔴 LIVE DATA UNAVAILABLE")
        self.assertEqual(res["confidence_multiplier"], 0.0)
        self.assertIsNone(res["age_seconds"])

    def test_database_stale_matches_count(self) -> None:
        """Database correctly counts live matches that have exceeded stale threshold."""
        match_id = 99881
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM live_match_states WHERE match_id = ?", (match_id,))
            cursor.execute("DELETE FROM matches WHERE id = ?", (match_id,))

            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, status)
                VALUES (?, 1, 1, 1, 'Arsenal', 'Chelsea', 'active')
            """, (match_id,))

            # Insert live match with last_updated_at 5 minutes in the past
            cursor.execute("""
                INSERT INTO live_match_states (match_id, season_id, division_id, status, period, minute, home_score, away_score, provider, last_updated_at)
                VALUES (?, 1, 1, 'LIVE', '1h', 20, 0, 0, 'mock', datetime('now', '-300 seconds'))
            """, (match_id,))

        stale_count = database.get_stale_provider_matches_count(stale_threshold_seconds=120)
        self.assertGreaterEqual(stale_count, 1)
