"""
services/sports/adapters/mock_provider.py

Logovo.bet — Deterministic Mock Sports Data Provider (Phase 8).
Used for unit, integration, and E2E testing.
Strict Invariant: Active only in test environments or explicit development overrides.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from services.sports.adapters.base import SportsDataProvider
from services.sports.models import (
    LiveEvent,
    LiveMatchState,
    LiveStatistics,
    ProviderEvent,
    ProviderInjury,
    ProviderLineup,
    ProviderMatch,
    ProviderOdds,
    ProviderStatistics,
)


class MockSportsDataProvider(SportsDataProvider):
    """Deterministic in-memory mock sports provider for automated tests."""

    def __init__(self) -> None:
        self.matches: dict[int, LiveMatchState] = {}
        self.events: dict[int, list[LiveEvent]] = {}
        self.statistics: dict[int, LiveStatistics] = {}
        self.odds: dict[int, list[dict[str, Any]]] = {}

        # Phase 8 Neutral Stores
        self.fixtures: dict[str, ProviderMatch] = {}
        self.neutral_events: dict[str, list[ProviderEvent]] = {}
        self.neutral_statistics: dict[str, ProviderStatistics] = {}
        self.lineups: dict[str, list[ProviderLineup]] = {}
        self.injuries: dict[str, list[ProviderInjury]] = {}
        self.neutral_odds: dict[str, list[ProviderOdds]] = {}
        self.standings: dict[str, list[dict[str, Any]]] = {}

        self.error_count = 0
        self.simulate_failure = False

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def is_connected(self) -> bool:
        return not self.simulate_failure

    def seed_match(self, state: LiveMatchState) -> None:
        self.matches[state.match_id] = state

    def seed_event(self, event: LiveEvent) -> None:
        self.events.setdefault(event.match_id, []).append(event)

    def seed_statistics(self, stats: LiveStatistics) -> None:
        self.statistics[stats.match_id] = stats

    def seed_odds(self, match_id: int, odds_list: list[dict[str, Any]]) -> None:
        self.odds[match_id] = odds_list

    def seed_fixture(self, fixture: ProviderMatch) -> None:
        self.fixtures[str(fixture.match_id)] = fixture

    def seed_neutral_event(self, event: ProviderEvent) -> None:
        self.neutral_events.setdefault(str(event.match_id), []).append(event)

    def seed_neutral_statistics(self, stats: ProviderStatistics) -> None:
        self.neutral_statistics[str(stats.match_id)] = stats

    def seed_lineup(self, lineup: ProviderLineup) -> None:
        self.lineups.setdefault(str(lineup.match_id), []).append(lineup)

    def seed_injury(self, injury: ProviderInjury) -> None:
        key = str(injury.fixture_id) if injury.fixture_id else str(injury.team_id or "global")
        self.injuries.setdefault(key, []).append(injury)

    def seed_neutral_odds(self, odds: ProviderOdds) -> None:
        self.neutral_odds.setdefault(str(odds.match_id), []).append(odds)

    def clear(self) -> None:
        """Purge all seeded test data."""
        self.matches.clear()
        self.events.clear()
        self.statistics.clear()
        self.odds.clear()
        self.fixtures.clear()
        self.neutral_events.clear()
        self.neutral_statistics.clear()
        self.lineups.clear()
        self.injuries.clear()
        self.neutral_odds.clear()
        self.standings.clear()

    def clear_fixtures(self) -> None:
        """Purge seeded neutral fixtures."""
        self.fixtures.clear()


    # ── Legacy & Live Match Lifecycle Contracts ──────────────────────────────

    async def get_matches(self, division_id: Optional[int] = None, season_id: Optional[int] = None) -> list[LiveMatchState]:
        if self.simulate_failure:
            raise ConnectionError("Simulated provider failure")
        res = list(self.matches.values())
        if division_id is not None:
            res = [m for m in res if m.division_id == division_id]
        if season_id is not None:
            res = [m for m in res if m.season_id == season_id]
        return res

    async def get_match(self, match_id: int) -> Optional[LiveMatchState]:
        if self.simulate_failure:
            raise ConnectionError("Simulated provider failure")
        return self.matches.get(match_id)

    async def get_live_matches(self) -> list[LiveMatchState]:
        if self.simulate_failure:
            raise ConnectionError("Simulated provider failure")
        return [m for m in self.matches.values() if m.status in ("LIVE", "HALFTIME")]

    async def get_match_events(self, match_id: int) -> list[LiveEvent]:
        if self.simulate_failure:
            raise ConnectionError("Simulated provider failure")
        return list(self.events.get(match_id, []))

    async def get_match_statistics(self, match_id: int) -> Optional[LiveStatistics]:
        if self.simulate_failure:
            raise ConnectionError("Simulated provider failure")
        return self.statistics.get(match_id)

    async def get_match_odds(self, match_id: int) -> list[dict[str, Any]]:
        if self.simulate_failure:
            raise ConnectionError("Simulated provider failure")
        return list(self.odds.get(match_id, []))

    def get_provider_status(self) -> dict[str, Any]:
        return {
            "provider": "mock",
            "connected": not self.simulate_failure,
            "status": "HEALTHY" if not self.simulate_failure else "DEGRADED",
            "message": "Deterministic mock provider active for testing",
            "last_sync": datetime.now(timezone.utc).isoformat(),
        }

    # ── Phase 8 Provider-Neutral Contracts ───────────────────────────────────

    async def get_fixtures(
        self,
        division_id: Optional[int] = None,
        season_id: Optional[int] = None,
        date: Optional[str] = None
    ) -> list[ProviderMatch]:
        if self.simulate_failure:
            raise ConnectionError("Simulated provider failure")
        return list(self.fixtures.values())

    async def get_fixture(self, match_id: int | str) -> Optional[ProviderMatch]:
        if self.simulate_failure:
            raise ConnectionError("Simulated provider failure")
        return self.fixtures.get(str(match_id))

    async def get_live_fixtures(self) -> list[ProviderMatch]:
        if self.simulate_failure:
            raise ConnectionError("Simulated provider failure")
        return [f for f in self.fixtures.values() if f.status in ("LIVE", "HALFTIME")]

    async def get_events(self, match_id: int | str) -> list[ProviderEvent]:
        if self.simulate_failure:
            raise ConnectionError("Simulated provider failure")
        return list(self.neutral_events.get(str(match_id), []))

    async def get_statistics(self, match_id: int | str) -> Optional[ProviderStatistics]:
        if self.simulate_failure:
            raise ConnectionError("Simulated provider failure")
        return self.neutral_statistics.get(str(match_id))

    async def get_lineups(self, match_id: int | str) -> list[ProviderLineup]:
        if self.simulate_failure:
            raise ConnectionError("Simulated provider failure")
        return list(self.lineups.get(str(match_id), []))

    async def get_injuries(self, match_id: int | str) -> list[ProviderInjury]:
        if self.simulate_failure:
            raise ConnectionError("Simulated provider failure")
        return list(self.injuries.get(str(match_id), []))

    async def get_odds(self, match_id: int | str) -> list[ProviderOdds]:
        if self.simulate_failure:
            raise ConnectionError("Simulated provider failure")
        return list(self.neutral_odds.get(str(match_id), []))

    async def get_standings(self, competition_id: int | str, season_id: int | str) -> list[dict[str, Any]]:
        if self.simulate_failure:
            raise ConnectionError("Simulated provider failure")
        key = f"{competition_id}_{season_id}"
        return list(self.standings.get(key, []))
