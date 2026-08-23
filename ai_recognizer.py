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
    "gemini-2.5-flash",
    "gemini-1.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

POS_TOKENS = {
    'вр', 'gk', 'цз', 'cb', 'пз', 'rb', 'лз', 'lb', 'цоп', 'cdm',
    'цп', 'cm', 'лп', 'lm', 'пп', 'rm', 'цап', 'cam', 'лв', 'lw',
    'пв', 'rw', 'фрд', 'cf', 'нп', 'st', 'нап', 'lf', 'rf'
}

def clean_player_name(raw_name: str) -> str:
    """
    Cleans raw player name extracted by OCR:
    - Strips player ratings (e.g. 108, 113, 75).
    - Strips player positions (e.g. ЦОП, ПП, ЦАП, ПВ, GK, ST, LW).
    - Strips badges, icons (👑, ⚽, ©, Ⓒ, C).
    - Strips minute marks (e.g. 32', 45').
    - Normalizes multiple spaces and punctuation.
    """
    if not raw_name:
        return ""
    name = str(raw_name).strip()
    
    # Strip emojis, symbols and minute marks
    name = re.sub(r'[⚽👑©Ⓒ\(\)\[\]\{\}\*#~|/\\<>]', ' ', name)
    name = re.sub(r'\b\d+[\'’]\b', ' ', name)
    
    # Strip leading/trailing rating numbers (e.g. "108 Bardghji" or "Bardghji 108")
    name = re.sub(r'^\d+\s+', '', name)
    name = re.sub(r'\s+\d+$', '', name)
    
    tokens = name.split()
    if len(tokens) > 1:
        if tokens[0].lower() in POS_TOKENS:
            tokens = tokens[1:]
        if tokens and tokens[-1].lower() in POS_TOKENS:
            tokens = tokens[:-1]
            
    name = " ".join(tokens).strip()
    name = re.sub(r'\b\d+\b', '', name).strip()
    name = re.sub(r'\s+', ' ', name)
    return name.strip()


