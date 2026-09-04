"""
tests/test_phase8_live_pipeline.py

Phase 8 — Live Sports Data Ingestion & Consistency Pipeline Test Suite.
Verifies end-to-end event ingestion, event deduplication, monotonic score progression,
finished match protection, and automated market suspension on major match events.
"""

import unittest
from datetime import datetime, timezone

import database
from services.live_ingestion import ingest_live_event, get_live_events, get_live_match_state
from services.live_state_machine import transition_live_match, LIVE, FINISHED
from services.odds_engine import get_or_create_market, get_or_create_selection
from services.sports.models import LiveEvent


class TestPhase8LivePipeline(unittest.TestCase):
    """Test suite verifying live match ingestion integrity and safety invariants."""

    def setUp(self) -> None:
        database.init_db()
        self.match_id = 8801
        with database.transaction() as conn:
            cursor = conn.cursor()
            # Clean test records
            cursor.execute("DELETE FROM live_events WHERE match_id = ?", (self.match_id,))
            cursor.execute("DELETE FROM live_match_states WHERE match_id = ?", (self.match_id,))
            cursor.execute("DELETE FROM markets WHERE match_id = ?", (self.match_id,))
            cursor.execute("DELETE FROM matches WHERE id = ?", (self.match_id,))

            # Seed match in league
            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, status)
                VALUES (?, 1, 1, 1, 'Real Madrid', 'Barcelona', 'active')
            """, (self.match_id,))

            # Initialize live state
            cursor.execute("""
                INSERT INTO live_match_states (match_id, season_id, division_id, status, period, minute, home_score, away_score, provider)
                VALUES (?, 1, 1, 'LIVE', '1h', 10, 0, 0, 'api_sports')
            """, (self.match_id,))

    def test_live_event_ingestion_and_score_consistency(self) -> None:
        """Goal event correctly increments score and updates live match state."""
        ev = LiveEvent(
            match_id=self.match_id,
            provider="api_sports",
            provider_event_id="ev_goal_1",
            event_type="goal",
            minute=24,
            team_name="Real Madrid",
            payload={"player": "Vinicius Jr"}
        )

        res = ingest_live_event(ev)
        self.assertEqual(res["status"], "applied")

        # Verify state updated
        state = get_live_match_state(self.match_id)
        self.assertIsNotNone(state)
        self.assertEqual(state["home_score"], 1)
        self.assertEqual(state["away_score"], 0)
        self.assertEqual(state["minute"], 24)

    def test_live_event_deduplication(self) -> None:
        """Duplicate (provider, provider_event_id) events are safely ignored."""
        ev = LiveEvent(
            match_id=self.match_id,
            provider="api_sports",
            provider_event_id="ev_card_yellow_1",
            event_type="yellow_card",
            minute=30,
            team_name="Barcelona",
            payload={"player": "Gavi"}
        )

        res1 = ingest_live_event(ev)
        self.assertEqual(res1["status"], "applied")


        # Second ingestion attempt of the exact same event
        res2 = ingest_live_event(ev)
        self.assertEqual(res2["status"], "duplicate")

        events = get_live_events(self.match_id)
        self.assertEqual(len(events), 1)

    def test_finished_match_protection(self) -> None:
        """Late provider events must NEVER mutate a finished/closed match."""
        # Transition match to FINISHED
        transition_live_match(self.match_id, FINISHED, source="admin")

        late_event = LiveEvent(
            match_id=self.match_id,
            provider="api_sports",
            provider_event_id="ev_late_goal_99",
            event_type="goal",
            minute=95,
            team_name="Real Madrid"
        )

        res = ingest_live_event(late_event)
        self.assertEqual(res["status"], "rejected")
        self.assertIn("terminal state", res["message"])

        state = get_live_match_state(self.match_id)
        self.assertEqual(state["status"], "FINISHED")
        self.assertEqual(state["home_score"], 0)

    def test_automated_market_suspension_on_major_event(self) -> None:
        """Goals and red cards trigger automatic suspension of in-play betting markets."""
        # Create an open market for this match
        mkt = get_or_create_market(self.match_id, "1x2", "Match Winner")
        get_or_create_selection(mkt["id"], "home", "П1", 1.85)

        # Ingest red card event
        ev = LiveEvent(
            match_id=self.match_id,
            provider="api_sports",
            provider_event_id="ev_red_card_1",
            event_type="red_card",
            minute=44,
            team_name="Barcelona"
        )

        ingest_live_event(ev)

        # Check market status was automatically set to suspended
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM markets WHERE id = ?", (mkt["id"],))
            row = cursor.fetchone()
            self.assertEqual(row["status"], "suspended")
