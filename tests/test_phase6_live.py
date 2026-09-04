"""
tests/test_phase6_live.py

Tests for Phase 6C: Live Match State Machine & Live Event Ingestion.
Ensures:
1. Strict state machine transitions (allowed vs forbidden).
2. Monotonic score consistency.
3. Idempotent event ingestion with duplicate protection.
4. Automatic market suspension on goal / VAR / penalty events.
5. Live statistics ingestion with NULL preservation.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from services.live_state_machine import (
    FINISHED,
    HALFTIME,
    LIVE,
    PRE_MATCH,
    SCHEDULED,
    InvalidStateTransitionError,
    can_transition,
    transition_live_match,
)
from services.live_ingestion import (
    get_live_events,
    get_live_match_state,
    get_live_statistics,
    ingest_live_event,
    ingest_live_statistics,
)
from services.sports_provider import LiveEvent, LiveStatistics


class TestPhase6Live(unittest.TestCase):

    def setUp(self) -> None:
        database.init_db()
        with database.transaction() as conn:
            cursor = conn.cursor()
            # Clean up test match and test live tables
            cursor.execute("DELETE FROM live_events WHERE match_id >= 99100")
            cursor.execute("DELETE FROM live_statistics WHERE match_id >= 99100")
            cursor.execute("DELETE FROM live_match_states WHERE match_id >= 99100")
            cursor.execute("DELETE FROM markets WHERE match_id >= 99100")
            cursor.execute("DELETE FROM matches WHERE id >= 99100")

            # Seed a test match
            cursor.execute("""
                INSERT INTO matches (
                    id, season_id, division_id, round_number,
                    player1_team, player2_team, status, player1_score, player2_score
                ) VALUES (99101, 1, 1, 5, 'Порту', 'Бенфика', 'scheduled', 0, 0)
            """)

            # Seed open markets
            cursor.execute("""
                INSERT INTO markets (id, match_id, market_key, market_name, category, status)
                VALUES (991011, 99101, '1x2', 'Основной исход', 'main', 'open'),
                       (991012, 99101, 'totals', 'Тотал 2.5', 'totals', 'open')
            """)

    def tearDown(self) -> None:
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM live_events WHERE match_id >= 99100")
            cursor.execute("DELETE FROM live_statistics WHERE match_id >= 99100")
            cursor.execute("DELETE FROM live_match_states WHERE match_id >= 99100")
            cursor.execute("DELETE FROM markets WHERE match_id >= 99100")
            cursor.execute("DELETE FROM matches WHERE id >= 99100")

    # ── State Machine Tests ──────────────────────────────────────────────────

    def test_allowed_state_transitions(self) -> None:
        """Verify normal match progression from SCHEDULED to FINISHED."""
        m_id = 99101
        self.assertTrue(can_transition(SCHEDULED, PRE_MATCH))
        self.assertTrue(can_transition(PRE_MATCH, LIVE))
        self.assertTrue(can_transition(LIVE, HALFTIME))
        self.assertTrue(can_transition(HALFTIME, LIVE))
        self.assertTrue(can_transition(LIVE, FINISHED))

        # Perform actual transitions
        transition_live_match(m_id, PRE_MATCH)
        st = get_live_match_state(m_id)
        self.assertEqual(st["status"], PRE_MATCH)

        transition_live_match(m_id, LIVE)
        st = get_live_match_state(m_id)
        self.assertEqual(st["status"], LIVE)

        transition_live_match(m_id, HALFTIME)
        st = get_live_match_state(m_id)
        self.assertEqual(st["status"], HALFTIME)

        transition_live_match(m_id, LIVE)
        st = get_live_match_state(m_id)
        self.assertEqual(st["status"], LIVE)

        transition_live_match(m_id, FINISHED)
        st = get_live_match_state(m_id)
        self.assertEqual(st["status"], FINISHED)

    def test_forbidden_state_transitions(self) -> None:
        """Verify forbidden transitions raise InvalidStateTransitionError."""
        m_id = 99101
        # Cannot jump from SCHEDULED directly to FINISHED
        with self.assertRaises(InvalidStateTransitionError):
            transition_live_match(m_id, FINISHED)

        # Transition to finished legitimately
        transition_live_match(m_id, PRE_MATCH)
        transition_live_match(m_id, LIVE)
        transition_live_match(m_id, FINISHED)

        # Terminal state: Cannot transition from FINISHED back to LIVE
        with self.assertRaises(InvalidStateTransitionError):
            transition_live_match(m_id, LIVE)

        # Force override works for administrative corrections
        ok, msg = transition_live_match(m_id, LIVE, force=True, actor_id=1, reason="Admin reopen correction")
        self.assertTrue(ok)
        st = get_live_match_state(m_id)
        self.assertEqual(st["status"], LIVE)

    # ── Ingestion Tests ──────────────────────────────────────────────────────

    def test_ingest_goal_score_consistency(self) -> None:
        """Ingesting a goal event updates score monotonically and suspends active markets."""
        m_id = 99101

        # Event 1: Goal for Порту (home) at minute 18
        ev1 = LiveEvent(
            match_id=m_id,
            provider="test",
            provider_event_id="test_ev_01",
            event_type="goal",
            minute=18,
            team_name="Порту",
            player_name="Galeno",
            payload={"side": "home"}
        )
        res1 = ingest_live_event(ev1)
        self.assertEqual(res1["status"], "applied")
        self.assertEqual(res1["home_score"], 1)
        self.assertEqual(res1["away_score"], 0)
        self.assertEqual(res1["minute"], 18)
        self.assertGreater(res1["suspended_markets"], 0)

        # Verify state in DB
        st = get_live_match_state(m_id)
        self.assertEqual(st["home_score"], 1)
        self.assertEqual(st["away_score"], 0)

        # Verify markets were suspended
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM markets WHERE id = 991011")
            self.assertEqual(cursor.fetchone()["status"], "suspended")

        # Event 2: Goal for Бенфика (away) at minute 42
        ev2 = LiveEvent(
            match_id=m_id,
            provider="test",
            provider_event_id="test_ev_02",
            event_type="goal",
            minute=42,
            team_name="Бенфика",
            player_name="Di Maria",
            payload={"side": "away"}
        )
        res2 = ingest_live_event(ev2)
        self.assertEqual(res2["status"], "applied")
        self.assertEqual(res2["home_score"], 1)
        self.assertEqual(res2["away_score"], 1)

    def test_duplicate_event_rejection(self) -> None:
        """Ingesting the same provider_event_id twice is an idempotent no-op."""
        m_id = 99101

        ev = LiveEvent(
            match_id=m_id,
            provider="test",
            provider_event_id="test_dup_01",
            event_type="goal",
            minute=12,
            team_name="Порту",
            player_name="Evanilson",
            payload={"side": "home"}
        )
        res1 = ingest_live_event(ev)
        self.assertEqual(res1["status"], "applied")
        self.assertEqual(res1["home_score"], 1)

        # Replay same event
        res2 = ingest_live_event(ev)
        self.assertEqual(res2["status"], "duplicate")

        # Score remains 1, not 2!
        st = get_live_match_state(m_id)
        self.assertEqual(st["home_score"], 1)

    def test_non_scoring_event_does_not_mutate_score(self) -> None:
        """Yellow cards or substitutions do not increment score."""
        m_id = 99101
        ev = LiveEvent(
            match_id=m_id,
            provider="test",
            provider_event_id="test_card_01",
            event_type="yellow_card",
            minute=33,
            team_name="Бенфика",
            player_name="Otamendi"
        )
        res = ingest_live_event(ev)
        self.assertEqual(res["status"], "applied")
        self.assertEqual(res["home_score"], 0)
        self.assertEqual(res["away_score"], 0)

        events = get_live_events(m_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "yellow_card")

    def test_live_statistics_upsert_and_null_preservation(self) -> None:
        """Ingesting statistics preserves None for unprovided stats."""
        m_id = 99101
        stats = LiveStatistics(
            match_id=m_id,
            possession_home=60.0,
            possession_away=40.0,
            shots_home=12,
            shots_away=5,
            corners_home=6,
            corners_away=2,
            xg_home=None,   # Unavailable
            xg_away=None,   # Unavailable
            provider="test"
        )
        ok = ingest_live_statistics(stats)
        self.assertTrue(ok)

        db_stats = get_live_statistics(m_id)
        self.assertIsNotNone(db_stats)
        self.assertEqual(db_stats["possession_home"], 60.0)
        self.assertEqual(db_stats["shots_home"], 12)
        self.assertIsNone(db_stats["xg_home"])  # Strictly None, NOT 0!


if __name__ == "__main__":
    unittest.main()