PROMPT_TEXT = """
Ты — узкоспециализированный OCR-сканер для извлечения сырых данных из скриншотов FIFA / EA FC Mobile / eFootball.

Твоя единственная задача — БУКВАЛЬНО считать голы, ассисты и счёт с экрана, НЕ ПЫТАЯСЬ угадывать логику матча.

⚠️ ИГНОРИРУЙ любые клубные эмблемы и названия лиг на самом скриншоте (например, Trafic Family FC, НЕТ ЛИГИ, Champions Clups).
⚠️ СТОРОНЫ И НАЗВАНИЯ КОМАНД (team1 и team2):
- `team1` — это ВСЕГДА команда, играющая СЛЕВА на скриншоте (чей счёт `left_score`, голы `left_goals` и ассисты `left_assists`).
- `team2` — это ВСЕГДА команда, играющая СПРАВА на скриншоте (чей счёт `right_score`, голы `right_goals` и ассисты `right_assists`).
- ⚠️ ВНИМАНИЕ: Порядок слов в тексте подписи пользователя (например, «Брюгге псв» или «Фейеноорд 3:2 Брюгге») НЕ ДОЛЖЕН МЕНЯТЬ стороны! `team1` ВСЕГДА команда СЛЕВА на изображении, а `team2` — СПРАВА!

---

### ЭТАП 0: ОПРЕДЕЛЕНИЕ ТИПА КАЖДОГО ИЗОБРАЖЕНИЯ

Изображений может быть ОДНО или НЕСКОЛЬКО (до 3). Для КАЖДОГО изображения СНАЧАЛА определи его тип ПО СОДЕРЖИМОМУ, а не по порядку отправки:

**ТИП 1 — «ВЕРТИКАЛЬНАЯ КОЛОНКА ГОЛОВ»:**
- В верхней части скриншота показан счёт матча (крупные цифры по центру, например `3 - 2`).
- Ниже идёт ВЕРТИКАЛЬНЫЙ список имён футболистов — это ИГРОКИ, ЗАБИВШИЕ ГОЛЫ.
⚠️ ВАЖНО: В игре EA FC Mobile список авторов голов ВСЕГДА отображается в правой части экрана, независимо от того, кто забил!
- Сторона гола определяется по ЦВЕТУ КРУЖКА рядом с голом: ЗЕЛЁНЫЙ кружок = гол левой команды (`left_goals`), СИНИЙ кружок = гол правой команды (`right_goals`).
- СВЕРКА СО СЧЁТОМ НА ТАБЛО: Если счёт 3 - 2 (левая команда 3, правая 2), значит в `left_goals` должно быть ровно 3 гола, а в `right_goals` — ровно 2!
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
   - ⚠️ **ПЛАШКИ И УВЕДОМЛЕНИЯ ПОВЕРХ СЧЁТА (iOS / Android / Игровой режим / Dynamic Island):**
     Если табло частично закрыто плашкой уведомления, ВНИМАТЕЛЬНО посмотри сквозь/под плашку — цифры счёта видны позади неё (например, `1 : 3`).
   - **СЕРИЯ ПЕНАЛЬТИ**: Если рядом со счётом есть маленькие цифры в скобках (например, `(3) 2 - 2 (2)`), это означает, что была серия пенальти. В таком случае прибавь +1 гол к итоговому счёту той команды, которая победила по пенальти (у которой число в скобках больше). Например, для `(3) 2 - 2 (2)` итоговый счёт должен быть `left_score = 3`, `right_score = 2`.

---

### ЭТАП 2: ОБРАБОТКА СКРИНШОТА ТИПА 2 (ТАБЛИЦА СТАТИСТИКИ)

2. **ТАБЛИЦА СТАТИСТИКИ (СТРОГО РАЗДЕЛЕНА ПОПОЛАМ ПО ВЕРТИКАЛИ):**
Экран четко разделен на две независимые таблицы (Левая и Правая команда).
⚠️ ОНИ ИМЕЮТ РАЗНЫЙ (ЗЕРКАЛЬНЫЙ) ПОРЯДОК СТОЛБЦОВ!

┌───────────────────────────────────────────────┬───────────────────────────────────────────────┐
│              ЛЕВАЯ КОМАНДА (team1)            │             ПРАВАЯ КОМАНДА (team2)            │
│  ПОЗ │ ИГРОКИ        │ ОБЩ │ ИС │  Г  │  А    │   А   │  Г  │ ИС │ ОБЩ │        ИГРОКИ │ ПОЗ    │
├──────┼───────────────┼─────┼────┼─────┼───────┼───────┼─────┼────┼─────┼───────────────┼────────┤
│  ЦАП │ Steijn        │ 113 │    │  2  │  0    │   0   │  0  │    │ 111 │       Christie│ ЦОП    │
│  ФРВ │ Guirassy      │ 110 │    │  2  │  1    │   0   │  0  │    │  80 │           Ríos│ ЦП     │
│  ПВ  │ Sterling      │ 108 │    │  1  │  2    │   0   │  1  │    │  83 │           Rafa│ ЦАП    │
│  ФРВ │ Dembélé       │ 120 │    │  0  │  0    │   0   │  0  │    │ 110 │    Igor Paixão│ ЛВ     │
│  ФРВ │ Messi         │ 118 │    │  0  │  0    │   2   │  2  │    │ 110 │        Rodrygo│ ПВ     │
│  ФРВ │ Kane          │ 119 │    │  0  │  0    │   2   │  1  │    │ 116 │     João Pedro│ ФРВ    │
└──────┴───────────────┴─────┴────┴─────┴───────┴───────┴─────┴────┴─────┴───────────────┴────────┘
                                    ▲     ▲         ▲     ▲
                                   ГОЛЫ АССИСТЫ   АССИСТЫ ГОЛЫ
                                   (Г)   (А)       (А)   (Г)
                                    |     |         |     |
                           [Предпоследняя] [Крайняя] [Крайняя] [Вторая от центра]

⚠️ ПРАВИЛО ЧТЕНИЯ ИМЕН И НУЛЕЙ:
- ЧИТАЙ СТРОГО ТЕ ИМЕНА, КОТОРЫЕ НАПИСАНЫ В КОЛОНКЕ «ИГРОКИ» (PLAYERS) ДЛЯ ДАННОЙ СТРОКИ!
- Игнорируй иконки капитана или бейджи (короны 👑, значки C, мячики ⚽) рядом с фамилией игрока — извлекай чистое имя.
- ⚠️ КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО добавлять игрока в ассисты, если в его колонке А/A стоит 0! Даже если он забил гол!
- ⚠️ КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО добавлять игрока в голы, если в его колонке Г/G стоит 0!
- ⚠️ Если у игрока в обеих колонках стоят нули (0 0), он НЕ ДОЛЖЕН попадать ни в голы, ни в ассисты!

3. **ЛЕВАЯ ПОЛОВИНА (LEFT SIDE — ЛЕВАЯ КОМАНДА):**
   - Порядок столбцов: `ПОЗ/POS` | `ИГРОКИ/PLAYERS` | `ОБЩ/OVR` | `ИС/PS` | `Г/G` (Голы) | `А/A` (Ассисты)
   - Имена игроков левой команды находятся в ЛЕВОЙ КОЛОНКЕ (слева от ОБЩ/OVR).
   - Столбец `Г` / `G` (Голы) идет ПЕРВЫМ из двух правых цифр (предпоследняя колонка левой таблицы).
   - Столбец `А` / `A` (Ассисты) идет ВТОРЫМ (крайняя правая колонка левой таблицы, ближе к центру).
   - Примеры для левой стороны:
     • `Steijn ... 2 0`: Г=2, А=0 -> Steijn забил 2 гола, 0 ассистов. (В `left_goals` 2 раза, в `left_assists` НЕТ).
     • `Guirassy ... 2 1`: Г=2, А=1 -> Guirassy забил 2 гола, 1 ассист.
     • `Sterling ... 1 2`: Г=1, А=2 -> Sterling забил 1 гол, 2 ассиста.

4. **ПРАВАЯ ПОЛОВИНА (RIGHT SIDE — ПРАВАЯ КОМАНДА):**
   - ⚠️ ВНИМАНИЕ: ЗЕРКАЛЬНЫЙ ПОРЯДОК СТОЛБЦОВ!
   - Порядок столбцов: `А/A` (Ассисты) | `Г/G` (Голы) | `ИС/PS` | `ОБЩ/OVR` | `ИГРОКИ/PLAYERS` | `ПОЗ/POS`
   - Имена игроков правой команды находятся в ПРАВОЙ КОЛОНКЕ (СПРАВА ОТ ОБЩ/OVR, ближе к правому краю)!
   - ⚠️ СТОЛБЕЦ `А` (Ассисты) ИДЕТ ПЕРВЫМ СЛЕВА В ПРАВОЙ ТАБЛИЦЕ (крайняя левая колонка правой таблицы, ближе к центру)!
   - ⚠️ СТОЛБЕЦ `Г` (Голы) ИДЕТ ВТОРЫМ (перед столбцом ОБЩ/OVR)!
   - ПРИМЕРЫ РАЗБОРА СТРОК ПРАВОЙ КОМАНДЫ (КРИТИЧЕСКИ ВАЖНО):
     • `0  0  ... Christie`: А=0, Г=0 -> 0 голов, 0 ассистов.
     • `0  0  ... Ríos`:     А=0, Г=0 -> 0 голов, 0 ассистов.
     • `0  1  ... Rafa`:     А=0 (колонка А), Г=1 (колонка Г) -> Rafa забил 1 ГОЛ, но 0 АССИСТОВ! (В `right_goals` 1 раз, в `right_assists` НЕТ!).
     • `2  2  ... Rodrygo`:  А=2 (колонка А), Г=2 (колонка Г) -> Rodrygo забил 2 ГОЛА и сделал 2 АССИСТА! (В `right_goals` 2 раза, в `right_assists` 2 раза).
     • `2  1  ... João Pedro`: А=2 (колонка А), Г=1 (колонка Г) -> João Pedro забил 1 ГОЛ и сделал 2 АССИСТА! (В `right_goals` 1 раз, в `right_assists` 2 раза).

5. **ОБЯЗАТЕЛЬНЫЙ МАТЕМАТИЧЕСКИЙ ЛИМИТ АССИСТОВ (ЗАКОН ФУТБОЛА):**
   - ⚠️ Общее количество ассистов команды НЕ МОЖЕТ превышать количество забитых ею голов (счёт команды)!
   - `len(left_assists) <= left_score`
   - `len(right_assists) <= right_score`
   - Если Бенфика забила 4 гола (`right_score = 4`), у неё в `right_assists` может быть МАКСИМУМ 4 ассиста (например, Rodrygo (2), João Pedro (2) = 4). 5-го ассиста быть НЕ МОЖЕТ!
   - Если Фейеноорд забил 5 голов (`left_score = 5`), у него в `left_assists` может быть МАКСИМУМ 5 ассистов!

6. **ОБРАБОТКА ДВУХ СКРИНШОТОВ ОДНОЙ ТАБЛИЦЫ (ПРИ ПРОКРУТКЕ/СКРОЛЛЕ):**
   - Если прислано 2 скриншота одной игры (верхняя и нижняя часть состава), один и тот же игрок может попасть на оба скриншота на стыке (например, `Diomande 1 0` или `Rodrygo 2 2`).
   - НЕ ДУБЛИРУЙ ЕГО! Это одна и та же строка одного матча — учитывай её ровно 1 раз!

---

### ЭТАП 3: ОПРЕДЕЛЕНИЕ МАТЧЕЙ (ОДИН, НЕСКОЛЬКО ИЛИ ИЗ ТЕКСТА ПОДПИСИ)

- **РАЗНЫЕ МАТЧИ** (например, Игра 1 со счётом 3-0 и Игра 2 со счётом 4-2): обработай каждый матч отдельно и верни их в массиве `matches`.
- **ОДИН МАТЧ** (например, два скриншота одной игры: верх и низ таблицы): объедини строки в один объект в массиве `matches`.
- ⚠️ **МАТЧИ, ОПИСАННЫЕ В ТЕКСТЕ ПОДПИСИ**:
  Если в тексте подписи пользователя описан дополнительный матч (например: «3 матч в пользу Бенфики 1:2, Голы Родриго, Жоау Педро, Гол Браги Рикардо Орта»), ОБЯЗАТЕЛЬНО извлеки его и добавь отдельным объектом в массив `matches`!

---

### ЭТАП 4: ФОРМАТ ОТВЕТА

⚠️ КРИТИЧЕСКИ ВАЖНО: В массивах left_goals, right_goals, left_assists, right_assists количество элементов ОБЯЗАНО строго равняться числу в соответствующей колонке таблицы (Г или А)!
- Если у игрока в колонке А стоит 2 — ровно 2 раза в массиве ассистов (например: ["Igor Paixão", "Igor Paixão"]).
- Если у игрока в колонке А стоит 0 — его НЕ ДОЛЖНО быть в массиве ассистов.
- Если у игрока в колонке Г стоит 2 — ровно 2 раза в массиве голов (например: ["Rodrygo", "Rodrygo"]).
- Если у игрока в колонке Г стоит 0 — его НЕ ДОЛЖНО быть в массиве голов.

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
      "right_goals": ["Diomande", "Leweling"],
      "left_assists": ["Vitor Carvalho", "Leonardo Lelo"],
      "right_assists": ["Ziyech", "Leweling"]
    }
  ]
}
"""

