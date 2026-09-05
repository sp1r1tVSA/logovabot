"""
api/routes_markets.py

REST API handlers for open tours, matches schedule, and relational odds lines.
"""

import logging
from aiohttp import web
import database
from api.auth import get_authenticated_user, check_user_access
from services.betting_engine import generate_round_markets
import services.odds_engine as odds_engine

logger = logging.getLogger(__name__)


async def handle_get_tours(request: web.Request) -> web.Response:
    """
    GET /api/markets/tours
    Returns all open tours with their matches and active odds.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)

    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]
    if not check_user_access(user_id):
        return web.json_response({
            "status": "error",
            "error": "lab_mode",
            "message": "Logovo.bet находится на закрытом тесте в Лаборатории."
        }, status=403)

    division_id_param = request.query.get("division_id")
    div_id = int(division_id_param) if division_id_param and division_id_param.isdigit() else None

    open_tours = database.get_open_betting_tours(division_id=div_id)
    results = []

    for t in open_tours:
        r_num = t["round_number"]
        # Ensure markets are generated
        generate_round_markets(r_num, division_id=div_id)
        markets = database.get_active_bet_markets(r_num, division_id=div_id)

        matches_list = []
        for m in markets:
            # Also ensure relational markets are populated for each match
            m_id = m["match_id"]
            t1 = m["team1_name"]
            t2 = m["team2_name"]
            try:
                odds_engine.generate_match_markets(m_id, t1, t2)
            except Exception as e:
                logger.debug(f"Could not generate relational markets for match #{m_id}: {e}")

            matches_list.append({
                "match_id": m["match_id"],
                "tour": m["tour"],
                "team1_name": m["team1_name"],
                "team2_name": m["team2_name"],
                "odds": {
                    "p1": round(m["odd_p1"], 2),
                    "x": round(m["odd_x"], 2),
                    "p2": round(m["odd_p2"], 2),
                    "tb25": round(m["odd_tb25"], 2),
                    "tm25": round(m["odd_tm25"], 2),
                    "btts_yes": round(m["odd_btts_yes"], 2),
                    "btts_no": round(m["odd_btts_no"], 2)
                }
            })

        results.append({
            "round_number": r_num,
            "deadline": t.get("deadline"),
            "total_matches": t.get("total_matches", len(matches_list)),
            "unplayed_matches": t.get("unplayed_matches", len(matches_list)),
            "matches": matches_list
        })

    return web.json_response({
        "status": "ok",
        "tours": results
    })


async def handle_get_match_markets(request: web.Request) -> web.Response:
    """
    GET /api/matches/{id}/markets
    Returns full categorized market tree with all selections and odds.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)

    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]
    if not check_user_access(user_id):
        return web.json_response({"status": "error", "error": "lab_mode"}, status=403)

    try:
        match_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Некорректный ID матча."}, status=400)

    # Get match info
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
        match_row = cursor.fetchone()

    if not match_row:
        return web.json_response({"status": "error", "message": "Матч не найден."}, status=404)

    t1 = match_row["player1_team"] or "Команда 1"
    t2 = match_row["player2_team"] or "Команда 2"

    markets = odds_engine.get_match_markets(match_id)
    if not markets:
        # Generate on the fly if not existing
        markets = odds_engine.generate_match_markets(match_id, t1, t2)

    # Normalize field name: alias odds_value -> current_odd for frontend consistency
    for mkt in markets:
        for sel in mkt.get("selections", []):
            if "current_odd" not in sel:
                sel["current_odd"] = sel.get("odds_value", 1.90)

    return web.json_response({
        "status": "ok",
        "match_id": match_id,
        "team1_name": t1,
        "team2_name": t2,
        "match_status": match_row["status"],
        "markets": markets
    })


async def handle_get_odds_history(request: web.Request) -> web.Response:
    """
    GET /api/markets/{id}/odds-history?selection_key=p1
    Returns chronological timeline of odds changes for a market selection.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)

    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    try:
        market_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Некорректный ID рынка."}, status=400)

    selection_key = request.query.get("selection_key")
    history = odds_engine.get_odds_history(market_id=market_id, selection_key=selection_key, limit=30)

    return web.json_response({
        "status": "ok",
        "market_id": market_id,
        "selection_key": selection_key,
        "history": history
    })
