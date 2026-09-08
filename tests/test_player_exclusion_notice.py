"""
Исключение игрока из лиги: объявление в дивизионе и удаление из группы.

Правило: уведомление уходит только игроку, который играет в дивизионе —
в группу этого дивизиона, в тему General, после чего игрок кикается.
Игрок без дивизиона исключается тихо: писать некуда.
"""

import itertools
import os
import sys
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram.error import BadRequest

import config
import database
import handlers.admin as admin_handlers
from handlers.admin import _announce_player_exclusion, admin_delete_player_execute

_ID_SEQ = itertools.count(881400)
_CHAT_SEQ = itertools.count(-1009900100)


def _make_context() -> MagicMock:
    context = MagicMock()
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    context.bot.ban_chat_member = AsyncMock()
    context.bot.unban_chat_member = AsyncMock()
    return context


class ExclusionTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        database.init_db()
        self.uid = uuid.uuid4().hex[:6].upper()
        self.group_id = next(_CHAT_SEQ)
        self.div_id = database.create_division(
            name=f"Исключения {self.uid}", code=f"EXCL_{self.uid}"
        )
        database.set_division_group(self.div_id, self.group_id)

        self.user_id = next(_ID_SEQ)
        self.team = f"Клуб {self.uid}"
        database.register_user(self.user_id, f"exile_{self.uid}", team_name=self.team)
        database.assign_user_division(self.user_id, self.div_id)

        # Исключаемый игрок не должен считаться глобальным админом.
        self._orig_admins = list(config.ADMIN_IDS)
        config.ADMIN_IDS = [next(_ID_SEQ)]
        self.addCleanup(setattr, config, "ADMIN_IDS", self._orig_admins)


class TestDivisionGroupResolution(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self.uid = uuid.uuid4().hex[:6].upper()

    def test_uses_explicit_division_group(self):
        div_id = database.create_division(name=f"Явная {self.uid}", code=f"EXPL_{self.uid}")
        chat_id = next(_CHAT_SEQ)
        database.set_division_group(div_id, chat_id)
        self.assertEqual(database.get_division_group_chat_id(div_id), chat_id)

    def test_falls_back_to_topic_bindings(self):
        """Группу не привязывали явно, но топики её знают."""
        div_id = database.create_division(name=f"Топики {self.uid}", code=f"TOPIC_{self.uid}")
        chat_id = next(_CHAT_SEQ)
        database.set_division_topic(div_id, "results", 42, chat_id)
        database.set_division_topic(div_id, "previews", 43, chat_id)
        self.assertEqual(database.get_division_group_chat_id(div_id), chat_id)

    def test_none_when_nothing_is_bound(self):
        div_id = database.create_division(name=f"Пустая {self.uid}", code=f"EMPTY_{self.uid}")
        self.assertIsNone(database.get_division_group_chat_id(div_id))

    def test_none_for_missing_division(self):
        self.assertIsNone(database.get_division_group_chat_id(None))
        self.assertIsNone(database.get_division_group_chat_id(0))


class TestAnnounceExclusion(ExclusionTestBase):
    async def test_posts_to_general_and_kicks(self):
        context = _make_context()
        await _announce_player_exclusion(
            context, self.div_id, self.user_id, f"@exile_{self.uid}", self.team
        )

        context.bot.send_message.assert_awaited_once()
        kwargs = context.bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], self.group_id)
        self.assertIn(self.team, kwargs["text"])
        self.assertIn(f"@exile_{self.uid}", kwargs["text"])
        # General — это отсутствие message_thread_id, а не какой-то его номер.
        self.assertNotIn("message_thread_id", kwargs)

        context.bot.ban_chat_member.assert_awaited_once_with(
            chat_id=self.group_id, user_id=self.user_id
        )
        context.bot.unban_chat_member.assert_awaited_once_with(
            chat_id=self.group_id, user_id=self.user_id, only_if_banned=True
        )

    async def test_player_without_division_is_silent(self):
        context = _make_context()
        await _announce_player_exclusion(context, None, self.user_id, "@nobody", "Без клуба")

        context.bot.send_message.assert_not_awaited()
        context.bot.ban_chat_member.assert_not_awaited()

    async def test_division_without_group_is_silent(self):
        div_id = database.create_division(name=f"Безгруппы {self.uid}", code=f"NOGRP_{self.uid}")
        context = _make_context()
        await _announce_player_exclusion(context, div_id, self.user_id, "@x", "Клуб")

        context.bot.send_message.assert_not_awaited()
        context.bot.ban_chat_member.assert_not_awaited()

    async def test_global_admin_is_announced_but_not_kicked(self):
        config.ADMIN_IDS = [self.user_id]
        context = _make_context()
        await _announce_player_exclusion(context, self.div_id, self.user_id, "@boss", self.team)

        context.bot.send_message.assert_awaited_once()
        context.bot.ban_chat_member.assert_not_awaited()

    async def test_kick_still_happens_when_the_notice_fails(self):
        context = _make_context()
        context.bot.send_message.side_effect = BadRequest("Chat not found")
        await _announce_player_exclusion(context, self.div_id, self.user_id, "@x", self.team)

        context.bot.ban_chat_member.assert_awaited_once()

    async def test_failed_kick_does_not_raise(self):
        context = _make_context()
        context.bot.ban_chat_member.side_effect = BadRequest("Not enough rights")
        await _announce_player_exclusion(context, self.div_id, self.user_id, "@x", self.team)

        context.bot.send_message.assert_awaited_once()


class TestDeletePlayerHandler(ExclusionTestBase):
    def _make_update(self, admin_id: int):
        query = MagicMock()
        query.data = f"admin_delete_player_execute_{self.user_id}"
        query.from_user = MagicMock(id=admin_id)
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query
        update.effective_user = query.from_user
        return update, query

    async def test_handler_announces_and_kicks_division_player(self):
        admin_id = config.ADMIN_IDS[0]
        update, _ = self._make_update(admin_id)
        context = _make_context()

        with patch.object(admin_handlers, "_post_or_update_debts_in_warns", new=AsyncMock()), \
             patch.object(admin_handlers, "admin_list_players_page", new=AsyncMock()):
            await admin_delete_player_execute(update, context)

        self.assertIsNone(database.get_user(self.user_id), "Игрок должен быть удалён из лиги")
        context.bot.send_message.assert_awaited_once()
        self.assertEqual(context.bot.send_message.await_args.kwargs["chat_id"], self.group_id)
        context.bot.ban_chat_member.assert_awaited_once()

    async def test_handler_is_silent_for_player_without_division(self):
        database.assign_user_division(self.user_id, None)
        admin_id = config.ADMIN_IDS[0]
        update, _ = self._make_update(admin_id)
        context = _make_context()

        with patch.object(admin_handlers, "_post_or_update_debts_in_warns", new=AsyncMock()), \
             patch.object(admin_handlers, "admin_list_players_page", new=AsyncMock()):
            await admin_delete_player_execute(update, context)

        self.assertIsNone(database.get_user(self.user_id))
        context.bot.send_message.assert_not_awaited()
        context.bot.ban_chat_member.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
