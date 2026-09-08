"""
Perceptual OCR for squad / lineup screenshots (EA FC, FIFA Mobile, eFootball).

The model only reports what is visibly printed on the screenshot — player names
and the position label rendered next to each of them. Matching those names
against an existing roster, deduplication and enrichment happen deterministically
in Python/SQLite afterwards, never inside the prompt.
"""

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.request

import config
from services.ai.ai_recognizer import (
    GEMINI_MODELS,
    _get_gemini_opener,
    clean_json_response,
    clean_player_name,
)

logger = logging.getLogger(__name__)

MAX_SQUAD_PLAYERS = 40

PROMPT_TEXT = """
Ты — узкоспециализированный OCR-сканер скриншотов состава команды из FIFA / EA FC Mobile / eFootball.

Твоя единственная задача — БУКВАЛЬНО считать со скриншота фамилии/имена футболистов и подписанную рядом позицию.

ПРАВИЛА:
1. Считывай ТОЛЬКО то, что реально напечатано на изображении. Ничего не додумывай, не дополняй состав «известными» игроками клуба и не исправляй фамилии на «правильные».
2. Если игрок на скриншоте есть, но его позиция не подписана — верни "position": null. НЕ угадывай позицию.
3. Считывай всех видимых футболистов: стартовый состав, запас и резерв.
4. Не включай тренеров, названия клубов, названия лиг, кнопки интерфейса, рейтинги, химию, цену и номера игроков в поле "name".
5. Позиция — ровно тот код, что напечатан на экране (ВР, ЦЗ, ЛЗ, ПЗ, ЦОП, ЦП, ЦАП, ЛП, ПП, ЛВ, ПВ, ФРД, НАП, GK, CB, LB, RB, CDM, CM, CAM, LM, RM, LW, RW, CF, ST).
6. Если один и тот же футболист виден дважды — верни его один раз.
7. Если на изображении нет состава футбольной команды — верни {"players": []}.

ФОРМАТ ОТВЕТА — строго JSON, без markdown и без комментариев:
{
  "players": [
    {"name": "Viktor Gyökeres", "position": "ST"},
    {"name": "Francisco Trincão", "position": null}
  ]
}
"""


def _parse_players(parsed_data: dict) -> list[dict]:
    raw = parsed_data.get("players")
    if not isinstance(raw, list):
        return []

    players: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            raw_name = item.get("name") or item.get("player_name") or ""
            raw_pos = item.get("position") or item.get("pos")
        else:
            raw_name, raw_pos = str(item), None

        name = clean_player_name(raw_name)
        if not name or len(name) > 50 or not re.search(r"[^\W\d_]", name):
            continue

        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)

        pos = str(raw_pos).strip() if raw_pos else None
        players.append({"player_name": name, "position": pos or None})

        if len(players) >= MAX_SQUAD_PLAYERS:
            break

    return players


def recognize_squad_screenshot_bytes(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    api_key: str | None = None,
) -> list[dict] | None:
    """
    Read player names and printed positions off a squad screenshot.

    Returns `[{"player_name": str, "position": str | None}, ...]`, an empty list
    when the image holds no readable squad, or None when every model failed.
    """
    target_api_key = (api_key or config.GEMINI_API_KEY).strip()
    if not target_api_key:
        logger.error("GEMINI_API_KEY is empty or not set!")
        return None
    if not image_bytes:
        return None

    opener = _get_gemini_opener()
    base_url = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com").rstrip("/")

    payload = {
        "contents": [{
            "parts": [
                {"text": PROMPT_TEXT},
                {"inline_data": {
                    "mime_type": mime_type,
                    "data": base64.b64encode(image_bytes).decode("utf-8"),
                }},
            ]
        }],
        "generationConfig": {"temperature": 0.0},
    }
    body = json.dumps(payload).encode("utf-8")

    for m_name in GEMINI_MODELS:
        try:
            req = urllib.request.Request(
                f"{base_url}/v1beta/models/{m_name}:generateContent?key={target_api_key}",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                },
            )
            with opener.open(req, timeout=30) as response:
                res_json = json.loads(response.read().decode("utf-8"))

            candidates = res_json.get("candidates", [])
            if not candidates or "content" not in candidates[0]:
                logger.warning(f"Gemini model '{m_name}' returned no candidates for squad OCR")
                continue

            text_content = candidates[0]["content"]["parts"][0]["text"]
            parsed_data = json.loads(clean_json_response(text_content))
            if not isinstance(parsed_data, dict):
                logger.warning(f"Gemini model '{m_name}' returned non-dict JSON for squad OCR")
                continue

            players = _parse_players(parsed_data)
            logger.info(f"Squad OCR ({m_name}) recognized {len(players)} player(s)")
            return players

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="ignore")
            logger.warning(f"Gemini model '{m_name}' HTTP {e.code}: {error_body[:300]}")
            continue
        except Exception as e:
            logger.exception(f"Gemini model '{m_name}' squad recognition error: {e}")
            continue

    logger.error("All Gemini models failed for squad recognition.")
    return None
