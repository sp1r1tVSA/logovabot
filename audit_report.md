# 🔍 Полный профессиональный Code Review — logovobot

> Версия аудита: 2026-07-26 | Аудитор: Senior Python Dev (10+ лет)

---

## 📊 ШАГ 2 — SCORECARD

| Категория | Оценка | Ключевая проблема |
|---|---|---|
| Telegram API & Ошибки | **7 / 10** | `query.answer()` пропускается в 10+ хендлерах при раннем возврате |
| Безопасность & Валидация | **7 / 10** | SQL f-string в `database.py:119`, deadline из пользователя не экранируется в HTML |
| Асинхронность & Производительность | **5 / 10** | **Критично**: все вызовы `database.*` в `async def` блокируют Event Loop без `asyncio.to_thread` |
| База данных & Транзакции | **8 / 10** | Нет индексов на `player1_id/player2_id`, N+1 в `get_player_stats` |
| Архитектура & Читаемость | **6 / 10** | Функции до 268 строк, `import datetime` внутри функции, магические числа |
| FSM & Пользовательский опыт | **7 / 10** | Нет `conversation_timeout`, состояние висит вечно; `save_guest_dispute_photo` ждёт только фото |

---

## 🔴 КРИТИЧЕСКИЕ БАГИ

### 1. Все DB-вызовы блокируют Event Loop (SQLite синхронный)

**Файлы:** `handlers/cabinet.py`, `handlers/admin.py`, `handlers/base.py`, `handlers/chat.py`

SQLite в Python — синхронная библиотека. Каждый `database.get_match(...)`, `database.get_standings()` и т.д. вызывается **прямо в `async def`** без `asyncio.to_thread`. Это замораживает весь event loop (а значит — всех пользователей бота) на время выполнения SQL-запроса.

**Пример проблемного кода (cabinet.py:1314):**
```python
async def save_report_photo(update, context):
    match = database.get_match(match_id)  # ❌ BLOCKING — freezes bot
    ...
```

**Исправление:**
```python
async def save_report_photo(update, context):
    match = await asyncio.to_thread(database.get_match, match_id)  # ✅ non-blocking
    ...
```

> **Масштаб:** Найдено 50+ синхронных DB-вызовов в async-функциях. 
> **Правильное долгосрочное решение** — перейти на `aiosqlite` или обернуть все `database.*` функции в `asyncio.to_thread` в слое хендлеров.

---

### 2. `urllib.request.urlopen` блокирует Event Loop (блокирующий HTTP)

**Файлы:** `ai_recognizer.py:118`, `ai_chat.py:83`

```python
# ai_recognizer.py:118 — ❌ BLOCKING HTTP внутри asyncio.to_thread, но...
with urllib.request.urlopen(req, timeout=20) as response:  # блокирует поток 20 сек
```

`ai_recognizer` вызывается через `asyncio.to_thread` — это **правильно**, он не блокирует event loop.
Но `ai_chat.generate_chat_reply` тоже вызывается через `to_thread` — тоже ок.

> ⚠️ **Но**: при 30+ секундных таймаутах и 16 игроках одновременно — пул потоков может кончиться. Рекомендуется `httpx.AsyncClient` в продакшне.

---

### 3. `query.answer()` пропускается на ранних возвратах

**Файл:** `handlers/admin.py`, строки 508, 540, 555, 589, 651, 733, 859, 887, 896, 905

Паттерн:
```python
async def some_handler(update, context):
    query = update.callback_query
    if not query or not is_admin(query.from_user.id): return  # ❌ query.answer() НЕ вызван!
    await query.answer()
```

Telegram ждёт `query.answer()` максимум **10 секунд**. Если он не вызван — клиент показывает "бесконечную загрузку", потом ошибку. При частых нажатиях это накапливается.

**Исправление:**
```python
async def some_handler(update, context):
    query = update.callback_query
    if not query:
        return
    await query.answer()  # ✅ ВСЕГДА первым делом
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Доступ запрещён", show_alert=True)
        return
```

