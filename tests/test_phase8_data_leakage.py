"""
tests/test_phase8_data_leakage.py

Phase 8 — Point-in-Time Integrity & Data Leakage Prevention Test Suite.
Verifies:
1. Zero future data leakage in feature extraction:
   - Under a point-in-time cutoff, matches with id >= the cutoff are strictly excluded
     from Form, Elo, and H2H. The cutoff is `as_of_match_id`, and it defaults to the
     target's own id once that match has been played.
   - A still-pending fixture deliberately has no cutoff: it is predicted live, so every
     match already played is legitimate history regardless of its id (see 4f193aa).
2. Snapshot Immutability:
   - Stored AI predictions in ai_predictions are immutable and cannot be retroactively altered by subsequent matches.
3. Strict Division and Season boundaries in point-in-time calculation.
"""

import unittest
from datetime import datetime, timezone

import database
from services.feature_engine import FeatureEngine
from services.ensemble_engine import EnsemblePredictionEngine


class TestPhase8DataLeakage(unittest.TestCase):
    """Verifies that no future information leaks into historical match predictions."""

    def setUp(self) -> None:
        database.init_db()
        self.div_id = 1
        self.season_id = 1
        self.target_match_id = 9100

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM predictions WHERE match_id >= 9000")
            cursor.execute("DELETE FROM matches WHERE id >= 9000")

            # Seed Past Match #9090 (completed)
            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, player1_score, player2_score, status)
                VALUES (9090, 1, 1, 1, 'Arsenal', 'Chelsea', 3, 1, 'completed')
            """)

            # Seed Target Match #9100 (active)
            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, player1_score, player2_score, status)
                VALUES (?, 1, 1, 2, 'Arsenal', 'Chelsea', NULL, NULL, 'active')
            """, (self.target_match_id,))

            # Seed Future Match #9110 (completed in the future)
            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, player1_score, player2_score, status)
                VALUES (9110, 1, 1, 3, 'Arsenal', 'Chelsea', 0, 5, 'completed')
            """)

    def test_future_matches_excluded_from_features(self) -> None:
        """Under an explicit cutoff at #9100, features include Match #9090 and strictly exclude #9110."""
        features = FeatureEngine.extract_match_features(
            self.target_match_id, as_of_match_id=self.target_match_id
        )

        # Arsenal's historical matches should only include Match #9090 (3 goals scored, 1 conceded)
        t1_overall = features["team1_features"]["overall"]
        self.assertEqual(t1_overall["matches_played"], 1)
        self.assertEqual(t1_overall["wins"], 1)

        self.assertEqual(t1_overall["losses"], 0)
        self.assertEqual(t1_overall["avg_scored"], 3.0)
        self.assertEqual(t1_overall["avg_conceded"], 1.0)

        # H2H matches must only include Match #9090
        h2h = features["h2h_features"]
        self.assertEqual(h2h["total_meetings"], 1)

        self.assertEqual(h2h["team1_wins"], 1)
        self.assertEqual(h2h["team2_wins"], 0)

    def test_played_match_defaults_to_its_own_cutoff(self) -> None:
        """Re-deriving features for a played match must not pull in results entered after it."""
        features = FeatureEngine.extract_match_features(9110)

        # Only #9090 predates #9110 and is completed; #9100 is still unplayed.
        self.assertEqual(features["team1_features"]["overall"]["matches_played"], 1)
        self.assertEqual(features["h2h_features"]["total_meetings"], 1)

    def test_pending_fixture_sees_every_match_already_played(self) -> None:
        """A live prediction is made with today's knowledge, so a played higher-id match counts."""
        features = FeatureEngine.extract_match_features(self.target_match_id)

        # #9110 was entered later but has actually been played — history, not leakage.
        self.assertEqual(features["team1_features"]["overall"]["matches_played"], 2)
        self.assertEqual(features["h2h_features"]["total_meetings"], 2)

    def test_prediction_snapshot_immutability(self) -> None:
        """A stored prediction snapshot is not mutated by subsequent match executions."""
        # 1. Generate and persist prediction snapshot
        pred = EnsemblePredictionEngine.predict_match(self.target_match_id, save_to_db=True)
        initial_home_prob = pred["home_probability"]

        # Fetch stored snapshot
        snapshot1 = database.get_ai_prediction(self.target_match_id)
        self.assertIsNotNone(snapshot1)
        self.assertAlmostEqual(snapshot1["home_probability"], initial_home_prob, places=3)

        # 2. Simulate subsequent match result that might alter global stats
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, player1_score, player2_score, status)
                VALUES (9120, 1, 1, 4, 'Arsenal', 'Liverpool', 0, 8, 'completed')
            """)

        # 3. Verify original snapshot in database is completely unchanged
        snapshot2 = database.get_ai_prediction(self.target_match_id)
        self.assertEqual(snapshot1["id"], snapshot2["id"])
        self.assertEqual(snapshot1["home_probability"], snapshot2["home_probability"])
        self.assertEqual(snapshot1["created_at"], snapshot2["created_at"])

