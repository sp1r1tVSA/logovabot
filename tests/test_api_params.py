"""Malformed numeric input answers 400, and division admins stay in their division."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

import database
from api.params import parse_int
from api.server import create_app
from config import TOKEN

DIV_ADMIN = 779951


def _init_data(user_id: int) -> str:
    params = {"auth_date": str(int(time.time())), "query_id": "q",
              "user": json.dumps({"id": user_id, "first_name": "A"}, separators=(",", ":"))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(params)


class TestParseInt:
    def test_default_for_missing_and_blank(self):
        assert parse_int(None, "limit", 50) == 50
        assert parse_int("  ", "limit", 50) == 50

    def test_valid_values(self):
        assert parse_int("7", "limit") == 7
        assert parse_int(3, "limit") == 3

    @pytest.mark.parametrize("raw", ["abc", "1.5", True, [1], {"a": 1}])
    def test_garbage_is_400(self, raw):
        with pytest.raises(web.HTTPBadRequest) as exc:
            parse_int(raw, "limit", 50)
        assert json.loads(exc.value.text)["error"] == "invalid_limit"

    def test_required_and_bounds(self):
        with pytest.raises(web.HTTPBadRequest):
            parse_int(None, "id")
        with pytest.raises(web.HTTPBadRequest):
            parse_int("-1", "offset", 0, minimum=0)


class TestAdminRoutes(AioHTTPTestCase):
    async def get_application(self):
        return create_app()

    def setUp(self):
        super().setUp()
        with database.transaction() as conn:
            conn.execute("DELETE FROM division_admins WHERE user_id = ?", (DIV_ADMIN,))
            conn.execute("INSERT OR IGNORE INTO users (telegram_id, username, division_id) VALUES (?, 'div1adm', 1)", (DIV_ADMIN,))
            conn.execute("INSERT INTO division_admins (user_id, division_id) VALUES (?, 1)", (DIV_ADMIN,))
        self.headers = {"X-Telegram-Init-Data": _init_data(DIV_ADMIN)}

    async def test_non_numeric_limit_is_400(self):
        for url in ("/api/admin/markets?limit=abc", "/api/admin/bets?offset=x", "/api/admin/audit-log?limit=1e3"):
            resp = await self.client.get(url, headers=self.headers)
            assert resp.status == 400, url
            assert (await resp.json())["status"] == "error"

    async def test_division_admin_cannot_read_other_division(self):
        for url in ("/api/admin/bets?division_id=2", "/api/admin/audit-log?division_id=2"):
            resp = await self.client.get(url, headers=self.headers)
            assert resp.status == 403, url

    async def test_division_admin_reads_own_division(self):
        for url in ("/api/admin/bets?division_id=1", "/api/admin/audit-log?division_id=1"):
            resp = await self.client.get(url, headers=self.headers)
            assert resp.status == 200, url
