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

Твоя единственная задача — БУКВАЛЬНО считать голы, ассисты и счёт с экрана, НЕ ПЫТАЯСЬ угадывать логику матча.

⚠️ ИГНОРИРУЙ любые клубные эмблемы и названия лиг на самом скриншоте (например, Trafic Family FC, НЕТ ЛИГИ, Champions Clups).
⚠️ НАЗВАНИЯ КОМАНД (team1 и team2) И СОПОСТАВЛЕНИЕ ПО СОСТАВАМ:
- Бери названия команд из текста подписи пользователя (например, Брага и Бенфика).
- Если переданы официальные составы клубов из базы данных:
  1. Сравни имена игроков на скриншоте (в колонке голов или таблице статистики) со списками составов:
     - Если игроки на ЛЕВОЙ стороне скриншота входят в состав клуба «Брага» ➔ значит ЛЕВАЯ команда (`team1`) = "Брага"!
     - Если игроки на ПРАВОЙ стороне скриншота входят в состав клуба «Бенфика» ➔ значит ПРАВАЯ команда (`team2`) = "Бенфика"!
  2. Нормализуй имена авторов голов и ассистов в точном соответствии с базой данных (например, `Родриго` ➔ `Rodrygo`, `Жоау Педро` ➔ `João Pedro`, `Рикардо Орта` ➔ `Ricardo Horta`).
- `team1` — это ВСЕГДА команда, играющая СЛЕВА на скриншоте (чей счёт `left_score`, голы `left_goals` и ассисты `left_assists`).
- `team2` — это ВСЕГДА команда, играющая СПРАВА на скриншоте (чей счёт `right_score`, голы `right_goals` и ассисты `right_assists`).

---

### ЭТАП 0: ОПРЕДЕЛЕНИЕ ТИПА КАЖДОГО ИЗОБРАЖЕНИЯ

Изображений может быть ОДНО или НЕСКОЛЬКО (до 3). Для КАЖДОГО изображения СНАЧАЛА определи его тип ПО СОДЕРЖИМОМУ, а не по порядку отправки:

**ТИП 1 — «ВЕРТИКАЛЬНАЯ КОЛОНКА ГОЛОВ»:**
- В верхней части скриншота показан счёт матча (крупные цифры по центру, например `3 - 0`).
- Ниже идёт ВЕРТИКАЛЬНЫЙ список имён футболистов — это ИГРОКИ, ЗАБИВШИЕ ГОЛЫ.
⚠️ ВАЖНО: В игре EA FC Mobile список авторов голов ВСЕГДА отображается в правой части экрана, независимо от того, кто забил!
- Сторона гола определяется по ЦВЕТУ КРУЖКА рядом с голом: ЗЕЛЁНЫЙ кружок = гол левой команды (`left_goals`), СИНИЙ кружок = гол правой команды (`right_goals`).
- СВЕРКА СО СЧЁТОМ НА ТАБЛО: Если счёт 3 - 0 (левая команда 3, правая 0), значит ВСЕ 3 гола со скриншота ОБЯЗАНЫ попасть в `left_goals`, а `right_goals` должен быть пустым `[]`!
- КРОСС-ПРОВЕРКА: Количество элементов в `left_goals` ОБЯЗАНО в точности равняться `left_score`, а в `right_goals` — `right_score`!
- ИГНОРИРУЙ раздел "ПЕНАЛЬТИ" (если он есть в списке). Игроков, забивших послематчевые пенальти, в списки `left_goals` и `right_goals` добавлять НЕ НУЖНО.
- `left_assists` / `right_assists` из этого типа = пустые списки `[]`.
- Для ТИП 1 установи `"is_single_timeline": true`.

