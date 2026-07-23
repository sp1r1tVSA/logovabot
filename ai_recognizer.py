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
        "Внимательно изучи прикрепленный скриншот (или 2 скриншота) результатов и статистики сыгранного матча.\n\n"
        "Скриншот может быть двух типов:\n\n"
        "=== ТИП 1 (Стандартная статистика с двумя колонками) ===\n"
        "Таблица ЗЕРКАЛЬНАЯ: левая половина — игроки ЛЕВОЙ (домашней) команды, правая — ПРАВОЙ (гостевой).\n"
        "КРИТИЧЕСКИ ВАЖНО для Типа 1:\n"
        "- Каждая строка таблицы содержит ДВУХ игроков: левый игрок (левая команда) и правый игрок (правая команда).\n"
        "- Колонки в центре таблицы зеркальные: для левой команды порядок [Г, А], для правой команды порядок [А, Г] (зеркально).\n"
        "- НИКОГДА не путай: цифры голов и ассистов из строки принадлежат СВОЕМУ игроку (левые цифры — левому игроку, правые цифры — правому игроку).\n"
        "- Если в строке стоят: 'Addai | 0 | 0 || 1 | 0 | Valera' — это означает: Addai (левая команда) имеет 0 голов, 0 ассистов; Valera (правая команда) имеет 1 ассист, 0 голов.\n"
        "- Ассист '1' в правой части строки принадлежит ПРАВОМУ игроку (Valera), а НЕ левому (Addai).\n\n"
        "=== ТИП 2 (Единая вертикальная колонка таймлайна) ===\n"
        "Сбоку — одна общая вертикальная колонка с хронологическим списком всех голов матча (например, 15' GOAL...). В этом типе ассисты не отображаются.\n\n"
        "Верни ответ СТРОГО в виде одного валидного JSON объекта без разметки markdown:\n"
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
        "Правила:\n"
        "- side1 = левая (домашняя) команда, side2 = правая (гостевая) команда.\n"
        "- is_single_timeline: true если Тип 2 (таймлайн), иначе false.\n"
        "- Голы и ассисты записывай списком имён (с повторением если забил/ассистировал дважды).\n"
        "- Если ассисты не отображаются (Тип 2 или нет данных) — верни пустые списки [].\n"
        "- Строго следи за принадлежностью цифр: левые цифры = левому игроку, правые = правому."
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
        "gemini-3.1-flash-lite",   # 500 RPD, 15 RPM — приоритет
        "gemini-3.5-flash-lite",   # 500 RPD, 15 RPM — фоллбек
        "gemini-2.5-flash-lite",   # 20 RPD
        "gemini-2.5-flash",        # 20 RPD
        "gemini-2.0-flash-lite",   # 429, но работает
        "gemini-2.0-flash",        # 429, последний фоллбек
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
