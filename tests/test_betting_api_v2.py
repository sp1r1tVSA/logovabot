"""
tests/test_betting_api_v2.py
Comprehensive integration tests for all Logovo.bet v2.0 REST API endpoints.
"""

import sys
import os
import unittest
import json
import time
import hmac
import hashlib
import urllib.parse
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from aiohttp import web

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import config
from api.server import create_app
import services.odds_engine as odds_engine


class TestBettingApiV2(AioHTTPTestCase):
    async def get_application(self):
        return create_app()

    def _generate_mock_init_data(self, user_id=999801, username="test_kapper"):
        token = config.TOKEN or "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
        user_dict = {
            "id": user_id,
            "first_name": "Tester",
            "username": username
        }
        data = {
            "auth_date": str(int(time.time())),
            "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
            "user": json.dumps(user_dict, separators=(",", ":"))
        }
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret_key = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
        hash_val = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        data["hash"] = hash_val
        return urllib.parse.urlencode(data)

    def setUp(self):
        super().setUp()
        database.init_db()
        self.user_id = 999801
        self.m1_id = 999811
        init_data_str = self._generate_mock_init_data(self.user_id, "test_kapper")
        self.headers = {"X-Telegram-Init-Data": init_data_str}

        with database.transaction() as conn:
            conn.execute("DELETE FROM favorites WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM saved_coupons WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM notifications WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM user_wallets WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM bet_items WHERE match_id = ?", (self.m1_id,))
            conn.execute("DELETE FROM markets WHERE match_id = ?", (self.m1_id,))
            conn.execute("DELETE FROM matches WHERE id = ?", (self.m1_id,))
            conn.execute("DELETE FROM users WHERE telegram_id = ?", (self.user_id,))

            conn.execute("""
                INSERT INTO users (telegram_id, username, role)
                VALUES (?, 'test_kapper', 'admin')
            """, (self.user_id,))

            conn.execute("""
                INSERT INTO matches (id, tournament_id, round_number, player1_team, player2_team, status, live_minute, player1_score, player2_score)
                VALUES (?, 1, 1, 'Интер', 'Милан', 'live', 65, 2, 1)
            """, (self.m1_id,))

        database.set_feature_flag("betting_market", "public")
        database.get_or_create_wallet(self.user_id)

    def tearDown(self):
        super().tearDown()
        with database.transaction() as conn:
            conn.execute("DELETE FROM favorites WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM saved_coupons WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM notifications WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM user_wallets WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM bet_items WHERE match_id = ?", (self.m1_id,))
            conn.execute("DELETE FROM markets WHERE match_id = ?", (self.m1_id,))
            conn.execute("DELETE FROM matches WHERE id = ?", (self.m1_id,))
            conn.execute("DELETE FROM users WHERE telegram_id = ?", (self.user_id,))

    @unittest_run_loop
    async def test_get_match_markets_and_odds_history(self):
        # 1. Get markets for match
        resp = await self.client.get(f"/api/matches/{self.m1_id}/markets", headers=self.headers)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertGreaterEqual(len(data["markets"]), 6)

        market_id = data["markets"][0]["id"]

        # 2. Get odds history
        resp_hist = await self.client.get(f"/api/markets/{market_id}/odds-history?selection_key=p1", headers=self.headers)
        self.assertEqual(resp_hist.status, 200)
        data_hist = await resp_hist.json()
        self.assertEqual(data_hist["status"], "ok")

    @unittest_run_loop
    async def test_match_stats_h2h_insights_live(self):
        # Stats
        r_stats = await self.client.get(f"/api/matches/{self.m1_id}/stats", headers=self.headers)
        self.assertEqual(r_stats.status, 200)
        d_stats = await r_stats.json()
        self.assertIn("team1", d_stats)

        # H2H
        r_h2h = await self.client.get(f"/api/matches/{self.m1_id}/h2h", headers=self.headers)
        self.assertEqual(r_h2h.status, 200)
        d_h2h = await r_h2h.json()
        self.assertIn("summary", d_h2h)

        # Insights
        r_ins = await self.client.get(f"/api/matches/{self.m1_id}/insights", headers=self.headers)
        self.assertEqual(r_ins.status, 200)
        d_ins = await r_ins.json()
        self.assertGreaterEqual(len(d_ins["insights"]), 1)

        # Live
        r_live = await self.client.get(f"/api/matches/{self.m1_id}/live", headers=self.headers)
        self.assertEqual(r_live.status, 200)
        d_live = await r_live.json()
        self.assertEqual(d_live["score1"], 2)
        self.assertEqual(d_live["score2"], 1)

    @unittest_run_loop
    async def test_saved_coupons_crud(self):
        # Save coupon
        coupon_payload = {
            "name": "Мой топ экспресс",
            "total_odd": 3.45,
            "selections": [{"match_id": self.m1_id, "outcome": "p1", "odd": 2.10}]
        }
        r_save = await self.client.post("/api/saved-coupons", headers=self.headers, json=coupon_payload)
        self.assertEqual(r_save.status, 200)
        d_save = await r_save.json()
        saved_id = d_save["saved_id"]

        # List saved coupons
        r_list = await self.client.get("/api/saved-coupons", headers=self.headers)
        self.assertEqual(r_list.status, 200)
        d_list = await r_list.json()
        self.assertEqual(len(d_list["saved_coupons"]), 1)

        # Delete saved coupon
        r_del = await self.client.delete(f"/api/saved-coupons/{saved_id}", headers=self.headers)
        self.assertEqual(r_del.status, 200)

        # List again -> 0
        r_list2 = await self.client.get("/api/saved-coupons", headers=self.headers)
        d_list2 = await r_list2.json()
        self.assertEqual(len(d_list2["saved_coupons"]), 0)

    @unittest_run_loop
    async def test_favorites_crud(self):
        # Add favorite
        r_fav = await self.client.post("/api/favorites", headers=self.headers, json={"target_type": "match", "target_id": self.m1_id})
        self.assertEqual(r_fav.status, 200)

        # Get favorites
        r_get = await self.client.get("/api/favorites", headers=self.headers)
        self.assertEqual(r_get.status, 200)
        d_get = await r_get.json()
        self.assertEqual(len(d_get["favorites"]), 1)
        fav_id = d_get["favorites"][0]["id"]

        # Delete favorite
        r_del = await self.client.delete(f"/api/favorites/{fav_id}", headers=self.headers)
        self.assertEqual(r_del.status, 200)

    @unittest_run_loop
    async def test_user_stats_me(self):
        resp = await self.client.get("/api/stats/me", headers=self.headers)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("roi_pct", data["stats"])
        self.assertIn("win_rate_pct", data["stats"])
        self.assertIn("balance", data["stats"])

    @unittest_run_loop
    async def test_tournaments_and_standings(self):
        r_t = await self.client.get("/api/tournaments", headers=self.headers)
        self.assertEqual(r_t.status, 200)

        r_st = await self.client.get("/api/tournaments/1/standings", headers=self.headers)
        self.assertEqual(r_st.status, 200)


if __name__ == "__main__":
    unittest.main()
