"""
tests/test_divisions_schema.py

Unit tests for Logovobot Division Database Schema (Stage 1):
- Tables: divisions, division_topics
- Safe migration columns: matches.division_id, users.division_id, rounds.division_id
- Indexes for performance and topic lookup
- Backward compatibility: NULL division_id for legacy records
- Division CRUD & User assignment
- Multi-topic routing
- Isolated division match cleanup
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database


class TestDivisionsSchema(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self.test_user_id = 987654321
        self.test_user_id_2 = 987654322

    def tearDown(self):
        with database.transaction() as conn:
            conn.execute("DELETE FROM division_topics WHERE division_id IN (SELECT id FROM divisions WHERE code LIKE 'TEST_%')")
            conn.execute("DELETE FROM matches WHERE division_id IN (SELECT id FROM divisions WHERE code LIKE 'TEST_%') OR player1_id IN (?, ?)", (self.test_user_id, self.test_user_id_2))
            conn.execute("DELETE FROM users WHERE telegram_id IN (?, ?)", (self.test_user_id, self.test_user_id_2))
            conn.execute("DELETE FROM divisions WHERE code LIKE 'TEST_%'")

    def test_tables_and_indexes_exist(self):
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cursor.fetchall()]
            self.assertIn("divisions", tables, "Table 'divisions' must exist.")
            self.assertIn("division_topics", tables, "Table 'division_topics' must exist.")

            cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indexes = [r[0] for r in cursor.fetchall()]
            for expected_idx in [
                "idx_matches_division",
                "idx_users_division",
                "idx_divisions_active",
                "idx_divisions_topic",
                "idx_div_topics_lookup"
            ]:
                self.assertIn(expected_idx, indexes, f"Index '{expected_idx}' must exist.")

    def test_columns_added_with_null_default(self):
        with database.transaction() as conn:
            cursor = conn.cursor()

            cursor.execute("PRAGMA table_info(matches)")
            match_cols = [r[1] for r in cursor.fetchall()]
            self.assertIn("division_id", match_cols, "Column 'division_id' must exist in matches.")

            cursor.execute("PRAGMA table_info(users)")
            user_cols = [r[1] for r in cursor.fetchall()]
            self.assertIn("division_id", user_cols, "Column 'division_id' must exist in users.")

            cursor.execute("PRAGMA table_info(rounds)")
            round_cols = [r[1] for r in cursor.fetchall()]
            self.assertIn("division_id", round_cols, "Column 'division_id' must exist in rounds.")

    def test_legacy_backward_compatibility(self):
        """Verify that legacy records with division_id = NULL continue to work perfectly."""
        with database.transaction() as conn:
            conn.execute("""
                INSERT INTO users (telegram_id, username, team_name, role)
                VALUES (?, 'legacy_player', 'Legacy Club', 'player')
            """, (self.test_user_id,))
            conn.execute("""
                INSERT INTO users (telegram_id, username, team_name, role)
                VALUES (?, 'legacy_player_2', 'Legacy Club 2', 'player')
            """, (self.test_user_id_2,))

        user = database.get_user(self.test_user_id)
        self.assertIsNotNone(user)
        self.assertIsNone(user["division_id"], "Legacy user must have division_id = NULL.")

        # Legacy user must be returned by get_division_users(None)
        unassigned_users = database.get_division_users(None)
        u_ids = [u["telegram_id"] for u in unassigned_users]
        self.assertIn(self.test_user_id, u_ids, "Legacy user without division must appear in get_division_users(None).")

        # Create legacy match
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, status)
                VALUES (1, ?, ?, 'Legacy Club 1', 'Legacy Club 2', 'pending')
            """, (self.test_user_id, self.test_user_id_2))
            match_id = cursor.lastrowid

        match = database.get_match(match_id)
        self.assertIsNotNone(match)
        self.assertIsNone(match["division_id"], "Legacy match must have division_id = NULL.")

    def test_division_crud(self):
        """Test creating, reading, and updating divisions."""
        div_id = database.create_division(
            name="Тестовый Дивизион А",
            code="TEST_DIV_A",
            tournament_id=1,
            topic_id=1001,
            sort_order=1
        )
        self.assertIsInstance(div_id, int)
        self.assertGreater(div_id, 0)

        # Read by ID
        div = database.get_division(div_id)
        self.assertIsNotNone(div)
        self.assertEqual(div["name"], "Тестовый Дивизион А")
        self.assertEqual(div["code"], "TEST_DIV_A")
        self.assertEqual(div["topic_id"], 1001)
        self.assertEqual(div["sort_order"], 1)
        self.assertEqual(div["is_active"], 1)

        # Read by Code
        div_by_code = database.get_division_by_code("test_div_a")
        self.assertIsNotNone(div_by_code)
        self.assertEqual(div_by_code["id"], div_id)

        # Update
        database.update_division(div_id, name="Тестовый Дивизион Премиум", sort_order=5)
        updated = database.get_division(div_id)
        self.assertEqual(updated["name"], "Тестовый Дивизион Премиум")
        self.assertEqual(updated["sort_order"], 5)

        # List
        all_divs = database.get_divisions(is_active=True)
        codes = [d["code"] for d in all_divs]
        self.assertIn("TEST_DIV_A", codes)

    def test_user_division_assignment(self):
        """Test assigning a user to a division and unassigning back to None."""
        div_id = database.create_division(name="Тестовый Дивизион Б", code="TEST_DIV_B")

        with database.transaction() as conn:
            conn.execute("""
                INSERT INTO users (telegram_id, username, team_name, role)
                VALUES (?, 'assigned_player', 'Test Club B', 'player')
            """, (self.test_user_id,))

        # Assign
        database.assign_user_division(self.test_user_id, div_id)
        u = database.get_user(self.test_user_id)
        self.assertEqual(u["division_id"], div_id)

        # get_division_users
        div_users = database.get_division_users(div_id)
        self.assertEqual(len(div_users), 1)
        self.assertEqual(div_users[0]["telegram_id"], self.test_user_id)

        # Unassign
        database.assign_user_division(self.test_user_id, None)
        u_unassigned = database.get_user(self.test_user_id)
        self.assertIsNone(u_unassigned["division_id"])

    def test_division_topics_routing(self):
        """Test registering topics and reverse lookup of division by topic_id."""
        div_id = database.create_division(name="Тестовый Дивизион В", code="TEST_DIV_C")

        # Set topic for drafts and results
        database.set_division_topic(div_id, "drafts", 55501)
        database.set_division_topic(div_id, "results", 55502)

        # Get topic
        self.assertEqual(database.get_division_topic(div_id, "drafts"), 55501)
        self.assertEqual(database.get_division_topic(div_id, "results"), 55502)
        self.assertIsNone(database.get_division_topic(div_id, "warns"))

        # Reverse lookup by thread ID
        found_div = database.get_division_by_topic(55501, "drafts")
        self.assertIsNotNone(found_div)
        self.assertEqual(found_div["id"], div_id)
        self.assertEqual(found_div["code"], "TEST_DIV_C")

        # Non-existent thread ID
        self.assertIsNone(database.get_division_by_topic(999999, "drafts"))

        # Upsert topic on conflict
        database.set_division_topic(div_id, "drafts", 55599)
        self.assertEqual(database.get_division_topic(div_id, "drafts"), 55599)
        self.assertEqual(database.get_division_by_topic(55599, "drafts")["id"], div_id)

    def test_safe_clear_matches_by_division(self):
        """
        Verify critical safety invariant:
        clear_matches_by_division must delete ONLY matches of that division,
        leaving legacy matches (division_id IS NULL) and other divisions completely intact!
        """
        div1_id = database.create_division(name="Дивизион 1", code="TEST_DIV_1")
        div2_id = database.create_division(name="Дивизион 2", code="TEST_DIV_2")

        with database.transaction() as conn:
            cursor = conn.cursor()
            # Match in Div 1
            cursor.execute("""
                INSERT INTO matches (round_number, player1_team, player2_team, division_id, status)
                VALUES (1, 'D1 Team A', 'D1 Team B', ?, 'pending')
            """, (div1_id,))
            m_div1_id = cursor.lastrowid

            # Match in Div 2
            cursor.execute("""
                INSERT INTO matches (round_number, player1_team, player2_team, division_id, status)
                VALUES (1, 'D2 Team A', 'D2 Team B', ?, 'pending')
            """, (div2_id,))
            m_div2_id = cursor.lastrowid

            # Legacy match with division_id = NULL
            cursor.execute("""
                INSERT INTO matches (round_number, player1_team, player2_team, division_id, status)
                VALUES (1, 'Legacy Team A', 'Legacy Team B', NULL, 'pending')
            """)
            m_legacy_id = cursor.lastrowid

        # Clear ONLY Div 1 matches
        database.clear_matches_by_division(div1_id)

        # Div 1 match must be gone
        self.assertIsNone(database.get_match(m_div1_id), "Division 1 match must be deleted.")

        # Div 2 match and Legacy match MUST STILL EXIST!
        self.assertIsNotNone(database.get_match(m_div2_id), "Division 2 match must NOT be deleted.")
        self.assertIsNotNone(database.get_match(m_legacy_id), "Legacy match (division_id = NULL) must NOT be deleted.")


if __name__ == "__main__":
    unittest.main()
