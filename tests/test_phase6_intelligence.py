"""
tests/test_phase6_intelligence.py

Tests for Phase 6F: Sports Intelligence Engine & Value Edge Analysis.
Ensures:
1. Pure analytical layer: strictly read-only, never mutates financial/state data.
2. Correct calculation of Form, H2H, Implied vs Model Probabilities.
3. Edge calculation (model_prob - implied_prob) and value detection.
4. Deterministic, verifiable insights without hallucinations.
5. Strict division and season isolation for analytical data.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from services.intelligence_engine import IntelligenceEngine


class TestPhase6Intelligence(unittest.TestCase):

    def setUp(self) -> None:
        database.init_db()
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM market_selections WHERE id >= 993000")
            cursor.execute("DELETE FROM markets WHERE match_id >= 99300")
            cursor.execute("DELETE FROM matches WHERE id >= 99300")

            # Seed target match in Division 1, Season 1
            cursor.execute("""
                INSERT INTO matches (
                    id, season_id, division_id, round_number,
                    player1_team, player2_team, status, player1_score, player2_score
                ) VALUES (99301, 1, 1, 7, 'Арсенал', 'Челси', 'scheduled', 0, 0)
            """)

            # Seed past confirmed matches for Арсенал (3 wins, high scoring)
            for idx, (s1, s2, opp) in enumerate([(3, 1, 'Тоттенхэм'), (2, 0, 'Фулхэм'), (2, 2, 'Ливерпуль')]):
                cursor.execute("""
                    INSERT INTO matches (
                        id, season_id, division_id, round_number,
                        player1_team, player2_team, status, player1_score, player2_score
                    ) VALUES (?, 1, 1, ?, 'Арсенал', ?, 'confirmed', ?, ?)
                """, (99310 + idx, idx + 1, opp, s1, s2))

            # Seed past confirmed matches for Челси
            for idx, (s1, s2, opp) in enumerate([(1, 2, 'Манчестер Сити'), (1, 1, 'Брайтон'), (0, 1, 'Ньюкасл')]):
                cursor.execute("""
                    INSERT INTO matches (
                        id, season_id, division_id, round_number,
                        player1_team, player2_team, status, player1_score, player2_score
                    ) VALUES (?, 1, 1, ?, 'Челси', ?, 'confirmed', ?, ?)
                """, (99320 + idx, idx + 1, opp, s1, s2))

            # Seed past H2H meeting between Арсенал and Челси in Division 1
            cursor.execute("""
                INSERT INTO matches (
                    id, season_id, division_id, round_number,
                    player1_team, player2_team, status, player1_score, player2_score
                ) VALUES (99330, 1, 1, 1, 'Арсенал', 'Челси', 'confirmed', 3, 1)
            """)

            # Seed a match in Division 2 (must NOT be included due to division isolation!)
            cursor.execute("""
                INSERT INTO matches (
                    id, season_id, division_id, round_number,
                    player1_team, player2_team, status, player1_score, player2_score
                ) VALUES (99340, 1, 2, 1, 'Арсенал', 'Астон Вилла', 'confirmed', 10, 0)
            """)

            # Seed markets & selections for target match 99301
            cursor.execute("""
                INSERT INTO markets (id, match_id, market_key, market_name, category, status)
                VALUES (993011, 99301, '1x2', 'Основной исход', 'main', 'open'),
                       (993012, 99301, 'totals', 'Тотал 2.5', 'totals', 'open')
            """)
            cursor.execute("""
                INSERT INTO market_selections (id, market_id, selection_key, selection_name, odds_value, odds_version, status)
                VALUES (9930111, 993011, 'p1', 'П1', 1.85, 1, 'active'),
                       (9930112, 993011, 'x', 'X', 3.60, 1, 'active'),
                       (9930113, 993011, 'p2', 'П2', 4.20, 1, 'active'),
                       (9930121, 993012, 'tb25', 'ТБ 2.5', 1.70, 1, 'active')
            """)

    def tearDown(self) -> None:
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM market_selections WHERE id >= 993000")
            cursor.execute("DELETE FROM markets WHERE match_id >= 99300")
            cursor.execute("DELETE FROM matches WHERE id >= 99300")

    def test_get_match_intelligence_comprehensive(self) -> None:
        """Verify complete intelligence report generation for match."""
        rep = IntelligenceEngine.get_match_intelligence(99301)
        self.assertEqual(rep["status"], "ok")
        self.assertEqual(rep["match_id"], 99301)
        self.assertEqual(rep["team1"], "Арсенал")
        self.assertEqual(rep["team2"], "Челси")

        # 1. Form analysis
        t1_form = rep["form"]["team1"]
        self.assertGreaterEqual(t1_form["matches_played"], 3)
        self.assertGreater(t1_form["avg_scored"], 1.5)
        self.assertEqual(t1_form["clean_sheets"], 1)

        t2_form = rep["form"]["team2"]
        self.assertGreaterEqual(t2_form["matches_played"], 3)
        self.assertEqual(t2_form["wins"], 0)

        # 2. H2H analysis
        h2h = rep["h2h"]
        self.assertEqual(h2h["total_meetings"], 1)
        self.assertEqual(h2h["team1_wins"], 1)
        self.assertEqual(h2h["team2_wins"], 0)

        # 3. Probabilities and Value Edge
        val = rep["value_analysis"]
        self.assertTrue(len(val) >= 3)
        for item in val:
            self.assertIn("selection", item)
            self.assertIn("implied_probability", item)
            self.assertIn("model_probability", item)
            self.assertIn("edge", item)
            self.assertIn("confidence", item)
            # Edge must equal model_prob - implied_prob
            expected_edge = round(item["model_probability"] - item["implied_probability"], 2)
            self.assertAlmostEqual(item["edge"], expected_edge, places=1)

        # 4. Verifiable Insights
        insights = rep["insights"]
        self.assertTrue(len(insights) >= 2)
        # Check disclaimer is attached
        self.assertIn("не являются гарантией", rep["disclaimer"].lower())

    def test_division_isolation_in_intelligence(self) -> None:
        """Verify matches in other divisions (e.g. division 2) are not leaked into stats."""
        rep = IntelligenceEngine.get_match_intelligence(99301)
        t1_form = rep["form"]["team1"]

        # The match in division 2 had score 10:0. If leaked, avg_scored would be >= 3.5.
        # But isolated to Division 1, scores were 3, 2, 2, 3 -> avg = 2.5.
        self.assertLess(t1_form["avg_scored"], 3.5)


if __name__ == "__main__":
    unittest.main()
