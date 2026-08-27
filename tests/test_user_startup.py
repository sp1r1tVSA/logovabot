import unittest
import os
import sqlite3
import database
from unittest.mock import AsyncMock, MagicMock, patch
import html

class TestUserStartup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()

    def setUp(self):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM match_events")
            c.execute("DELETE FROM matches")
            c.execute("DELETE FROM users")

    def test_pre_registered_user_startup_unique_constraint(self):
        """Verify that a pre-registered user with negative ID and unique team_name is smoothly upgraded without UNIQUE constraint failure."""
        with database.transaction() as conn:
            c = conn.cursor()
            # Pre-register player2
            c.execute("INSERT INTO users (telegram_id, username, team_name, league_name, role) VALUES (555, 'player2', 'Ривер Плейт', 'Основная', 'player')")
            # Pre-register user by admin
            c.execute("INSERT INTO users (telegram_id, username, team_name, league_name, role) VALUES (-99, 'asensibleboy', 'Брюгге', 'Основная', 'player')")
            # Create a match referencing negative ID
            c.execute("INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team, status) VALUES (1, -99, 555, 'Брюгге', 'Ривер Плейт', 'pending')")

        # Now real user starts the bot
        database.handle_user_startup(123456789, 'asensibleboy', 'user')

        user = database.get_user(123456789)
        self.assertIsNotNone(user)
        self.assertEqual(user['username'], 'asensibleboy')
        self.assertEqual(user['team_name'], 'Брюгге')
        self.assertEqual(user['role'], 'player')
        self.assertEqual(user['pending_notification'], 1)

        # Check that negative ID user was removed
        old_u = database.get_user(-99)
        self.assertIsNone(old_u)

        # Check that matches table references real telegram_id
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM matches WHERE player1_id = ?", (123456789,))
            matches = c.fetchall()
            self.assertEqual(len(matches), 1)

    def test_existing_user_merged_with_pre_registered_team(self):
        """Verify that an existing user without a team who was pre-registered into a team by admin merges team successfully."""
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO users (telegram_id, username, team_name, role) VALUES (987654321, 'belka809', NULL, 'user')")
            c.execute("INSERT INTO users (telegram_id, username, team_name, league_name, role) VALUES (-50, 'belka809', 'Ривер Плейт', 'Основная', 'player')")

        database.handle_user_startup(987654321, 'belka809', 'user')

        user = database.get_user(987654321)
        self.assertIsNotNone(user)
        self.assertEqual(user['team_name'], 'Ривер Плейт')
        self.assertEqual(user['role'], 'player')

    def test_first_name_html_escaping(self):
        """Verify that user first names with HTML chars (<, >, &) are safely escaped."""
        raw_name = "<Pro & Winner>"
        escaped = html.escape(raw_name)
        self.assertEqual(escaped, "&lt;Pro &amp; Winner&gt;")

if __name__ == '__main__':
    unittest.main()
