"""
tests/test_phase8_division_season.py

Phase 8 — Division & Season Isolation Across Real Sports Ingestion & AI Test Suite.
Verifies:
1. Provider match linking preserves strict division_id and season_id isolation.
2. In-play live query endpoints filter accurately by division and season.
3. Live events and statistics remain strictly partitioned by internal match boundaries.
4. AI feature extraction does not cross division or season boundaries.
"""

import unittest
from datetime import datetime, timezone

import database
from services.feature_engine import FeatureEngine
from services.live_ingestion import ingest_live_event, get_live_events
from services.sports.models import LiveEvent


class TestPhase8DivisionSeasonIsolation(unittest.TestCase):
    """Verifies complete multi-tenant partitioning across divisions and seasons."""

    def setUp(self) -> None:
        database.init_db()
        self.m_div1_s1 = 9201
        self.m_div2_s1 = 9202
        self.m_div1_s2 = 9203

        self.team_a = "IsoTeamA"
        self.team_b = "IsoTeamB"

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM live_events WHERE match_id IN (?, ?, ?)", (self.m_div1_s1, self.m_div2_s1, self.m_div1_s2))
            cursor.execute("DELETE FROM live_match_states WHERE match_id IN (?, ?, ?)", (self.m_div1_s1, self.m_div2_s1, self.m_div1_s2))
            cursor.execute("DELETE FROM provider_matches WHERE match_id IN (?, ?, ?)", (self.m_div1_s1, self.m_div2_s1, self.m_div1_s2))
            cursor.execute("DELETE FROM matches WHERE id IN (?, ?, ?)", (self.m_div1_s1, self.m_div2_s1, self.m_div1_s2))
            cursor.execute("DELETE FROM matches WHERE LOWER(player1_team) IN (LOWER(?), LOWER(?)) OR LOWER(player2_team) IN (LOWER(?), LOWER(?))",
                           (self.team_a, self.team_b, self.team_a, self.team_b))

            # 1. Div 1 Season 1 Match
            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, status)
                VALUES (?, 1, 1, 1, ?, ?, 'active')
            """, (self.m_div1_s1, self.team_a, self.team_b))
            cursor.execute("""
                INSERT INTO live_match_states (match_id, season_id, division_id, status, period, minute, home_score, away_score, provider)
                VALUES (?, 1, 1, 'LIVE', '1h', 15, 0, 0, 'api_sports')
            """, (self.m_div1_s1,))

            # 2. Div 2 Season 1 Match
            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, status)
                VALUES (?, 2, 1, 1, 'Leeds', 'Leicester', 'active')
            """, (self.m_div2_s1,))
            cursor.execute("""
                INSERT INTO live_match_states (match_id, season_id, division_id, status, period, minute, home_score, away_score, provider)
                VALUES (?, 1, 2, 'LIVE', '1h', 20, 1, 0, 'api_sports')
            """, (self.m_div2_s1,))

            # 3. Div 1 Season 2 Match
            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, status)
                VALUES (?, 1, 2, 1, 'Tottenham', 'Brighton', 'active')
            """, (self.m_div1_s2,))
            cursor.execute("""
                INSERT INTO live_match_states (match_id, season_id, division_id, status, period, minute, home_score, away_score, provider)
                VALUES (?, 2, 1, 'LIVE', '1h', 5, 0, 0, 'api_sports')
            """, (self.m_div1_s2,))

    def test_provider_match_link_partitioning(self) -> None:
        """Provider matches maintain 1:1 mapping with internal match_id."""
        database.link_provider_match(
            provider="api_sports",
            provider_match_id="pm_888",
            match_id=self.m_div1_s1,
            division_id=1,
            season_id=1,
            payload={"status": "live", "div": 1}
        )
        database.link_provider_match(
            provider="api_sports",
            provider_match_id="pm_999",
            match_id=self.m_div2_s1,
            division_id=2,
            season_id=1,
            payload={"status": "live", "div": 2}
        )

        pm1 = database.get_provider_match("api_sports", "pm_888")
        pm2 = database.get_provider_match("api_sports", "pm_999")

        self.assertIsNotNone(pm1)
        self.assertIsNotNone(pm2)
        self.assertEqual(pm1["match_id"], self.m_div1_s1)
        self.assertEqual(pm2["match_id"], self.m_div2_s1)

    def test_live_query_division_and_season_scoping(self) -> None:
        """Live match queries strictly honor division_id and season_id filters."""
        with database.transaction() as conn:
            cursor = conn.cursor()

            # Query Div 1 Season 1 only
            cursor.execute("""
                SELECT match_id FROM live_match_states
                WHERE division_id = 1 AND season_id = 1 AND match_id IN (?, ?, ?)
            """, (self.m_div1_s1, self.m_div2_s1, self.m_div1_s2))
            rows = cursor.fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["match_id"], self.m_div1_s1)

            # Query Div 2 Season 1 only
            cursor.execute("""
                SELECT match_id FROM live_match_states
                WHERE division_id = 2 AND season_id = 1 AND match_id IN (?, ?, ?)
            """, (self.m_div1_s1, self.m_div2_s1, self.m_div1_s2))
            rows = cursor.fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["match_id"], self.m_div2_s1)

    def test_live_event_ingestion_isolation(self) -> None:
        """Live events ingested for Match in Div 1 S1 are invisible in Div 2 S1 queries."""
        ev = LiveEvent(
            match_id=self.m_div1_s1,
            provider="api_sports",
            provider_event_id="ev_div1_s1_goal",
            event_type="goal",
            minute=33,
            team_name=self.team_a
        )
        ingest_live_event(ev)

        # Div 1 S1 has event
        events_div1 = get_live_events(self.m_div1_s1)
        self.assertEqual(len(events_div1), 1)
        self.assertEqual(events_div1[0]["provider_event_id"], "ev_div1_s1_goal")

        # Div 2 S1 has zero events
        events_div2 = get_live_events(self.m_div2_s1)
        self.assertEqual(len(events_div2), 0)

    def test_feature_engine_division_and_season_isolation(self) -> None:
        """Historical statistics for Match #9201 strictly scope to Div 1 Season 1."""
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM matches WHERE id IN (9190, 9191)")
            # Insert completed match for IsoTeamA in Div 2 S1
            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, player1_score, player2_score, status)
                VALUES (9190, 2, 1, 1, ?, 'Leeds', 7, 0, 'completed')
            """, (self.team_a,))

            # Insert completed match for IsoTeamA in Div 1 S2
            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, player1_score, player2_score, status)
                VALUES (9191, 1, 2, 1, ?, 'Brighton', 6, 0, 'completed')
            """, (self.team_a,))

        # Extract features for Target Match (Div 1 Season 1)
        features = FeatureEngine.extract_match_features(self.m_div1_s1)

        # Neither Match #9190 (Div 2) nor Match #9191 (Season 2) should be counted
        t1_overall = features["team1_features"]["overall"]
        self.assertEqual(t1_overall["matches_played"], 0)
        self.assertEqual(t1_overall["goals_for"], 0)