**ТИП 2 — «ТАБЛИЦА СТАТИСТИКИ» (с колонками голов и ассистов):**
- В верхней части скриншота показан счёт матча.
- Ниже экран разделён на две таблицы (Левая и Правая команда).
- Заголовки столбцов могут быть на РУССКОМ (`ПОЗ`, `ИГРОКИ`, `ОБЩ`, `ИС`, `Г`, `А`) или на АНГЛИЙСКОМ (`POS`, `PLAYERS`, `OVR`, `PS`, `G`, `A`).
- Колонка `Г` или `G` = ГОЛЫ (Goals).
- Колонка `А` или `A` = АССИСТЫ (Assists).
- Для ТИП 2 установи `"is_single_timeline": false`.
- Из этого типа ОБЯЗАТЕЛЬНО берутся: `left_score`, `right_score`, все авторы голов И ВСЕ АССИСТЕНТЫ (`left_assists`, `right_assists`).

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
Обрати внимание на заголовки столбцов (на русском `Г`/`А` или на английском `G`/`A`): `Г` или `G` = Голы, `А` или `A` = Ассисты.
⚠️ ВАЖНО ПРО ПЕНАЛЬТИ В ТАБЛИЦЕ: В игре EA FC Mobile голы с послематчевых пенальти ПЛЮСУЮТСЯ к обычным голам в колонке `Г` / `G`! Из-за этого невозможно отличить, кто забил в матче, а кто в пенальти. 
ПОЭТОМУ: Если в матче была серия пенальти (ты видишь маленькие цифры в скобках возле счёта), ПОЛНОСТЬЮ ИГНОРИРУЙ колонку `Г`/`G` в таблице статистики (ТИП 2) и НЕ извлекай оттуда голы (оставь списки `left_goals` и `right_goals` пустыми, если нет скриншота ТИП 1). Ассисты (колонка `А`/`A`) при этом извлекай как обычно.

3. **ЛЕВАЯ ПОЛОВИНА (LEFT SIDE):**
   - Порядок столбцов: `ПОЗ/POS` | `ИГРОКИ/PLAYERS` | `ОБЩ/OVR` | `ИС/PS` | `Г/G` | `А/A`
   - Столбец `Г` / `G` (Голы) идет ПЕРВЫМ из двух правых цифр (предпоследняя колонка).
   - Столбец `А` / `A` (Ассисты) идет ВТОРЫМ (крайняя правая колонка левой таблицы).
   - ПРИМЕРЫ:
     Если в строке Raspadori стоит: CAM Raspadori 111 2 0
     -> 2 (предпоследнее число) = ГОЛЫ (G = 2) -> занеси "Raspadori" ДВАЖДЫ в `left_goals`: ["Raspadori", "Raspadori"]!
     -> 0 (последнее число) = АССИСТЫ (A = 0)
     Если в строке Lang стоит: LW Lang 113 0 3
     -> 0 (предпоследнее число) = ГОЛЫ (G = 0)
     -> 3 (последнее число) = АССИСТЫ (A = 3) -> занеси "Lang" ТРИЖДЫ в `left_assists`: ["Lang", "Lang", "Lang"]!
   - ⚠️ ВНИМАТЕЛЬНО ПРОХОДИ ПО ВСЕМ СТРОКАМ: не пропускай игроков с 0 голов, если у них есть ассисты (0 1, 0 2, 0 3)!
   - Занеси ИМЯ ИГРОКА в `left_goals`, если число в столбце `Г`/`G` > 0 (СТРОГО столько раз, чему равно число).
   - Занеси ИМЯ ИГРОКА в `left_assists`, если число в столбце `А`/`A` > 0 (СТРОГО столько раз, чему равно число: если стоит 3, добавь имя ровно 3 раза!).

4. **ПРАВАЯ ПОЛОВИНА (RIGHT SIDE) — ВНИМАНИЕ, ЗЕРКАЛЬНЫЙ ПОРЯДОК:**
   - Порядок столбцов: `А/A` | `Г/G` | `ИС/PS` | `ОБЩ/OVR` | `ИГРОКИ/PLAYERS` | `ПОЗ/POS`
   - Столбец `А` / `A` (Ассисты) идет ПЕРВЫМ (крайняя левая колонка правой таблицы, ближе к центру).
   - Столбец `Г` / `G` (Голы) идет ВТОРЫМ (дальше от центра, перед столбцом ОБЩ/OVR).
   - ПРИМЕРЫ:
     Если в строке Cabella стоит: 0  1  104 Cabella
     -> 0 (первое число) = АССИСТЫ (A = 0)
     -> 1 (второе число) = ГОЛЫ (G = 1) -> занеси "Cabella" в `right_goals`!
     Если в строке Barron стоит: 2  0  72 Barron
     -> 2 (первое число) = АССИСТЫ (A = 2) -> занеси "Barron" ДВАЖДЫ в `right_assists`: ["Barron", "Barron"]!
     Если в строке Cristian Tello стоит: 1  1  108 Cristian Tello
     -> 1 (первое число) = АССИСТЫ (A = 1) -> занеси "Cristian Tello" в `right_assists`!
     -> 1 (второе число) = ГОЛЫ (G = 1) -> занеси "Cristian Tello" в `right_goals`!
   - СТРОГО: первое число слева в правой таблице — это АССИСТЫ (А/A), второе — ГОЛЫ (Г/G). НЕ ПЕРЕПУТАЙ!
   - Занеси ИМЯ ИГРОКА в `right_goals`, если число в столбце `Г`/`G` > 0 (СТРОГО столько раз, чему равно число).
   - Занеси ИМЯ ИГРОКА в `right_assists`, если число в столбце `А`/`A` > 0 (СТРОГО столько раз, чему равно число: если стоит 2, добавь имя ровно 2 раза!).

