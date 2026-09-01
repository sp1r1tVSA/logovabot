"""
api/routes_wallet.py

REST API handlers for bootstrap data, user wallet, daily bonus, and leaderboard.
"""

import json
import logging
from aiohttp import web
import database
from api.auth import get_authenticated_user, check_user_access
from handlers.base import is_admin

logger = logging.getLogger(__name__)


async def handle_bootstrap(request: web.Request) -> web.Response:
    """
    GET /api/bootstrap
    Returns current user info, balance, active tours count, and bonus cooldown.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)

    if not user_info or "id" not in user_info:
        return web.json_response(
            {"status": "error", "error": "unauthorized", "message": "Недействительные данные авторизации Telegram."},
            status=401
        )

    user_id = user_info["id"]
    is_adm = is_admin(user_id)
    has_access = check_user_access(user_id)

    # Auto-settle any finished matches in background
    try:
        database.settle_all_pending_finished_matches()
    except Exception as e:
        logger.warning(f"Error in auto-settlement: {e}")

    # Fetch wallet
    wallet = database.get_or_create_wallet(user_id)

    # Check bonus availability
    can_claim = True
    cooldown_sec = 0
    last_bonus = wallet.get("last_bonus_at")
    if last_bonus:
        import datetime
        try:
            last_dt = datetime.datetime.fromisoformat(last_bonus)
            elapsed = (datetime.datetime.now() - last_dt).total_seconds()
            if elapsed < 86400:
                can_claim = False
                cooldown_sec = int(86400 - elapsed)
        except Exception:
            pass

    # Fetch open tours summary
    open_tours = database.get_open_betting_tours()

    return web.json_response({
        "status": "ok",
        "user": {
            "user_id": user_id,
            "first_name": user_info.get("first_name", "Игрок"),
            "username": user_info.get("username", ""),
            "balance": wallet.get("balance", 1000),
            "total_wagered": wallet.get("total_wagered", 0),
            "total_won": wallet.get("total_won", 0),
            "bets_count": wallet.get("bets_count", 0),
            "bets_won": wallet.get("bets_won", 0),
            "is_admin": is_adm,
            "has_access": has_access
        },
        "bonus": {
            "can_claim": can_claim,
            "cooldown_seconds": cooldown_sec,
            "reward_amount": 250
        },
        "open_tours_count": len(open_tours)
    })


async def handle_claim_bonus(request: web.Request) -> web.Response:
    """
    POST /api/bonus/claim
    Claim daily +250 coins bonus.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)

    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]
    if not check_user_access(user_id):
        return web.json_response(
            {"status": "error", "error": "lab_mode", "message": "Logovo.bet находится на закрытом тесте в Лаборатории."},
            status=403
        )

    success, val, msg = database.claim_daily_bonus(user_id, 250)
    if not success:
        return web.json_response({
            "status": "error",
            "error": "cooldown",
            "message": msg,
            "remaining_hours": val
        }, status=400)

    return web.json_response({
        "status": "ok",
        "message": msg,
        "new_balance": val,
        "claimed_amount": 250
    })


async def handle_leaderboard(request: web.Request) -> web.Response:
    """
    GET /api/leaderboard
    Get top bettors ranking by coin balance.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    user_id = user_info.get("id") if user_info else 0
    if not check_user_access(user_id):
        return web.json_response({
            "status": "error",
            "error": "lab_mode",
            "message": "Logovo.bet находится на закрытом тесте в Лаборатории."
        }, status=403)

    leaders = database.get_top_bettors(20)

    my_rank = None
    if user_info and "id" in user_info:
        user_id = user_info["id"]
        for idx, item in enumerate(leaders, 1):
            if item["user_id"] == user_id:
                my_rank = {
                    "rank": idx,
                    "balance": item["balance"],
                    "bets_won": item["bets_won"],
                    "bets_count": item["bets_count"]
                }
                break

    return web.json_response({
        "status": "ok",
        "leaders": leaders,
        "my_rank": my_rank
    })
