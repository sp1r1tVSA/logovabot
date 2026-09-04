"""
tests/test_phase6_notifications.py

Phase 6 Smart Notifications Tests:
1. Notification delivery & persistence in notification_events and notifications.
2. Strict deduplication (UNIQUE constraint on user_id, event_type, source_event_id).
3. User preferences (user_notification_settings).
4. Cooldown throttling for normal priority events vs high-priority bypass.
5. Match broadcast targeting (bettors and team favorites).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from services.notification_service import (
    EVENT_TYPE_BET_SETTLED,
    EVENT_TYPE_GOAL,
    EVENT_TYPE_HOT_MATCH,
    EVENT_TYPE_MATCH_FINISHED,
    EVENT_TYPE_ODDS_MOVEMENT,
    broadcast_match_event,
    get_user_notification_events,
    is_notification_enabled,
    mark_notification_sent,
    queue_notification,
    set_notification_preference,
)


class TestPhase6Notifications(unittest.TestCase):

    def setUp(self) -> None:
        database.init_db()
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM notification_events WHERE user_id >= 779000")
            cursor.execute("DELETE FROM notifications WHERE user_id >= 779000")
            cursor.execute("DELETE FROM user_notification_settings WHERE user_id >= 779000")
            cursor.execute("DELETE FROM favorites WHERE user_id >= 779000")
            cursor.execute("DELETE FROM bet_items WHERE bet_id IN (SELECT id FROM user_bets WHERE user_id >= 779000)")
            cursor.execute("DELETE FROM user_bets WHERE user_id >= 779000")
            cursor.execute("DELETE FROM user_wallets WHERE user_id >= 779000")
            cursor.execute("DELETE FROM users WHERE telegram_id >= 779000")
            cursor.execute("DELETE FROM matches WHERE id = 99501")

            # Seed user 779001 and 779002
            cursor.execute("""
                INSERT INTO users (telegram_id, username, division_id, team_name)
                VALUES (779001, 'notif_user_1', 1, 'Челси')
            """)
            cursor.execute("INSERT INTO user_wallets (user_id, balance) VALUES (779001, 1000)")

            cursor.execute("""
                INSERT INTO users (telegram_id, username, division_id, team_name)
                VALUES (779002, 'notif_user_2', 1, 'Арсенал')
            """)
            cursor.execute("INSERT INTO user_wallets (user_id, balance) VALUES (779002, 1000)")

            # Seed match 99501
            cursor.execute("""
                INSERT INTO matches (
                    id, season_id, division_id, round_number,
                    player1_team, player2_team, status
                ) VALUES (99501, 1, 1, 5, 'Челси', 'Арсенал', 'open')
            """)

    def tearDown(self) -> None:
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM notification_events WHERE user_id >= 779000")
            cursor.execute("DELETE FROM notifications WHERE user_id >= 779000")
            cursor.execute("DELETE FROM user_notification_settings WHERE user_id >= 779000")
            cursor.execute("DELETE FROM favorites WHERE user_id >= 779000")
            cursor.execute("DELETE FROM bet_items WHERE bet_id IN (SELECT id FROM user_bets WHERE user_id >= 779000)")
            cursor.execute("DELETE FROM user_bets WHERE user_id >= 779000")
            cursor.execute("DELETE FROM user_wallets WHERE user_id >= 779000")
            cursor.execute("DELETE FROM users WHERE telegram_id >= 779000")
            cursor.execute("DELETE FROM matches WHERE id = 99501")

    def test_queue_notification_success_and_read(self) -> None:
        """Successfully queue a notification and verify it is returned for the user."""
        success, status = queue_notification(
            user_id=779001,
            event_type=EVENT_TYPE_BET_SETTLED,
            source_event_id="bet_101",
            title="Ставка рассчитана!",
            body="Ваша ставка на Челси выиграла!",
            priority="high"
        )
        self.assertTrue(success)
        self.assertEqual(status, "queued")

        events = get_user_notification_events(779001)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], EVENT_TYPE_BET_SETTLED)
        self.assertEqual(events[0]["title"], "Ставка рассчитана!")
        self.assertEqual(events[0]["status"], "pending")

        # Mark sent
        mark_notification_sent(events[0]["id"])
        updated_events = get_user_notification_events(779001)
        self.assertEqual(updated_events[0]["status"], "sent")

    def test_strict_deduplication_same_source_event(self) -> None:
        """Attempting to insert identical (user_id, event_type, source_event_id) must be blocked."""
        s1, st1 = queue_notification(
            user_id=779001,
            event_type=EVENT_TYPE_GOAL,
            source_event_id="goal_99501_1",
            title="ГОЛ!",
            body="Челси забил гол (1:0)"
        )
        self.assertTrue(s1)
        self.assertEqual(st1, "queued")

        # Second attempt with same source event
        s2, st2 = queue_notification(
            user_id=779001,
            event_type=EVENT_TYPE_GOAL,
            source_event_id="goal_99501_1",
            title="ГОЛ! (дубль)",
            body="Челси забил гол (1:0)"
        )
        self.assertFalse(s2)
        self.assertEqual(st2, "duplicate")

        events = get_user_notification_events(779001)
        self.assertEqual(len(events), 1)

    def test_user_notification_preference_toggle(self) -> None:
        """User can disable a notification type, blocking subsequent events."""
        self.assertTrue(is_notification_enabled(779001, EVENT_TYPE_ODDS_MOVEMENT))

        # Disable ODDS_MOVEMENT
        set_notification_preference(779001, EVENT_TYPE_ODDS_MOVEMENT, False)
        self.assertFalse(is_notification_enabled(779001, EVENT_TYPE_ODDS_MOVEMENT))

        # Queue attempt should be refused
        success, status = queue_notification(
            user_id=779001,
            event_type=EVENT_TYPE_ODDS_MOVEMENT,
            source_event_id="move_1",
            title="Движение кэфов",
            body="Кэф на Челси упал"
        )
        self.assertFalse(success)
        self.assertEqual(status, "disabled")

        # Re-enable
        set_notification_preference(779001, EVENT_TYPE_ODDS_MOVEMENT, True)
        self.assertTrue(is_notification_enabled(779001, EVENT_TYPE_ODDS_MOVEMENT))

        s2, st2 = queue_notification(
            user_id=779001,
            event_type=EVENT_TYPE_ODDS_MOVEMENT,
            source_event_id="move_2",
            title="Движение кэфов",
            body="Кэф на Челси поднялся"
        )
        self.assertTrue(s2)
        self.assertEqual(st2, "queued")

    def test_cooldown_and_high_priority_bypass(self) -> None:
        """Frequent non-critical events are throttled by cooldown, high priority bypasses."""
        # 1. First event queued
        s1, st1 = queue_notification(
            user_id=779001,
            event_type=EVENT_TYPE_HOT_MATCH,
            source_event_id="hot_1",
            title="Горячий матч",
            cooldown_seconds=600  # 10 minutes
        )
        self.assertTrue(s1)

        # 2. Immediate second hot match with different ID should be throttled by cooldown
        s2, st2 = queue_notification(
            user_id=779001,
            event_type=EVENT_TYPE_HOT_MATCH,
            source_event_id="hot_2",
            title="Еще один горячий матч",
            cooldown_seconds=600
        )
        self.assertFalse(s2)
        self.assertEqual(st2, "cooldown")

        # 3. High priority event (GOAL) bypasses cooldown even if cooldown_seconds is requested
        s3, st3 = queue_notification(
            user_id=779001,
            event_type=EVENT_TYPE_GOAL,
            source_event_id="goal_1",
            title="ГОЛ!",
            cooldown_seconds=600
        )
        self.assertTrue(s3)
        self.assertEqual(st3, "queued")

    def test_broadcast_match_event_targeting(self) -> None:
        """Broadcast reaches bettors and team favorites, but not unrelated users."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            # User 779001 placed a bet on match 99501
            cursor.execute("""
                INSERT INTO user_bets (id, user_id, bet_type, amount, potential_win, total_odd, status)
                VALUES (995001, 779001, 'single', 100, 200, 2.0, 'pending')
            """)
            cursor.execute("""
                INSERT INTO bet_items (bet_id, match_id, outcome_type, odd, status)
                VALUES (995001, 99501, 'p1', 2.0, 'pending')
            """)

            # User 779002 has 'Арсенал' as favorite
            cursor.execute("""
                INSERT INTO favorites (user_id, target_type, target_id)
                VALUES (779002, 'team', 'Арсенал')
            """)

        # Broadcast goal in match 99501
        count = broadcast_match_event(
            match_id=99501,
            event_type=EVENT_TYPE_GOAL,
            source_event_id="goal_22",
            title="Гол в матче Челси - Арсенал!",
            body="Счет открыт: 1:0"
        )

        # Both user 779001 and 779002 should receive the notification
        self.assertEqual(count, 2)

        events_1 = get_user_notification_events(779001)
        events_2 = get_user_notification_events(779002)
        self.assertEqual(len(events_1), 1)
        self.assertEqual(len(events_2), 1)


if __name__ == "__main__":
    unittest.main()