def clean_json_response(raw_text: str) -> str:
    """Очищает ответ модели от возможных markdown-тегов ```json ... ``` и извлекает чистый JSON."""
    text = raw_text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r'(\{[\s\S]*\})', text)
    if match:
        return match.group(1).strip()
    return text.strip()


def validate_and_sanitize_match_events(m: dict) -> None:
    """
    Enforces deterministic football laws and eliminates duplicate/hallucinated assists:
    1. Total assists for a team cannot exceed total goals (team score).
    2. If len(assists) > team_score, prioritize pure assist-makers and remove goal-scorers who had duplicate assists hallucinated.
    """
    left_score = int(m.get("left_score", 0))
    right_score = int(m.get("right_score", 0))
    
    # Sanitize left assists
    if left_score >= 0 and len(m.get("left_assists", [])) > left_score:
        excess = len(m["left_assists"]) - left_score
        dual_players = [p for p in m["left_assists"] if p in m.get("left_goals", [])]
        for p in dual_players:
            if excess <= 0:
                break
            m["left_assists"].remove(p)
            excess -= 1
        while len(m["left_assists"]) > left_score:
            m["left_assists"].pop()

    # Sanitize right assists
    if right_score >= 0 and len(m.get("right_assists", [])) > right_score:
        excess = len(m["right_assists"]) - right_score
        dual_players = [p for p in m["right_assists"] if p in m.get("right_goals", [])]
        for p in dual_players:
            if excess <= 0:
                break
            m["right_assists"].remove(p)
            excess -= 1
        while len(m["right_assists"]) > right_score:
            m["right_assists"].pop()