5. **ОБЯЗАТЕЛЬНАЯ КРОСС-ПРОВЕРКА:**
   - Общее количество элементов в `left_goals` ОБЯЗАНО совпадать со счётом `left_score`!
   - Общее количество элементов в `right_goals` ОБЯЗАНО совпадать со счётом `right_score`!
   - ⚠️ ИСКЛЮЧЕНИЕ: Если в матче была серия пенальти, ты проигнорировал колонку `Г` и списки голов могут быть вообще пустыми (если нет скриншота ТИП 1). Это НОРМАЛЬНО, в таком случае кросс-проверка голов отменяется. Не выдумывай несуществующие голы!
   - Если количество забитых голов не совпадает со счётом на табло (и это не серия пенальти, и ты не брал голы только с ТИП 1), ты перепутал столбцы `Г` и `А` в одной из таблиц. Вспомни: в правой таблице АССИСТЫ (А) идут перед ГОЛАМИ (Г)!

---

### ЭТАП 3: ОПРЕДЕЛЕНИЕ МАТЧЕЙ (ОДИН, НЕСКОЛЬКО ИЛИ ИЗ ТЕКСТА ПОДПИСИ)

- **РАЗНЫЕ МАТЧИ** (например, Игра 1 со счётом 3-0 и Игра 2 со счётом 4-2): обработай каждый матч отдельно и верни их в массиве `matches`.
- **ОДИН МАТЧ** (например, два скриншота одной игры: вертикальная колонка голов и таблица статистики с одинаковым счётом): объедини голы и ассисты в один объект в массиве `matches`.
- ⚠️ **МАТЧИ, ОПИСАННЫЕ В ТЕКСТЕ ПОДПИСИ**:
  Если в тексте подписи пользователя описан дополнительный матч (например: «3 матч в пользу Бенфики 1:2, Голы Родриго, Жоау Педро, Гол Браги Рикардо Орта»), ОБЯЗАТЕЛЬНО извлеки его и добавь отдельным объектом в массив `matches` (со счётом 2-1 и авторами голов из текста)!

---

### ЭТАП 4: ФОРМАТ ОТВЕТА

⚠️ КРИТИЧЕСКИ ВАЖНО: В массивах left_goals, right_goals, left_assists, right_assists количество элементов ОБЯЗАНО строго равняться числу в соответствующей колонке таблицы (Г или А)!
- Если у игрока в колонке А стоит 2 — его имя ОБЯЗАНО быть указано 2 раза в массиве ассистов (например: ["Barron", "Barron"]).
- Если у игрока в колонке А стоит 3 — ровно 3 раза (например: ["Lang", "Lang", "Lang"]).
- Если у игрока в колонке Г стоит 2 — ровно 2 раза (например: ["Raspadori", "Raspadori"]).

Верни результат СТРОГО в виде одного валидного JSON-объекта без разметки markdown:

{
  "matches": [
    {
      "team1": "Название левой команды (например, Брага)",
      "team2": "Название правой команды (например, Рейнджерс)",
      "left_score": 3,
      "right_score": 2,
      "is_single_timeline": false,
      "left_goals": ["Addai", "Ricardo Horta", "Ricardo Horta"],
      "right_goals": ["Diomande", "Ziyech"],
      "left_assists": ["Vitor Carvalho", "Leonardo Lelo"],
      "right_assists": ["Barron", "Barron"]
    }
  ]
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
    # If custom GEMINI_BASE_URL (like Cloudflare Worker) is used, default to direct connection
    if os.environ.get("GEMINI_BASE_URL") and not os.environ.get("GEMINI_PROXY"):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    proxy_url = (
        os.environ.get("GEMINI_PROXY")
        or os.environ.get("ALL_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("WARP_PROXY", "http://127.0.0.1:4001")
    )
    if proxy_url and _check_proxy_alive(proxy_url):
        try:
            logger.info(f"AI Vision: Using proxy {proxy_url}")
            handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            return urllib.request.build_opener(handler)
        except Exception:
            pass
    # Explicitly disable proxy for direct connection if proxy is inactive/down
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))

