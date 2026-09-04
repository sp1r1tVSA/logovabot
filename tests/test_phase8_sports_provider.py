"""
tests/test_phase8_sports_provider.py

Phase 8 — Real Sports Data Provider Abstraction & Adapters Test Suite.
Verifies provider-neutral contracts, normalization of all sports entities,
zero synthetic data preservation, and adapter behavior.
"""

import unittest

from services.sports.models import (
    ProviderTeam,
    ProviderMatch,
    ProviderEvent,
    ProviderStatistics,
    ProviderLineup,
    ProviderInjury,
    ProviderOdds,
)
from services.sports.adapters.null_provider import NullSportsDataProvider
from services.sports.adapters.mock_provider import MockSportsDataProvider
from services.sports.adapters.api_sports import APISportsProvider


class TestPhase8SportsProvider(unittest.IsolatedAsyncioTestCase):
    """Test suite for sports provider abstractions and normalization layer."""

    async def test_null_provider_guarantees(self) -> None:
        """NullSportsDataProvider strictly adheres to zero-fake-data contract."""
        prov = NullSportsDataProvider()
        self.assertEqual(prov.provider_name, "null")
        self.assertFalse(prov.is_connected)

        # Legacy calls return empty/None
        self.assertEqual(await prov.get_matches(), [])
        self.assertIsNone(await prov.get_match(1))
        self.assertEqual(await prov.get_live_matches(), [])
        self.assertEqual(await prov.get_match_events(1), [])
        self.assertIsNone(await prov.get_match_statistics(1))
        self.assertEqual(await prov.get_match_odds(1), [])

        # Provider-neutral calls return empty/None
        self.assertEqual(await prov.get_fixtures(), [])
        self.assertIsNone(await prov.get_fixture(1))
        self.assertEqual(await prov.get_live_fixtures(), [])
        self.assertEqual(await prov.get_events(1), [])
        self.assertIsNone(await prov.get_statistics(1))
        self.assertEqual(await prov.get_lineups(1), [])
        self.assertEqual(await prov.get_injuries(1), [])
        self.assertEqual(await prov.get_odds(1), [])
        self.assertEqual(await prov.get_standings(1, 1), [])

        status = prov.get_provider_status()
        self.assertEqual(status["status"], "UNAVAILABLE")
        self.assertIn("LIVE DATA UNAVAILABLE", status["message"])

    async def test_mock_provider_seeding_and_clearing(self) -> None:
        """MockSportsDataProvider supports deterministic seeding and retrieval."""
        mock_p = MockSportsDataProvider()
        self.assertEqual(mock_p.provider_name, "mock")
        self.assertTrue(mock_p.is_connected)

        # Seed fixture
        m = ProviderMatch(
            match_id="m_100",
            provider="mock",
            home_team=ProviderTeam(team_id="t1", name="Arsenal"),
            away_team=ProviderTeam(team_id="t2", name="Chelsea"),
            status="LIVE",
            minute=35,
            home_score=1,
            away_score=0
        )
        mock_p.seed_fixture(m)

        # Seed lineup
        lu = ProviderLineup(
            match_id="m_100",
            provider="mock",
            team_id="t1",
            team_name="Arsenal",
            formation="4-3-3",
            starting_xi=[{"name": "Saka", "number": 7, "pos": "FW"}],
            substitutes=[{"name": "Jesus", "number": 9, "pos": "FW"}]
        )
        mock_p.seed_lineup(lu)

        # Seed injury
        inj = ProviderInjury(
            fixture_id="m_100",
            team_id="t2",
            team_name="Chelsea",
            player_name="James",
            injury_type="Hamstring",
            status="Missing"
        )
        mock_p.seed_injury(inj)

        # Seed odds
        odd = ProviderOdds(
            match_id="m_100",
            provider="mock",
            bookmaker_id=1,
            bookmaker_name="MockBet",
            market_key="match_result",
            market_name="1X2",
            selections=[{"selection_key": "home", "name": "П1", "odds": 1.95}]
        )
        mock_p.seed_neutral_odds(odd)


        # Verify retrieval
        fixtures = await mock_p.get_fixtures()
        self.assertEqual(len(fixtures), 1)
        self.assertEqual(fixtures[0].home_team.name, "Arsenal")

        lineups = await mock_p.get_lineups("m_100")
        self.assertEqual(len(lineups), 1)
        self.assertEqual(lineups[0].formation, "4-3-3")

        injuries = await mock_p.get_injuries("m_100")
        self.assertEqual(len(injuries), 1)
        self.assertEqual(injuries[0].injury_type, "Hamstring")

        odds = await mock_p.get_odds("m_100")
        self.assertEqual(len(odds), 1)
        self.assertEqual(odds[0].selections[0]["odds"], 1.95)

        # Clear fixtures
        mock_p.clear_fixtures()
        self.assertEqual(len(await mock_p.get_fixtures()), 0)

    async def test_api_sports_normalization_pipeline(self) -> None:
        """APISportsProvider normalizes raw JSON payloads into canonical models."""
        prov = APISportsProvider(api_key="test_api_key")

        raw_fixture = {
            "fixture": {
                "id": 867946,
                "status": {"short": "2H", "elapsed": 67},
                "date": "2026-09-04T18:00:00+00:00",
                "referee": "Michael Oliver",
                "venue": {"name": "Emirates Stadium"}
            },
            "league": {"id": 39, "name": "Premier League", "season": 2026, "round": "Regular Season - 5"},
            "teams": {
                "home": {"id": 42, "name": "Arsenal", "logo": "https://media.api-sports.io/football/teams/42.png"},
                "away": {"id": 49, "name": "Chelsea", "logo": "https://media.api-sports.io/football/teams/49.png"}
            },
            "goals": {"home": 2, "away": 1}
        }

        m = prov._normalize_fixture(raw_fixture)
        self.assertEqual(m.provider, "api_sports")
        self.assertEqual(m.match_id, 867946)
        self.assertEqual(m.home_team.name, "Arsenal")
        self.assertEqual(m.away_team.name, "Chelsea")
        self.assertEqual(m.status, "LIVE")
        self.assertEqual(m.minute, 67)
        self.assertEqual(m.home_score, 2)
        self.assertEqual(m.away_score, 1)

    async def test_api_sports_statistics_no_synthetic_xg(self) -> None:
        """APISportsProvider preserves explicit None for unprovided stats (zero fake xG)."""
        prov = APISportsProvider(api_key="test_api_key")

        raw_stats = [
            {
                "team": {"id": 42, "name": "Arsenal"},
                "statistics": [
                    {"type": "Ball Possession", "value": "58%"},
                    {"type": "Total Shots", "value": 14},
                    {"type": "Shots on Goal", "value": 6},
                    {"type": "Corner Kicks", "value": 7},
                    {"type": "Fouls", "value": 10},
                    {"type": "expected_goals", "value": None}  # Unprovided xG
                ]
            },
            {
                "team": {"id": 49, "name": "Chelsea"},
                "statistics": [
                    {"type": "Ball Possession", "value": "42%"},
                    {"type": "Total Shots", "value": 8},
                    {"type": "Shots on Goal", "value": 3},
                    {"type": "Corner Kicks", "value": 4},
                    {"type": "Fouls", "value": 12},
                    {"type": "expected_goals", "value": None}
                ]
            }
        ]

        stats = prov._normalize_statistics("867946", raw_stats)
        self.assertIsNotNone(stats)
        self.assertEqual(stats.possession_home, 58)
        self.assertEqual(stats.possession_away, 42)
        self.assertEqual(stats.shots_home, 14)
        self.assertEqual(stats.shots_away, 8)
        # Strictly verify no synthetic 0.00 xG
        self.assertIsNone(stats.xg_home)
        self.assertIsNone(stats.xg_away)

    async def test_api_sports_lineup_and_injury_normalization(self) -> None:
        """APISportsProvider normalizes lineups and injuries correctly."""
        prov = APISportsProvider(api_key="test_api_key")

        raw_lineup = {
            "team": {"id": 42, "name": "Arsenal"},
            "coach": {"name": "Mikel Arteta"},
            "formation": "4-3-3",
            "startXI": [
                {"player": {"id": 101, "name": "Raya", "number": 1, "pos": "G"}},
                {"player": {"id": 102, "name": "Saka", "number": 7, "pos": "F"}}
            ],
            "substitutes": [
                {"player": {"id": 103, "name": "Jesus", "number": 9, "pos": "F"}}
            ]
        }

        lineup = prov._normalize_lineup("867946", raw_lineup)
        self.assertEqual(lineup.formation, "4-3-3")
        self.assertEqual(lineup.coach_name, "Mikel Arteta")
        self.assertEqual(len(lineup.starting_xi), 2)
        self.assertEqual(len(lineup.substitutes), 1)

        raw_injury = {
            "player": {"id": 201, "name": "Odegaard", "type": "Ankle Injury", "reason": "Missing"},
            "team": {"id": 42, "name": "Arsenal"},
            "fixture": {"id": 867946}
        }

        injury = prov._normalize_injury(raw_injury)
        self.assertEqual(injury.player_name, "Odegaard")
        self.assertEqual(injury.injury_type, "Ankle Injury")
        self.assertEqual(injury.status, "Missing")
