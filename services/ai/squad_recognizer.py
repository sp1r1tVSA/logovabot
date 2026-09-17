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
    get_ordered_ocr_keys,
    get_ordered_ocr_models,
)

logger = logging.getLogger(__name__)

MAX_SQUAD_PLAYERS = 40

PROMPT_TEXT = """
You are an expert OCR system for football / soccer squad and lineup screens (EA Sports FC, FIFA Mobile, eFootball).
Extract the visible starting lineup and substitutes shown on the screenshot.

Return JSON strictly matching this schema:
{
  "players": [
    {"name": "SURNAME or FULL NAME", "position": "ST"}
  ]
}

Rules:
- Read only names that are visibly rendered. Do NOT guess unreadable names.
- Drop kit numbers, ratings, chemistry values, club badges.
- 'position' must be the 2-4 letter abbreviation printed near the player (e.g. ST, CF, LW, RW, CAM, CM, CDM, LM, RM, CB, LB, RB, LWB, RWB, GK, or Russian equivalents: ВР, ЦЗ, ПЗ, ЛЗ, ЦОП, ЦП, ЦАП, ЛП, ПП, ЛВ, ПВ, НАП, ФРВ). If no position is printed, use null.
- If the image is not a lineup/squad screen, return {"players": []}.
- Return ONLY valid JSON, without markdown formatting or code fences.
"""


def _parse_players(payload: dict) -> list[dict]:
    raw_list = payload.get("players")
    if not isinstance(raw_list, list):
        return []

    result: list[dict] = []
    seen: set[str] = set()
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        raw_name = item.get("name") or ""
        cleaned_name = clean_player_name(str(raw_name))
        if not cleaned_name:
            continue
        norm_key = cleaned_name.lower()
        if norm_key in seen:
            continue
        seen.add(norm_key)

        raw_pos = item.get("position")
        pos_str = str(raw_pos).strip().upper() if raw_pos else None
        if pos_str and len(pos_str) > 6:
            pos_str = None

        result.append({
            "player_name": cleaned_name,
            "position": pos_str,
        })
        if len(result) >= MAX_SQUAD_PLAYERS:
            break
    return result


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
    keys_to_try = get_ordered_ocr_keys(api_key)
    if not keys_to_try:
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

    for m_name in get_ordered_ocr_models():
        for target_api_key in keys_to_try:
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
                key_suffix = f"...{target_api_key[-4:]}" if len(target_api_key) > 4 else "***"
                logger.warning(f"Gemini model '{m_name}' (key {key_suffix}) HTTP {e.code}: {error_body[:300]}")
                if e.code in (429, 403, 503):
                    continue
                continue
            except Exception as e:
                logger.exception(f"Gemini model '{m_name}' squad recognition error: {e}")
                continue

    logger.error("All Gemini models and keys failed for squad recognition.")
    return None
