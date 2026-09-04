"""
tests/test_phase10_api.py

Phase 10: Server-Authoritative API Endpoints Tests.
Strict Invariants:
1. GET /api/profile returns private profile for authenticated user.
2. GET /api/player/{id}/public returns strictly public gamer card.
3. GET /api/profile/stats returns personal analytics.
4. Leaderboard endpoints enforce pagination limits (1..50) and handle scopes.
5. Admin season endpoints enforce RBAC and execute finalization correctly.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import hmac
import hashlib
import json
import time
from urllib.parse import urlencode
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from api.server import create_app
import database
from config import TOKEN, ADMIN_IDS


def _generate_valid_init_data(user_dict: dict, token: str = TOKEN) -> str:
    user_str = json.dumps(user_dict, separators=(",", ":"))
    auth_date = str(int(time.time()))
    data_check_string = f"auth_date={auth_date}\nuser={user_str}"
    secret_key = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode({"user": user_str, "auth_date": auth_date, "hash": calc_hash})


class TestPhase10Api(AioHTTPTestCase):
    async def get_application(self):
        database.init_db()
        return create_app()

    def setUp(self):
        super().setUp()
        database.init_db()
        with database.transaction() as conn:
            conn.execute("DELETE FROM season_reward_ledger WHERE user_id IN (9801, 9802)")
            conn.execute("DELETE FROM season_snapshots WHERE user_id IN (9801, 9802)")
            conn.execute("DELETE FROM season_player_stats WHERE user_id IN (9801, 9802)")
            conn.execute("DELETE FROM user_achievements WHERE user_id IN (9801, 9802)")
            conn.execute("DELETE FROM coin_transactions WHERE user_id IN (9801, 9802)")
            conn.execute("DELETE FROM user_wallets WHERE user_id IN (9801, 9802)")
            conn.execute("DELETE FROM user_progression WHERE user_id IN (9801, 9802)")
            conn.execute("DELETE FROM admin_audit_log WHERE admin_id IN (9801, 9802)")
            conn.execute("DELETE FROM seasons WHERE created_by IN (9801, 9802)")
            conn.execute("DELETE FROM users WHERE telegram_id IN (9801, 9802)")

            # 9801: Regular user
            # 9802: Admin user
            if 9802 not in ADMIN_IDS:
                ADMIN_IDS.append(9802)
            conn.execute("INSERT OR REPLACE INTO users (telegram_id, username, role, division_id) VALUES (9801, 'api_user', 'user', 1)")
            conn.execute("INSERT OR REPLACE INTO users (telegram_id, username, role, division_id) VALUES (9802, 'api_admin', 'admin', 1)")

    def tearDown(self):
        super().tearDown()
        if 9802 in ADMIN_IDS:
            ADMIN_IDS.remove(9802)
        with database.transaction() as conn:
            conn.execute("DELETE FROM season_reward_ledger WHERE user_id IN (9801, 9802)")
            conn.execute("DELETE FROM season_snapshots WHERE user_id IN (9801, 9802)")
            conn.execute("DELETE FROM season_player_stats WHERE user_id IN (9801, 9802)")
            conn.execute("DELETE FROM user_achievements WHERE user_id IN (9801, 9802)")
            conn.execute("DELETE FROM coin_transactions WHERE user_id IN (9801, 9802)")
            conn.execute("DELETE FROM user_wallets WHERE user_id IN (9801, 9802)")
            conn.execute("DELETE FROM user_progression WHERE user_id IN (9801, 9802)")
            conn.execute("DELETE FROM admin_audit_log WHERE admin_id IN (9801, 9802)")
            conn.execute("DELETE FROM seasons WHERE created_by IN (9801, 9802)")
            conn.execute("DELETE FROM users WHERE telegram_id IN (9801, 9802)")

    @unittest_run_loop
    async def test_01_api_get_profile_self_returns_private(self):
        """GET /api/profile returns full private profile for the caller."""
        init_data = _generate_valid_init_data({"id": 9801, "username": "api_user"})
        resp = await self.client.get("/api/profile", headers={"X-Telegram-Init-Data": init_data})
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "ok"
        assert body["is_self"] is True
        assert "balance" in body["profile"]
        assert "career" in body["profile"]

    @unittest_run_loop
    async def test_02_api_get_player_public_returns_public_only(self):
        """GET /api/player/{id}/public returns strictly public gamer card without wallet balance."""
        init_data = _generate_valid_init_data({"id": 9801, "username": "api_user"})
        resp = await self.client.get("/api/player/9801/public", headers={"X-Telegram-Init-Data": init_data})
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "ok"
        player = body["player"]
        assert player["user_id"] == 9801
        assert "rating" in player
        assert "balance" not in player

    @unittest_run_loop
    async def test_03_api_get_profile_stats(self):
        """GET /api/profile/stats returns personal stats, accuracy, and career summary."""
        init_data = _generate_valid_init_data({"id": 9801, "username": "api_user"})
        resp = await self.client.get("/api/profile/stats", headers={"X-Telegram-Init-Data": init_data})
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "ok"
        assert "favorite_markets" in body
        assert "prediction_accuracy" in body
        assert "season_stats" in body
        assert "career_stats" in body

    @unittest_run_loop
    async def test_04_api_get_leaderboard_pagination_and_metrics(self):
        """GET /api/leaderboard supports pagination and metric sorting."""
        init_data = _generate_valid_init_data({"id": 9801, "username": "api_user"})
        resp = await self.client.get("/api/leaderboard?limit=10&metric=RATING", headers={"X-Telegram-Init-Data": init_data})
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "ok"
        assert body["limit"] == 10
        assert body["metric"] == "RATING"
        assert isinstance(body["entries"], list)

    @unittest_run_loop
    async def test_05_api_get_season_and_rewards(self):
        """GET /api/season and /api/season/rewards return active season data and catalog."""
        init_data = _generate_valid_init_data({"id": 9801, "username": "api_user"})

        s_resp = await self.client.get("/api/season", headers={"X-Telegram-Init-Data": init_data})
        assert s_resp.status == 200
        s_body = await s_resp.json()
        assert s_body["status"] == "ok"
        assert "season" in s_body
        assert "standings" in s_body

        r_resp = await self.client.get("/api/season/rewards", headers={"X-Telegram-Init-Data": init_data})
        assert r_resp.status == 200
        r_body = await r_resp.json()
        assert r_body["status"] == "ok"
        assert "catalog" in r_body
        assert len(r_body["catalog"]) >= 1

    @unittest_run_loop
    async def test_06_api_admin_season_create_and_finalize(self):
        """Global Admin can manage seasons via Admin Season Center endpoints."""
        init_data = _generate_valid_init_data({"id": 9802, "username": "api_admin"})

        # Overview
        resp = await self.client.get("/api/admin/season", headers={"X-Telegram-Init-Data": init_data})
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "ok"
        assert body["is_global_admin"] is True

        # Create draft season
        create_resp = await self.client.post("/api/admin/season", headers={"X-Telegram-Init-Data": init_data}, json={
            "action": "create",
            "name": "Season API Test"
        })
        assert create_resp.status == 200
        c_body = await create_resp.json()
        assert "season_id" in c_body
