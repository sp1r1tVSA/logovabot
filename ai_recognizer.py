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
        "В верхней части скриншота отображается счёт матча в формате: [Команда A] [счёт A] - [счёт B] [Команда B].\n"
        "Команда A — это команда, показанная СЛЕВА в счёте; Команда B — СПРАВА в счёте.\n"
        "Таблица игроков тоже разделена на две половины:\n"
        "- Левая половина таблицы = игроки команды A (левой в счёте)\n"
        "- Правая половина таблицы = игроки команды B (правой в счёте)\n\n"
        "КРИТИЧЕСКИ ВАЖНО для Типа 1:\n"
        "- Каждая строка таблицы содержит ДВУХ игроков: левый игрок (команда A) и правый игрок (команда B).\n"
        "- Колонки статистики зеркальные: для левых игроков порядок [..., Г, А], для правых — [А, Г, ...] (зеркально от центра).\n"
        "- СТРОГО следи: цифры принадлежат СВОЕМУ игроку в строке. Левые цифры — левому игроку, правые — правому.\n"
        "- Пример: строка 'Addai | 0 | 0 || 1 | 0 | Valera' означает: Addai имеет 0 голов, 0 ассистов; Valera имеет 1 ассист, 0 голов.\n"
        "- Цифра '1' в правой части строки принадлежит ПРАВОМУ игроку (Valera), а НЕ левому (Addai).\n\n"
        "=== ТИП 2 (Единая вертикальная колонка таймлайна) ===\n"
        "Сбоку — одна вертикальная колонка с хронологическим списком голов (например, 15' GOAL...). Ассисты не отображаются.\n\n"
        "Верни ответ СТРОГО в виде одного валидного JSON объекта без разметки markdown:\n"
        "{\n"
        '  "home_team": "Название команды A (левой в счёте) или null",\n'
        '  "away_team": "Название команды B (правой в счёте) или null",\n'
        '  "home_score": 3,\n'
        '  "away_score": 2,\n'
        '  "is_single_timeline": false,\n'
        '  "side1_goals": ["Player1", "Player2"],\n'
        '  "side2_goals": ["Player3"],\n'
        '  "side1_assists": ["Player4"],\n'
        '  "side2_assists": []\n'
        "}\n"
        "Правила:\n"
        "- home_team и home_score = команда A (левая в счёте), away_team и away_score = команда B (правая в счёте).\n"
        "- side1 = игроки ЛЕВОЙ колонки таблицы (команда A), side2 = игроки ПРАВОЙ колонки (команда B).\n"
        "- is_single_timeline: true если Тип 2 (таймлайн), иначе false.\n"
        "- Голы и ассисты — списки имён (с повторением если один игрок забил/ассистировал дважды).\n"
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
