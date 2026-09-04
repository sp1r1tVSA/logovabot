"""
tests/test_phase6_provider.py

Tests for Phase 6B: Sports Data Provider Abstraction & Data Normalization.
Ensures:
1. Zero fake live data in production.
2. NullSportsDataProvider safely reports "LIVE DATA UNAVAILABLE".
3. MockSportsDataProvider allows deterministic testing.
4. APISportsProvider circuit breaker, error handling, and data normalization.
5. All statistics preserve NULL (None) when unavailable (NEVER 0).
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.sports_provider import (
    APISportsProvider,
    LiveEvent,
    LiveMatchState,
    LiveStatistics,
    MockSportsDataProvider,
    NullSportsDataProvider,
    get_sports_provider,
    set_sports_provider,
)


class TestPhase6Provider(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        set_sports_provider(None)

    def tearDown(self) -> None:
        set_sports_provider(None)

    async def test_null_provider_zero_fake_data(self) -> None:
        """NullSportsDataProvider must return empty data and explicit unavailable status."""
        null_prov = NullSportsDataProvider()
        status = null_prov.get_provider_status()

        self.assertEqual(status["provider"], "null")
        self.assertFalse(status["connected"])
        self.assertEqual(status["status"], "UNAVAILABLE")
        self.assertIn("LIVE DATA UNAVAILABLE", status["message"])

        matches = await null_prov.get_matches()
        self.assertEqual(matches, [])

        live_matches = await null_prov.get_live_matches()
        self.assertEqual(live_matches, [])

        single = await null_prov.get_match(12345)
        self.assertIsNone(single)

        events = await null_prov.get_match_events(12345)
        self.assertEqual(events, [])

        stats = await null_prov.get_match_statistics(12345)
        self.assertIsNone(stats)

        odds = await null_prov.get_match_odds(12345)
        self.assertEqual(odds, [])

    async def test_mock_provider_deterministic_seeding(self) -> None:
        """MockSportsDataProvider seeds and retrieves deterministic fixtures."""
        mock_prov = MockSportsDataProvider()

        m = LiveMatchState(
            match_id=9901,
            season_id=1,
            division_id=2,
            status="LIVE",
            period="1h",
            minute=35,
            home_score=1,
            away_score=0,
            provider="mock"
        )
        mock_prov.seed_match(m)

        ev = LiveEvent(
            match_id=9901,
            provider="mock",
            provider_event_id="9901_35_1_goal",
            event_type="goal",
            minute=35,
            team_name="Порту",
            player_name="Galeno"
        )
        mock_prov.seed_event(ev)

        st = LiveStatistics(
            match_id=9901,
            possession_home=58.5,
            possession_away=41.5,
            shots_home=6,
            shots_away=2,
            corners_home=4,
            corners_away=1,
            # Unavailable fields MUST be None
            xg_home=None,
            xg_away=None,
            provider="mock"
        )
        mock_prov.seed_statistics(st)

        # Retrieve and verify
        match = await mock_prov.get_match(9901)
        self.assertIsNotNone(match)
        self.assertEqual(match.minute, 35)
        self.assertEqual(match.home_score, 1)

        live_matches = await mock_prov.get_live_matches()
        self.assertEqual(len(live_matches), 1)
        self.assertEqual(live_matches[0].match_id, 9901)

        events = await mock_prov.get_match_events(9901)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "goal")
        self.assertEqual(events[0].player_name, "Galeno")

        stats = await mock_prov.get_match_statistics(9901)
        self.assertIsNotNone(stats)
        self.assertEqual(stats.possession_home, 58.5)
        self.assertIsNone(stats.xg_home)  # Never fake 0!

    async def test_mock_provider_simulated_failure(self) -> None:
        """Mock provider handles simulated network failure cleanly."""
        mock_prov = MockSportsDataProvider()
        mock_prov.simulate_failure = True

        status = mock_prov.get_provider_status()
        self.assertEqual(status["status"], "DEGRADED")
        self.assertFalse(status["connected"])

        with self.assertRaises(ConnectionError):
            await mock_prov.get_live_matches()

    def test_api_sports_unconfigured_status(self) -> None:
        """APISportsProvider without API key reports unconfigured status."""
        prov = APISportsProvider(api_key="")
        status = prov.get_provider_status()
        self.assertEqual(status["status"], "UNCONFIGURED")
        self.assertFalse(status["connected"])

    def test_api_sports_circuit_breaker(self) -> None:
        """APISportsProvider circuit breaker trips after repeated errors."""
        prov = APISportsProvider(api_key="test_key")
        self.assertFalse(prov.circuit_open)

        # Trigger 5 errors to trip breaker
        for i in range(5):
            prov._record_failure(Exception(f"Error {i}"))

        self.assertTrue(prov.circuit_open)
        status = prov.get_provider_status()
        self.assertEqual(status["status"], "CIRCUIT_OPEN")
        self.assertFalse(status["connected"])

        # Reset via success
        prov._record_success()
        self.assertFalse(prov.circuit_open)
        self.assertEqual(prov.get_provider_status()["status"], "HEALTHY")

    def test_api_sports_event_normalization(self) -> None:
        """APISportsProvider accurately maps external event payloads to canonical LiveEvents."""
        prov = APISportsProvider(api_key="test_key")

        # 1. Goal
        ev_data = {
            "time": {"elapsed": 24, "extra": None},
            "type": "Goal",
            "detail": "Normal Goal",
            "team": {"id": 10, "name": "Benfica"},
            "player": {"id": 501, "name": "Di Maria"}
        }
        norm = prov._normalize_event(777, 0, ev_data)
        self.assertEqual(norm.event_type, "goal")
        self.assertEqual(norm.minute, 24)
        self.assertEqual(norm.player_name, "Di Maria")

        # 2. Own Goal
        ev_data["detail"] = "Own Goal"
        norm_og = prov._normalize_event(777, 1, ev_data)
        self.assertEqual(norm_og.event_type, "own_goal")

        # 3. Penalty
        ev_data["detail"] = "Penalty"
        norm_pen = prov._normalize_event(777, 2, ev_data)
        self.assertEqual(norm_pen.event_type, "penalty")

        # 4. Red Card
        card_data = {
            "time": {"elapsed": 68, "extra": 2},
            "type": "Card",
            "detail": "Red Card",
            "team": {"id": 10, "name": "Benfica"},
            "player": {"id": 502, "name": "Otamendi"}
        }
        norm_card = prov._normalize_event(777, 3, card_data)
        self.assertEqual(norm_card.event_type, "red_card")
        self.assertEqual(norm_card.added_time, 2)

    def test_api_sports_statistics_normalization_null_preservation(self) -> None:
        """Verify statistics normalization preserves None for unprovided stats."""
        prov = APISportsProvider(api_key="test_key")

        raw_stats = [
            {
                "team": {"id": 1, "name": "Team A"},
                "statistics": [
                    {"type": "Ball Possession", "value": "62%"},
                    {"type": "Total Shots", "value": 14},
                    {"type": "Corner Kicks", "value": 7}
                    # xG, fouls, cards omitted
                ]
            },
            {
                "team": {"id": 2, "name": "Team B"},
                "statistics": [
                    {"type": "Ball Possession", "value": "38%"},
                    {"type": "Total Shots", "value": 5},
                    {"type": "Corner Kicks", "value": 2}
                ]
            }
        ]
        stats = prov._normalize_statistics(888, raw_stats)
        self.assertEqual(stats.match_id, 888)
        self.assertEqual(stats.possession_home, 62.0)
        self.assertEqual(stats.possession_away, 38.0)
        self.assertEqual(stats.shots_home, 14)
        self.assertEqual(stats.corners_home, 7)
        # Critical assertion: Missing stats are None, NOT 0.0 or 0
        self.assertIsNone(stats.xg_home)
        self.assertIsNone(stats.xg_away)
        self.assertIsNone(stats.fouls_home)
        self.assertIsNone(stats.yellow_cards_home)


if __name__ == "__main__":
    unittest.main()
