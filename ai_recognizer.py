import os
import base64
import json
import logging
import urllib.request
import urllib.error
import config

logger = logging.getLogger(__name__)

def recognize_match_screenshots_bytes(images_bytes_list: list[bytes], mime_type: str = "image/jpeg") -> dict | None:
    """
    Sends 1 or 2 match screenshot image bytes to Google Gemini API (default: gemini-1.5-flash-lite).
    Supports both 2-column match stats screenshots and single vertical timeline goal list screenshots.
    Returns structured dict with match scores, goals, assists, and is_single_timeline flag.
    """
    api_key = getattr(config, "GEMINI_API_KEY", "")
    model_setting = getattr(config, "GEMINI_MODEL", "gemini-1.5-flash-lite")

    if not api_key:
        logger.warning("GEMINI_API_KEY is not set.")
        return None

    if not images_bytes_list:
        return None

    prompt_text = (
        "Ты — эксперт по распознаванию футбольных матчей из симуляторов (FIFA / EA FC / eFootball).\n"
        "Внимательно изучи прикрепленный скриншот (или 2 скриншота) результатов и статистики сыгранного матча.\n"
        "Обрати внимание, скриншот может быть двух типов:\n"
        "Тип 1 (Стандартная статистика): 2 отдельные колонки статистики слева и справа с голами и ассистами.\n"
        "Тип 2 (Единая вертикальная колонка таймлайна): сбоку отображается одна общая вертикальная колонка с хронологическим списком всех голов матча (например, 15' GOAL, 24' GOAL, 32' GOAL...). В этом типе ассисты не отображаются.\n\n"
        "Верни ответ СТРОГО в виде одного валидного JSON объекта без разметки markdown со следующими полями:\n"
        "{\n"
        '  "home_team": "Название левой (домашней) команды или null",\n'
        '  "away_team": "Название правой (гостевой) команды или null",\n'
        '  "home_score": 4,\n'
        '  "away_score": 2,\n'
        '  "is_single_timeline": false,\n'
        '  "side1_goals": ["Player1", "Player2"],\n'
        '  "side2_goals": ["Player3"],\n'
        '  "side1_assists": ["Player4"],\n'
        '  "side2_assists": []\n'
        "}\n"
        "Где:\n"
        "- is_single_timeline: true если голы выведены в одну общую вертикальную колонку таймлайна (Тип 2), иначе false.\n"
        "- side1_goals и side2_goals: списки распознанных авторов голов.\n"
        "- Если ассисты не указаны на экране (или Тип 2), верни пустые списки []."
    )

    parts = [{"text": prompt_text}]

    for img_bytes in images_bytes_list[:2]:
        b64_image = base64.b64encode(img_bytes).decode("utf-8")
        parts.append({
            "inline_data": {
                "mime_type": mime_type,
                "data": b64_image
            }
        })

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.1
        }
    }

    req_data = json.dumps(payload).encode("utf-8")

    candidate_models = list(dict.fromkeys(filter(None, [
        model_setting,
        "gemini-2.0-flash-lite",   # 429 значит модель есть, ждём паузы
        "gemini-2.5-flash-lite",   # 500 RPD — приоритетная
        "gemini-1.5-flash-8b",     # правильное API-имя lite 1.5
        "gemini-2.5-flash",        # 20 RPD
        "gemini-2.0-flash",        # фоллбек
    ])))

    import time
    for m_name in candidate_models:
        if not m_name:
            continue
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_name}:generateContent?key={api_key}"
        
        req = urllib.request.Request(
            url,
            data=req_data,
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                res_body = response.read().decode("utf-8")
                res_json = json.loads(res_body)
                
                candidates = res_json.get("candidates", [])
                if candidates:
                    text_content = candidates[0]["content"]["parts"][0]["text"]
                    parsed_data = json.loads(text_content)
                    logger.info(f"AI Vision ({m_name}) recognized match: {parsed_data.get('home_score')} - {parsed_data.get('away_score')} (is_single_timeline={parsed_data.get('is_single_timeline')})")
                    return parsed_data
                else:
                    logger.warning(f"Gemini model '{m_name}' returned no candidates: {res_json}")
                    continue
        except urllib.error.HTTPError as e:
            if e.code == 429:
                logger.warning(f"Gemini model '{m_name}' rate-limited (429). Trying next fallback model...")
                time.sleep(1)
            elif e.code == 404:
                logger.warning(f"Gemini model '{m_name}' not found (404). Trying next fallback model...")
            else:
                logger.error(f"Gemini model '{m_name}' HTTP Error {e.code}: {e}")
            continue
        except Exception as e:
            logger.error(f"Gemini model '{m_name}' recognition error: {e}")
            continue

    logger.error("All Gemini Vision fallback models failed or were rate-limited.")
    return None

def recognize_match_screenshot_bytes(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict | None:
    """Wrapper for backward compatibility."""
    return recognize_match_screenshots_bytes([image_bytes], mime_type=mime_type)
