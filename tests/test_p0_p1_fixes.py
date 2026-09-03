"""
tests/test_p0_p1_fixes.py

Comprehensive tests verifying all P0/P1 fixes:
1. Topic isolation between different chats with identical thread_id
2. Strict rejection of bare thread_id lookups (no chat_id = None fallback)
3. Parity between TopicCache and database.get_topic_binding
4. Rounds table composite schema (UNIQUE(division_id, round_number))
5. Independent round lifecycle (opening round 1 in Div 1 does not open Div 2)
6. Division match clearance (clear Div 1 preserves Div 2 rounds)
7. Standings isolation (Div 1 vs Div 2)
8. /table command execution without dict crash in division topics
9. Round Robin protection against re-generation when confirmed matches exist
10. Cross-division topic theft protection (Division Admin cannot steal other division's topic)
11. Reset league restricted to Global Admins
12. API Auth: mock_admin blocked unless ALLOW_DEV_AUTH_BYPASS=1, 24h auth_date freshness
"""

import os
import sys
import time
import uuid
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import database
from services.topic_cache import topic_cache
from handlers.base import group_table_command
from handlers.admin import admin_generate_matches_execute, admin_clear_league_start
from handlers.topic_management import cb_reassign_topic_confirm
from api.auth import get_authenticated_user, validate_telegram_init_data


