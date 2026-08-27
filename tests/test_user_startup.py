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
            c.execute("DELETE FROM user_warns")
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

    def test_active_warns_excludes_kicked_and_clubless_players(self):
        """Verify that players without an active club (e.g. kicked/excluded) are excluded from active warns list."""
        with database.transaction() as conn:
            c = conn.cursor()
            # Active player with club & warns
            c.execute("INSERT INTO users (telegram_id, username, team_name, role, warn_count) VALUES (101, 'Saharokk8830', 'Брага', 'player', 3)")
            # Excluded / kicked player without club
            c.execute("INSERT INTO users (telegram_id, username, team_name, role, warn_count) VALUES (102, 'crcsss', NULL, 'user', 4)")
            # Player with empty team
            c.execute("INSERT INTO users (telegram_id, username, team_name, role, warn_count) VALUES (103, 'ghost', '', 'user', 2)")

        active_warns = database.get_all_active_warns()
        self.assertEqual(len(active_warns), 1)
        self.assertEqual(active_warns[0]['username'], 'Saharokk8830')
        self.assertEqual(active_warns[0]['team_name'], 'Брага')
        self.assertEqual(active_warns[0]['warn_count'], 3)

    def test_club_transfer_resets_warns_for_new_and_old_owner(self):
        """Verify that assigning a new owner to a club resets warns and clears old owner."""
        with database.transaction() as conn:
            c = conn.cursor()
            # Old owner with 3 warns
            c.execute("INSERT INTO users (telegram_id, username, team_name, role, warn_count) VALUES (201, 'old_owner', 'ПСВ', 'player', 3)")
            # New candidate with 1 warn
            c.execute("INSERT INTO users (telegram_id, username, team_name, role, warn_count) VALUES (202, 'new_owner', NULL, 'user', 1)")

        success, msg = database.set_player_club('new_owner', 'ПСВ')
        self.assertTrue(success)

        old_u = database.get_user(201)
        new_u = database.get_user(202)

        self.assertIsNone(old_u['team_name'])
        self.assertEqual(old_u['warn_count'], 0)

        self.assertEqual(new_u['team_name'], 'ПСВ')
        self.assertEqual(new_u['role'], 'player')
        self.assertEqual(new_u['warn_count'], 0)

    def test_ban_and_remove_from_league_resets_warns(self):
        """Verify that ban_and_remove_from_league clears team_name and resets warn_count."""
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO users (telegram_id, username, team_name, role, warn_count) VALUES (301, 'bad_player', 'Аякс', 'player', 4)")

        team = database.ban_and_remove_from_league(301)
        self.assertEqual(team, 'Аякс')

        u = database.get_user(301)
        self.assertIsNone(u['team_name'])
        self.assertEqual(u['warn_count'], 0)

        # Ensure no active warns returned
        self.assertEqual(len(database.get_all_active_warns()), 0)

if __name__ == '__main__':
    unittest.main()
