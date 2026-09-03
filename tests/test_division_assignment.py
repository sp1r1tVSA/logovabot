"""
Tests for Stage 3: Division Assignment & Users
Verifies player assignment to divisions, unassignment back to None,
division filtering, admin UI callback handlers, and profile rendering.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock
import database
from handlers.admin import (
    admin_div_players_menu,
    admin_list_div_players,
    admin_edit_div_select,
    admin_edit_div_execute
)


class TestDivisionAssignment(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.init_db()
        self.div1_code = "TEST_ASSIGN_D1"
        self.div2_code = "TEST_ASSIGN_D2"

        self.div1_id = database.create_division(name="Премьер Дивизион", code=self.div1_code)
        self.div2_id = database.create_division(name="Первый Дивизион", code=self.div2_code)

        self.u1_id = 920001
        self.u2_id = 920002
        self.u3_id = 920003

        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT OR REPLACE INTO users (telegram_id, username, team_name, role, division_id)
                VALUES (?, 'assign_player_1', 'Assign Club 1', 'player', ?)
            """, (self.u1_id, self.div1_id))
            c.execute("""
                INSERT OR REPLACE INTO users (telegram_id, username, team_name, role, division_id)
                VALUES (?, 'assign_player_2', 'Assign Club 2', 'player', ?)
            """, (self.u2_id, self.div2_id))
            c.execute("""
                INSERT OR REPLACE INTO users (telegram_id, username, team_name, role, division_id)
                VALUES (?, 'assign_player_3', 'Assign Club 3', 'player', NULL)
            """, (self.u3_id,))

    async def asyncTearDown(self):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM users WHERE telegram_id IN (?, ?, ?)", (self.u1_id, self.u2_id, self.u3_id))
            c.execute("DELETE FROM divisions WHERE id IN (?, ?)", (self.div1_id, self.div2_id))

    def test_assign_and_unassign_user_division(self):
        """Verify assigning a user to a division and unassigning back to None."""
        # Check initial state
        u3 = database.get_user(self.u3_id)
        self.assertIsNone(u3["division_id"])

        # Assign u3 to div1
        database.assign_user_division(self.u3_id, self.div1_id)
        u3_updated = database.get_user(self.u3_id)
        self.assertEqual(u3_updated["division_id"], self.div1_id)

        # Unassign u3 back to None
        database.assign_user_division(self.u3_id, None)
        u3_unassigned = database.get_user(self.u3_id)
        self.assertIsNone(u3_unassigned["division_id"])

    def test_get_division_users_filtering(self):
        """Verify get_division_users filters accurately by division and supports None."""
        div1_users = database.get_division_users(self.div1_id)
        div1_ids = [u["telegram_id"] for u in div1_users]
        self.assertIn(self.u1_id, div1_ids)
        self.assertNotIn(self.u2_id, div1_ids)
        self.assertNotIn(self.u3_id, div1_ids)

        div2_users = database.get_division_users(self.div2_id)
        div2_ids = [u["telegram_id"] for u in div2_users]
        self.assertIn(self.u2_id, div2_ids)
        self.assertNotIn(self.u1_id, div2_ids)

        unassigned_users = database.get_division_users(None)
        unassigned_ids = [u["telegram_id"] for u in unassigned_users]
        self.assertIn(self.u3_id, unassigned_ids)
        self.assertNotIn(self.u1_id, unassigned_ids)
        self.assertNotIn(self.u2_id, unassigned_ids)

    async def test_admin_edit_div_execute_flow(self):
        """Test the callback execution of assigning and unassigning a user division via admin handler."""
        update = MagicMock()
        context = MagicMock()
        query = MagicMock()
        admin_id = 999999
        query.from_user.id = admin_id
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        update.callback_query = query
        update.effective_user = MagicMock()
        update.effective_user.id = admin_id

        # Mock is_admin in both base and admin modules
        with unittest.mock.patch("handlers.base.is_admin", return_value=True), \
             unittest.mock.patch("handlers.admin.is_admin", return_value=True):
            # 1. Assign u3 to div2
            query.data = f"admin_ediv_{self.u3_id}_{self.div2_id}"
            await admin_edit_div_execute(update, context)
            u3 = database.get_user(self.u3_id)
            self.assertEqual(u3["division_id"], self.div2_id)

            # 2. Unassign u3 back to none
            query.data = f"admin_ediv_{self.u3_id}_none"
            await admin_edit_div_execute(update, context)
            u3 = database.get_user(self.u3_id)
            self.assertIsNone(u3["division_id"])


if __name__ == "__main__":
    unittest.main()
