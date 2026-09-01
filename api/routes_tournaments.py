"""
api/routes_tournaments.py

Logovo.bet — Tournament Hub & Results Center API:
- Tournaments catalog
- Standings tables
- Finished matches & results archive
- Top scorers / stats leaders
"""

import logging
from aiohttp import web
import database
from api.auth import get_authenticated_user

logger = logging.getLogger(__name__)


async def handle_get_tournaments(request: web.Request) -> web.Response:
    """
    GET /api/tournaments
    List active tournaments and championships.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tournaments WHERE is_active = 1 ORDER BY id ASC")
        tournaments = [dict(r) for r in cursor.fetchall()]

        if not tournaments:
            tournaments = [{
                "id": 1,
                "name": "Логово Фифарей (Основная Лига)",
                "type": "league",
                "season": "Сезон 2026",
                "is_active": 1
            }]

    return web.json_response({
        "status": "ok",
        "tournaments": tournaments
    })


async def handle_get_standings(request: web.Request) -> web.Response:
    """
    GET /api/tournaments/{id}/standings
    Returns tournament standings table.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    try:
        standings = database.get_standings()
    except Exception as e:
        logger.warning(f"Error fetching standings: {e}")
        standings = []

    return web.json_response({
        "status": "ok",
        "standings": standings
    })


async def handle_get_results(request: web.Request) -> web.Response:
    """
    GET /api/tournaments/{id}/results?limit=30
    Returns finished match results archive.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    limit = min(50, int(request.query.get("limit", 30)))

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT m.*, 
                   COALESCE(m.player1_team, 'Хозяева') as team1_name,
                   COALESCE(m.player2_team, 'Гости') as team2_name
            FROM matches m
            WHERE m.status IN ('confirmed', 'completed', 'finished')
              AND m.player1_score IS NOT NULL AND m.player2_score IS NOT NULL
            ORDER BY m.played_at DESC, m.id DESC
            LIMIT ?
        """, (limit,))
        results = [dict(r) for r in cursor.fetchall()]

    return web.json_response({
        "status": "ok",
        "results": results
    })


async def handle_get_top_scorers(request: web.Request) -> web.Response:
    """
    GET /api/tournaments/{id}/top-scorers
    Returns top goalscorers and assist leaders.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT player_name, team_name, SUM(count) as goals
            FROM match_events
            WHERE event_type = 'goal'
            GROUP BY player_name, team_name
            ORDER BY goals DESC
            LIMIT 15
        """)
        top_scorers = [dict(r) for r in cursor.fetchall()]

    return web.json_response({
        "status": "ok",
        "top_scorers": top_scorers
    })
