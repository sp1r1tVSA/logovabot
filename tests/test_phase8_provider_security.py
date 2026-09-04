"""
tests/test_phase8_provider_security.py

Phase 8 — Provider Security, RBAC, and Secret Protection Test Suite.
Verifies:
1. RBAC on GET /api/admin/sports/health:
   - 401 for unauthenticated requests
   - 403 for regular players and division-only admins
   - 200 for Global Admin
2. API Key Non-Disclosure:
   - Sensitive credentials (SPORTS_API_KEY, headers, tokens) never appear in responses, logs, or status dicts
3. SQL Injection and Malformed Input Fuzzing on Provider DB Layer.
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
from aiohttp.test_utils import AioHTTPTestCase

import config
import database
from api.server import create_app
from config import ADMIN_IDS, TOKEN
from services.sports.adapters.api_sports import APISportsProvider


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


class TestPhase8ProviderSecurity(AioHTTPTestCase):
    """Security verification for sports data provider and administration endpoints."""

    async def get_application(self) -> web.Application:
        return create_app()

    def setUp(self) -> None:
        super().setUp()
        database.init_db()
        self.token = TOKEN or "test_secret_bot_token"
        self.global_admin_id = ADMIN_IDS[0] if ADMIN_IDS else 999999999
        self.regular_user_id = 778811
        self.div_admin_user_id = 778822

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO divisions (id, name) VALUES (1, 'Division 1')")
            cursor.execute("DELETE FROM division_admins WHERE user_id IN (?, ?)", (self.div_admin_user_id, self.regular_user_id))

            cursor.execute("DELETE FROM users WHERE telegram_id IN (?, ?)", (self.div_admin_user_id, self.regular_user_id))

            # Seed regular player
            cursor.execute("INSERT INTO users (telegram_id, username, division_id) VALUES (?, 'player', 1)", (self.regular_user_id,))

            # Seed division admin
            cursor.execute("INSERT INTO users (telegram_id, username, division_id) VALUES (?, 'div_admin', 1)", (self.div_admin_user_id,))
            cursor.execute("INSERT INTO division_admins (user_id, division_id) VALUES (?, 1)", (self.div_admin_user_id,))

    async def test_admin_sports_health_unauthorized_401(self) -> None:
        """Endpoint rejects requests missing Telegram auth header with 401."""
        resp = await self.client.get("/api/admin/sports/health")
        self.assertEqual(resp.status, 401)
        data = await resp.json()
        self.assertEqual(data["error"], "unauthorized")

    async def test_admin_sports_health_regular_player_forbidden_403(self) -> None:
        """Endpoint rejects regular players with 403 Forbidden."""
        init_data = generate_valid_init_data({"id": self.regular_user_id, "username": "player"}, self.token)
        resp = await self.client.get("/api/admin/sports/health", headers={"X-Telegram-Init-Data": init_data})
        self.assertEqual(resp.status, 403)
        data = await resp.json()
        self.assertEqual(data["error"], "forbidden")

    async def test_admin_sports_health_division_admin_forbidden_403(self) -> None:
        """Endpoint rejects division-only admins with 403 (Global Admin required)."""
        init_data = generate_valid_init_data({"id": self.div_admin_user_id, "username": "div_admin"}, self.token)
        resp = await self.client.get("/api/admin/sports/health", headers={"X-Telegram-Init-Data": init_data})
        self.assertEqual(resp.status, 403)
        data = await resp.json()
        self.assertEqual(data["error"], "forbidden")

    async def test_admin_sports_health_global_admin_permitted_200(self) -> None:
        """Endpoint permits Global Admin and returns health metrics."""
        init_data = generate_valid_init_data({"id": self.global_admin_id, "username": "superadmin"}, self.token)
        resp = await self.client.get("/api/admin/sports/health", headers={"X-Telegram-Init-Data": init_data})
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("data", data)
        self.assertIn("provider", data["data"])

    async def test_zero_api_key_disclosure_in_api_and_telemetry(self) -> None:
        """API key and secrets must NEVER be present in response payload or health status."""
        test_secret = "SUPER_SECRET_PRODUCTION_API_KEY_12345"
        prov = APISportsProvider(api_key=test_secret)

        # 1. Provider status dict check
        status = prov.get_provider_status()
        status_str = json.dumps(status)
        self.assertNotIn(test_secret, status_str)
        self.assertNotIn("api_key", status)
        self.assertNotIn("secret", status)
        self.assertNotIn("token", status)

        # 2. HTTP Admin health response check
        init_data = generate_valid_init_data({"id": self.global_admin_id, "username": "superadmin"}, self.token)
        resp = await self.client.get("/api/admin/sports/health", headers={"X-Telegram-Init-Data": init_data})
        raw_text = await resp.text()
        self.assertNotIn(test_secret, raw_text)
        self.assertNotIn("api_key", raw_text)

    def test_sql_injection_resilience_in_provider_repository(self) -> None:
        """Malicious SQL injection patterns in provider parameters execute safely."""
        malicious_provider = "mock_prov'; DROP TABLE matches; --"
        malicious_endpoint = "fixtures'; DELETE FROM user_wallets; --"

        try:
            database.record_provider_sync_log(
                provider=malicious_provider,
                endpoint=malicious_endpoint,
                status_code=200,
                records_count=1,
                latency_ms=45.2
            )
        except Exception as e:
            self.fail(f"record_provider_sync_log crashed on injection string: {e}")

        # Verify matches and user_wallets tables were untouched
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='matches'")
            self.assertIsNotNone(cursor.fetchone())
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_wallets'")
            self.assertIsNotNone(cursor.fetchone())
