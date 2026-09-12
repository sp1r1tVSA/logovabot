"""
Проверяет, что после распознавания состава (`squad_ai_apply`) бот запускает
фоновую предзагрузку фото игроков через `services.graphics.player_photos`,
и что при отмене распознавания предзагрузка не запускается.
"""

import asyncio
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import database
import handlers.squad_ai as squad_ai


class TestSquadAiPhotoPrefetch(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.init_db()
        uid = uuid.uuid4().hex[:6].upper()
        self.club = f"Test FC {uid}"
        self.players = [
            {"player_name": "Test Player One", "position": "ST"},
            {"player_name": "Test Player Two", "position": "GK"},
        ]

    async def asyncTearDown(self):
        with database.transaction() as conn:
            conn.execute("DELETE FROM squad_players WHERE team_name = ?", (self.club,))

    def _build_update_and_context(self, callback_data: str):
        query = MagicMock()
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        update = MagicMock()
        update.callback_query = query

        context = MagicMock()
        context.user_data = {
            squad_ai.PENDING_KEY: {
                "club": self.club,
                "players": self.players,
                "back_cb": "back",
            }
        }
        return update, context

    async def test_add_schedules_photo_prefetch(self):
        update, context = self._build_update_and_context("squadai_add")

        with patch(
            "services.graphics.player_photos.fetch_all_players", new=MagicMock(return_value={})
        ) as mock_fetch:
            await squad_ai.squad_ai_apply(update, context)
            # allow the fire-and-forget asyncio.create_task to run
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        mock_fetch.assert_called_once()
        (pairs,), _ = mock_fetch.call_args
        self.assertEqual(
            sorted(pairs),
            sorted([("Test Player One", self.club), ("Test Player Two", self.club)]),
        )

    async def test_replace_schedules_photo_prefetch(self):
        update, context = self._build_update_and_context("squadai_replace")

        with patch(
            "services.graphics.player_photos.fetch_all_players", new=MagicMock(return_value={})
        ) as mock_fetch:
            await squad_ai.squad_ai_apply(update, context)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        mock_fetch.assert_called_once()

    async def test_cancel_does_not_schedule_photo_prefetch(self):
        update, context = self._build_update_and_context("squadai_cancel")

        with patch(
            "services.graphics.player_photos.fetch_all_players", new=MagicMock(return_value={})
        ) as mock_fetch:
            await squad_ai.squad_ai_apply(update, context)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        mock_fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
