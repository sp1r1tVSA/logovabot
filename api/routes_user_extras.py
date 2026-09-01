"""
api/routes_user_extras.py

Logovo.bet — User Analytics, Saved Coupons, Favorites & Notifications API.
"""

import json
import logging
from aiohttp import web
import database
from api.auth import get_authenticated_user

logger = logging.getLogger(__name__)


async def handle_get_my_stats(request: web.Request) -> web.Response:
    """
    GET /api/stats/me
    Calculates detailed betting statistics: ROI %, Win Rate %, Average Odds, Favorite Market.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]

    with database.transaction() as conn:
        cursor = conn.cursor()
        wallet = database.get_or_create_wallet(user_id)
        prog = database.get_or_create_progression(user_id)

        wagered = wallet["total_wagered"]
        won = wallet["total_won"]
        bets_count = wallet["bets_count"]
        bets_won = wallet["bets_won"]

        win_rate = round((bets_won / max(1, bets_count)) * 100, 1)
        net_profit = won - wagered
        roi = round((net_profit / max(1, wagered)) * 100, 1) if wagered > 0 else 0.0

        # Average Odds
        cursor.execute("SELECT AVG(total_odd) as avg_odd, MAX(potential_win) as max_win FROM user_bets WHERE user_id = ?", (user_id,))
        stats_row = cursor.fetchone()
        avg_odd = round(float(stats_row["avg_odd"] or 1.0), 2)
        best_win = int(stats_row["max_win"] or 0)

        # Favorite Market Pick
        cursor.execute("""
            SELECT outcome_type, COUNT(*) as cnt
            FROM bet_items bi
            JOIN user_bets ub ON bi.bet_id = ub.id
            WHERE ub.user_id = ?
            GROUP BY outcome_type
            ORDER BY cnt DESC
            LIMIT 1
        """, (user_id,))
        fav_row = cursor.fetchone()
        favorite_market = fav_row["outcome_type"].upper() if fav_row else "П1"

    return web.json_response({
        "status": "ok",
        "stats": {
            "balance": wallet["balance"],
            "total_wagered": wagered,
            "total_won": won,
            "net_profit": net_profit,
            "roi_pct": roi,
            "win_rate_pct": win_rate,
            "total_predictions": bets_count,
            "won_predictions": bets_won,
            "average_odds": avg_odd,
            "best_win": best_win,
            "current_streak": prog["current_streak"],
            "best_streak": prog["best_streak"],
            "favorite_market": favorite_market,
            "level": prog["level"],
            "xp": prog["current_xp"]
        }
    })


async def handle_save_coupon(request: web.Request) -> web.Response:
    """
    POST /api/saved-coupons
    Save a draft coupon for later.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]
    try:
        data = await request.json()
        name = str(data.get("name", "Мой купон")).strip()
        selections = list(data.get("selections", []))
        total_odd = float(data.get("total_odd", 1.0))
    except Exception:
        return web.json_response({"status": "error", "message": "Некорректные данные купона."}, status=400)

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO saved_coupons (user_id, name, selections_json, total_odd, status)
            VALUES (?, ?, ?, ?, 'active')
        """, (user_id, name, json.dumps(selections), total_odd))
        saved_id = cursor.lastrowid

    return web.json_response({
        "status": "ok",
        "saved_id": saved_id,
        "message": "💾 Купон сохранен!"
    })


async def handle_get_saved_coupons(request: web.Request) -> web.Response:
    """
    GET /api/saved-coupons
    List user's saved draft coupons.
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM saved_coupons WHERE user_id = ? ORDER BY id DESC", (user_id,))
        rows = [dict(r) for r in cursor.fetchall()]
        for r in rows:
            try:
                r["selections"] = json.loads(r["selections_json"])
            except Exception:
                r["selections"] = []

    return web.json_response({
        "status": "ok",
        "saved_coupons": rows
    })


async def handle_delete_saved_coupon(request: web.Request) -> web.Response:
    """
    DELETE /api/saved-coupons/{id}
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]
    try:
        c_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Некорректный ID."}, status=400)

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM saved_coupons WHERE id = ? AND user_id = ?", (c_id, user_id))

    return web.json_response({"status": "ok", "message": "Купон удален."})


async def handle_add_favorite(request: web.Request) -> web.Response:
    """
    POST /api/favorites
    Body: {"target_type": "match", "target_id": 12}
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]
    try:
        data = await request.json()
        target_type = str(data.get("target_type", "match"))
        target_id = int(data.get("target_id", 0))
    except Exception:
        return web.json_response({"status": "error", "message": "Некорректные параметры."}, status=400)

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO favorites (user_id, target_type, target_id)
            VALUES (?, ?, ?)
        """, (user_id, target_type, target_id))

    return web.json_response({"status": "ok", "message": "⭐ Добавлено в избранное!"})


async def handle_get_favorites(request: web.Request) -> web.Response:
    """
    GET /api/favorites
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM favorites WHERE user_id = ? ORDER BY id DESC", (user_id,))
        favorites = [dict(r) for r in cursor.fetchall()]

    return web.json_response({"status": "ok", "favorites": favorites})


async def handle_delete_favorite(request: web.Request) -> web.Response:
    """
    DELETE /api/favorites/{id}
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]
    try:
        f_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"status": "error", "message": "Некорректный ID."}, status=400)

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM favorites WHERE id = ? AND user_id = ?", (f_id, user_id))

    return web.json_response({"status": "ok", "message": "Удалено из избранного."})


async def handle_get_notifications(request: web.Request) -> web.Response:
    """
    GET /api/notifications
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 30", (user_id,))
        notifications = [dict(r) for r in cursor.fetchall()]

    return web.json_response({"status": "ok", "notifications": notifications})


async def handle_mark_notifications_read(request: web.Request) -> web.Response:
    """
    POST /api/notifications/read
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_info = get_authenticated_user(init_data)
    if not user_info or "id" not in user_info:
        return web.json_response({"status": "error", "error": "unauthorized"}, status=401)

    user_id = user_info["id"]

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user_id,))

    return web.json_response({"status": "ok", "message": "Уведомления прочитаны."})
