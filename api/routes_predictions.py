"""
api/routes_predictions.py

REST API handlers for bet slip placement and prediction history.
"""

import json
import logging
from aiohttp import web
import database
from api.auth import get_authenticated_user, check_user_access

logger = logging.getLogger(__name__)


async def handle_place_prediction(request: web.Request) -> web.Response:
    """
    POST /api/predictions
    Place a single or express prediction coupon.
    Body:
    {
        "amount": 250,
        "selections": [
            { "match_id": 101, "outcome": "p1", "odd": 2.10 },
            { "match_id": 102, "outcome": "tb25", "odd": 1.75 }
        ]
    }
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

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Некорректный JSON тела запроса."}, status=400)

    amount = int(data.get("amount", 0))
    selections = data.get("selections", [])

    if amount <= 0:
        return web.json_response({"status": "error", "message": "Сумма ставки должна быть больше 0."}, status=400)

    if not selections or not isinstance(selections, list):
        return web.json_response({"status": "error", "message": "Купон не содержит выбранных исходов."}, status=400)

    success, result = database.place_user_bet(user_id, amount, selections)

    if not success:
        return web.json_response({"status": "error", "message": str(result)}, status=400)

    bet_id = result
    user_balance = database.get_wallet_balance(user_id)
    bet_type = "single" if len(selections) == 1 else "express"

    # Gamification hooks
    try:
        database.add_user_xp(user_id, 30 if bet_type == "single" else 60)
        database.evaluate_quest_progress(user_id, "place_bets", 1)
        if bet_type == "express":
            database.evaluate_quest_progress(user_id, "express_count", 1)
        database.evaluate_betting_achievements(user_id)
    except Exception as e:
        logger.warning(f"Error in gamification hook on bet placement: {e}")

    return web.json_response({
        "status": "ok",
        "bet_id": bet_id,
        "bet_type": bet_type,
        "amount": amount,
        "new_balance": user_balance,
        "message": "🎉 Прогноз успешно принят!"
    })


async def handle_get_predictions(request: web.Request) -> web.Response:
    """
    GET /api/predictions
    Retrieve user's bet history.
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

    try:
        database.settle_all_pending_finished_matches()
    except Exception as e:
        logger.warning(f"Error auto-settling in get_predictions: {e}")

    bets = database.get_user_bets(user_id, limit=30)

    return web.json_response({
        "status": "ok",
        "bets": bets
    })
