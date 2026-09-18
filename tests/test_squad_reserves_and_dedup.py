"""
Unit tests for squad OCR player deduplication (is_same_footballer)
and separate reserves upload flow in cabinet, admin and squad_ai.
"""

import asyncio
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import database
import handlers.cabinet as cabinet
import handlers.admin as admin
import handlers.squad_ai as squad_ai
from services.ai.squad_recognizer import (
    _parse_players,
    is_same_footballer,
    normalize_footballer_name,
)


class TestSquadDeduplication(unittest.TestCase):
    def test_normalize_footballer_name(self):
        self.assertEqual(normalize_footballer_name("Éder Militão"), "eder militao")
        self.assertEqual(normalize_footballer_name("Alexander-Arnold"), "alexander arnold")
        self.assertEqual(normalize_footballer_name("GÜLER"), "guler")
        self.assertEqual(normalize_footballer_name(""), "")

    def test_is_same_footballer_vini_variations(self):
        self.assertTrue(is_same_footballer("VINI JR.", "VINÍCIUS JÚNIOR"))
        self.assertTrue(is_same_footballer("Vini Jr", "Vinicius Junior"))
        self.assertTrue(is_same_footballer("vinicius jr", "vini jr"))
        self.assertTrue(is_same_footballer("Vinícius Júnior", "VINI JR."))

    def test_is_same_footballer_surname_matching(self):
        self.assertTrue(is_same_footballer("MBAPPÉ", "KYLIAN MBAPPÉ"))
        self.assertTrue(is_same_footballer("Kylian Mbappé", "Mbappe"))
        self.assertTrue(is_same_footballer("BELLINGHAM", "JUDE BELLINGHAM"))
        self.assertTrue(is_same_footballer("ÉDER MILITÃO", "MILITÃO"))
        self.assertTrue(is_same_footballer("MARC CUCURELLA", "CUCURELLA"))
        self.assertTrue(is_same_footballer("RODRYGO", "RODRYGO GOES"))
        self.assertTrue(is_same_footballer("ALEXANDER-ARNOLD", "TRENT ALEXANDER-ARNOLD"))

    def test_is_same_footballer_different_players(self):
        self.assertFalse(is_same_footballer("GABRIEL JESUS", "GABRIEL MARTINELLI"))
        self.assertFalse(is_same_footballer("LUCAS HERNANDEZ", "THEO HERNANDEZ"))
        self.assertFalse(is_same_footballer("DIOGO JOTA", "PEDRO NETO"))
        self.assertFalse(is_same_footballer("VALVERDE", "GÜLER"))

    def test_parse_players_deduplicates_duplicate_cards(self):
        raw_payload = {
            "players": [
                {"name": "VINI JR.", "position": "ST"},
                {"name": "MBAPPÉ", "position": "ST"},
                {"name": "BRAHIM", "position": "CAM"},
                {"name": "GÜLER", "position": "CAM"},
                {"name": "BELLINGHAM", "position": "CDM"},
                {"name": "VALVERDE", "position": "CDM"},
                {"name": "MARC CUCURELLA", "position": "LB"},
                {"name": "ÉDER MILITÃO", "position": "CB"},
                {"name": "DUMFRIES", "position": "CB"},
                {"name": "ALEXANDER-ARNOLD", "position": "RB"},
                {"name": "COURTOIS", "position": "GK"},
                # Duplicates from cut-off bench:
                {"name": "VINÍCIUS JÚNIOR", "position": "LM"},
                {"name": "KYLIAN MBAPPÉ", "position": "ST"},
            ]
        }
        parsed = _parse_players(raw_payload)
        names = [p["player_name"] for p in parsed]

        self.assertIn("VINI JR.", names)
        self.assertNotIn("VINÍCIUS JÚNIOR", names)
        self.assertIn("MBAPPÉ", names)
        self.assertNotIn("KYLIAN MBAPPÉ", names)
        self.assertEqual(len(parsed), 11)