---

### 4. SQL f-string — потенциальная SQL-инъекция (низкий риск, но нарушение принципов)

**Файл:** `database.py:119`

```python
# database.py:119
cursor.execute(f"ALTER TABLE matches ADD COLUMN {col[0]} {col[1]}")
```

Здесь `col[0]` и `col[1]` — жёстко заданы в коде (не из пользовательского ввода), поэтому реального риска инъекции нет. Но это нарушает принцип "никаких f-строк в SQL" и создаёт прецедент для будущих ошибок.

**Исправление:**
```python
SAFE_COLUMNS = [
    ("photo_id", "TEXT"),
    ("dispute_photos", "TEXT"),
    ("reported_by", "INTEGER"),
    ("proposed_time", "TEXT"),
    ("proposed_by", "INTEGER"),
    ("time_status", "TEXT DEFAULT 'none'"),
]
for col_name, col_type in SAFE_COLUMNS:
    try:
        cursor.execute(f"ALTER TABLE matches ADD COLUMN {col_name} {col_type}")
        # col_name/col_type — константы из кода, не из ввода — безопасно
    except sqlite3.OperationalError:
        pass
```
> Добавить комментарий-обоснование почему f-string тут допустим.

---

### 5. `time.sleep(1)` внутри функции, вызываемой из `asyncio.to_thread`

**Файл:** `ai_recognizer.py:134`

```python
except urllib.error.HTTPError as e:
    if e.code == 429:
        time.sleep(1)  # ⚠️ блокирует поток на 1 сек при каждом 429
```

Поскольку `recognize_match_screenshots_bytes` вызывается через `asyncio.to_thread`, `time.sleep` блокирует именно **рабочий поток**, а не event loop. Это допустимо, но при множестве одновременных AI-вызовов — занимает все потоки пула. Лимит пула по умолчанию — 32 потока.

---

## 🟡 ПРЕДУПРЕЖДЕНИЯ

### 6. Нет `conversation_timeout` ни в одном `ConversationHandler`

**Файл:** `handlers/__init__.py`

Все 9 ConversationHandler'ов (reg_conv, score_report_conv, dispute_conv, admin_*_conv...) созданы без `conversation_timeout`. Если пользователь начал регистрацию, нажал «Отмена» через главное меню (не через FSM-fallback) — его состояние зависает **навсегда**.

```python
# ❌ Текущий код — нет таймаута
reg_conv = ConversationHandler(
    entry_points=[...],
    states={...},
    fallbacks=[...],
    # conversation_timeout=None  ← висит вечно
)
```

**Исправление:**
```python
reg_conv = ConversationHandler(
    entry_points=[...],
    states={...},
    fallbacks=[...],
    conversation_timeout=300,  # 5 минут — сброс состояния
)
```

---

### 7. `import datetime` внутри функции (плохая практика)

**Файл:** `handlers/admin.py:605`

```python
# admin.py:605 — импорт в середине файла, внутри модуля, но вне функции
import datetime
```

Это находится не внутри функции, но в середине файла — должен быть наверху с остальными импортами.

---

### 8. Нет индексов на `player1_id`, `player2_id`, `round_number`

**Файл:** `database.py`

Большинство запросов фильтруют по `player1_id`, `player2_id`, `round_number`, `status` — но индексов нет. При 30 турах × 16 игроков = 240 матчей это не критично сейчас, но при масштабировании создаст проблемы.

```sql
-- Добавить в init_db():
CREATE INDEX IF NOT EXISTS idx_matches_player1 ON matches(player1_id);
CREATE INDEX IF NOT EXISTS idx_matches_player2 ON matches(player2_id);
CREATE INDEX IF NOT EXISTS idx_matches_round ON matches(round_number);
CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status);
CREATE INDEX IF NOT EXISTS idx_match_events_match ON match_events(match_id);
```

---

### 9. `deadline_text` из пользователя вставляется в HTML без экранирования

**Файл:** `handlers/admin.py:637`, `handlers/cabinet.py:531`

