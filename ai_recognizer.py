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
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

PROMPT_TEXT = """
Ты — узкоспециализированный OCR-сканер для извлечения сырых данных из скриншотов FIFA / EA FC Mobile / eFootball.

Твоя единственная задача — БУКВАЛЬНО считать текст и цифры с экрана, НЕ ПЫТАЯСЬ угадывать названия команд, логику матча или додумывать контекст.

⚠️ ИГНОРИРУЙ любые названия клубов, эмблемы и никнеймы над счётом! Они являются декоративными и не используются для логики.

---

### ЭТАП 0: ОПРЕДЕЛЕНИЕ ТИПА КАЖДОГО ИЗОБРАЖЕНИЯ

Изображений может быть ОДНО или НЕСКОЛЬКО (до 3). Для КАЖДОГО изображения СНАЧАЛА определи его тип ПО СОДЕРЖИМОМУ, а не по порядку отправки:

**ТИП 1 — «ВЕРТИКАЛЬНАЯ КОЛОНКА ГОЛОВ»:**
- В верхней части скриншота показан счёт матча (крупные цифры).
- Ниже идёт ВЕРТИКАЛЬНЫЙ список имён футболистов — это ИГРОКИ, ЗАБИВШИЕ ГОЛЫ.
- В таком списке НЕТ разделения на голы/ассисты и НЕТ столбцов `Г` / `А`.
- ИГНОРИРУЙ раздел "ПЕНАЛЬТИ" (если он есть в списке). Игроков, забивших послематчевые пенальти, в списки `left_goals` и `right_goals` добавлять НЕ НУЖНО.
- Из этого типа берутся ТОЛЬКО: `left_score`, `right_score` и имена забивших в `left_goals` / `right_goals` (кроме пенальти).
- `left_assists` / `right_assists` из этого типа = пустые списки.

**ТИП 2 — «ТАБЛИЦА СТАТИСТИКИ»:**
- В верхней части скриншота показан счёт матча.
- Ниже экран разделён на две таблицы (Левая и Правая команда) с колонками и заголовками `Г` (Голы) и `А` (Ассисты).
- Из этого типа берутся: `left_score`, `right_score`, голы И ассисты.

---

### ЭТАП 1: АНАЛИЗ СЧЁТА

1. **Счёт на табло (КРУПНЫЙ БЕЛЫЙ ТЕКСТ ПО ЦЕНТРУ):**
   - `left_score` = Первое число (слева от дефиса).
   - `right_score` = Второе число (справа от дефиса).
   - **СЕРИЯ ПЕНАЛЬТИ**: Если рядом со счётом есть маленькие цифры в скобках (например, `(3) 2 - 2 (2)`), это означает, что была серия пенальти. В таком случае прибавь +1 гол к итоговому счёту той команды, которая победила по пенальти (у которой число в скобках больше). Например, для `(3) 2 - 2 (2)` итоговый счёт должен быть `left_score = 3`, `right_score = 2`.

---

### ЭТАП 2: ОБРАБОТКА СКРИНШОТА ТИПА 2 (ТАБЛИЦА СТАТИСТИКИ)

2. **ТАБЛИЦА СТАТИСТИКИ (СТРОГО РАЗДЕЛЕНА ПОПОЛАМ ПО ВЕРТИКАЛИ):**
Экран четко разделен на две независимые таблицы (Левая и Правая команда).
ОНИ ИМЕЮТ РАЗНЫЙ (ЗЕРКАЛЬНЫЙ) ПОРЯДОК СТОЛБЦОВ!
Обрати внимание на заголовки столбцов: `Г` = Голы, `А` = Ассисты.

3. **ЛЕВАЯ ПОЛОВИНА (LEFT SIDE):**
   - Порядок столбцов: `ПОЗ` | `ИГРОКИ` | `ОБЩ` | `ИС` | `Г` | `А`
   - Столбец `Г` (Голы) идет ПЕРВЫМ из двух правых цифр (дальше от центра).
   - Столбец `А` (Ассисты) идет ВТОРЫМ (ближе всего к центру экрана).
   - Занеси ИМЯ ИГРОКА в `left_goals`, если число в столбце `Г` > 0 (столько раз, чему равно число).
   - Занеси ИМЯ ИГРОКА в `left_assists`, если число в столбце `А` > 0 (столько раз, чему равно число).

4. **ПРАВАЯ ПОЛОВИНА (RIGHT SIDE) — ВНИМАНИЕ, ЗЕРКАЛЬНЫЙ ПОРЯДОК:**
   - Порядок столбцов: `А` | `Г` | `ИС` | `ОБЩ` | `ИГРОКИ` | `ПОЗ`
   - Столбец `А` (Ассисты) идет ПЕРВЫМ (ближе всего к центру экрана).
   - Столбец `Г` (Голы) идет ВТОРЫМ (дальше от центра).
   - СТРОГО: первое число слева в правой таблице — это АССИСТЫ (А), второе — ГОЛЫ (Г). НЕ ПЕРЕПУТАЙ!
   - Занеси ИМЯ ИГРОКА в `right_goals`, если число в столбце `Г` > 0 (столько раз, чему равно число).
   - Занеси ИМЯ ИГРОКА в `right_assists`, если число в столбце `А` > 0 (столько раз, чему равно число).

5. **ОБЯЗАТЕЛЬНАЯ КРОСС-ПРОВЕРКА:**
   - Общее количество элементов в `left_goals` ОБЯЗАНО совпадать со счётом `left_score`!
   - Общее количество элементов в `right_goals` ОБЯЗАНО совпадать со счётом `right_score`!
   - ⚠️ ИСКЛЮЧЕНИЕ: Если в матче была серия пенальти (ты прибавил +1 гол победителю), то количество забитых голов в списках `left_goals` / `right_goals` будет на 1 меньше, чем итоговый счёт у победившей команды. Это нормально, не выдумывай несуществующий гол в список!
   - Если количество забитых голов не совпадает со счётом на табло (и это не победа по пенальти), ты перепутал столбцы `Г` и `А` в одной из таблиц. Вспомни: в правой таблице АССИСТЫ (А) идут перед ГОЛАМИ (Г)!

---

### ЭТАП 3: СБОРКА ИТОГОВОГО РЕЗУЛЬТАТА

Если прислано несколько изображений РАЗНЫХ типов, собери единый итог:

- **Счёт матча** присутствует на обоих типах — возьми его (если значения различаются, приоритет у ТИПА 2 «таблица статистики»).
- **Голы (`left_goals` / `right_goals`):** объедини имена забивших из вертикальной колонки (ТИП 1) и из столбца `Г` таблицы (ТИП 2) БЕЗ ДУБЛИРОВАНИЯ одинаковых игроков.
- **Ассисты (`left_assists` / `right_assists`):** берутся ТОЛЬКО из столбца `А` таблицы (ТИП 2).
- Если прислан только один скриншот — заполни те поля, которые можно извлечь из его типа, остальные оставь пустыми.

---

### ЭТАП 4: ФОРМАТ ОТВЕТА

Верни результат СТРОГО в виде одного валидного JSON-объекта без разметки markdown:

{
  "left_score": 4,
  "right_score": 2,
  "is_single_timeline": false,
  "left_goals": ["Raspadori", "Raspadori", "Lang", "Gittens"],
  "right_goals": ["Morita", "Suárez"],
  "left_assists": ["Ndidi", "Raspadori", "Raspadori", "Gittens"],
  "right_assists": ["Zhegrova", "Pedro Gonçalves"]
}
"""

