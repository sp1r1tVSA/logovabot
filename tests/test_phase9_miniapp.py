"""
tests/test_phase9_miniapp.py

Phase 9 — Mini App API & Cashout HTTP Integration Test Suite.
Verifies:
1. GET  /api/predictions/{id}/cashout-quote
2. POST /api/predictions/{id}/cashout
3. Rejection of duplicate cashout via API with ALREADY_SETTLED
4. GET  /api/admin/risk/limits
5. POST /api/admin/risk/limits (Global Admin configuration)
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


class TestPhase9MiniApp(AioHTTPTestCase):

    async def get_application(self) -> web.Application:
        return create_app()

    def setUp(self) -> None:
        super().setUp()
        database.init_db()
        self.player_id = 995001
        self.super_admin_id = ADMIN_IDS[0] if ADMIN_IDS else 995002
        self.match_id = 995011

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE telegram_id IN (?, ?)", (self.player_id, self.super_admin_id))
            cursor.execute("INSERT INTO users (telegram_id, username, role) VALUES (?, 'app_player', 'user')", (self.player_id,))
            cursor.execute("INSERT INTO users (telegram_id, username, role) VALUES (?, 'app_admin', 'admin')", (self.super_admin_id,))

            cursor.execute("DELETE FROM matches WHERE id = ?", (self.match_id,))
            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, status)
                VALUES (?, 1, 1, 1, 'Ajax', 'Feyenoord', 'open')
            """, (self.match_id,))
            cursor.execute("DELETE FROM markets WHERE match_id = ?", (self.match_id,))
            cursor.execute("INSERT INTO markets (id, match_id, market_key, market_name, status) VALUES (9951, ?, 'match_result', 'Match Winner', 'open')", (self.match_id,))
            cursor.execute("""
                INSERT INTO market_selections (id, market_id, selection_key, selection_name, odds_value, status, odds_version)
                VALUES (99511, 9951, 'home', 'Ajax', 2.00, 'active', 1)
            """)

        database.get_or_create_wallet(self.player_id)
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE user_wallets SET balance = 5000 WHERE user_id = ?", (self.player_id,))

        self.player_headers = {
            "X-Telegram-Init-Data": generate_valid_init_data({"id": self.player_id, "username": "app_player"}, TOKEN)
        }
        self.super_admin_headers = {
            "X-Telegram-Init-Data": generate_valid_init_data({"id": self.super_admin_id, "username": "app_admin"}, TOKEN)
        }

    @unittest_run_loop
    async def test_p9_app_01_cashout_quote_endpoint(self):
        """P9-APP-01: GET /api/predictions/{id}/cashout-quote returns live quote."""
        ok, bet_id = database.place_user_bet(
            user_id=self.player_id,
            amount=100,
            selections=[{"match_id": self.match_id, "market_id": 9951, "selection_id": 99511, "outcome": "home", "odds": 2.00}],
            idempotency_key="app-bet-1"
        )
        self.assertTrue(ok)

        resp = await self.client.get(f"/api/predictions/{bet_id}/cashout-quote", headers=self.player_headers)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data.get("status"), "ok")
        quote = data.get("quote", {})
        self.assertTrue(quote.get("available"))
        self.assertGreater(quote.get("offer", 0), 0)
        self.assertEqual(quote.get("stake"), 100)
        self.assertEqual(quote.get("potential_win"), 200)

    @unittest_run_loop
    async def test_p9_app_02_cashout_execute_endpoint(self):
        """P9-APP-02: POST /api/predictions/{id}/cashout executes atomic cashout."""
        ok, bet_id = database.place_user_bet(
            user_id=self.player_id,
            amount=100,
            selections=[{"match_id": self.match_id, "market_id": 9951, "selection_id": 99511, "outcome": "home", "odds": 2.00}],
            idempotency_key="app-bet-2"
        )
        self.assertTrue(ok)

        bal_before = database.get_or_create_wallet(self.player_id)["balance"]

        resp = await self.client.post(
            f"/api/predictions/{bet_id}/cashout",
            headers=self.player_headers,
            json={"idempotency_key": "app-cash-1"}
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data.get("status"), "ok")
        result = data.get("result", {})
        payout = result.get("cashout_payout") or result.get("payout")
        self.assertGreater(payout, 0)

        # Balance credited
        bal_after = database.get_or_create_wallet(self.player_id)["balance"]
        self.assertEqual(bal_after, bal_before + payout)

    @unittest_run_loop
    async def test_p9_app_03_duplicate_cashout_returns_structured_error(self):
        """P9-APP-03: Repeated cashout via API returns 400 with ALREADY_SETTLED error."""
        ok, bet_id = database.place_user_bet(
            user_id=self.player_id,
            amount=100,
            selections=[{"match_id": self.match_id, "market_id": 9951, "selection_id": 99511, "outcome": "home", "odds": 2.00}],
            idempotency_key="app-bet-3"
        )
        self.assertTrue(ok)

        # First cashout
        r1 = await self.client.post(f"/api/predictions/{bet_id}/cashout", headers=self.player_headers, json={})
        self.assertEqual(r1.status, 200)

        # Second cashout attempt
        r2 = await self.client.post(f"/api/predictions/{bet_id}/cashout", headers=self.player_headers, json={})
        self.assertEqual(r2.status, 400)
        d2 = await r2.json()
        self.assertEqual(d2.get("status"), "error")
        self.assertEqual(d2.get("error"), "ALREADY_SETTLED")

    @unittest_run_loop
    async def test_p9_app_04_get_centralized_limits_endpoint(self):
        """P9-APP-04: GET /api/admin/risk/limits returns system limits."""
        resp = await self.client.get("/api/admin/risk/limits", headers=self.super_admin_headers)
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data.get("status"), "ok")
        limits = data.get("limits", {})
        self.assertIn("min_bet", limits)
        self.assertIn("max_bet", limits)
        self.assertIn("max_payout", limits)

    @unittest_run_loop
    async def test_p9_app_05_update_centralized_limits_endpoint(self):
        """P9-APP-05: POST /api/admin/risk/limits updates limits in DB config."""
        update_payload = {
            "scope_type": "division",
            "scope_id": 1,
            "limit_key": "max_bet",
            "limit_value": 35000
        }
        resp = await self.client.post(
            "/api/admin/risk/limits",
            headers=self.super_admin_headers,
            json=update_payload
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data.get("status"), "ok")

        # Verify update reflected
        resp2 = await self.client.get("/api/admin/risk/limits?division_id=1", headers=self.super_admin_headers)
        self.assertEqual(resp2.status, 200)
        d2 = await resp2.json()
        self.assertEqual(d2.get("limits", {}).get("max_bet"), 35000)


if __name__ == "__main__":
    unittest.main()
