"""
services/leaderboard_service.py

Fair Leaderboard Engine with Multi-Scope, Multi-Metric Rankings & Cached Pagination.
Strict Invariants:
1. One Canonical Primary Metric: 'rating' (competitive skill).
   Optional secondary metric views: 'roi', 'accuracy', 'value', 'streak', 'season_points'.
2. Fair Leaderboard: Players with fewer than minimum qualifying bets are marked
   is_qualified = False and status = 'NOT_ENOUGH_DATA'.
3. Strict Pagination: limit is enforced between 1 and 50.
4. Scoped In-Memory Caching with immediate invalidation upon settlement or result correction.
5. User Pin: Always computes authenticated user's exact ranking even if outside the requested page.
"""

import time
import logging
from typing import Optional, Any
import database
from services.player_rating import PlayerRatingEngine, MIN_QUALIFYING_BETS

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 30.0
_leaderboard_cache: dict[str, tuple[float, list[dict], int]] = {}


def _get_cache_key(season_id: int, division_id: Optional[int], scope: str, period: str, metric: str) -> str:
    div_part = str(division_id) if division_id is not None else "all"
    return f"leaderboard:{season_id}:{div_part}:{scope.lower()}:{period.lower()}:{metric.lower()}"


def invalidate_leaderboard_cache(season_id: Optional[int] = None, division_id: Optional[int] = None) -> None:
    """Invalidate all or scoped leaderboard caches."""
    global _leaderboard_cache
    if season_id is None and division_id is None:
        _leaderboard_cache.clear()
        return

    keys_to_del = []
    prefix = f"leaderboard:{season_id if season_id is not None else ''}"
    for k in _leaderboard_cache.keys():
        if season_id is not None and str(season_id) not in k:
            continue
        if division_id is not None and str(division_id) not in k:
            continue
        keys_to_del.append(k)

    for k in keys_to_del:
        _leaderboard_cache.pop(k, None)