def recognize_match_screenshots_bytes(
    images_bytes_list: list[bytes], 
    mime_type: str = "image/jpeg", 
    api_key: str = None, 
    caption: str = "",
    squad_hints: dict[str, list[str]] = None
) -> dict | None:
    target_api_key = (api_key or config.GEMINI_API_KEY).strip()

    if not target_api_key:
        logger.error("GEMINI_API_KEY is empty or not set!")
        return None

    if not images_bytes_list:
        return None

    opener = _get_gemini_opener()

    for m_name in GEMINI_MODELS:
        try:
            prompt_with_caption = PROMPT_TEXT
            if squad_hints:
                prompt_with_caption += "\n\n--- ОФИЦИАЛЬНЫЕ СОСТАВЫ КЛУБОВ ИЗ БАЗЫ ДАННЫХ ---\n"
                for tname, splayers in squad_hints.items():
                    if splayers:
                        prompt_with_caption += f"• Клуб «{tname}»: {', '.join(splayers)}\n"
            if caption:
                prompt_with_caption += f"\n\n--- ПОДПИСЬ ПОЛЬЗОВАТЕЛЯ ---\n{caption}"
                
            parts = [{"text": prompt_with_caption}]
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
                    "temperature": 0.0
                }
            }

            base_url = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com").rstrip("/")
            url = f"{base_url}/v1beta/models/{m_name}:generateContent?key={target_api_key}"
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                }
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

                    # Support matches array or single match object
                    raw_matches = parsed_data.get("matches")
                    if isinstance(raw_matches, list) and len(raw_matches) > 0:
                        matches_list = raw_matches
                    else:
                        matches_list = [parsed_data]

                    for m in matches_list:
                        m.setdefault("left_score", 0)
                        m.setdefault("right_score", 0)
                        m.setdefault("left_goals", [])
                        m.setdefault("right_goals", [])
                        m.setdefault("left_assists", [])
                        m.setdefault("right_assists", [])
                        m.setdefault("is_single_timeline", False)

                        # If assists are present, it is NEVER a single timeline!
                        if len(m["left_assists"]) > 0 or len(m["right_assists"]) > 0:
                            m["is_single_timeline"] = False

                        # Cross-check and side swap if goals were put on the wrong side
                        if m["left_score"] > 0 and m["right_score"] == 0:
                            if len(m["left_goals"]) == 0 and len(m["right_goals"]) > 0:
                                m["left_goals"], m["right_goals"] = m["right_goals"], m["left_goals"]
                            if len(m["left_assists"]) == 0 and len(m["right_assists"]) > 0:
                                m["left_assists"], m["right_assists"] = m["right_assists"], m["left_assists"]
                        elif m["right_score"] > 0 and m["left_score"] == 0:
                            if len(m["right_goals"]) == 0 and len(m["left_goals"]) > 0:
                                m["left_goals"], m["right_goals"] = m["right_goals"], m["left_goals"]
                            if len(m["right_assists"]) == 0 and len(m["left_assists"]) > 0:
                                m["left_assists"], m["right_assists"] = m["right_assists"], m["left_assists"]

                        # Совместимость
                        m["home_score"] = m["left_score"]
                        m["away_score"] = m["right_score"]
                        m["side1_goals"] = m["left_goals"]
                        m["side2_goals"] = m["right_goals"]
                        m["side1_assists"] = m["left_assists"]
                        m["side2_assists"] = m["right_assists"]

                        if m["left_score"] == 0 and len(m["left_goals"]) > 0:
                            m["left_score"] = len(m["left_goals"])
                            m["home_score"] = len(m["left_goals"])

                        if m["right_score"] == 0 and len(m["right_goals"]) > 0:
                            m["right_score"] = len(m["right_goals"])
                            m["away_score"] = len(m["right_goals"])

                    parsed_data["matches"] = matches_list
                    first_m = matches_list[0]
                    for k, v in first_m.items():
                        if k != "matches":
                            parsed_data[k] = v

                    logger.info(
                        f"AI Vision ({m_name}) recognized {len(matches_list)} match(es): "
                        + ", ".join([f"{m.get('left_score')}-{m.get('right_score')}" for m in matches_list])
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

def recognize_match_screenshot_bytes(image_bytes: bytes, mime_type: str = "image/jpeg", api_key: str = None, caption: str = "") -> dict | None:
    return recognize_match_screenshots_bytes([image_bytes], mime_type=mime_type, api_key=api_key, caption=caption)