def _check_proxy_alive(proxy_url: str) -> bool:
    """Check if proxy host:port is accepting connections."""
    import socket
    from urllib.parse import urlparse
    try:
        parsed = urlparse(proxy_url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            return False
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except Exception:
        return False
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

                        # Clean and sanitize player names
                        m["left_goals"] = [clean_player_name(p) for p in m["left_goals"] if clean_player_name(p)]
                        m["right_goals"] = [clean_player_name(p) for p in m["right_goals"] if clean_player_name(p)]
                        m["left_assists"] = [clean_player_name(p) for p in m["left_assists"] if clean_player_name(p)]
                        m["right_assists"] = [clean_player_name(p) for p in m["right_assists"] if clean_player_name(p)]

                        # Enforce mathematical football laws (assists <= goals)
                        validate_and_sanitize_match_events(m)

                        # If assists are present, it is NEVER a single timeline!
                        if len(m["left_assists"]) > 0 or len(m["right_assists"]) > 0:
                            m["is_single_timeline"] = False

                        # Cross-check and side swap if goals were put on the wrong side
                        if int(m["left_score"]) > 0 and int(m["right_score"]) == 0:
                            if len(m["left_goals"]) == 0 and len(m["right_goals"]) > 0:
                                m["left_goals"], m["right_goals"] = m["right_goals"], m["left_goals"]
                            if len(m["left_assists"]) == 0 and len(m["right_assists"]) > 0:
                                m["left_assists"], m["right_assists"] = m["right_assists"], m["left_assists"]
                        elif int(m["right_score"]) > 0 and int(m["left_score"]) == 0:
                            if len(m["right_goals"]) == 0 and len(m["left_goals"]) > 0:
                                m["left_goals"], m["right_goals"] = m["right_goals"], m["left_goals"]
                            if len(m["right_assists"]) == 0 and len(m["left_assists"]) > 0:
                                m["left_assists"], m["right_assists"] = m["right_assists"], m["left_assists"]

                        # Compatibility aliases
                        m["home_score"] = int(m["left_score"])
                        m["away_score"] = int(m["right_score"])
                        m["side1_goals"] = m["left_goals"]
                        m["side2_goals"] = m["right_goals"]
                        m["side1_assists"] = m["left_assists"]
                        m["side2_assists"] = m["right_assists"]

                        if int(m["left_score"]) == 0 and len(m["left_goals"]) > 0:
                            m["left_score"] = len(m["left_goals"])
                            m["home_score"] = len(m["left_goals"])

                        if int(m["right_score"]) == 0 and len(m["right_goals"]) > 0:
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