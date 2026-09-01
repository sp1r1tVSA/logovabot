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
from api.routes_markets import handle_get_tours
from api.routes_predictions import handle_place_prediction, handle_get_predictions
from api.routes_gamification import (
    handle_get_progression,
    handle_claim_quest,
    handle_get_achievements,
    handle_claim_achievement,
    handle_get_duels,
    handle_create_duel,
    handle_accept_duel,
    handle_get_profile
)

logger = logging.getLogger(__name__)

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")


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
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
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

    # REST API Endpoints
    app.router.add_get("/api/bootstrap", handle_bootstrap)
    app.router.add_post("/api/bonus/claim", handle_claim_bonus)
    app.router.add_get("/api/leaderboard", handle_leaderboard)
    app.router.add_get("/api/markets/tours", handle_get_tours)
    app.router.add_post("/api/predictions", handle_place_prediction)
    app.router.add_get("/api/predictions", handle_get_predictions)

    # Gamification, Quests, Achievements & Duels
    app.router.add_get("/api/progression", handle_get_progression)
    app.router.add_post("/api/quests/claim", handle_claim_quest)
    app.router.add_get("/api/achievements", handle_get_achievements)
    app.router.add_post("/api/achievements/claim", handle_claim_achievement)
    app.router.add_get("/api/duels", handle_get_duels)
    app.router.add_post("/api/duels/create", handle_create_duel)
    app.router.add_post("/api/duels/accept", handle_accept_duel)
    app.router.add_get("/api/profile/{user_id}", handle_get_profile)

    # Static SPA Frontend
    app.router.add_get("/", handle_index)
    app.router.add_get("/app", handle_index)
    if os.path.exists(WEB_DIR):
        app.router.add_static("/static/", WEB_DIR, show_index=True)
        app.router.add_static("/css/", os.path.join(WEB_DIR, "css"))
        app.router.add_static("/js/", os.path.join(WEB_DIR, "js"))
        if os.path.exists(os.path.join(WEB_DIR, "assets")):
            app.router.add_static("/assets/", os.path.join(WEB_DIR, "assets"))

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
