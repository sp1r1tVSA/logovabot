import os
import base64
import json
import re
import logging
import urllib.request
import urllib.error
import config

logger = logging.getLogger(__name__)

def recognize_match_screenshots_bytes(images_bytes_list: list[bytes], mime_type: str = "image/jpeg") -> dict | None:
    """
    Sends 1 or 2 match screenshot image bytes to Google Gemini API.
    Supports both 2-column match stats screenshots and single vertical timeline goal list screenshots.
    Returns structured dict with match scores, goals, assists, and is_single_timeline flag.
    """
    api_key = getattr(config, "GEMINI_API_KEY", "")
    model_setting = getattr(config, "GEMINI_MODEL", "gemini-2.5-flash-lite")

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
        "Таблица игроков разделена на две половины:\n"
        "- Левая половина таблицы = игроки команды A (левой в счёте)\n"
        "- Правая половина таблицы = игроки команды B (правой в счёте)\n\n"
        "=== КРИТИЧЕСКОЕ ПРАВИЛО: КОЛОНКИ 'Г' И 'А' ЗЕРКАЛЬНЫ ДЛЯ ПРАВОЙ КОМАНДЫ ===\n\n"
        "▶ ДЛЯ ЛЕВОЙ КОМАНДЫ (side1): заголовки слева направо: [ Г | А ]\n"
        "   - Левая цифра = Голы (Г), Правая цифра = Ассисты (А).\n"
        "   - Пример: строка 'Evander | 2 | 0' → 2 Гола, 0 Ассистов. Запиши Evander ДВАЖДЫ в side1_goals.\n"
        "   - Пример: строка 'Khedira | 1 | 0' → 1 Гол, 0 Ассистов.\n"
        "   - Пример: строка 'Pineda | 0 | 1' → 0 Голов, 1 Ассист.\n\n"
        "▶ ДЛЯ ПРАВОЙ КОМАНДЫ (side2): заголовки ЗЕРКАЛЬНЫЕ слева направо: [ А | Г ]\n"
        "   ⚠️ ВНИМАНИЕ: Для правой команды ПЕРВАЯ (левая) цифра = АССИСТЫ, ВТОРАЯ (правая) цифра = ГОЛЫ!\n"
        "   - Пример: строка '0 | 3 | Driussi' под 'А | Г' → А=0 Ассистов, Г=3 Гола. Driussi забил 3 гола!\n"
        "   - Пример: строка '1 | 0 | Martínez' под 'А | Г' → А=1 Ассист, Г=0 Голов. У Martínez НЕТ гола, только ассист!\n"
        "   - Пример: строка '2 | 0 | Paquetá' под 'А | Г' → А=2 Ассиста, Г=0 Голов. Запиши Paquetá ДВАЖДЫ в side2_assists!\n"
        "   - Пример: строка '0 | 1 | Vargas' под 'А | Г' → А=0 Ассистов, Г=1 Гол. Vargas забил 1 гол!\n\n"
        "=== ОБЯЗАТЕЛЬНАЯ ПРОВЕРКА ПЕРЕД ОТВЕТОМ ===\n"
        "После составления списков ПРОВЕРЬ:\n"
        "1. Сумма голов в side1_goals == home_score? Если нет — исправь!\n"
        "2. Сумма голов в side2_goals == away_score? Если нет — исправь!\n"
        "3. Каждый игрок правой колонки с цифрой > 0 в колонке 'Г' (правая цифра) попадает в side2_goals.\n"
        "4. Каждый игрок правой колонки с цифрой > 0 в колонке 'А' (левая цифра) попадает в side2_assists.\n"
        "5. Количество упоминаний имени = цифра в колонке (2 гола = имя дважды в списке).\n\n"
        "=== ТИП 2 (Единая вертикальная колонка таймлайна) ===\n"
        "Сбоку — одна вертикальная колонка с хронологическим списком голов (например, 15' GOAL...). Ассисты не отображаются.\n\n"
        "Верни ответ СТРОГО в виде одного валидного JSON объекта без разметки markdown:\n"
        "{\n"
        '  "home_team": "Название команды A (левой в счёте) или null",\n'
        '  "away_team": "Название команды B (правой в счёте) или null",\n'
        '  "home_score": 3,\n'
        '  "away_score": 3,\n'
        '  "is_single_timeline": false,\n'
        '  "side1_goals": ["Khedira", "Evander", "Evander"],\n'
        '  "side2_goals": ["Driussi", "Driussi", "Driussi"],\n'
        '  "side1_assists": ["Pineda", "Bardghji"],\n'
        '  "side2_assists": ["Paquetá", "Paquetá", "Martínez"]\n'
        "}\n"
        "Правила:\n"
        "- home_team и home_score = команда A (левая в счёте), away_team и away_score = команда B (правая в счёте).\n"
        "- side1 = игроки ЛЕВОЙ колонки таблицы (команда A), side2 = игроки ПРАВОЙ колонки (команда B).\n"
        "- is_single_timeline: true если Тип 2 (таймлайн), иначе false.\n"
        "- Голы и ассисты — списки имён (с повторением если один игрок забил/ассистировал дважды).\n"
        "- Если ассисты не отображаются (Тип 2 или нет данных) — верни пустые списки []."
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
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.0-flash-lite",
        "gemini-2.0-flash",
    ])))


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
                    text_content = candidates[0]["content"]["parts"][0]["text"].strip()
                    
                    # Robust cleaning of markdown codeblocks if model returned ```json ... ```
                    if text_content.startswith("```"):
                        text_content = re.sub(r"^```(?:json)?\s*", "", text_content, flags=re.IGNORECASE)
                        text_content = re.sub(r"\s*```$", "", text_content)
                    
                    parsed_data = json.loads(text_content)
                    
                    if not isinstance(parsed_data, dict):
                        logger.warning(f"Gemini model '{m_name}' returned non-dict JSON: {parsed_data}")
                        continue
                    
                    # Ensure mandatory fields are present
                    parsed_data.setdefault("home_score", 0)
                    parsed_data.setdefault("away_score", 0)
                    parsed_data.setdefault("side1_goals", [])
                    parsed_data.setdefault("side2_goals", [])
                    parsed_data.setdefault("side1_assists", [])
                    parsed_data.setdefault("side2_assists", [])
                    parsed_data.setdefault("is_single_timeline", False)
                    
                    logger.info(
                        f"AI Vision ({m_name}) recognized match: "
                        f"{parsed_data.get('home_score')} - {parsed_data.get('away_score')} "
                        f"(is_single_timeline={parsed_data.get('is_single_timeline')})"
                    )
                    return parsed_data
                else:
                    logger.warning(f"Gemini model '{m_name}' returned no candidates: {res_json}")
                    continue
        except urllib.error.HTTPError as e:
            if e.code == 429:
                logger.warning(f"Gemini model '{m_name}' rate-limited (429). Trying next fallback model...")
            elif e.code == 404:
                logger.warning(f"Gemini model '{m_name}' not found (404). Trying next fallback model...")
            else:
                logger.exception(f"Gemini model '{m_name}' HTTP Error {e.code}")
            continue
        except Exception as e:
            logger.exception(f"Gemini model '{m_name}' recognition error")
            continue

    logger.error("All Gemini Vision fallback models failed or were rate-limited.")
    return None

def recognize_match_screenshot_bytes(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict | None:
    """Wrapper for backward compatibility."""
    return recognize_match_screenshots_bytes([image_bytes], mime_type=mime_type)
