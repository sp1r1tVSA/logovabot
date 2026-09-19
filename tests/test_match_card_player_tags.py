"""
tests/test_match_card_player_tags.py

Тестирование проброса тегов/юзернеймов игроков (player1_username, player2_username)
для карточки матча в линии ставок Logovo.bet.
"""

import os
import tempfile
import unittest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

import database
from api.routes_markets import handle_get_tours
from api.routes_matches import handle_get_matches


class TestMatchCardPlayerTags(unittest.TestCase):
    def setUp(self):
        self._orig_db_path = database.DB_PATH
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        database.DB_PATH = self._tmp.name
        database.init_db()

        with database.transaction() as conn:
            c = conn.cursor()
            # 1. Создаём пользователей с командами
            c.execute("""
                INSERT INTO users (telegram_id, username, team_name, role)
                VALUES (101, 'Serghe1KO', 'Будё Глимт', 'user')
            """)
            c.execute("""
                INSERT INTO users (telegram_id, username, team_name, role)
                VALUES (102, 'Prizrakks', 'Лос Анджелес', 'user')
            """)
            # 2. Создаём тур 1
            c.execute("""
                INSERT INTO rounds (season_id, division_id, round_number, is_open, bets_open)
                VALUES (1, 1, 1, 1, 1)
            """)
            # 3. Создаём матч, привязанный по player_id и team_name
            c.execute("""
                INSERT INTO matches (id, season_id, division_id, round_number, player1_id, player2_id,
                                     player1_team, player2_team, status)
                VALUES (1001, 1, 1, 1, 101, 102, 'Будё Глимт', 'Лос Анджелес', 'pending')
            """)
            # 4. Создаём рынок для матча 1001
            c.execute("""
                INSERT INTO bet_markets (match_id, tour, team1_name, team2_name, odd_p1, odd_x, odd_p2, is_active)
                VALUES (1001, 1, 'Будё Глимт', 'Лос Анджелес', 1.95, 3.20, 2.10, 1)
            """)

            # 5. Создаём матч 1002 БЕЗ player1_id/player2_id (только по названию команд)
            c.execute("""
                INSERT INTO matches (id, season_id, division_id, round_number, player1_id, player2_id,
                                     player1_team, player2_team, status)
                VALUES (1002, 1, 1, 1, NULL, NULL, 'Будё Глимт', 'Лос Анджелес', 'pending')
            """)
            c.execute("""
                INSERT INTO bet_markets (match_id, tour, team1_name, team2_name, odd_p1, odd_x, odd_p2, is_active)
                VALUES (1002, 1, 'Будё Глимт', 'Лос Анджелес', 2.00, 3.10, 2.20, 1)
            """)

    def tearDown(self):
        database.DB_PATH = self._orig_db_path
        try:
            os.remove(self._tmp.name)
        except OSError:
            pass

    def test_get_active_bet_markets_returns_player_usernames_by_id(self):
        markets = database.get_active_bet_markets(tour=1, division_id=1, season_id=1)
        m1 = next((m for m in markets if m["match_id"] == 1001), None)
        self.assertIsNotNone(m1)
        self.assertEqual(m1["player1_username"], "Serghe1KO")
        self.assertEqual(m1["player2_username"], "Prizrakks")

    def test_get_active_bet_markets_returns_player_usernames_by_team_fallback(self):
        markets = database.get_active_bet_markets(tour=1, division_id=1, season_id=1)
        m2 = next((m for m in markets if m["match_id"] == 1002), None)
        self.assertIsNotNone(m2)
        self.assertEqual(m2["player1_username"], "Serghe1KO")
        self.assertEqual(m2["player2_username"], "Prizrakks")

    def test_get_bet_market_by_match_id_returns_player_usernames(self):
        m = database.get_bet_market_by_match_id(1001)
        self.assertIsNotNone(m)
        self.assertEqual(m["player1_username"], "Serghe1KO")
        self.assertEqual(m["player2_username"], "Prizrakks")

    def test_get_matches_by_round_returns_player_usernames(self):
        matches = database.get_matches_by_round(1, division_id=1, season_id=1)
        m1 = next((m for m in matches if m["id"] == 1001), None)
        self.assertIsNotNone(m1)
        self.assertEqual(m1["player1_username"], "Serghe1KO")
        self.assertEqual(m1["player2_username"], "Prizrakks")


from tests.test_phase4_betting_experience import make_valid_telegram_init_data


class TestMatchCardApiEndpoints(AioHTTPTestCase):
    async def get_application(self):
        app = web.Application()
        app.router.add_get('/api/markets/tours', handle_get_tours)
        app.router.add_get('/api/matches', handle_get_matches)
        return app

    def setUp(self):
        self._orig_db_path = database.DB_PATH
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        database.DB_PATH = self._tmp.name
        database.init_db()

        self.user_id = 998001
        self.headers = {"X-Telegram-Init-Data": make_valid_telegram_init_data(self.user_id, "api_tester")}

        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO users (telegram_id, username, team_name, role)
                VALUES (201, 'HoffenCapper', 'Хоффенхайм', 'user')
            """)
            c.execute("""
                INSERT INTO users (telegram_id, username, team_name, role)
                VALUES (202, 'LACapper', 'Лос Анджелес', 'user')
            """)
            c.execute("""
                INSERT INTO users (telegram_id, username, team_name, role)
                VALUES (?, 'api_tester', 'API FC', 'user')
            """, (self.user_id,))
            c.execute("""
                INSERT INTO rounds (season_id, division_id, round_number, is_open, bets_open)
                VALUES (1, 2, 2, 1, 1)
            """)
            c.execute("""
                INSERT INTO matches (id, season_id, division_id, round_number, player1_id, player2_id,
                                     player1_team, player2_team, status)
                VALUES (2001, 1, 2, 2, 201, 202, 'Хоффенхайм', 'Лос Анджелес', 'pending')
            """)
            c.execute("""
                INSERT INTO bet_markets (match_id, tour, team1_name, team2_name, odd_p1, odd_x, odd_p2, is_active)
                VALUES (2001, 2, 'Хоффенхайм', 'Лос Анджелес', 2.10, 3.40, 2.50, 1)
            """)
        super().setUp()

    def tearDown(self):
        super().tearDown()
        database.DB_PATH = self._orig_db_path
        try:
            os.remove(self._tmp.name)
        except OSError:
            pass

    @unittest_run_loop
    async def test_tours_markets_endpoint_includes_player_tags(self):
        resp = await self.client.request("GET", "/api/markets/tours?division_id=2", headers=self.headers)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertTrue(len(data["tours"]) > 0)
        matches = data["tours"][0]["matches"]
        self.assertTrue(len(matches) > 0)
        m = matches[0]
        self.assertEqual(m["match_id"], 2001)
        self.assertEqual(m["team1_name"], "Хоффенхайм")
        self.assertEqual(m["team2_name"], "Лос Анджелес")
        self.assertEqual(m["player1_username"], "HoffenCapper")
        self.assertEqual(m["player2_username"], "LACapper")

    @unittest_run_loop
    async def test_matches_endpoint_includes_player_tags(self):
        resp = await self.client.request("GET", "/api/matches?division_id=2", headers=self.headers)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["status"], "ok")
        matches = data["matches"]
        self.assertTrue(len(matches) > 0)
        m = matches[0]
        self.assertEqual(m["id"], 2001)
        self.assertEqual(m["player1_username"], "HoffenCapper")
        self.assertEqual(m["player2_username"], "LACapper")
