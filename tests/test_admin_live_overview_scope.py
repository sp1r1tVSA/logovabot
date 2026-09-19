"""/api/admin/live/overview: live state matching and division scoping."""
import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

import database
from api import routes_admin_live

DIV_ADMIN = 7401


@pytest.fixture(autouse=True)
def seeded():
    with database.transaction() as conn:
        conn.execute("DELETE FROM live_match_states")
        conn.execute("DELETE FROM matches")
        conn.execute("DELETE FROM division_admins")
        for div in (1, 2):
            conn.execute("INSERT OR IGNORE INTO divisions (id, name, code) VALUES (?, ?, ?)", (div, f"Д{div}", f"DIV_{div}"))
        conn.execute("INSERT OR IGNORE INTO users (telegram_id, username) VALUES (?, 'divadmin')", (DIV_ADMIN,))
        conn.execute("INSERT INTO division_admins (division_id, user_id) VALUES (1, ?)", (DIV_ADMIN,))
        # Live only through the state machine (matches.status not yet flipped).
        for mid, div in ((501, 1), (502, 2)):
            conn.execute(
                "INSERT INTO matches (id, round_number, player1_team, player2_team, status, division_id) "
                "VALUES (?, 1, 'А', 'Б', 'pending', ?)", (mid, div))
            conn.execute(
                "INSERT INTO live_match_states (match_id, division_id, status) VALUES (?, ?, 'LIVE')", (mid, div))


def _overview():
    with patch.object(routes_admin_live, "_get_actor_id", return_value=DIV_ADMIN):
        resp = asyncio.run(routes_admin_live.handle_admin_live_overview(MagicMock()))
    return {m["id"] for m in json.loads(resp.body)["live_matches"]}


def test_division_admin_sees_only_own_live_match():
    assert _overview() == {501}
