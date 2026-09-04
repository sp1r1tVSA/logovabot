"""
tests/test_phase9_security.py

Phase 9 — Security Red Team, RBAC & Parameter Tampering Test Suite.
Verifies:
1. 401 on unauthenticated access to risk admin endpoints.
2. 403 on player attempting to access Admin Risk Center.
3. 403 on division admin attempting cross-division access.
4. Server-authoritative payout: Client-tampered payout or odds are ignored/rejected.
5. SQL injection resilience across risk query parameters.
"""

import hashlib
import hmac
import json
import os
import sys
import time
from urllib.parse import urlencode

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

import database
from api.server import create_app
from config import TOKEN, ADMIN_IDS


def generate_valid_init_data(user_dict: dict, bot_token: str, auth_date: int | None = None) -> str:
    """Helper to generate cryptographically valid Telegram initData string."""
    if auth_date is None:
        auth_date = int(time.time())

    params = {
        "auth_date": str(auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(user_dict, separators=(",", ":"))
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    hash_val = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    params["hash"] = hash_val
    return urlencode(params)


class TestPhase9Security(AioHTTPTestCase):

    async def get_application(self) -> web.Application:
        return create_app()

    def setUp(self) -> None:
        super().setUp()
        database.init_db()
        self.player_id = 980001
        self.div_admin_id = 980002
        self.super_admin_id = ADMIN_IDS[0] if ADMIN_IDS else 980003
        self.match_id = 980011

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE telegram_id IN (?, ?, ?)", (self.player_id, self.div_admin_id, self.super_admin_id))
            cursor.execute("INSERT INTO users (telegram_id, username, role) VALUES (?, 'sec_player', 'user')", (self.player_id,))
            cursor.execute("INSERT INTO users (telegram_id, username, role) VALUES (?, 'sec_divadmin', 'division_admin')", (self.div_admin_id,))
            cursor.execute("INSERT INTO users (telegram_id, username, role) VALUES (?, 'sec_superadmin', 'admin')", (self.super_admin_id,))

            cursor.execute("DELETE FROM division_admins WHERE user_id = ?", (self.div_admin_id,))
            cursor.execute("INSERT INTO division_admins (user_id, division_id) VALUES (?, 1)", (self.div_admin_id,))

            cursor.execute("DELETE FROM matches WHERE id = ?", (self.match_id,))
            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, status)
                VALUES (?, 1, 1, 1, 'Real', 'Barca', 'open')
            """, (self.match_id,))
            cursor.execute("DELETE FROM markets WHERE match_id = ?", (self.match_id,))
            cursor.execute("INSERT INTO markets (id, match_id, market_key, market_name, status) VALUES (981, ?, 'match_result', 'Match Winner', 'open')", (self.match_id,))
            cursor.execute("""
                INSERT INTO market_selections (id, market_id, selection_key, selection_name, odds_value, status, odds_version)
                VALUES (9811, 981, 'home', 'Real', 1.80, 'active', 1)
            """)

        database.get_or_create_wallet(self.player_id)
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE user_wallets SET balance = 10000 WHERE user_id = ?", (self.player_id,))

        self.player_headers = {
            "X-Telegram-Init-Data": generate_valid_init_data({"id": self.player_id, "username": "sec_player"}, TOKEN)
        }
        self.div_admin_headers = {
            "X-Telegram-Init-Data": generate_valid_init_data({"id": self.div_admin_id, "username": "sec_divadmin"}, TOKEN)
        }
        self.super_admin_headers = {
            "X-Telegram-Init-Data": generate_valid_init_data({"id": self.super_admin_id, "username": "sec_superadmin"}, TOKEN)
        }

    @unittest_run_loop
    async def test_p9_sec_01_unauthenticated_request_rejected_401(self):
        """P9-SEC-01: Requests without valid Telegram credentials return 401 Unauthorized."""
        resp = await self.client.get("/api/admin/risk/exposure")
        self.assertEqual(resp.status, 401)
        data = await resp.json()
        self.assertEqual(data.get("error"), "unauthorized")

    @unittest_run_loop
    async def test_p9_sec_02_player_cannot_access_admin_risk_403(self):
        """P9-SEC-02: Regular player requesting admin risk endpoints receives 403 Forbidden."""
        resp = await self.client.get("/api/admin/risk/exposure", headers=self.player_headers)
        self.assertEqual(resp.status, 403)
        data = await resp.json()
        self.assertEqual(data.get("error"), "forbidden")

    @unittest_run_loop
    async def test_p9_sec_03_division_admin_cannot_access_other_division_403(self):
        """P9-SEC-03: Division admin assigned to Div 1 receives 403 when requesting Div 2 exposure."""
        resp = await self.client.get("/api/admin/risk/exposure?division_id=2", headers=self.div_admin_headers)
        self.assertEqual(resp.status, 403)
        data = await resp.json()
        self.assertEqual(data.get("error"), "forbidden")

    @unittest_run_loop
    async def test_p9_sec_04_tampered_payout_rejected_server_authoritative(self):
        """P9-SEC-04: Client sending stake=1 with fake payout=1000000 cannot manipulate potential win."""
        # Attempt to place bet with manipulated odd and fake potential_win
        payload = {
            "amount": 10,
            "selections": [{
                "match_id": self.match_id,
                "market_id": 981,
                "selection_id": 9811,
                "outcome": "home",
                "odd": 10000.0,  # Client lies that odd is 10000
                "client_payout": 1000000  # Client sends fake payout
            }],
            "idempotency_key": "sec-tamper-1"
        }
        # Calling place_user_bet directly
        ok, res = database.place_user_bet(
            user_id=self.player_id,
            amount=10,
            selections=payload["selections"],
            idempotency_key=payload["idempotency_key"]
        )
        # Should be rejected because client_odd (10000) != server_odd (1.80) -> ODDS_CHANGED
        self.assertFalse(ok)
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("error"), "ODDS_CHANGED")

        # Now test placing without odd field (pure server calculation)
        ok2, res2 = database.place_user_bet(
            user_id=self.player_id,
            amount=10,
            selections=[{
                "match_id": self.match_id,
                "market_id": 981,
                "selection_id": 9811,
                "outcome": "home",
                "client_payout": 1000000
            }],
            idempotency_key="sec-tamper-2"
        )
        self.assertTrue(ok2)
        bet_id = res2
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT potential_win FROM user_bets WHERE id = ?", (bet_id,))
            row = cursor.fetchone()
            # Server calculates 10 * 1.80 = 18 coins, client_payout 1000000 is completely discarded!
            self.assertEqual(row["potential_win"], 18)

    @unittest_run_loop
    async def test_p9_sec_05_sql_injection_payloads_safely_handled(self):
        """P9-SEC-05: SQL injection strings in query params are neutralized."""
        malicious_queries = [
            "1' OR '1'='1",
            "1; DROP TABLE users; --",
            "9999 UNION SELECT * FROM users",
            "' AND SLEEP(5) --"
        ]
        for payload in malicious_queries:
            # Querying with super admin headers
            resp = await self.client.get(f"/api/admin/risk/exposure?division_id={payload}", headers=self.super_admin_headers)
            # Must return 200 (gracefully ignored/cast) or 400/404, never 500 error
            self.assertIn(resp.status, (200, 400, 403, 404))

        # Check users table intact
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM users")
            count = cursor.fetchone()[0]
            self.assertGreater(count, 0)


if __name__ == "__main__":
    unittest.main()