class TestP0P1Fixes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        database.init_db()
        self.uid = uuid.uuid4().hex[:6].upper()

        self.div1_id = database.create_division(name=f"Fix Div 1 {self.uid}", code=f"FD1_{self.uid}")
        self.div2_id = database.create_division(name=f"Fix Div 2 {self.uid}", code=f"FD2_{self.uid}")

        self.global_admin_id = 91000001
        self.div1_admin_id = 91000002
        self.div2_admin_id = 91000003
        self.player1_id = 91000004
        self.player2_id = 91000005

        database.register_user(self.global_admin_id, f"g_admin_{self.uid}", team_name=f"AdminTeam_{self.uid}")
        database.register_user(self.div1_admin_id, f"d1_admin_{self.uid}", team_name=f"D1Team_{self.uid}")
        database.register_user(self.div2_admin_id, f"d2_admin_{self.uid}", team_name=f"D2Team_{self.uid}")
        database.register_user(self.player1_id, f"p1_{self.uid}", team_name=f"ClubA_{self.uid}")
        database.register_user(self.player2_id, f"p2_{self.uid}", team_name=f"ClubB_{self.uid}")

        with database.transaction() as conn:
            conn.execute("UPDATE users SET role = 'admin', division_id = NULL WHERE telegram_id = ?", (self.global_admin_id,))
            conn.execute("UPDATE users SET role = 'division_admin', division_id = ? WHERE telegram_id = ?", (self.div1_id, self.div1_admin_id))
            conn.execute("UPDATE users SET role = 'division_admin', division_id = ? WHERE telegram_id = ?", (self.div2_id, self.div2_admin_id))
            conn.execute("UPDATE users SET division_id = ? WHERE telegram_id = ?", (self.div1_id, self.player1_id))
            conn.execute("UPDATE users SET division_id = ? WHERE telegram_id = ?", (self.div2_id, self.player2_id))

        database.add_division_admin(self.div1_id, self.div1_admin_id)
        database.add_division_admin(self.div2_id, self.div2_admin_id)

    def tearDown(self):
        with database.transaction() as conn:
            conn.execute("DELETE FROM division_topics WHERE division_id IN (?, ?)", (self.div1_id, self.div2_id))
            conn.execute("DELETE FROM matches WHERE division_id IN (?, ?) OR player1_id IN (?, ?, ?, ?, ?) OR player2_id IN (?, ?, ?, ?, ?)",
                         (self.div1_id, self.div2_id, self.global_admin_id, self.div1_admin_id, self.div2_admin_id, self.player1_id, self.player2_id,
                          self.global_admin_id, self.div1_admin_id, self.div2_admin_id, self.player1_id, self.player2_id))
            conn.execute("DELETE FROM rounds WHERE division_id IN (?, ?)", (self.div1_id, self.div2_id))
            conn.execute("DELETE FROM division_admins WHERE division_id IN (?, ?)", (self.div1_id, self.div2_id))
            conn.execute("DELETE FROM users WHERE telegram_id IN (?, ?, ?, ?, ?)",
                         (self.global_admin_id, self.div1_admin_id, self.div2_admin_id, self.player1_id, self.player2_id))
            conn.execute("DELETE FROM divisions WHERE id IN (?, ?)", (self.div1_id, self.div2_id))

    # 1. TOPIC ISOLATION ACROSS CHATS
    def test_01_topic_isolation_different_chats_same_thread(self):
        chat_a = -100999111
        chat_b = -100999222
        thread_id = 777

        # Bind same thread_id in chat_a to Div 1 and in chat_b to Div 2
        database.bind_division_topic(self.div1_id, chat_a, thread_id, "drafts")
        database.bind_division_topic(self.div2_id, chat_b, thread_id, "drafts")

        topic_cache.reload_cache()

        # Cache lookups
        lookup_a = topic_cache.get_by_topic(chat_a, thread_id)
        lookup_b = topic_cache.get_by_topic(chat_b, thread_id)

        self.assertIsNotNone(lookup_a)
        self.assertEqual(lookup_a["division_id"], self.div1_id)

        self.assertIsNotNone(lookup_b)
        self.assertEqual(lookup_b["division_id"], self.div2_id)

        # Rejection of group_chat_id = None
        self.assertIsNone(topic_cache.get_by_topic(None, thread_id))
        self.assertIsNone(database.get_topic_binding(None, thread_id))

        # DB lookups parity
        db_a = database.get_topic_binding(chat_a, thread_id)
        db_b = database.get_topic_binding(chat_b, thread_id)
        self.assertEqual(db_a["division_id"], self.div1_id)
        self.assertEqual(db_b["division_id"], self.div2_id)

    # 2. ROUNDS TABLE COMPOSITE SCHEMA & INDEPENDENT LIFECYCLE
    def test_02_rounds_composite_schema_and_lifecycle(self):
        # Create Round 1 for Div 1 with deadline A
        database.create_round(round_number=1, deadline="10.10.2026 18:00", division_id=self.div1_id)
        # Create Round 1 for Div 2 with deadline B
        database.create_round(round_number=1, deadline="20.10.2026 20:00", division_id=self.div2_id)

        r1_div1 = database.get_round_info(1, division_id=self.div1_id)
        r1_div2 = database.get_round_info(1, division_id=self.div2_id)

        self.assertIsNotNone(r1_div1)
        self.assertIsNotNone(r1_div2)
        self.assertEqual(r1_div1["deadline"], "10.10.2026 18:00")
        self.assertEqual(r1_div2["deadline"], "20.10.2026 20:00")
        self.assertEqual(r1_div1["is_open"], 0)
        self.assertEqual(r1_div2["is_open"], 0)

        # Open Round 1 strictly in Div 1 with deadline preserved
        database.update_round_status(round_number=1, is_open=True, deadline="10.10.2026 18:00", division_id=self.div1_id)

        r1_div1_opened = database.get_round_info(1, division_id=self.div1_id)
        r1_div2_after = database.get_round_info(1, division_id=self.div2_id)

        self.assertEqual(r1_div1_opened["is_open"], 1)
        self.assertEqual(r1_div2_after["is_open"], 0, "Opening Round 1 in Div 1 must NOT open Round 1 in Div 2")

        # Open rounds with deadlines query
        open_div1 = database.get_open_rounds_with_deadlines(division_id=self.div1_id)
        open_div2 = database.get_open_rounds_with_deadlines(division_id=self.div2_id)
        self.assertEqual(len(open_div1), 1)
        self.assertEqual(len(open_div2), 0)

        # Safe clearing of Div 1 preserves Div 2 rounds
        database.clear_matches_by_division(self.div1_id)
        self.assertIsNone(database.get_round_info(1, division_id=self.div1_id))
        self.assertIsNotNone(database.get_round_info(1, division_id=self.div2_id))

    # 3. STANDINGS ISOLATION
    def test_03_standings_isolation(self):
        # Create match in Div 1
        with database.transaction() as conn:
            conn.execute("""
                INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team,
                                     player1_score, player2_score, status, division_id)
                VALUES (1, ?, ?, ?, 'Opponent Team 1', 3, 1, 'confirmed', ?)
            """, (self.player1_id, self.div1_admin_id, f"ClubA_{self.uid}", self.div1_id))

        # Create match in Div 2
        with database.transaction() as conn:
            conn.execute("""
                INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team,
                                     player1_score, player2_score, status, division_id)
                VALUES (1, ?, ?, ?, 'Opponent Team 2', 2, 0, 'confirmed', ?)
            """, (self.player2_id, self.div2_admin_id, f"ClubB_{self.uid}", self.div2_id))

        standings_div1 = database.get_standings(division_id=self.div1_id)
        standings_div2 = database.get_standings(division_id=self.div2_id)

        div1_teams = [s["team_name"] for s in standings_div1]
        div2_teams = [s["team_name"] for s in standings_div2]

        self.assertIn(f"ClubA_{self.uid}", div1_teams)
        self.assertNotIn(f"ClubB_{self.uid}", div1_teams, "Div 2 team must not appear in Div 1 standings")

        self.assertIn(f"ClubB_{self.uid}", div2_teams)
        self.assertNotIn(f"ClubA_{self.uid}", div2_teams, "Div 1 team must not appear in Div 2 standings")

    # 4. /table COMMAND EXECUTION WITHOUT DICTIONARY CRASH
    async def test_04_group_table_command_no_crash(self):
        chat_id = -100888333
        thread_id = 555
        database.bind_division_topic(self.div1_id, chat_id, thread_id, "tables")
        topic_cache.reload_cache()

        update = MagicMock()
        update.effective_chat.id = chat_id
        update.message.message_thread_id = thread_id
        update.message.reply_photo = AsyncMock()

        context = MagicMock()
        context.args = []

        with patch("handlers.base.generate_league_table_image", return_value=b"fake_image_bytes"):
            await group_table_command(update, context)

        update.message.reply_photo.assert_called_once()
        call_kwargs = update.message.reply_photo.call_args[1]
        self.assertIn(f"refresh_div_table_{self.div1_id}", str(call_kwargs.get("reply_markup")))
        self.assertIn(f"Fix Div 1 {self.uid}", call_kwargs.get("caption"))

    # 5. ROUND ROBIN PROTECTION AGAINST RE-GENERATION WHEN MATCHES ARE CONFIRMED
    async def test_05_round_robin_repeat_generation_protection(self):
        # Insert a confirmed match into Div 1
        with database.transaction() as conn:
            conn.execute("""
                INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team,
                                     player1_score, player2_score, status, division_id)
                VALUES (1, ?, ?, 'Team1', 'Team2', 2, 1, 'confirmed', ?)
            """, (self.player1_id, self.div1_admin_id, self.div1_id))

        self.assertTrue(database.division_has_played_matches(self.div1_id))

        query = MagicMock()
        query.from_user.id = self.div1_admin_id
        query.data = f"admin_gen_exec_{self.div1_id}"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.effective_user.id = self.div1_admin_id
        update.callback_query = query
        context = MagicMock()

        await admin_generate_matches_execute(update, context)

        # Generation should be blocked
        query.edit_message_text.assert_called_once()
        text_arg = query.edit_message_text.call_args[0][0]
        self.assertIn("Генерация заблокирована", text_arg)

        # Match must NOT be deleted
        with database.transaction() as conn:
            count = conn.execute("SELECT COUNT(*) FROM matches WHERE division_id = ?", (self.div1_id,)).fetchone()[0]
        self.assertEqual(count, 1)

    # 6. CROSS-DIVISION TOPIC REASSIGNMENT RBAC
    async def test_06_cross_division_topic_reassignment_protection(self):
        chat_id = -100555666
        thread_id = 999
        database.bind_division_topic(self.div1_id, chat_id, thread_id, "drafts")

        # Div 2 Admin attempts to steal topic 999 for Div 2
        query = MagicMock()
        query.from_user.id = self.div2_admin_id  # Admin of Div 2, NOT Div 1
        query.message.chat.id = chat_id
        query.message.message_thread_id = thread_id
        query.data = f"reassign_topic_confirm:{self.div2_id}:d"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.effective_user.id = self.div2_admin_id
        update.callback_query = query
        context = MagicMock()

        await cb_reassign_topic_confirm(update, context)

        # cb_reassign_topic_confirm acknowledges query, then sends alert
        self.assertGreaterEqual(query.answer.call_count, 1)
        alert_calls = [c for c in query.answer.call_args_list if c[0] and "принадлежит дивизиону" in c[0][0]]
        self.assertTrue(len(alert_calls) > 0, "Expected cross-division theft warning alert in query.answer")

        # Verify topic binding did NOT change
        binding = database.get_topic_binding(chat_id, thread_id)
        self.assertEqual(binding["division_id"], self.div1_id)

    # 7. RESET LEAGUE RESTRICTED TO GLOBAL ADMIN ONLY
    async def test_07_clear_league_restricted_to_global_admin(self):
        # Division admin (has admin access, but is NOT in config.ADMIN_IDS)
        query = MagicMock()
        query.from_user.id = self.div1_admin_id
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.effective_user.id = self.div1_admin_id
        update.callback_query = query
        context = MagicMock()

        orig_admins = list(config.ADMIN_IDS)
        config.ADMIN_IDS = [self.global_admin_id]

        try:
            res = await admin_clear_league_start(update, context)
            alert_calls = [c for c in query.answer.call_args_list if c[0] and "только Главному Администратору" in c[0][0]]
            self.assertTrue(len(alert_calls) > 0, "Expected reset league restricted to Global Admin alert")
        finally:
            config.ADMIN_IDS = orig_admins

    # 8. API AUTH: MOCK ADMIN GATE & 24H FRESHNESS
    def test_08_api_auth_hardening(self):
        # 1. mock_admin without ALLOW_DEV_AUTH_BYPASS -> Rejected
        os.environ.pop("ALLOW_DEV_AUTH_BYPASS", None)
        with patch("api.auth.is_admin", return_value=True):
            user = get_authenticated_user(f"mock_admin_{self.global_admin_id}")
            self.assertIsNone(user, "mock_admin must be rejected when ALLOW_DEV_AUTH_BYPASS is not set")

        # 2. mock_admin with ALLOW_DEV_AUTH_BYPASS=1 -> Accepted for real admin
        os.environ["ALLOW_DEV_AUTH_BYPASS"] = "1"
        try:
            with patch("api.auth.is_admin", return_value=True):
                user = get_authenticated_user(f"mock_admin_{self.global_admin_id}")
                self.assertIsNotNone(user)
                self.assertEqual(user["id"], self.global_admin_id)
        finally:
            os.environ.pop("ALLOW_DEV_AUTH_BYPASS", None)

        # 3. auth_date expired (> 86400s)
        old_auth_date = int(time.time()) - 90000  # 25 hours ago
        fake_parsed = f"auth_date={old_auth_date}&user=%7B%22id%22%3A123%7D&hash=fakehash"
        with patch("api.auth.hmac.compare_digest", return_value=True):
            res = validate_telegram_init_data(fake_parsed)
            self.assertIsNone(res, "auth_date older than 24h must be rejected")


if __name__ == "__main__":
    unittest.main()
