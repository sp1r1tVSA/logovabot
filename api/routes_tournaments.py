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


async def handle_get_divisions(request: web.Request) -> web.Response:
    """
    GET /api/divisions
    List all active divisions.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    divisions = database.get_divisions(only_active=True)
    return web.json_response({
        "status": "ok",
        "divisions": divisions
    })


async def handle_get_seasons(request: web.Request) -> web.Response:
    """
    GET /api/seasons
    List all seasons.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    seasons = database.list_seasons()
    return web.json_response({
        "status": "ok",
        "seasons": seasons
    })


async def handle_get_season_by_id(request: web.Request) -> web.Response:
    """
    GET /api/seasons/{id}
    Get specific season details.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    s_id_str = request.match_info.get("id")
    if not s_id_str or not s_id_str.isdigit():
        return web.json_response({"status": "error", "error": "invalid_season_id"}, status=400)

    season = database.get_season(int(s_id_str))
    if not season:
        return web.json_response({"status": "error", "error": "season_not_found"}, status=404)

    return web.json_response({
        "status": "ok",
        "season": season
    })


async def handle_get_standings(request: web.Request) -> web.Response:
    """
    GET /api/tournaments/{id}/standings?division_id=X&season_id=Y
    Returns tournament standings table strictly isolated by division and season.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    division_id = request.query.get("division_id")
    season_id = request.query.get("season_id")
    div_id = int(division_id) if division_id and division_id.isdigit() else None
    s_id = int(season_id) if season_id and season_id.isdigit() else None

    try:
        standings = database.get_standings(division_id=div_id, season_id=s_id)
    except Exception as e:
        logger.warning(f"Error fetching standings: {e}")
        standings = []

    return web.json_response({
        "status": "ok",
        "standings": standings
    })


async def handle_get_results(request: web.Request) -> web.Response:
    """
    GET /api/tournaments/{id}/results?limit=30&division_id=X&season_id=Y
    Returns finished match results archive filtered by division and season.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    limit = min(50, int(request.query.get("limit", 30)))
    div_param = request.query.get("division_id")
    season_param = request.query.get("season_id")

    with database.transaction() as conn:
        cursor = conn.cursor()
        query = """
            SELECT m.*, 
                   COALESCE(m.player1_team, 'Хозяева') as team1_name,
                   COALESCE(m.player2_team, 'Гости') as team2_name
            FROM matches m
            WHERE m.status IN ('confirmed', 'completed', 'finished')
              AND m.player1_score IS NOT NULL AND m.player2_score IS NOT NULL
        """
        params = []
        if div_param and div_param.isdigit():
            query += " AND m.division_id = ?"
            params.append(int(div_param))
        if season_param and season_param.isdigit():
            query += " AND (m.season_id = ? OR m.season_id IS NULL)"
            params.append(int(season_param))

        query += " ORDER BY m.played_at DESC, m.id DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, tuple(params))
        results = [dict(r) for r in cursor.fetchall()]

    return web.json_response({
        "status": "ok",
        "results": results
    })


async def handle_get_top_scorers(request: web.Request) -> web.Response:
    """
    GET /api/tournaments/{id}/top-scorers?division_id=X
    Returns top goalscorers and assist leaders.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    div_param = request.query.get("division_id")
    div_id = int(div_param) if div_param and div_param.isdigit() else None

    top_scorers = database.get_top_scorers(limit=15, division_id=div_id)
    top_assists = database.get_top_assists(limit=15, division_id=div_id)

    return web.json_response({
        "status": "ok",
        "top_scorers": top_scorers,
        "top_assists": top_assists
    })