```python
# admin.py:637
f"🟢 **Открыт {round_number}-й Тур!**\n\n🕒 Дедлайн: {deadline_text}\n\n..."
# parse_mode: "Markdown" — deadline_text валидирован через strptime, риск минимален
```

Здесь `deadline_text` проходит через `strptime` перед сохранением — то есть содержит только `DD.MM.YYYY HH:MM`, HTML/Markdown-спецсимволов там нет. Это **безопасно**, но рекомендуется явно экранировать для ясности намерений.

---

### 10. Race Condition при двойном нажатии "Ввести результат"

**Файл:** `handlers/cabinet.py`

Быстрое двойное нажатие на "✅ Всё верно" может создать два параллельных вызова `cb_confirm_ai_final`. Оба вызовут `database.confirm_and_finalize_match(match_id, ...)` — дублирование событий в `match_events`.

**Исправление:** Проверять статус матча перед записью:
```python
m = await asyncio.to_thread(database.get_match, match_id)
if m['status'] != 'pending':
    await query.answer("Результат уже внесён!", show_alert=True)
    return
```

---

### 11. `register_all_handlers` — 268 строк, слишком большая функция

**Файл:** `handlers/__init__.py:186-454`

Функция `register_all_handlers` — 268 строк. Нарушает принцип единственной ответственности. Сложно читать и тестировать.

**Рефакторинг:** Разбить на `_register_user_handlers`, `_register_admin_handlers`, `_register_cabinet_handlers`.

---

### 12. Магические строки callback_data разбросаны по коду

Строки вроде `"main_menu"`, `"cabinet_my_matches"`, `"admin_main_menu"` встречаются в десятках мест. Опечатка — и хендлер молча перестанет работать.

**Исправление:** Завести `constants.py`:
```python
# constants.py
CB_MAIN_MENU = "main_menu"
CB_CABINET = "menu_cabinet"
CB_MY_MATCHES = "cabinet_my_matches"
```

---

### 13. `get_player_stats` делает 5 отдельных SQL-запросов вместо 1

**Файл:** `database.py:172`

```python
def get_player_stats(telegram_id):
    # Wins  — SELECT
    # Draws — SELECT  
    # Losses — SELECT
    # Goals Scored — SELECT
    # Goals Conceded — SELECT
```

**Исправление** — один запрос:
```sql
SELECT
  SUM(CASE WHEN (player1_id=? AND player1_score>player2_score) OR (player2_id=? AND player2_score>player1_score) THEN 1 ELSE 0 END) AS wins,
  SUM(CASE WHEN player1_score=player2_score THEN 1 ELSE 0 END) AS draws,
  SUM(CASE WHEN (player1_id=? AND player1_score<player2_score) OR (player2_id=? AND player2_score<player1_score) THEN 1 ELSE 0 END) AS losses,
  ...
FROM matches WHERE status='confirmed' AND (player1_id=? OR player2_id=?)
```

---

## 🟢 УЛУЧШЕНИЯ (Best Practices)

1. **Перейти на `aiosqlite`** — нативный async SQLite. Все `database.*` станут `async def`, вызываются с `await`. Полностью убирает проблему #1.

2. **Добавить `GEMINI_MODEL` валидацию при старте** — если `GEMINI_MODEL` не в списке допустимых, логировать предупреждение.

3. **`logger.exception()` вместо `logger.error(f"... {e}")`** — `exception()` автоматически добавляет полный traceback:
   ```python
   # ❌ Текущий подход
   except Exception as e:
       logger.error(f"Failed to confirm match: {e}")
   
   # ✅ Лучше
   except Exception:
       logger.exception("Failed to confirm match")
   ```

4. **Вынести строковые шаблоны уведомлений** — `instruction_text` и подобные блоки дублируются в `notify_players_rounds_opened` и `send_round_reminders`. Вынести в отдельный модуль `templates.py`.

