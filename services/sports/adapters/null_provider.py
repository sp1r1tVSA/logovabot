"""
services/sports/adapters/null_provider.py

Logovo.bet — Production Safe Null Sports Data Provider (Phase 8).
Active whenever no valid API key is provisioned or service is intentionally paused.
Strict Invariant: Zero fake data, zero hallucinations, zero synthetic xG.
"""

from __future__ import annotations

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


class NullSportsDataProvider(SportsDataProvider):
    """
    Default provider when no real sports data feed is connected.
    Guarantees that production never fabricates or hallucinates live events or statistics.
    """

    def __init__(self, reason: str = "LIVE DATA UNAVAILABLE: No live provider configured.") -> None:
        self.reason = reason

    @property
    def provider_name(self) -> str:
        return "null"

    @property
    def is_connected(self) -> bool:
        return False

    async def get_matches(self, division_id: Optional[int] = None, season_id: Optional[int] = None) -> list[LiveMatchState]:
        return []

    async def get_match(self, match_id: int) -> Optional[LiveMatchState]:
        return None

    async def get_live_matches(self) -> list[LiveMatchState]:
        return []

    async def get_match_events(self, match_id: int) -> list[LiveEvent]:
        return []

    async def get_match_statistics(self, match_id: int) -> Optional[LiveStatistics]:
        return None

    async def get_match_odds(self, match_id: int) -> list[dict[str, Any]]:
        return []

    def get_provider_status(self) -> dict[str, Any]:
        return {
            "provider": "null",
            "connected": False,
            "status": "UNAVAILABLE",
            "message": self.reason,
            "last_sync": None,
        }

    # ── Phase 8 Provider-Neutral Contracts ───────────────────────────────────

    async def get_fixtures(
        self,
        division_id: Optional[int] = None,
        season_id: Optional[int] = None,
        date: Optional[str] = None
    ) -> list[ProviderMatch]:
        return []

    async def get_fixture(self, match_id: int | str) -> Optional[ProviderMatch]:
        return None

    async def get_live_fixtures(self) -> list[ProviderMatch]:
        return []

    async def get_events(self, match_id: int | str) -> list[ProviderEvent]:
        return []

    async def get_statistics(self, match_id: int | str) -> Optional[ProviderStatistics]:
        return None

    async def get_lineups(self, match_id: int | str) -> list[ProviderLineup]:
        return []

    async def get_injuries(self, match_id: int | str) -> list[ProviderInjury]:
        return []

    async def get_odds(self, match_id: int | str) -> list[ProviderOdds]:
        return []

    async def get_standings(self, competition_id: int | str, season_id: int | str) -> list[dict[str, Any]]:
        return []
