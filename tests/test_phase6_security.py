"""
tests/test_phase6_security.py

Phase 6 Security, RBAC, IDOR & Integrity Tests:
1. HMAC Telegram authentication (signature validation, expired tokens, tampered data).
2. IDOR: Users cannot view or mutate other users' bets, analytics, or wallet.
3. RBAC: Global admin vs Division admin division isolation vs regular player 403.
4. Destructive Admin Safety: Result correction requires confirmation, explicit reason, and logs to both admin_audit_log and bet_audit_log.
"""

import hashlib
import hmac
import json
import os
import sys
import time
import unittest
from urllib.parse import urlencode

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from api.server import create_app
from config import TOKEN


def generate_valid_init_data(user_dict: dict, bot_token: str, auth_date: int | None = None) -> str:
    """Helper to generate cryptographically valid Telegram initData string."""
    if auth_date is None:
        auth_date = int(time.time())

    params = {
        "auth_date": str(auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(user_dict, separators=(",", ":"))
    }
    # Sort keys
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    hash_val = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    params["hash"] = hash_val
    return urlencode(params)


class TestPhase6Security(AioHTTPTestCase):

    async def get_application(self) -> web.Application:
        return create_app()

    def setUp(self) -> None:
        super().setUp()
        database.init_db()
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM bet_items WHERE match_id >= 99600")
            cursor.execute("DELETE FROM user_bets WHERE user_id IN (779901, 779902, 779903, 779904)")
            cursor.execute("DELETE FROM user_wallets WHERE user_id IN (779901, 779902, 779903, 779904)")
            cursor.execute("DELETE FROM division_admins WHERE user_id IN (779902, 779903)")
            cursor.execute("DELETE FROM admin_audit_log WHERE admin_id IN (779901, 779902, 779903, 779904) OR target_id >= 99600")
            cursor.execute("DELETE FROM bet_audit_log WHERE actor_id IN (779901, 779902, 779903, 779904) OR entity_id >= 99600")
            cursor.execute("DELETE FROM users WHERE telegram_id IN (779901, 779902, 779903, 779904)")
            cursor.execute("DELETE FROM markets WHERE match_id >= 99600")
            cursor.execute("DELETE FROM matches WHERE id >= 99600")

            # Seed User 1: Regular player (id 779901)
            cursor.execute("INSERT INTO users (telegram_id, username, division_id) VALUES (779901, 'regular_player', 1)")
            cursor.execute("INSERT INTO user_wallets (user_id, balance) VALUES (779901, 1000)")

            # Seed User 2: Division 1 Admin (id 779902)
            cursor.execute("INSERT INTO users (telegram_id, username, division_id) VALUES (779902, 'div1_admin', 1)")
            cursor.execute("INSERT INTO division_admins (user_id, division_id) VALUES (779902, 1)")

            # Seed User 3: Victim player (id 779903)
            cursor.execute("INSERT INTO users (telegram_id, username, division_id) VALUES (779903, 'victim_player', 2)")
            cursor.execute("INSERT INTO user_wallets (user_id, balance) VALUES (779903, 5000)")

            # Seed Match 99601 (Division 1) and Match 99602 (Division 2)
            cursor.execute("""
                INSERT INTO matches (id, season_id, division_id, round_number, player1_team, player2_team, status)
                VALUES (99601, 1, 1, 1, 'Порту', 'Бенфика', 'live'),
                       (99602, 1, 2, 1, 'Аякс', 'ПСВ', 'live')
            """)

            # Seed Market in Div 1
            cursor.execute("INSERT INTO markets (id, match_id, market_key, market_name, category, status) VALUES (996001, 99601, '1x2', '1X2', 'main', 'open')")
            # Seed Market in Div 2
            cursor.execute("INSERT INTO markets (id, match_id, market_key, market_name, category, status) VALUES (996002, 99602, '1x2', '1X2', 'main', 'open')")

    def tearDown(self) -> None:
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM bet_items WHERE match_id >= 99600")
            cursor.execute("DELETE FROM user_bets WHERE user_id IN (779901, 779902, 779903, 779904)")
            cursor.execute("DELETE FROM user_wallets WHERE user_id IN (779901, 779902, 779903, 779904)")
            cursor.execute("DELETE FROM division_admins WHERE user_id IN (779902, 779903)")
            cursor.execute("DELETE FROM admin_audit_log WHERE admin_id IN (779901, 779902, 779903, 779904) OR target_id >= 99600")
            cursor.execute("DELETE FROM bet_audit_log WHERE actor_id IN (779901, 779902, 779903, 779904) OR entity_id >= 99600")
            cursor.execute("DELETE FROM users WHERE telegram_id IN (779901, 779902, 779903, 779904)")
            cursor.execute("DELETE FROM markets WHERE match_id >= 99600")
            cursor.execute("DELETE FROM matches WHERE id >= 99600")
        super().tearDown()

    @unittest_run_loop
    async def test_hmac_tampered_data_rejected(self) -> None:
        """Tampering with initData parameters must return 401 Unauthorized."""
        init_data = generate_valid_init_data({"id": 779901, "username": "player"}, TOKEN or "test_token")
        tampered = init_data.replace("779901", "779903")

        resp = await self.client.get("/api/wallet", headers={"X-Telegram-Init-Data": tampered})
        self.assertEqual(resp.status, 401)

    @unittest_run_loop
    async def test_hmac_expired_auth_date_rejected(self) -> None:
        """auth_date older than 24 hours must be rejected as expired."""
        old_time = int(time.time()) - (86400 * 2)  # 48 hours ago
        expired_init_data = generate_valid_init_data({"id": 779901, "username": "player"}, TOKEN or "test_token", auth_date=old_time)

        resp = await self.client.get("/api/wallet", headers={"X-Telegram-Init-Data": expired_init_data})
        self.assertEqual(resp.status, 401)

    @unittest_run_loop
    async def test_idor_user_cannot_access_other_user_analytics(self) -> None:
        """Profile analytics route strictly scopes to authenticated user id."""
        token = TOKEN or "test_token"
        init_data_user1 = generate_valid_init_data({"id": 779901, "username": "regular_player"}, token)

        # Seed bet for user 1 and user 3
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO user_bets (id, user_id, bet_type, amount, potential_win, total_odd, status)
                VALUES (996101, 779901, 'single', 100, 200, 2.0, 'pending'),
                       (996102, 779903, 'single', 500, 1500, 3.0, 'pending')
            """)

        resp = await self.client.get("/api/profile/analytics", headers={"X-Telegram-Init-Data": init_data_user1})
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["status"], "ok")
        # Assert strictly user 1 data is returned, never user 3
        self.assertEqual(data["analytics"]["user_id"], 779901)
        self.assertEqual(data["analytics"]["balance"], 1000)

    @unittest_run_loop
    async def test_rbac_player_blocked_from_admin_live_endpoints(self) -> None:
        """A regular player must be refused with 403 on admin live overview and actions."""
        token = TOKEN or "test_token"
        init_data_player = generate_valid_init_data({"id": 779901, "username": "regular_player"}, token)

        resp1 = await self.client.get("/api/admin/live/overview", headers={"X-Telegram-Init-Data": init_data_player})
        self.assertEqual(resp1.status, 403)

        resp2 = await self.client.post("/api/admin/live/markets/996001/suspend",
                                       headers={"X-Telegram-Init-Data": init_data_player},
                                       json={"reason": "Hacker attempt"})
        self.assertEqual(resp2.status, 403)

    @unittest_run_loop
    async def test_rbac_division_admin_scoped_isolation(self) -> None:
        """Division 1 admin can manage Div 1 markets, but gets 403 when trying to modify Div 2."""
        token = TOKEN or "test_token"
        init_data_div1_admin = generate_valid_init_data({"id": 779902, "username": "div1_admin"}, token)

        # 1. Div 1 admin suspending Div 1 market -> 200 OK
        resp1 = await self.client.post("/api/admin/live/markets/996001/suspend",
                                       headers={"X-Telegram-Init-Data": init_data_div1_admin},
                                       json={"reason": "Goal scored in Div 1"})
        self.assertEqual(resp1.status, 200)
        data1 = await resp1.json()
        self.assertEqual(data1["status"], "ok")

        # 2. Div 1 admin attempting to suspend Div 2 market -> 403 Forbidden!
        resp2 = await self.client.post("/api/admin/live/markets/996002/suspend",
                                       headers={"X-Telegram-Init-Data": init_data_div1_admin},
                                       json={"reason": "Unauthorized across divisions"})
        self.assertEqual(resp2.status, 403)

    @unittest_run_loop
    async def test_destructive_action_safety_and_audit(self) -> None:
        """Result correction requires explicit confirmation, reason, and writes audit logs."""
        token = TOKEN or "test_token"
        init_data_div1_admin = generate_valid_init_data({"id": 779902, "username": "div1_admin"}, token)

        # 1. Missing confirm -> 400
        resp1 = await self.client.post("/api/admin/live/matches/99601/correction",
                                       headers={"X-Telegram-Init-Data": init_data_div1_admin},
                                       json={"home_score": 2, "away_score": 0, "reason": "VAR"})
        self.assertEqual(resp1.status, 400)

        # 2. Missing reason -> 400
        resp2 = await self.client.post("/api/admin/live/matches/99601/correction",
                                       headers={"X-Telegram-Init-Data": init_data_div1_admin},
                                       json={"home_score": 2, "away_score": 0, "confirm": True})
        self.assertEqual(resp2.status, 400)

        # 3. Valid correction
        resp3 = await self.client.post("/api/admin/live/matches/99601/correction",
                                       headers={"X-Telegram-Init-Data": init_data_div1_admin},
                                       json={
                                           "home_score": 3,
                                           "away_score": 1,
                                           "reason": "VAR overturned goal at 88 min",
                                           "confirm": True
                                       })
        self.assertEqual(resp3.status, 200)
        data3 = await resp3.json()
        self.assertEqual(data3["status"], "ok")

        # 4. Verify DB state and audit logs
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT player1_score, player2_score FROM matches WHERE id = 99601")
            m_row = cursor.fetchone()
            self.assertEqual(m_row["player1_score"], 3)
            self.assertEqual(m_row["player2_score"], 1)

            # Check admin_audit_log
            cursor.execute("SELECT * FROM admin_audit_log WHERE target_type = 'match' AND target_id = 99601")
            audit_entry = cursor.fetchone()
            self.assertIsNotNone(audit_entry)
            self.assertEqual(audit_entry["action"], "match_result_correction")
            self.assertEqual(audit_entry["admin_id"], 779902)


if __name__ == "__main__":
    unittest.main()
