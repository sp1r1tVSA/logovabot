import os
import base64
import json
import re
import logging
import urllib.request
import urllib.error
import config

logger = logging.getLogger(__name__)

GEMINI_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash"
]

PROMPT_TEXT = """
Ты — узкоспециализированный OCR-сканер для извлечения сырых данных из скриншотов FIFA / EA FC Mobile / eFootball.

Твоя единственная задача — БУКВАЛЬНО считать текст и цифры с экрана, НЕ ПЫТАЯСЬ угадывать названия команд, логику матча или додумывать контекст.

⚠️ ИГНОРИРУЙ любые названия клубов, эмблемы и никнеймы над счётом! Они являются декоративными и не используются для логики.

---

### ЭТАП 1: ПОШАГОВЫЙ АНАЛИЗ ГЕОМЕТРИИ ЭКРАНА

1. **Счёт на табло (КРУПНЫЙ БЕЛЫЙ ТЕКСТ ПО ЦЕНТРУ):**
   - `left_score` = Первое число (слева от дефиса).
   - `right_score` = Второе число (справа от дефиса).
   - Перепроверь верхнюю треть экрана 3 раза, чтобы точно распознать счет!

2. **Левая половина таблицы (LEFT SIDE):**
   - Формат колонок: [ПОЗ] | [ИМЯ ИГРОКА] | [OVR] | [ Г ] | [ А ]
   - Игнорируй ПОЗ и OVR (числа от 60 до 120).
   - Первая цифра после OVR = ГОЛЫ (Г).
   - Вторая цифра после OVR = АССИСТЫ (А).
   - Если Г > 0 -> занеси ИМЯ ИГРОКА в left_goals столько раз, чему равно число.
   - Если А > 0 -> занеси ИМЯ ИГРОКА в left_assists столько раз, чему равно число.

3. **Правая половина таблицы (RIGHT SIDE):**
   - Формат колонок ЗЕРКАЛЬНЫЙ: [ А ] | [ Г ] | [OVR] | [ИМЯ ИГРОКА] | [ПОЗ]
   - Игнорируй ПОЗ и OVR (числа от 60 до 120).
   - ⚠️ ВНИМАНИЕ: Первая цифра (крайняя левая) = АССИСТЫ (А)!
   - ⚠️ ВНИМАНИЕ: Вторая цифра (перед OVR) = ГОЛЫ (Г)!
   - Если Г > 0 -> занеси ИМЯ ИГРОКА в right_goals столько раз, чему равно число.
   - Если А > 0 -> занеси ИМЯ ИГРОКА в right_assists столько раз, чему равно число.

4. **КРОСС-ПРОВЕРКА:**
   - Количество элементов в left_goals ДОЛЖНО совпадать с left_score.
   - Количество элементов в right_goals ДОЛЖНО совпадать с right_score.
   - Если не совпадает — перепроверь данные!

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

def clean_json_response(raw_text: str) -> str:
    """Очищает ответ модели от возможных markdown-тегов ```json ... ```."""
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()

def recognize_match_screenshots_bytes(images_bytes_list: list[bytes], mime_type: str = "image/jpeg", api_key: str = None) -> dict | None:
    target_api_key = (api_key or config.GEMINI_API_KEY).strip()

    if not target_api_key:
        logger.error("GEMINI_API_KEY is empty or not set!")
        return None

    if not images_bytes_list:
        return None

    for m_name in GEMINI_MODELS:
        try:
            parts = [{"text": PROMPT_TEXT}]
            for img_bytes in images_bytes_list:
                parts.append({
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": base64.b64encode(img_bytes).decode("utf-8")
                    }
                })

            # Безопасный payload без конфликтных полей в generationConfig
            payload = {
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "temperature": 0.1
                }
            }

            url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_name}:generateContent?key={target_api_key}"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                res_json = json.loads(response.read().decode("utf-8"))

                candidates = res_json.get("candidates", [])
                if candidates and "content" in candidates[0]:
                    text_content = candidates[0]["content"]["parts"][0]["text"]
                    clean_text = clean_json_response(text_content)
                    parsed_data = json.loads(clean_text)

                    if not isinstance(parsed_data, dict):
                        logger.warning(f"Gemini model '{m_name}' returned non-dict JSON: {parsed_data}")
                        continue

                    parsed_data.setdefault("left_score", 0)
                    parsed_data.setdefault("right_score", 0)
                    parsed_data.setdefault("left_goals", [])
                    parsed_data.setdefault("right_goals", [])
                    parsed_data.setdefault("left_assists", [])
                    parsed_data.setdefault("right_assists", [])
                    parsed_data.setdefault("is_single_timeline", False)

                    # Совместимость со старой структурой
                    parsed_data.setdefault("home_score", parsed_data["left_score"])
                    parsed_data.setdefault("away_score", parsed_data["right_score"])
                    parsed_data.setdefault("side1_goals", parsed_data["left_goals"])
                    parsed_data.setdefault("side2_goals", parsed_data["right_goals"])
                    parsed_data.setdefault("side1_assists", parsed_data["left_assists"])
                    parsed_data.setdefault("side2_assists", parsed_data["right_assists"])

                    # Корректировка счета 0 - 0 по списку голов
                    if parsed_data["left_score"] == 0 and len(parsed_data["left_goals"]) > 0:
                        parsed_data["left_score"] = len(parsed_data["left_goals"])
                        parsed_data["home_score"] = len(parsed_data["left_goals"])

                    if parsed_data["right_score"] == 0 and len(parsed_data["right_goals"]) > 0:
                        parsed_data["right_score"] = len(parsed_data["right_goals"])
                        parsed_data["away_score"] = len(parsed_data["right_goals"])

                    logger.info(
                        f"AI Vision ({m_name}) recognized match: "
                        f"{parsed_data.get('left_score')} - {parsed_data.get('right_score')} "
                        f"(is_single_timeline={parsed_data.get('is_single_timeline')})"
                    )
                    return parsed_data
                else:
                    logger.warning(f"Gemini model '{m_name}' returned no candidates: {res_json}")
                    continue

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="ignore")
            if e.code == 429:
                logger.warning(f"Gemini model '{m_name}' rate-limited (429). Trying next fallback model...")
            elif e.code == 404:
                logger.warning(f"Gemini model '{m_name}' not found (404). Trying next fallback model...")
            else:
                logger.error(f"Gemini model '{m_name}' HTTP Error {e.code}: {error_body}")
            continue
        except Exception as e:
            logger.exception(f"Gemini model '{m_name}' recognition error: {e}")
            continue

    logger.error("All Gemini Vision fallback models failed or were rate-limited.")
    return None

def recognize_match_screenshot_bytes(image_bytes: bytes, mime_type: str = "image/jpeg", api_key: str = None) -> dict | None:
    return recognize_match_screenshots_bytes([image_bytes], mime_type=mime_type, api_key=api_key)