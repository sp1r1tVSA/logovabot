import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import database
from handlers.admin import admin_force_update


class TestDataReconciliation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        database.init_db()
        self.uid = uuid.uuid4().hex[:6].upper()
        self.div_id = database.create_division(name=f"Reconcile Div {self.uid}", code=f"REC_{self.uid}")

        self.u1_id = int(f"91{int(uuid.uuid4().int % 1000000):06d}")
        self.u2_id = int(f"92{int(uuid.uuid4().int % 1000000):06d}")

        self.team1 = f"Real {self.uid}"
        self.team2 = f"Barca {self.uid}"

        database.register_user(self.u1_id, f"user1_{self.uid}", team_name=self.team1)
        database.register_user(self.u2_id, f"user2_{self.uid}", team_name=self.team2)

        database.assign_user_division(self.u1_id, self.div_id)
        database.assign_user_division(self.u2_id, self.div_id)

    def test_reconcile_consistent_division_data(self):
        """When matches and events match completely, status is ok and discrepancies is empty."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team,
                                    player1_score, player2_score, status, division_id, season_id)
                VALUES (1, ?, ?, ?, ?, 2, 1, 'confirmed', ?, 1)
            """, (self.u1_id, self.u2_id, self.team1, self.team2, self.div_id))
            m_id = cursor.lastrowid

            cursor.execute("""
                INSERT INTO match_events (match_id, team_name, player_name, event_type, count)
                VALUES (?, ?, 'Striker1', 'goal', 2), (?, ?, 'Striker2', 'goal', 1)
            """, (m_id, self.team1, m_id, self.team2))

        result = database.reconcile_all_divisions_data(season_id=1)
        self.assertIn("divisions_checked", result)
        self.assertGreaterEqual(result["divisions_checked"], 1)
        self.assertGreaterEqual(result["matches_checked"], 1)

        # Check this division in summary
        div_summary = next((d for d in result["division_summaries"] if d["division_id"] == self.div_id), None)
        self.assertIsNotNone(div_summary)
        self.assertEqual(div_summary["confirmed_matches"], 1)
        self.assertEqual(div_summary["total_goals"], 3)
        self.assertIn("Striker1", div_summary["top_scorer"])

        # No discrepancies for this match
        self.assertFalse(any(f"Матч #{m_id}" in d for d in result["discrepancies"]))

    def test_reconcile_detects_goal_events_mismatch(self):
        """When match score is 3:1 but only 1 goal event is recorded, discrepancy is reported."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO matches (round_number, player1_id, player2_id, player1_team, player2_team,
                                    player1_score, player2_score, status, division_id, season_id)
                VALUES (2, ?, ?, ?, ?, 3, 1, 'confirmed', ?, 1)
            """, (self.u1_id, self.u2_id, self.team1, self.team2, self.div_id))
            m_id = cursor.lastrowid

            # Only 1 goal instead of 3:1 (4 total)
            cursor.execute("""
                INSERT INTO match_events (match_id, team_name, player_name, event_type, count)
                VALUES (?, ?, 'Striker1', 'goal', 1)
            """, (m_id, self.team1))

        result = database.reconcile_all_divisions_data(season_id=1)
        self.assertEqual(result["status"], "warning")
        mismatch_disc = [d for d in result["discrepancies"] if f"Матч #{m_id}" in d]
        self.assertTrue(len(mismatch_disc) > 0)
        self.assertIn("в событиях авторов", mismatch_disc[0])

    async def test_admin_force_update_executes_reconciliation_and_replies(self):
        """Verify admin_force_update runs reconciliation and sends formatted response without posting new cards."""
        update = MagicMock()
        context = MagicMock()

        # Effective admin user
        update.effective_user.id = 999999
        update.callback_query = AsyncMock()
        update.message = None

        with patch("handlers.base.is_admin", return_value=True), \
             patch("handlers.admin.is_admin", return_value=True), \
             patch("handlers.admin.post_league_table_to_reports", new_callable=AsyncMock) as mock_post_tables:

            await admin_force_update(update, context)

            update.callback_query.answer.assert_awaited()
            update.callback_query.message.reply_text.assert_awaited()
            mock_post_tables.assert_awaited_once_with(context)

            reply_text = update.callback_query.message.reply_text.await_args[0][0]
            self.assertIn("Итоги аудита дивизионов", reply_text)
            self.assertIn("Активных дивизионов", reply_text)
            self.assertIn("Турнирные таблицы актуализированы", reply_text)


if __name__ == "__main__":
    unittest.main()
