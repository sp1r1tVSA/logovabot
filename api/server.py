"""
api/server.py

Embedded async HTTP server (aiohttp.web) for Logovo.bet Telegram Mini App.
Serves REST API and static single-page application assets.
"""

import os
import asyncio
import logging
from aiohttp import web

from api.routes_wallet import handle_bootstrap, handle_claim_bonus, handle_leaderboard
from api.routes_markets import handle_get_tours, handle_get_match_markets, handle_get_odds_history
from api.routes_predictions import (
    handle_place_prediction,
    handle_get_predictions,
    handle_get_prediction_detail,
    handle_repeat_prediction
)
from api.routes_matches import (
    handle_get_matches,
    handle_get_match_detail,
    handle_get_match_stats,
    handle_get_match_h2h,
    handle_get_match_insights,
    handle_get_match_live
)
from api.routes_tournaments import (
    handle_get_tournaments,
    handle_get_standings,
    handle_get_results,
    handle_get_top_scorers
)
from api.routes_user_extras import (
    handle_get_my_stats,
    handle_save_coupon,
    handle_get_saved_coupons,
    handle_delete_saved_coupon,
    handle_add_favorite,
    handle_get_favorites,
    handle_delete_favorite,
    handle_get_notifications,
    handle_mark_notifications_read
)
from api.routes_gamification import (
    handle_get_progression,
    handle_get_achievements,
    handle_claim_achievement,
    handle_get_profile
)

logger = logging.getLogger(__name__)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT_DIR, "web")
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")


@web.middleware
async def cors_middleware(request: web.Request, handler):
    """Enable CORS headers for Telegram Mini App webview clients."""
    if request.method == "OPTIONS":
        response = web.Response(status=200)
    else:
        try:
            response = await handler(request)
        except web.HTTPException as ex:
            response = ex

    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Telegram-Init-Data, Authorization"
    response.headers["Access-Control-Max-Age"] = "86400"
    return response


async def handle_index(request: web.Request) -> web.FileResponse:
    """Serve SPA index.html."""
    index_path = os.path.join(WEB_DIR, "index.html")
    return web.FileResponse(index_path)


def create_app() -> web.Application:
    """Construct and configure the aiohttp Application."""
    app = web.Application(middlewares=[cors_middleware])

    # 1. Wallet & Bootstrap
    app.router.add_get("/api/bootstrap", handle_bootstrap)
    app.router.add_post("/api/bonus/claim", handle_claim_bonus)
    app.router.add_get("/api/leaderboard", handle_leaderboard)

    # 2. Markets & Odds
    app.router.add_get("/api/markets/tours", handle_get_tours)
    app.router.add_get("/api/matches/{id}/markets", handle_get_match_markets)
    app.router.add_get("/api/markets/{id}/odds-history", handle_get_odds_history)

    # 3. Match Center 3.0
    app.router.add_get("/api/matches", handle_get_matches)
    app.router.add_get("/api/matches/{id}", handle_get_match_detail)
    app.router.add_get("/api/matches/{id}/stats", handle_get_match_stats)
    app.router.add_get("/api/matches/{id}/h2h", handle_get_match_h2h)
    app.router.add_get("/api/matches/{id}/insights", handle_get_match_insights)
    app.router.add_get("/api/matches/{id}/live", handle_get_match_live)

    # 4. Predictions & Coupon Engine
    app.router.add_post("/api/predictions", handle_place_prediction)
    app.router.add_get("/api/predictions", handle_get_predictions)
    app.router.add_get("/api/predictions/{id}", handle_get_prediction_detail)
    app.router.add_post("/api/predictions/{id}/repeat", handle_repeat_prediction)

    # 5. Tournament Hub
    app.router.add_get("/api/tournaments", handle_get_tournaments)
    app.router.add_get("/api/tournaments/{id}/standings", handle_get_standings)
    app.router.add_get("/api/tournaments/{id}/results", handle_get_results)
    app.router.add_get("/api/tournaments/{id}/top-scorers", handle_get_top_scorers)

    # 6. User Stats, Saved Coupons, Favorites & Notifications
    app.router.add_get("/api/stats/me", handle_get_my_stats)
    app.router.add_post("/api/saved-coupons", handle_save_coupon)
    app.router.add_get("/api/saved-coupons", handle_get_saved_coupons)
    app.router.add_delete("/api/saved-coupons/{id}", handle_delete_saved_coupon)
    app.router.add_post("/api/favorites", handle_add_favorite)
    app.router.add_get("/api/favorites", handle_get_favorites)
    app.router.add_delete("/api/favorites/{id}", handle_delete_favorite)
    app.router.add_get("/api/notifications", handle_get_notifications)
    app.router.add_post("/api/notifications/read", handle_mark_notifications_read)

    # 7. Secondary Gamification (Progression, Achievements & Profile)
    app.router.add_get("/api/progression", handle_get_progression)
    app.router.add_get("/api/achievements", handle_get_achievements)
    app.router.add_post("/api/achievements/claim", handle_claim_achievement)
    app.router.add_get("/api/profile/{user_id}", handle_get_profile)

    # Static SPA Frontend & Assets
    app.router.add_get("/", handle_index)
    app.router.add_get("/app", handle_index)
    if os.path.exists(WEB_DIR):
        app.router.add_static("/static/", WEB_DIR, show_index=True)
        app.router.add_static("/css/", os.path.join(WEB_DIR, "css"))
        app.router.add_static("/js/", os.path.join(WEB_DIR, "js"))
    if os.path.exists(ASSETS_DIR):
        app.router.add_static("/assets/", ASSETS_DIR, show_index=True)

    return app


async def start_api_server_background(host: str = "0.0.0.0", port: int = 8080) -> web.AppRunner:
    """Start embedded API server in background within current asyncio loop."""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"🎰 Logovo.bet Mini App Server running at http://{host}:{port}")
    return runner


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    web_app = create_app()
    web.run_app(web_app, host="0.0.0.0", port=8080)
