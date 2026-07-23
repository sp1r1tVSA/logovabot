import os
import base64
import json
import logging
import urllib.request
import urllib.error
from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

def recognize_match_screenshot_bytes(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict | None:
    """
    Sends match screenshot image bytes to Google Gemini API (gemini-flash-latest).
    Returns structured dict with match scores, goals, and assists.
    """
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY is not set.")
        return None

    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={GEMINI_API_KEY}"

    prompt_text = (
        "Ты — эксперт по распознаванию футбольных матчей из симуляторов FIFA / EA FC / eFootball.\n"
        "Внимательно изучи прикрепленный скриншот результатов/статистики сыгранного матча.\n"
        "Считай счет матча, названия команд (если видна информация), а также авторов голов и авторов ассистов (голевых передач).\n"
        "Верни ответ СТРОГО в виде одного валидного JSON объекта без разметки markdown со следующими полями:\n"
        "{\n"
        '  "home_team": "Название левой (домашней) команды или null",\n'
        '  "away_team": "Название правой (гостевой) команды или null",\n'
        '  "home_score": 4,\n'
        '  "away_score": 2,\n'
        '  "home_goals": ["Player1", "Player2"],\n'
        '  "away_goals": ["Player3"],\n'
        '  "home_assists": ["Player4"],\n'
        '  "away_assists": []\n'
        "}\n"
        "Если ассисты не указаны на экране, верни пустой список []."
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt_text},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": b64_image
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.1
        }
    }

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=req_data,
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            res_body = response.read().decode("utf-8")
            res_json = json.loads(res_body)
            
            candidates = res_json.get("candidates", [])
            if candidates:
                text_content = candidates[0]["content"]["parts"][0]["text"]
                parsed_data = json.loads(text_content)
                logger.info(f"AI Vision recognized match: {parsed_data.get('home_score')} - {parsed_data.get('away_score')}")
                return parsed_data
            else:
                logger.warning(f"Gemini API returned no candidates: {res_json}")
                return None
    except Exception as e:
        logger.error(f"Gemini Vision recognition failed: {e}")
        return None