class TestSquadReservesFlow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.init_db()
        self.uid = uuid.uuid4().hex[:6].upper()
        self.club = f"Test Club {self.uid}"
        self.user_id = 99120000 + int(uuid.uuid4().int % 100000)
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO users (telegram_id, username, team_name, role) VALUES (?, ?, ?, 'user')",
                (self.user_id, f"coach_{self.uid}", self.club)
            )

    async def asyncTearDown(self):
        with database.transaction() as conn:
            conn.execute("DELETE FROM squad_players WHERE team_name = ?", (self.club,))
            conn.execute("DELETE FROM users WHERE telegram_id = ?", (self.user_id,))

    def test_build_review_message_reserves(self):
        players = [
            {"player_name": "Rodrygo", "position": "RW"},
            {"player_name": "Ferland Mendy", "position": "LB"},
        ]
        text, markup = squad_ai.build_review_message(self.club, players, current_count=11, is_reserves=True)
        self.assertIn("резерв (скамейка)", text)
        self.assertIn("Найдено резервистов: <b>2</b>", text)
        buttons = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        self.assertIn("squadai_add", buttons)
        self.assertIn("squadai_cancel", buttons)
        self.assertNotIn("squadai_replace", buttons)

    async def test_offer_recognized_squad_does_not_auto_save_reserves(self):
        """Even if club has 0 players and >= 11 reserves recognized, do not auto-save when is_reserves=True."""
        eleven_players = [{"player_name": f"Sub_{i}", "position": "CM"} for i in range(11)]
        update = MagicMock()
        status_msg = MagicMock()
        status_msg.edit_text = AsyncMock()
        update.effective_message.reply_text = AsyncMock(return_value=status_msg)

        context = MagicMock()
        context.user_data = {}

        with patch("handlers.squad_ai.recognize_squad_photo", new=AsyncMock(return_value=eleven_players)):
            await squad_ai.offer_recognized_squad(
                update, context,
                club=self.club,
                file_id="photo_reserves",
                back_cb="cabinet_my_squad",
                is_reserves=True,
            )

        # Database must still be empty
        saved = database.get_squad(self.club)
        self.assertEqual(len(saved), 0)
        # Review prompt shown with is_reserves flag in pending state
        self.assertIn(squad_ai.PENDING_KEY, context.user_data)
        self.assertTrue(context.user_data[squad_ai.PENDING_KEY]["is_reserves"])

    async def test_squad_ai_apply_reserves_add(self):
        database.add_squad(self.club, ["Starter One", "Starter Two"])

        query = MagicMock()
        query.data = "squadai_add"
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update = MagicMock()
        update.callback_query = query

        context = MagicMock()
        context.user_data = {
            squad_ai.PENDING_KEY: {
                "club": self.club,
                "players": [{"player_name": "Reserve One", "position": "ST"}],
                "back_cb": "cabinet_my_squad",
                "is_reserves": True,
            }
        }

        with patch("services.graphics.player_photos.fetch_all_players", new=MagicMock()):
            await squad_ai.squad_ai_apply(update, context)

        squad = database.get_squad(self.club)
        self.assertEqual(len(squad), 3)
        self.assertIn("Starter One", squad)
        self.assertIn("Reserve One", squad)

        # Text confirms reserves were added
        query.edit_message_text.assert_called_once()
        self.assertIn("добавлено резервистов", query.edit_message_text.call_args[0][0])

    async def test_cabinet_start_upload_reserves(self):
        query = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update = MagicMock()
        update.callback_query = query
        context = MagicMock()
        context.user_data = {}

        res = await cabinet.start_upload_reserves(update, context)
        self.assertEqual(res, cabinet.SQUAD_PHOTO)
        self.assertEqual(context.user_data.get("squad_upload_mode"), "reserves")

    async def test_cabinet_save_reserves_photo_does_not_overwrite_main_photo(self):
        # Set existing main photo
        database.update_single_field(self.user_id, "squad_photo_id", "main_photo_123")

        update = MagicMock()
        update.effective_chat.type = "private"
        update.effective_user.id = self.user_id
        update.message.photo = [MagicMock(file_id="reserves_photo_456")]
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.user_data = {"squad_upload_mode": "reserves"}

        with patch("handlers.squad_ai.offer_recognized_squad", new=AsyncMock()) as mock_offer:
            await cabinet.save_squad_photo(update, context)
            mock_offer.assert_called_once()
            _, kwargs = mock_offer.call_args
            self.assertTrue(kwargs.get("is_reserves"))

        user = database.get_user(self.user_id)
        # Main squad photo must remain intact!
        self.assertEqual(user["squad_photo_id"], "main_photo_123")

    async def test_admin_squad_upload_reserves_start(self):
        query = MagicMock()
        query.data = f"admin_squad_upload_reserves_{self.club}"
        query.from_user.id = 12345
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update = MagicMock()
        update.effective_user.id = 12345
        update.callback_query = query
        context = MagicMock()
        context.user_data = {}

        with patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin.is_admin", return_value=True):
            res = await admin.admin_squad_upload_start(update, context)

        self.assertEqual(res, admin.ADMIN_EXPECT_SQUAD_TEXT)
        self.assertEqual(context.user_data.get("admin_squad_club"), self.club)
        self.assertTrue(context.user_data.get("admin_squad_is_reserves"))


if __name__ == "__main__":
    unittest.main()
