"""
tests/test_phase10_security.py

Phase 10: Security, RBAC & IDOR Protection Tests.
Strict Invariants:
1. Unauthenticated requests to gamification endpoints return 401.
2. Regular players accessing Admin Season Center return 403 Forbidden.
3. Division Admins can only view and configure their assigned division(s).
4. Division Admins cannot finalize seasons (Global Admin only).
5. IDOR: Querying /api/profile/{other_user} returns strictly public data.
6. SQL injection payloads in leaderboard queries are safely neutralized.
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


class TestPhase10Security(AioHTTPTestCase):
    async def get_application(self):
        database.init_db()
        return create_app()

    def setUp(self):
        super().setUp()
        database.init_db()
        with database.transaction() as conn:
            conn.execute("DELETE FROM season_reward_ledger WHERE user_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM season_snapshots WHERE user_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM season_player_stats WHERE user_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM user_achievements WHERE user_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM coin_transactions WHERE user_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM user_wallets WHERE user_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM user_progression WHERE user_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM division_admins WHERE user_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM admin_audit_log WHERE admin_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM users WHERE telegram_id IN (9001, 9002, 9003)")

            # 9001: Regular Player
            # 9002: Division Admin for Division 2
            # 9003: Global Admin (add to ADMIN_IDS)
            if 9003 not in ADMIN_IDS:
                ADMIN_IDS.append(9003)
            conn.execute("INSERT OR REPLACE INTO users (telegram_id, username, role, division_id) VALUES (9001, 'regular_player', 'user', 1)")
            conn.execute("INSERT OR REPLACE INTO users (telegram_id, username, role, division_id) VALUES (9002, 'div_admin', 'admin', 2)")
            conn.execute("INSERT OR REPLACE INTO division_admins (division_id, user_id) VALUES (2, 9002)")

    def tearDown(self):
        super().tearDown()
        if 9003 in ADMIN_IDS:
            ADMIN_IDS.remove(9003)
        with database.transaction() as conn:
            conn.execute("DELETE FROM season_reward_ledger WHERE user_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM season_snapshots WHERE user_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM season_player_stats WHERE user_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM user_achievements WHERE user_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM coin_transactions WHERE user_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM user_wallets WHERE user_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM user_progression WHERE user_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM division_admins WHERE user_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM admin_audit_log WHERE admin_id IN (9001, 9002, 9003)")
            conn.execute("DELETE FROM users WHERE telegram_id IN (9001, 9002, 9003)")

    @unittest_run_loop
    async def test_01_unauthenticated_request_returns_401(self):
        """Verify request without HMAC init-data returns 401."""
        resp = await self.client.get("/api/profile")
        assert resp.status == 401

    @unittest_run_loop
    async def test_02_regular_player_accessing_admin_season_returns_403(self):
        """Verify regular player receives 403 when accessing Admin Season Center."""
        init_data = _generate_valid_init_data({"id": 9001, "username": "regular_player"})
        resp = await self.client.get("/api/admin/season", headers={"X-Telegram-Init-Data": init_data})
        assert resp.status == 403

    @unittest_run_loop
    async def test_03_division_admin_scoped_to_assigned_division(self):
        """Verify Division Admin for Div 2 can access Div 2 but is blocked from configuring Div 1."""
        init_data = _generate_valid_init_data({"id": 9002, "username": "div_admin"})

        # GET admin season overview
        resp = await self.client.get("/api/admin/season", headers={"X-Telegram-Init-Data": init_data})
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "ok"
        accessible_divs = [d["division"]["id"] for d in body["accessible_divisions"]]
        assert accessible_divs == [2], f"Division Admin must only see Div 2, saw {accessible_divs}"

        # Attempt to configure Division 1 rules -> 403
        cfg_resp = await self.client.post("/api/admin/season", headers={"X-Telegram-Init-Data": init_data}, json={
            "action": "configure_rules",
            "season_id": 1,
            "division_id": 1,
            "promotion_slots": 5
        })
        assert cfg_resp.status == 403

    @unittest_run_loop
    async def test_04_division_admin_cannot_finalize_season(self):
        """Verify Division Admin cannot finalize seasons (Global Admin only)."""
        init_data = _generate_valid_init_data({"id": 9002, "username": "div_admin"})
        resp = await self.client.post("/api/admin/season/finalize", headers={"X-Telegram-Init-Data": init_data}, json={
            "season_id": 1,
            "confirm": True
        })
        assert resp.status == 403

    @unittest_run_loop
    async def test_05_idor_protection_public_vs_private_profile(self):
        """Verify querying /api/profile/{other_user} returns strictly public profile without balance."""
        database.add_coins(9002, 100000, tx_type="deposit")
        init_data = _generate_valid_init_data({"id": 9001, "username": "regular_player"})

        # User 9001 queries 9002's profile
        resp = await self.client.get("/api/profile/9002", headers={"X-Telegram-Init-Data": init_data})
        assert resp.status == 200
        body = await resp.json()
        prof = body["profile"]
        assert body["is_self"] is False
        assert "balance" not in prof, "IDOR Vulnerability: balance leaked to other user!"

    @unittest_run_loop
    async def test_06_sql_injection_defense_in_leaderboard_queries(self):
        """Verify SQL injection attempt in query params is safely handled."""
        init_data = _generate_valid_init_data({"id": 9001, "username": "regular_player"})
        sqli = "1; DROP TABLE users; --"
        resp = await self.client.get(f"/api/leaderboard?division_id={sqli}", headers={"X-Telegram-Init-Data": init_data})
        # Must return 200 or 400, never 500 or execute DROP TABLE
        assert resp.status in (200, 400)
        # Verify users table still intact
        with database.transaction() as conn:
            assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
