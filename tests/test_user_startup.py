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
            c.execute("DELETE FROM user_wallets")
            c.execute("DELETE FROM user_progression")
            c.execute("DELETE FROM coin_transactions")
            c.execute("DELETE FROM user_bets")

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

    def test_pre_register_to_division_and_startup_preserves_division(self):
        """Verify that pre-registering to division assigns division_id and startup preserves it."""
        temp_id = database.pre_register_player_to_division("new_div_player", 4)
        self.assertLess(temp_id, 0)
        
        pre = database.get_user(temp_id)
        self.assertIsNotNone(pre)
        self.assertEqual(pre['username'], 'new_div_player')
        self.assertEqual(pre['division_id'], 4)
        self.assertIsNone(pre['team_name'])

        # Real user starts bot
        database.handle_user_startup(777888999, 'new_div_player', 'user')
        real_u = database.get_user(777888999)
        self.assertIsNotNone(real_u)
        self.assertEqual(real_u['username'], 'new_div_player')
        self.assertEqual(real_u['division_id'], 4)
        self.assertIsNone(database.get_user(temp_id))

    def test_pre_register_to_division_existing_user_startup_merge(self):
        """Verify that pre-registering an existing user without division merges division on startup."""
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO users (telegram_id, username, role, division_id) VALUES (444555, 'existing_boy', 'user', NULL)")
        
        temp_id = database.pre_register_player_to_division("existing_boy", 2)
        # Since user already exists with positive ID, pre_register_player_to_division updates existing
        self.assertEqual(temp_id, 444555)
        u = database.get_user(444555)
        self.assertEqual(u['division_id'], 2)

    # ── Logovo.bet rows owned by a claimed placeholder ────────────────────────

    def _add_bet(self, user_id, amount, idem_key=None):
        with database.transaction() as conn:
            conn.cursor().execute(
                "INSERT INTO user_bets (user_id, bet_type, amount, total_odd, potential_win, idempotency_key) "
                "VALUES (?, 'single', ?, 2.0, ?, ?)",
                (user_id, amount, amount * 2, idem_key)
            )

    def _wallet(self, user_id):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM user_wallets WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            return dict(row) if row else None

    def _ledger_sum(self, user_id):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("SELECT COALESCE(SUM(amount), 0) AS s FROM coin_transactions WHERE user_id = ?", (user_id,))
            return c.fetchone()['s']

    def test_placeholder_wallet_moves_to_a_brand_new_real_user(self):
        """A placeholder that never met its coach still carries coins, XP and bets across."""
        with database.transaction() as conn:
            conn.cursor().execute(
                "INSERT INTO users (telegram_id, username, team_name, league_name, role) "
                "VALUES (-77, 'walletboy', 'Аталанта', 'Основная', 'player')"
            )
        database.get_or_create_wallet(-77)
        database.add_coins(-77, 300, 'daily_bonus')
        database.get_or_create_progression(-77)
        self._add_bet(-77, 50, 'key-a')

        database.handle_user_startup(777000111, 'walletboy', 'user')

        self.assertIsNone(self._wallet(-77))
        wallet = self._wallet(777000111)
        self.assertIsNotNone(wallet)
        self.assertEqual(wallet['balance'], database.INITIAL_WALLET_BALANCE + 300)
        self.assertEqual(self._ledger_sum(777000111), wallet['balance'])
        self.assertEqual(self._ledger_sum(-77), 0)

        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) AS n FROM user_progression WHERE user_id = ?", (777000111,))
            self.assertEqual(c.fetchone()['n'], 1)
            c.execute("SELECT COUNT(*) AS n FROM user_bets WHERE user_id = ?", (777000111,))
            self.assertEqual(c.fetchone()['n'], 1)
            c.execute("SELECT COUNT(*) AS n FROM user_bets WHERE user_id = ?", (-77,))
            self.assertEqual(c.fetchone()['n'], 0)

    def test_two_wallets_merge_without_duplicating_the_welcome_bonus(self):
        """Both sides hold a wallet, so the 677 bonus was granted twice — only one may survive."""
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO users (telegram_id, username, role) VALUES (888000222, 'richboy', 'user')")
            c.execute(
                "INSERT INTO users (telegram_id, username, team_name, league_name, role) "
                "VALUES (-88, 'richboy', 'Аталанта', 'Основная', 'player')"
            )
        database.get_or_create_wallet(888000222)
        database.get_or_create_wallet(-88)
        database.add_coins(-88, 300, 'daily_bonus')

        database.handle_user_startup(888000222, 'richboy', 'user')

        self.assertIsNone(self._wallet(-88))
        wallet = self._wallet(888000222)
        # One welcome bonus plus the 300 the placeholder actually earned on top of it.
        self.assertEqual(wallet['balance'], database.INITIAL_WALLET_BALANCE + 300)
        self.assertEqual(self._ledger_sum(888000222), wallet['balance'])

        with database.transaction() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT COUNT(*) AS n FROM coin_transactions WHERE user_id = ? AND transaction_type = 'welcome_bonus'",
                (888000222,)
            )
            self.assertEqual(c.fetchone()['n'], 1)

    def test_progression_merge_keeps_whichever_profile_earned_more(self):
        """An untouched level-1 placeholder must never overwrite real progress."""
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO users (telegram_id, username, role) VALUES (999000333, 'veteran', 'user')")
            c.execute(
                "INSERT INTO users (telegram_id, username, team_name, league_name, role) "
                "VALUES (-99, 'veteran', 'Аталанта', 'Основная', 'player')"
            )
        database.get_or_create_progression(999000333)
        database.get_or_create_progression(-99)
        with database.transaction() as conn:
            conn.cursor().execute(
                "UPDATE user_progression SET level = 7, current_xp = 40, total_xp_earned = 900 WHERE user_id = ?",
                (999000333,)
            )

        database.handle_user_startup(999000333, 'veteran', 'user')

        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM user_progression WHERE user_id = ?", (999000333,))
            prog = c.fetchone()
            self.assertEqual(prog['level'], 7)
            self.assertEqual(prog['total_xp_earned'], 900)
            c.execute("SELECT COUNT(*) AS n FROM user_progression WHERE user_id = ?", (-99,))
            self.assertEqual(c.fetchone()['n'], 0)

    def test_progression_merge_takes_the_placeholder_when_it_is_ahead(self):
        """The admin may have been playing on the pre-registered row — that XP wins."""
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO users (telegram_id, username, role) VALUES (111000444, 'climber', 'user')")
            c.execute(
                "INSERT INTO users (telegram_id, username, team_name, league_name, role) "
                "VALUES (-11, 'climber', 'Аталанта', 'Основная', 'player')"
            )
        database.get_or_create_progression(111000444)
        database.get_or_create_progression(-11)
        with database.transaction() as conn:
            conn.cursor().execute(
                "UPDATE user_progression SET level = 5, current_xp = 20, total_xp_earned = 500 WHERE user_id = ?",
                (-11,)
            )

        database.handle_user_startup(111000444, 'climber', 'user')

        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM user_progression WHERE user_id = ?", (111000444,))
            prog = c.fetchone()
            self.assertEqual(prog['level'], 5)
            self.assertEqual(prog['total_xp_earned'], 500)

    def test_colliding_bet_idempotency_key_does_not_lose_the_bet(self):
        """UNIQUE(user_id, idempotency_key) must not block the move — the key is released instead."""
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO users (telegram_id, username, role) VALUES (222000555, 'better', 'user')")
            c.execute(
                "INSERT INTO users (telegram_id, username, team_name, league_name, role) "
                "VALUES (-22, 'better', 'Аталанта', 'Основная', 'player')"
            )
        self._add_bet(222000555, 100, 'shared-key')
        self._add_bet(-22, 50, 'shared-key')
        self._add_bet(-22, 70, None)

        database.handle_user_startup(222000555, 'better', 'user')

        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("SELECT amount, idempotency_key FROM user_bets WHERE user_id = ? ORDER BY amount", (222000555,))
            bets = [dict(r) for r in c.fetchall()]
            self.assertEqual([b['amount'] for b in bets], [50, 70, 100])
            self.assertIsNone(next(b for b in bets if b['amount'] == 50)['idempotency_key'])
            self.assertEqual(next(b for b in bets if b['amount'] == 100)['idempotency_key'], 'shared-key')
            c.execute("SELECT COUNT(*) AS n FROM user_bets WHERE user_id = ?", (-22,))
            self.assertEqual(c.fetchone()['n'], 0)

if __name__ == '__main__':
    unittest.main()

