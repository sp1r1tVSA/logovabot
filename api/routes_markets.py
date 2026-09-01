"""
api/routes_markets.py

REST API handlers for open tours, matches schedule, and odds lines.
"""

import logging
from aiohttp import web
import database
from api.auth import get_authenticated_user, check_user_access
from services.betting_engine import generate_round_markets

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

    open_tours = database.get_open_betting_tours()
    results = []

    for t in open_tours:
        r_num = t["round_number"]
        # Ensure markets are generated
        generate_round_markets(r_num)
        markets = database.get_active_bet_markets(r_num)

        matches_list = []
        for m in markets:
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