class LeaderboardService:
    """Provides high-performance, manipulation-resistant leaderboards."""

    @classmethod
    def get_leaderboard(
        cls,
        season_id: Optional[int] = None,
        division_id: Optional[int] = None,
        scope: str = "GLOBAL",
        period: str = "ALL_TIME",
        metric: str = "RATING",
        page: int = 1,
        limit: int = 20,
        user_id: Optional[int] = None
    ) -> dict[str, Any]:
        """
        Fetch paginated leaderboard for given scope, period, and metric.
        """
        target_season_id = season_id
        if target_season_id is None:
            act = database.get_active_season()
            target_season_id = act["id"] if act else 1

        scope = scope.upper()
        if scope not in ("GLOBAL", "DIVISION", "SEASON", "WEEKLY", "MONTHLY"):
            scope = "GLOBAL"

        period = period.upper()
        metric = metric.upper()
        if metric not in ("RATING", "ROI", "ACCURACY", "VALUE", "STREAK", "SEASON_POINTS"):
            metric = "RATING"

        # Boundary checks for pagination
        page = max(1, page)
        limit = max(1, min(50, limit))

        # Check cache
        cache_key = _get_cache_key(target_season_id, division_id, scope, period, metric)
        now = time.time()
        cached = _leaderboard_cache.get(cache_key)

        all_entries: list[dict]
        total_players: int

        if cached and (now - cached[0] < CACHE_TTL_SECONDS):
            _, all_entries, total_players = cached
        else:
            all_entries, total_players = cls._compute_leaderboard(
                season_id=target_season_id,
                division_id=division_id if scope == "DIVISION" else division_id,
                scope=scope,
                period=period,
                metric=metric
            )
            _leaderboard_cache[cache_key] = (now, all_entries, total_players)

        # Slice for current page
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        page_entries = all_entries[start_idx:end_idx]

        # Calculate User Pin if user_id is given
        user_pin = None
        if user_id:
            for idx, entry in enumerate(all_entries):
                if entry["player_id"] == user_id:
                    user_pin = {
                        "rank": entry["rank"],
                        "entry": entry,
                        "on_current_page": (start_idx <= idx < end_idx)
                    }
                    break

        return {
            "scope": scope,
            "season_id": target_season_id,
            "division_id": division_id,
            "period": period,
            "metric": metric,
            "page": page,
            "limit": limit,
            "total_players": total_players,
            "total_pages": max(1, (total_players + limit - 1) // limit),
            "entries": page_entries,
            "user_pin": user_pin
        }

    @classmethod
    def _compute_leaderboard(
        cls,
        season_id: int,
        division_id: Optional[int],
        scope: str,
        period: str,
        metric: str
    ) -> tuple[list[dict], int]:
        """Compute full sorted leaderboard list directly from SQLite."""
        with database.transaction() as conn:
            cursor = conn.cursor()

            query = """
                SELECT 
                    sps.user_id as player_id,
                    u.username,
                    u.team_name,
                    sps.division_id,
                    sps.season_id,
                    sps.rating,
                    sps.season_points,
                    sps.settled_bets,
                    sps.wins,
                    sps.losses,
                    sps.win_rate,
                    sps.roi,
                    sps.current_streak,
                    sps.best_streak,
                    sps.value_bets_hit,
                    sps.status,
                    p.equipped_title as title,
                    p.equipped_frame as frame,
                    p.level
                FROM season_player_stats sps
                LEFT JOIN users u ON sps.user_id = u.telegram_id
                LEFT JOIN user_progression p ON sps.user_id = p.user_id
                WHERE sps.season_id = ?
            """
            params: list[Any] = [season_id]

            if division_id is not None and scope == "DIVISION":
                query += " AND sps.division_id = ?"
                params.append(division_id)

            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

            # Rules check for qualification
            rules = database.get_season_rules(season_id, division_id or 1)
            min_b = rules.get("min_bets_qualification", MIN_QUALIFYING_BETS)

            entries = []
            for r in rows:
                settled = r["settled_bets"]
                is_qualified = (settled >= min_b)
                tier = PlayerRatingEngine.get_tier(r["rating"], settled, min_b)
                status_label = "ACTIVE" if is_qualified else "NOT_ENOUGH_DATA"

                val: float
                if metric == "RATING":
                    val = round(float(r["rating"]), 1)
                elif metric == "ROI":
                    val = round(float(r["roi"]), 1)
                elif metric == "ACCURACY":
                    val = round(float(r["win_rate"]), 1)
                elif metric == "SEASON_POINTS":
                    val = round(float(r["season_points"]), 1)
                elif metric == "STREAK":
                    val = float(r["best_streak"])
                elif metric == "VALUE":
                    val = float(r["value_bets_hit"])
                else:
                    val = round(float(r["rating"]), 1)

                entries.append({
                    "player_id": r["player_id"],
                    "username": r["username"] or f"Игрок #{r['player_id']}",
                    "team_name": r["team_name"] or "Свободный игрок",
                    "division_id": r["division_id"],
                    "season_id": r["season_id"],
                    "rating": round(float(r["rating"]), 1),
                    "tier": tier,
                    "season_points": round(float(r["season_points"]), 1),
                    "settled_bets": settled,
                    "wins": r["wins"],
                    "losses": r["losses"],
                    "win_rate": round(float(r["win_rate"]), 1),
                    "roi": round(float(r["roi"]), 1),
                    "streak": r["current_streak"],
                    "best_streak": r["best_streak"],
                    "value_bets_hit": r["value_bets_hit"],
                    "value": val,
                    "is_qualified": is_qualified,
                    "status": status_label,
                    "level": r["level"] or 1,
                    "title": r["title"] or "Новичок",
                    "frame": r["frame"] or "default"
                })

            # Fair sorting: Qualified players sorted by target metric DESC, then season_points DESC.
            # Unqualified players (NOT_ENOUGH_DATA) placed below qualified players.
            def sort_key(item: dict):
                return (
                    1 if item["is_qualified"] else 0,
                    item["value"],
                    item["season_points"],
                    item["settled_bets"]
                )

            entries.sort(key=sort_key, reverse=True)

            # Assign ranks 1..N
            for idx, item in enumerate(entries):
                item["rank"] = idx + 1

            return entries, len(entries)
