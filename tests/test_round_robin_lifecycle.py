import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import database
from handlers.admin import (
    generate_round_robin_fixtures,
    admin_generate_matches_confirm,
    admin_gen_div_select,
    admin_generate_matches_execute,
)


import uuid


class TestRoundRobinLifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.init_db()
        uid = uuid.uuid4().hex[:6].upper()
        self.div_a_code = f"RR_A_{uid}"
        self.div_b_code = f"RR_B_{uid}"

        self.div_a_id = database.create_division(name="Высшая Лига", code=self.div_a_code)
        self.div_b_id = database.create_division(name="Первая Лига", code=self.div_b_code)

        # Clear test divisions matches
        database.clear_matches_by_division(self.div_a_id)
        database.clear_matches_by_division(self.div_b_id)

        # Seed players for Division A
        self.p_a1 = 930001
        self.p_a2 = 930002
        self.p_a3 = 930003
        self.p_a4 = 930004

        # Seed players for Division B
        self.p_b1 = 930011
        self.p_b2 = 930012

        with database.transaction() as conn:
            c = conn.cursor()
            for idx, pid in enumerate([self.p_a1, self.p_a2, self.p_a3, self.p_a4]):
                c.execute("""
                    INSERT OR REPLACE INTO users (telegram_id, username, team_name, role, division_id)
                    VALUES (?, ?, ?, 'player', ?)
                """, (pid, f"rr_a_{idx}", f"Club A{idx}", self.div_a_id))

            for idx, pid in enumerate([self.p_b1, self.p_b2]):
                c.execute("""
                    INSERT OR REPLACE INTO users (telegram_id, username, team_name, role, division_id)
                    VALUES (?, ?, ?, 'player', ?)
                """, (pid, f"rr_b_{idx}", f"Club B{idx}", self.div_b_id))

    def test_round_robin_algorithm_fairness(self):
        """Test that round robin algorithm generates full double round-robin (Home & Away) with no self-matches."""
        players = [101, 102, 103, 104]
        fixtures = generate_round_robin_fixtures(players)

        self.assertTrue(len(fixtures) > 0)
        # 4 players in double round robin = 4 * 3 = 12 matches
        self.assertEqual(len(fixtures), 12)

        ordered_pairs = set()
        for r_num, p1, p2 in fixtures:
            self.assertNotEqual(p1, p2)
            pair = (p1, p2)
            self.assertNotIn(pair, ordered_pairs)
            ordered_pairs.add(pair)

        self.assertEqual(len(ordered_pairs), 12)

    def test_division_match_isolation_and_safe_clear(self):
        """Test that batch inserting and clearing matches per division does not touch other divisions."""
        fixtures_a = [(1, self.p_a1, self.p_a2), (1, self.p_a3, self.p_a4)]
        fixtures_b = [(1, self.p_b1, self.p_b2)]

        database.batch_insert_matches(fixtures_a, division_id=self.div_a_id)
        database.batch_insert_matches(fixtures_b, division_id=self.div_b_id)

        # Query matches by round filtered by division
        matches_a = database.get_matches_by_round(1, division_id=self.div_a_id)
        matches_b = database.get_matches_by_round(1, division_id=self.div_b_id)

        self.assertEqual(len(matches_a), 2)
        self.assertEqual(len(matches_b), 1)

        # Clear ONLY Division A matches
        database.clear_matches_by_division(self.div_a_id)

        matches_a_after = database.get_matches_by_round(1, division_id=self.div_a_id)
        matches_b_after = database.get_matches_by_round(1, division_id=self.div_b_id)

        self.assertEqual(len(matches_a_after), 0)
        self.assertEqual(len(matches_b_after), 1)

    async def test_admin_generate_matches_round_robin_flow(self):
        """Test interactive admin flow for generating Round Robin schedule for a specific division."""
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

        with patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin.is_admin", return_value=True):
            # 1. Open generation menu
            await admin_generate_matches_confirm(update, context)
            self.assertTrue(query.edit_message_text.called)
            args, kwargs = query.edit_message_text.call_args
            self.assertIn("Генерация расписания", args[0])

            # 2. Select Division A
            query.data = f"admin_gen_div_{self.div_a_id}"
            await admin_gen_div_select(update, context)
            args, kwargs = query.edit_message_text.call_args
            self.assertIn("Подтверждение генерации расписания", args[0])

            # 3. Execute generation for Division A
            query.data = f"admin_gen_exec_{self.div_a_id}"
            await admin_generate_matches_execute(update, context)
            args, kwargs = query.edit_message_text.call_args
            self.assertIn("Расписание успешно сгенерировано!", args[0])

            # 4. Verify matches in DB
            matches_a = database.get_matches_by_round(1, division_id=self.div_a_id)
            self.assertTrue(len(matches_a) > 0)
            for m in matches_a:
                self.assertEqual(m["division_id"], self.div_a_id)


if __name__ == "__main__":
    unittest.main()
