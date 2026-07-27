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
    model_setting = getattr(config, "GEMINI_MODEL", "gemini-3.1-flash-lite")

    if not api_key:
        logger.warning("GEMINI_API_KEY is not set.")
        return None

    if not images_bytes_list:
        return None

    prompt_text = ("""
Ты — узкоспециализированный OCR-сканер для извлечения сырых данных из скриншотов FIFA / EA FC Mobile.

Твоя единственная задача — БУКВАЛЬНО считать текст и цифры с экрана, НЕ ПЫТАЯСЬ угадывать названия команд, логику матча или додумывать контекст.

⚠️ ИГНОРИРУЙ любые названия клубов, эмблемы и никнеймы над счётом! Они являются декоративными и не используются для логики.

---

### ЭТАП 1: ПОШАГОВЫЙ АНАЛИЗ ГЕОМЕТРИИ ЭКРАНА

1. **Счёт на табло:**
- `left_score` = Первое число (слева от дефиса).
- `right_score` = Второе число (справа от дефиса).
2. **Левая половина таблицы (LEFT SIDE):**
- Формат колонок: [ПОЗ] | [ИМЯ ИГРОКА] | [OVR] | [ Г ] | [ А ]
- Игнорируй ПОЗ и OVR (числа от 60 до 120).
- Первая цифра после OVR = ГОЛЫ (Г).
- Вторая цифра после OVR = АССИСТЫ (А).
- Если Г > 0 -> занеси ИМЯ ИГРОКА в `left_goals` столько раз, чему равно число.
- Если А > 0 -> занеси ИМЯ ИГРОКА в `left_assists` столько раз, чему равно число.

3. **Правая половина таблицы (RIGHT SIDE):**
- Формат колонок ЗЕРКАЛЬНЫЙ: [ А ] | [ Г ] | [OVR] | [ИМЯ ИГРОКА] | [ПОЗ]
- Игнорируй ПОЗ и OVR (числа от 60 до 120).
- ⚠️ ВНИМАНИЕ: Первая цифра (крайняя левая) = АССИСТЫ (А)!
- ⚠️ ВНИМАНИЕ: Вторая цифра (перед OVR) = ГОЛЫ (Г)!
- Если Г > 0 -> занеси ИМЯ ИГРОКА в `right_goals` столько раз, чему равно число.
- Если А > 0 -> занеси ИМЯ ИГРОКА в `right_assists` столько раз, чему равно число.

4. **КРОСС-ПРОВЕРКА:**
- Количество элементов в `left_goals` ДОЛЖНО совпадать с `left_score`.
- Количество элементов в `right_goals` ДОЛЖНО совпадать с `right_score`.
- Если не совпадает — ты перепутал OVR с голами или ассистами. Перепроверь!

---

### ЭТАП 2: ФОРМАТ ОТВЕТА

Верни результат СТРОГО в виде одного валидного JSON-объекта без разметки markdown:

{
"left_score": 3,
"right_score": 2,
"is_single_timeline": false,
"left_goals": ["Leonardo Lelo", "Ricardo Horta", "Grønbæk"],
"right_goals": ["Belmonte", "Zeballos"],
"left_assists": ["Leonardo Lelo", "Gabri Martínez", "Grønbæk"],
"right_assists": ["Zeballos", "Valera"]
}
"""
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
