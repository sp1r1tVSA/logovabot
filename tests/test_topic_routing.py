"""
Tests for Stage 2: Topic Routing & Configuration
Verifies division-aware match lookup, Telegram forum topic routing, and formatted post labeling.
"""

import unittest
import database
from handlers.cabinet import build_formatted_match_post


class TestTopicRouting(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self.div1_code = "TEST_ROUTE_DIV1"
        self.div2_code = "TEST_ROUTE_DIV2"

        # Create two test divisions
        self.div1_id = database.create_division(name="Первый Дивизион", code=self.div1_code)
        self.div2_id = database.create_division(name="Второй Дивизион", code=self.div2_code)

        self.u1_id = 910001
        self.u2_id = 910002
        self.u3_id = 910003
        self.u4_id = 910004

        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT OR REPLACE INTO users (telegram_id, username, team_name, role, division_id)
                VALUES (?, 'user_div1_a', 'Route FC Alpha', 'player', ?)
            """, (self.u1_id, self.div1_id))
            c.execute("""
                INSERT OR REPLACE INTO users (telegram_id, username, team_name, role, division_id)
                VALUES (?, 'user_div1_b', 'Route FC Beta', 'player', ?)
            """, (self.u2_id, self.div1_id))
            c.execute("""
                INSERT OR REPLACE INTO users (telegram_id, username, team_name, role, division_id)
                VALUES (?, 'user_div2_a', 'Route FC Gamma', 'player', ?)
            """, (self.u3_id, self.div2_id))
            c.execute("""
                INSERT OR REPLACE INTO users (telegram_id, username, team_name, role, division_id)
                VALUES (?, 'user_div2_b', 'Route FC Delta', 'player', ?)
            """, (self.u4_id, self.div2_id))

    def tearDown(self):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM matches WHERE player1_id IN (?, ?, ?, ?)", (self.u1_id, self.u2_id, self.u3_id, self.u4_id))
            c.execute("DELETE FROM division_topics WHERE division_id IN (?, ?)", (self.div1_id, self.div2_id))
            c.execute("DELETE FROM divisions WHERE id IN (?, ?)", (self.div1_id, self.div2_id))
            c.execute("DELETE FROM users WHERE telegram_id IN (?, ?, ?, ?)", (self.u1_id, self.u2_id, self.u3_id, self.u4_id))

    def test_topic_routing_lookup(self):
        """Test binding and resolving divisions by Telegram forum topic message_thread_id."""
        database.set_division_topic(self.div1_id, "drafts", 5501)
        database.set_division_topic(self.div1_id, "results", 5502)
        database.set_division_topic(self.div2_id, "drafts", 6601)

        # Lookup by thread ID
        div_found_1 = database.get_division_by_topic(5501, "drafts")
        self.assertIsNotNone(div_found_1)
        self.assertEqual(div_found_1["id"], self.div1_id)
        self.assertEqual(div_found_1["name"], "Первый Дивизион")

        div_found_2 = database.get_division_by_topic(6601, "drafts")
        self.assertIsNotNone(div_found_2)
        self.assertEqual(div_found_2["id"], self.div2_id)

        # Non-existent topic returns None
        self.assertIsNone(database.get_division_by_topic(9999, "drafts"))

        # Topic lookup by division
        self.assertEqual(database.get_division_topic(self.div1_id, "results"), 5502)
        self.assertIsNone(database.get_division_topic(self.div2_id, "results"))

    def test_get_active_match_filtered_by_division(self):
        """
        Verify that get_active_match_by_teams accurately isolates matches belonging to
        the specified division and does not cross division boundaries.
        """
        # Create match in Division 1
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, status, division_id)
                VALUES (1, ?, ?, 'Route FC Alpha', 'Route FC Beta', 'pending', ?)
            """, (self.u1_id, self.u2_id, self.div1_id))
            match_div1_id = c.lastrowid

            # Create another match with the same team names in Division 2
            c.execute("""
                INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, status, division_id)
                VALUES (1, ?, ?, 'Route FC Alpha', 'Route FC Beta', 'pending', ?)
            """, (self.u3_id, self.u4_id, self.div2_id))
            match_div2_id = c.lastrowid

        # Search with division_id = div1
        m1 = database.get_active_match_by_teams("Route FC Alpha", "Route FC Beta", division_id=self.div1_id)
        self.assertIsNotNone(m1)
        self.assertEqual(m1["id"], match_div1_id)
        self.assertEqual(m1["division_id"], self.div1_id)

        # Search with division_id = div2
        m2 = database.get_active_match_by_teams("Route FC Alpha", "Route FC Beta", division_id=self.div2_id)
        self.assertIsNotNone(m2)
        self.assertEqual(m2["id"], match_div2_id)
        self.assertEqual(m2["division_id"], self.div2_id)

        # Search with division_id = non-existent division (returns None)
        m_none = database.get_active_match_by_teams("Route FC Alpha", "Route FC Beta", division_id=999999)
        self.assertIsNone(m_none)

        # Search without division_id (legacy mode) returns a match
        m_legacy = database.get_active_match_by_teams("Route FC Alpha", "Route FC Beta", division_id=None)
        self.assertIsNotNone(m_legacy)

    def test_formatted_match_post_division_label(self):
        """Test that build_formatted_match_post includes division name when match has division_id."""
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, status, division_id)
                VALUES (3, ?, ?, 'Route FC Alpha', 'Route FC Beta', 'confirmed', ?)
            """, (self.u1_id, self.u2_id, self.div1_id))
            div_match_id = c.lastrowid

            c.execute("""
                INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, status, division_id)
                VALUES (3, ?, ?, 'Route FC Alpha', 'Route FC Beta', 'confirmed', NULL)
            """, (self.u1_id, self.u2_id))
            legacy_match_id = c.lastrowid

        # Post with division
        post_div = build_formatted_match_post(
            round_number=3,
            home_team="Route FC Alpha",
            away_team="Route FC Beta",
            h_score=2,
            a_score=1,
            match_id=div_match_id,
            is_draft=False
        )
        self.assertIn("Первый Дивизион", post_div, "Official post must contain division name.")

        # Legacy post without division
        post_legacy = build_formatted_match_post(
            round_number=3,
            home_team="Route FC Alpha",
            away_team="Route FC Beta",
            h_score=2,
            a_score=1,
            match_id=legacy_match_id,
            is_draft=False
        )
        self.assertNotIn("•", post_legacy.split("\n")[0], "Legacy post must not contain bullet separator in title.")


if __name__ == "__main__":
    unittest.main()