def clean_json_response(raw_text: str) -> str:
    """Очищает ответ модели от возможных markdown-тегов ```json ... ```."""
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()

def _check_proxy_alive(proxy_url: str) -> bool:
    """Check if proxy host:port is accepting connections."""
    import socket
    from urllib.parse import urlparse
    try:
        parsed = urlparse(proxy_url if "://" in proxy_url else f"http://{proxy_url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 4001
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except Exception:
        return False

def _get_gemini_opener():
    """Returns a urllib opener with proxy support if alive, or direct opener."""
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("WARP_PROXY", "http://127.0.0.1:4001")
    if proxy_url and _check_proxy_alive(proxy_url):
        try:
            logger.info(f"AI Vision: Using proxy {proxy_url}")
            handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            return urllib.request.build_opener(handler)
        except Exception:
            pass
    # Explicitly disable proxy for direct connection if proxy is inactive/down
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))

def recognize_match_screenshots_bytes(images_bytes_list: list[bytes], mime_type: str = "image/jpeg", api_key: str = None) -> dict | None:
    target_api_key = (api_key or config.GEMINI_API_KEY).strip()

    if not target_api_key:
        logger.error("GEMINI_API_KEY is empty or not set!")
        return None

    if not images_bytes_list:
        return None

    opener = _get_gemini_opener()

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

            with opener.open(req, timeout=30) as response:
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
            logger.warning(f"Gemini model '{m_name}' HTTP {e.code}: {error_body[:300]}")
            if e.code == 429:
                import time
                time.sleep(1.0)
            continue
        except Exception as e:
            logger.exception(f"Gemini model '{m_name}' recognition error: {e}")
            continue

    logger.error("All Gemini Vision fallback models failed or were rate-limited.")
    return None

def recognize_match_screenshot_bytes(image_bytes: bytes, mime_type: str = "image/jpeg", api_key: str = None) -> dict | None:
    return recognize_match_screenshots_bytes([image_bytes], mime_type=mime_type, api_key=api_key)