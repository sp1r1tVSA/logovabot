"""
tests/test_all_buttons.py

Comprehensive test suite verifying button integrity, callback coverage,
RBAC division isolation, financial idempotency/debouncing, and API handlers.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
import database
from handlers.base import is_admin, is_global_admin
from handlers.betting import cb_bet_place_amount
from handlers.admin import admin_generate_matches_execute
from handlers.lab import cb_lab_ovr_calc_demo
from handlers.__init__ import handle_placeholders


@pytest.fixture(autouse=True)
def setup_test_environment():
    """Ensure in-memory/test database is initialized with required tables."""
    database.init_db()


def test_achievement_claim_string_id():
    """Verify that claiming an achievement with a string ID works properly."""
    user_id = 777123
    with database.transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO users (telegram_id, username) VALUES (?, ?)", (user_id, "ach_tester"))
        database.get_or_create_wallet(user_id)
        database.get_or_create_progression(user_id)
        # Ensure achievement catalog has ACH_FIRST_BET
        conn.execute("""
            INSERT OR REPLACE INTO achievements_catalog (id, name, description, category, rarity, reward_xp, reward_coins, badge_icon)
            VALUES ('ACH_FIRST_BET', 'Первая ставка', 'Сделайте ставку', 'betting', 'common', 50, 200, '🎯')
        """)
        conn.execute("""
            INSERT OR REPLACE INTO user_achievements (user_id, achievement_id, is_claimed)
            VALUES (?, 'ACH_FIRST_BET', 0)
        """, (user_id,))

    # Claim reward via database
    success, msg, payload = database.claim_achievement_reward(user_id, "ACH_FIRST_BET")
    assert success is True
    assert payload["coins"] == 200
    assert payload["xp"] == 50

    # Verify second claim fails (idempotent / non-duplicate)
    success2, msg2, payload2 = database.claim_achievement_reward(user_id, "ACH_FIRST_BET")
    assert success2 is False
    assert "уже получена" in msg2


def test_telegram_bet_placement_in_flight_guard():
    """Verify that rapid duplicate callback clicks are guarded by _bet_in_flight and atomic slip extraction."""
    async def _test():
        user_id = 888123
        with database.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO users (telegram_id, username) VALUES (?, ?)", (user_id, "bet_tester"))
        database.get_or_create_wallet(user_id)

        update = MagicMock()
        query = MagicMock()
        query.data = "bet_place_100"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        update.effective_user.id = user_id

        context = MagicMock()
        context.user_data = {
            "bet_slip": [{"match_id": 1, "outcome": "W1", "odd": 1.85, "market_type": "1X2"}]
        }

        # Simulate in-flight flag already set (another request currently running)
        context.user_data["_bet_in_flight"] = True
        await cb_bet_place_amount(update, context)

        # Must answer immediately and abort without modifying slip
        assert query.answer.called
        assert len(context.user_data["bet_slip"]) == 1
        assert not query.edit_message_text.called

    asyncio.run(_test())


def test_is_global_admin_vs_division_admin():
    """Verify that is_global_admin correctly distinguishes superadmins from division-scoped admins."""
    super_admin_id = 99999901
    div_admin_id = 99999902
    regular_player_id = 99999903

    with database.transaction() as conn:
        # Super admin: role='admin', division_id=None
        conn.execute("INSERT OR REPLACE INTO users (telegram_id, username, role, division_id) VALUES (?, ?, 'admin', NULL)",
                     (super_admin_id, "super_admin"))
        # Division admin: role='division_admin', division_id=1
        conn.execute("INSERT OR REPLACE INTO users (telegram_id, username, role, division_id) VALUES (?, ?, 'division_admin', 1)",
                     (div_admin_id, "div_admin"))
        # Regular player: role='player'
        conn.execute("INSERT OR REPLACE INTO users (telegram_id, username, role, division_id) VALUES (?, ?, 'player', NULL)",
                     (regular_player_id, "player"))

    assert is_global_admin(super_admin_id) is True
    assert is_global_admin(div_admin_id) is False
    assert is_global_admin(regular_player_id) is False

    assert is_admin(super_admin_id) is True
    assert is_admin(div_admin_id) is True
    assert is_admin(regular_player_id) is False


def test_admin_schedule_generation_rbac_isolation():
    """Verify that a division admin cannot generate schedule for another division."""
    async def _test():
        div1_admin_id = 99999911
        with database.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO divisions (id, name, code) VALUES (1, 'Division 1', 'div1')")
            conn.execute("INSERT OR REPLACE INTO divisions (id, name, code) VALUES (2, 'Division 2', 'div2')")
            conn.execute("INSERT OR REPLACE INTO users (telegram_id, username, role, division_id) VALUES (?, ?, 'division_admin', 1)",
                         (div1_admin_id, "div1_admin"))
            conn.execute("INSERT OR REPLACE INTO division_admins (division_id, user_id) VALUES (1, ?)", (div1_admin_id,))

        # Attempting to generate matches for Division 2
        update = MagicMock()
        query = MagicMock()
        query.data = "admin_gen_exec_2"
        query.from_user.id = div1_admin_id
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        update.effective_user.id = div1_admin_id

        context = MagicMock()

        await admin_generate_matches_execute(update, context)

        # Must be rejected with permission denied alert
        assert query.answer.called
        args, kwargs = query.answer.call_args
        assert "нет прав" in args[0]
        assert kwargs.get("show_alert") is True

    asyncio.run(_test())


def test_lab_ovr_calc_demo_handler():
    """Verify that cb_lab_ovr_calc_demo answers callback and displays OVR formula card."""
    async def _test():
        admin_id = 99999921
        with database.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, ?, 'admin')",
                         (admin_id, "lab_admin"))

        update = MagicMock()
        query = MagicMock()
        query.data = "lab_ovr_calc_demo"
        query.from_user.id = admin_id
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        update.effective_user.id = admin_id

        context = MagicMock()

        await cb_lab_ovr_calc_demo(update, context)

        assert query.answer.called
        assert query.edit_message_text.called
        call_text = query.edit_message_text.call_args[0][0]
        assert "Калькулятор и формула OVR" in call_text
        assert "75 OVR" in call_text

    asyncio.run(_test())


def test_handle_placeholders_noop_is_silent():
    """Verify that callback 'noop' is silently answered without 'в разработке' alert."""
    async def _test():
        update = MagicMock()
        query = MagicMock()
        query.data = "noop"
        query.answer = AsyncMock()
        update.callback_query = query

        context = MagicMock()

        await handle_placeholders(update, context)

        assert query.answer.called
        assert query.answer.call_args == ((), {})

    asyncio.run(_test())


def test_callback_pattern_dispatching():
    """Verify that all newly wired and fixed patterns resolve to correct handlers in Application."""
    from telegram.ext import ApplicationBuilder, CallbackQueryHandler
    from handlers import _register_admin_handlers, _register_cabinet_handlers

    app = ApplicationBuilder().token("123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11").build()
    _register_admin_handlers(app)
    _register_cabinet_handlers(app)

    # Collect all handlers registered on app
    registered_handlers = [h for h in app.handlers.get(0, []) if isinstance(h, CallbackQueryHandler)]

    # 1. Verify admin_confirm_delete_player matches both conventions
    delete_handlers_1 = [h for h in registered_handlers if h.pattern and h.pattern.search("admin_confirm_delete_player_42")]
    delete_handlers_2 = [h for h in registered_handlers if h.pattern and h.pattern.search("admin_delete_player_confirm_42")]
    assert len(delete_handlers_1) > 0, "admin_confirm_delete_player_42 must match a registered handler"
    assert len(delete_handlers_2) > 0, "admin_delete_player_confirm_42 must match a registered handler"

    # 2. Verify lab_ovr_calc_demo
    ovr_handlers = [h for h in registered_handlers if h.pattern and h.pattern.search("lab_ovr_calc_demo")]
    assert len(ovr_handlers) > 0, "lab_ovr_calc_demo must match a registered handler"

    # 3. Verify cabinet game history is registered exactly once without duplicates
    history_handlers = [h for h in registered_handlers if h.pattern and h.pattern.search("cabinet_game_history")]
    assert len(history_handlers) == 1, f"cabinet_game_history should be registered exactly once, found {len(history_handlers)}"