5. **Добавить `MATCH_MAX_SCORE = 99` и `INPUT_MAX_LEN = 200`** в `config.py` — ограничить ввод в `save_custom_match_time`.

---

## 🛠️ ТОП-3 ПРИОРИТЕТНЫХ РЕФАКТОРИНГА

---

### Рефакторинг #1 — Обёртка для DB-вызовов (устраняет проблему #1)

**Проблема:** 50+ блокирующих `database.*` вызовов в async-контексте.

**ДО (handlers/chat.py:35-95):**
```python
async def handle_ai_chat(update, context):
    user_data = database.get_user(user_id)           # ❌ блокирует loop
    standings = database.get_standings()              # ❌
    top_scorers = database.get_top_scorers(limit=15) # ❌
    ...
```

**ПОСЛЕ:**
```python
import asyncio

async def handle_ai_chat(update, context):
    user_data, standings, top_scorers = await asyncio.gather(
        asyncio.to_thread(database.get_user, user_id),
        asyncio.to_thread(database.get_standings),
        asyncio.to_thread(database.get_top_scorers, 15),
    )
    ...
```

> **Бонус:** `asyncio.gather` запускает все 3 DB-запроса параллельно — AI-контекст собирается в ~3x быстрее.

---

### Рефакторинг #2 — Декоратор `@admin_only` (устраняет проблему #3)

**Проблема:** 10+ хендлеров с паттерном `if not query or not is_admin(...)`.

**ДО (admin.py:116-120):**
```python
async def admin_generate_matches_confirm(update, context):
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()
```

**ПОСЛЕ:**
```python
# handlers/base.py — добавить декоратор
from functools import wraps

def admin_only(func):
    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        query = update.callback_query
        user = update.effective_user
        if query:
            await query.answer()  # ВСЕГДА отвечаем
        if not user or not is_admin(user.id):
            if query:
                await query.answer("⛔ Доступ запрещён", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

# Использование:
@admin_only
async def admin_generate_matches_confirm(update, context):
    # query.answer() уже вызван декоратором
    ...
```

---

### Рефакторинг #3 — Индексы + `conversation_timeout` (устраняет #8 + #6)

**ДО (database.py:36, handlers/__init__.py:193):**
```python
def init_db():
    # ... CREATE TABLE ...
    # Нет индексов

ConversationHandler(
    states={...},
    fallbacks=[...],
    # нет timeout
)
```

**ПОСЛЕ:**
```python
# database.py — в конце init_db(), перед logger.info(...)
cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_p1 ON matches(player1_id)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_p2 ON matches(player2_id)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_round ON matches(round_number)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_match ON match_events(match_id)")

# handlers/__init__.py — все ConversationHandler'ы:
reg_conv = ConversationHandler(
    entry_points=[...],
    states={...},
    fallbacks=[...],
    allow_reentry=True,
    conversation_timeout=300,  # ✅ 5 минут — автосброс
)
```

---

## 📈 ИТОГ

| Приоритет | Задача | Усилие |
|---|---|---|
| 🔴 P0 | Обернуть DB в `asyncio.to_thread` (или перейти на aiosqlite) | 4-6ч |
| 🔴 P0 | Добавить `await query.answer()` на все ранние возвраты | 1ч |
| 🟡 P1 | Добавить `conversation_timeout=300` ко всем ConversationHandler | 20мин |
| 🟡 P1 | Добавить индексы в `init_db()` | 15мин |
| 🟡 P1 | Декоратор `@admin_only` | 1ч |
| 🟢 P2 | Оптимизировать `get_player_stats` (1 запрос вместо 5) | 30мин |
| 🟢 P2 | Константы для callback_data | 2ч |
| 🟢 P2 | `logger.exception()` вместо `logger.error(f"...")` | 1ч |

**Общая оценка кода: 7/10** — архитектура продуманная, безопасность хорошая, HTML-экранирование применяется. Главная уязвимость — синхронные DB-вызовы в async-контексте. Исправление P0-задач займёт ~6-8 часов и значительно повысит стабильность бота под нагрузкой.
