# FULL_BOT_AUDIT.md — Logovo.bet

**Дата аудита:** 2026-09-09
**Ветка / коммит:** `main` @ `4c18ded`
**Метод:** статический аудит реального кода и execution paths + прогон существующего test suite.
**Ничего не исправлено, не удалено, не переписано. БД не изменялась. Миграции не создавались.**

Легенда достоверности:
- **FACT** — подтверждено чтением кода, указано точное место.
- **INFERENCE** — вывод из совокупности фактов, не проверялся исполнением.
- **RISK** — потенциальная проблема, зависящая от данных/нагрузки в проде.
- **THEORETICAL** — проблема существует в коде, но пока недостижима на текущих данных/конфигурации.

---

## 1. EXECUTIVE SUMMARY

Logovo.bet — зрелая, работающая система. 105 production-модулей Python (~45 000 строк), 60 таблиц SQLite, 102 REST-эндпоинта, 87 файлов тестов. **Test suite полностью зелёный: 634 passed, 3 subtests passed, 0 failed (42.8 s).** Маршрутизация Telegram-кнопок чистая: 216 уникальных `callback_data` против 159 зарегистрированных паттернов — **0 несопоставленных**. Мини-приложение не содержит мёртвых кнопок. Захардкоженных секретов нет, `.env` и `league.db` корректно в `.gitignore`.

Главный вопрос аудита — **«можно ли поставить после закрытия линии?»** — имеет ответ **ДА**. Найдено 4 подтверждённых обхода (раздел 10). Причина одна и системная: **в системе два независимых представления «линии» — legacy `bet_markets` и реляционные `markets`/`market_selections` — а операция «закрыть линию» гасит только первое.** Telegram-путь читает legacy-таблицу и корректно блокируется; Mini App читает реляционную и продолжает принимать ставки.

Второе системное расхождение: **целевая бизнес-модель («ставки ДО открытия тура, открытие тура ЗАКРЫВАЕТ линию») в коде не реализована вообще.** Реализована обратная модель: линия открывается ВМЕСТЕ с туром (`update_round_status(is_open=True)` выставляет `bets_open = 1`) и дополнительно преоткрывает линию для тура N+1. Приём ставок разрешён, пока `is_open = 1`.

Третья находка того же класса: `init_db()` при **каждом старте бота** принудительно сбрасывает feature-флаг `betting_market` в `'public'`.

Дополнительно: 7 LIVE-эндпоинтов REST API не имеют никакой аутентификации, а любое исключение внутри RiskEngine приводит к молчаливому полному обходу риск-контроля.

**Оценка:** ядро (расчёт ставок, кошелёк, идемпотентность, settlement, initData HMAC) написано аккуратно и защищено. Проблемы сосредоточены на границе «состояние тура ↔ состояние рынка» и в изоляции по division/season.

---

## 2. АРХИТЕКТУРА

### 2.1 Инвентарь

| Слой | Файлы | Объём |
|---|---|---|
| Entrypoint | `main.py` | 121 стр. |
| Config | `config.py` | всё чтение env |
| Storage | `database.py` | ~8.7k стр., 60 таблиц, 66 индексов |
| Handlers (Telegram) | `handlers/` — 10 модулей | `admin.py` 6012, `cabinet.py` 3619, `base.py` 773, `topic_management.py` 688, `drafts.py` 645, `text_commands.py` 621, `betting.py` 557, `chat.py` 299 |
| Services | `services/` — 39 модулей + `ai/`, `graphics/`, `sports/`, `intelligence/` |
| REST API | `api/` — `server.py`, `auth.py`, 13 × `routes_*.py`, 102 маршрута |
| Frontend | `web/` — `index.html` (458), `ui.js` (79 KB), `app.js` (31 KB), `store.js`, `api.js`, `effects.js`, `tg.js` |
| Tests | `tests/` — 87 файлов |

### 2.2 Карта потока

```
USER
 ├─ TELEGRAM ────────────────────────────────────────────────┐
 │   Update → TypeHandler(global_lockdown_guard) group=-1     │
 │          → group-tracking group=1                          │
 │          → Command/Callback/Conversation handlers          │
 │          → [catch-all] MessageHandler → services/ai/ai_chat│
 │          → [catch-all] CallbackQueryHandler(".*")          │
 │                                                            │
 └─ MINI APP (WebApp) ────────────────────────────────────────┤
     HTTPS → aiohttp (api/server.py, ТОТ ЖЕ event loop)       │
           → cors_middleware → lockdown_middleware            │
           → routes_*.handle_* → api/auth.get_authenticated_user
                                                              │
                          ┌───────────────────────────────────┘
                          ▼
                    SERVICE LAYER
   betting_engine · odds_engine · risk_engine · betting_limits
   exposure_service · cashout_engine · market_settler
   settlement_engine · season_progression · notification_service
                          │
                          ▼
                    database.py  (единственный владелец SQL)
                    transaction() — реентрантный контекст
                          │
                          ▼
                    SQLite (WAL, FK=ON, busy_timeout=10s)

EXTERNAL: Gemini REST (aiohttp) · api-sports.io (не подключён в проде)
```

**FACT.** API-сервер стартует из `post_init` (`main.py:21-26`) внутри try/except — падение API не мешает боту. Изоляция соблюдена, как предписывает `CLAUDE.md`.

**FACT.** aiohttp работает в **том же** event loop, что и PTB. Любая синхронная блокировка в API-хендлере тормозит и Telegram-бота. Это реализовано корректно почти везде (`asyncio.to_thread`), кроме `api/routes_markets.py` (см. API-02).

### 2.3 Фоновые задачи (`main.py:56-88`)

| Job | Интервал | Назначение |
|---|---|---|
| `job_check_deadlines_and_remind` | 1800 s | напоминания по дедлайнам |
| `job_post_debts_to_warns` | 12 h | дайджест долгов в ПРЕДЫ |
| `job_debt_lifecycle_tracker` | 1800 s | авто-варны / авто-кик |
| `sync_live_provider_job` | 45 s | синк внешнего провайдера |
| `sync_intelligence_cache_job` | 300 s | пересчёт intelligence |
| `process_notification_queue_job` | 15 s | очередь уведомлений |
| `settle_finished_bets_job` | 60 s | расчёт ставок |
| `job_post_round_preview` | 600 s | превью тура в АНАЛИТИКА |
| `job_post_round_digest` | 900 s | итоги тура |

Каждая группа обёрнута в свой try/except — падение одной подсистемы не роняет остальные. **Соответствует конвенции.**

---

## 3. TELEGRAM BOT

### 3.1 Карта кнопок и обработчиков

**FACT.** Программный обход всех `callback_data`, эмитируемых в `handlers/*.py` и `services/**/*.py`, сопоставленный со всеми `CallbackQueryHandler(pattern=...)`, зарегистрированными в `handlers/__init__.py`:

```
зарегистрированных паттернов:            159
уникальных эмитируемых callback_data:    216
НЕСОПОСТАВЛЕННЫХ callback_data:            0
```

**Мёртвых кнопок в Telegram-боте нет.** Разница 216 vs 159 — следствие того, что один паттерн (`^admin_manage_round_\d+$` и т. п.) покрывает множество литералов.

### 3.2 Порядок регистрации

**FACT** (`handlers/__init__.py:711-795`):

| Строка | Регистрация | Группа |
|---|---|---|
| 776 | `TypeHandler(Update, global_lockdown_guard)` | **-1** |
| 778 | группа-трекинг | 1 |
| 719 | `admin_toggle_round_bets`, `^admin_bets_(open\|close)_round_\d+$` | 0 |
| … | user → cabinet/admin FSM → betting | 0 |
| 792 | `MessageHandler((TEXT\|VOICE) & ~COMMAND, handle_ai_chat)` | 0 |
| **795** | `CallbackQueryHandler(handle_placeholders, pattern=".*")` | 0, **последний** |

Порядок в точности соответствует предписанию `CLAUDE.md`. Ничего не зарегистрировано после AI-catch-all. **Orphan / duplicate / unreachable handlers не обнаружены.**

### 3.3 `query.answer()`

**FACT.** Catch-all `handle_placeholders` корректен:

```python
if query.data == "noop": await query.answer(); return
await query.answer("Эта функция находится в разработке.", show_alert=True)
```

Декоратор `admin_only` (`handlers/base.py`) отвечает на CallbackQuery **до** проверки прав, поэтому отказ не оставляет «часики». Просмотренные хендлеры (`admin_manage_round`, `admin_toggle_round_bets`, `cb_bet_*`) вызывают `query.answer()` на всех ветках, включая ошибочные.

**Одно исключение — не баг:** `_render_round_management` намеренно не отвечает («вызывающий уже это сделал», `handlers/admin.py:2039`). Оба вызывающих (`admin_manage_round:2032`, `admin_toggle_round_bets:2097-2099`) действительно отвечают.

### 3.4 Главное меню

`get_main_inline_keyboard` (`handlers/base.py`) — единая точка сборки. Все пункты имеют обработчики. **Кнопка «Поддержка» (`show_support`) — заглушка «🚧 В разработке»** — намеренная, не мёртвая (см. UX-01, P3).

---

## 4. ОСНОВНЫЕ ПОЛЬЗОВАТЕЛЬСКИЕ ПОТОКИ

| Поток | Точка входа | Статус |
|---|---|---|
| `/start` → главное меню | `handlers/base` | **WORKING** |
| Дивизионы → таблица / бомбардиры / ассистенты | `show_divisions_list` → `show_division_*` | **WORKING** |
| Сдача результата (OCR) | `handlers/drafts` → `services/ai/ai_recognizer` | **WORKING** |
| Подтверждение матча | `confirm_and_finalize_match` → `settle_match_bets` | **WORKING** |
| Ставка через Telegram | `cmd_bet_hub` → `cb_bet_*` → `place_user_bet` | **PARTIAL** (ранняя линия видна, ставка отклоняется — LINE-01) |
| Ставка через Mini App | `POST /api/predictions` → `place_user_bet` | **PARTIAL** (принимает после закрытия линии — BET-01) |
| Кошелёк / бонус / история | `bet_claim_bonus`, `bet_my_history`, `/api/wallet` | **WORKING** |
| Cashout | `/api/predictions/{id}/cashout` → `execute_cashout` | **WORKING** |
| Админ: открыть/закрыть тур | `admin_open_round_N` / `admin_close_round_N` | **PARTIAL** (глобально по всем дивизионам — DIV-01) |
| Админ: открыть/закрыть линию | `admin_bets_(open\|close)_round_N` | **BROKEN** (закрытие не действует на Mini App — BET-01) |
| LIVE-центр в Mini App | — | **UNREACHABLE** (удалён из фронтенда, эндпоинты живы) |

---

## 5. СЕЗОНЫ

**FACT.** Таблица `seasons`, `status CHECK IN ('draft','active','finished','archived')`. Строка id=1 засеяна как `'active'`. Активный сезон резолвится через `get_active_season()`; при отсутствии — фолбэк `s_id = 1`.

**FACT — изоляция нарушена в двух местах горячего пути:**

`database.py:6676` (`place_user_bet`):
```sql
SELECT is_open, deadline FROM rounds WHERE division_id = ? AND round_number = ?
```
`services/risk_engine.py:189` — тот же запрос с добавлением `bets_open`.

Обе выборки **не фильтруют по `season_id`**, тогда как `rounds` имеет `UNIQUE(season_id, division_id, round_number)`. При наличии более одного сезона `fetchone()` вернёт строку с наименьшим rowid — то есть **состояние тура из СТАРОГО сезона**.

**FACT.** `get_all_rounds()` (`database.py:4542-4560`) фильтрует по активному сезону, но при пустом результате (строка 4559) **сбрасывает фильтр сезона полностью**.

**FACT.** `get_round_info(round_number)` без `division_id` (`database.py:4356-4359`) выбирает `LIMIT 1` **без `ORDER BY`** — произвольная строка среди всех дивизионов.

**Оценка:** на текущих данных (один активный сезон) это не проявляется — **THEORETICAL** до старта второго сезона, после чего становится **P1** немедленно.

---

## 6. ДИВИЗИОНЫ

**FACT.** `divisions` с уникальным `code` и привязкой `group_chat_id` + `topic_id`. `services/topic_cache.py` кеширует роутинг, перезагружается при регистрации хендлеров. Таблица `division_admins` (PK `division_id + user_id`, FK CASCADE) — корректно.

**FACT — изоляция по дивизиону не соблюдается в управлении турами.** Все вызывающие передают `division_id = None`:

| Файл:строка | Вызов |
|---|---|
| `handlers/admin.py:2094` | `set_round_bets_open(round_number, opening)` |
| `handlers/admin.py:2198` | `update_round_status(...)` |
| `handlers/admin.py:2326` | `update_round_status(...)` |
| `handlers/text_commands.py:350` | `set_round_bets_open(...)` |
| `handlers/text_commands.py:381, 399, 423` | `update_round_status(...)` |

В ветке `division_id is None` (`database.py:4423-4449`, `4519-4522`) все `UPDATE rounds` выполняются **без предиката `division_id`** → изменяется состояние тура N во **всех** дивизионах сразу.

**FACT.** Три оператора `UPDATE bet_markets SET is_active = 0 WHERE tour = ?` (`database.py:4410, 4441, 4527`) также не ограничены дивизионом.

**FACT.** `get_open_betting_tours()` вызывается из `handlers/betting.py:138` без `division_id` — Telegram показывает линию всех дивизионов сразу.

---

## 7. ТУРЫ (ROUNDS)

### 7.1 Схема

`rounds`: `id`, `season_id DEFAULT 1`, `division_id DEFAULT 1`, `round_number`, `is_open`, `deadline`, `UNIQUE(season_id, division_id, round_number)`.
Миграция (`database.py:378-388`) добавляет `bets_open BOOLEAN DEFAULT 0` и `bets_opened_at TEXT`, бэкфилл: `UPDATE rounds SET bets_open = 1 WHERE is_open = 1`.

### 7.2 Два флага, два смысла

| Флаг | Смысл | Кто читает |
|---|---|---|
| `rounds.is_open` | тур открыт **для игры** (сдача результатов) | `place_user_bet:6682`, `risk_engine:201`, `get_open_betting_tours`, вся админка |
| `rounds.bets_open` | линия Logovo.bet открыта | `risk_engine:201`, `get_open_betting_tours`, UI админки |

### 7.3 Фактическая семантика открытия/закрытия

**FACT** (`database.py:4364-4468`, `update_round_status`):
```python
UPDATE rounds SET is_open = ? ...
UPDATE bet_markets SET is_active = 0 WHERE tour = ?
# "Открытие тура для игры всегда открывает и линию; закрытие — закрывает."
UPDATE rounds SET bets_open = ? ...        # тот же 1/0, что и is_open
if is_open:
    generate_round_markets(round_number, ...)
    set_round_bets_open(round_number + 1, True, ...)   # ПРЕОТКРЫТИЕ линии тура N+1
```

**FACT** (`database.py:4470-4539`, `set_round_bets_open`):
```python
UPDATE rounds SET bets_open = ?, bets_opened_at = ? WHERE ...
if not bets_open:
    UPDATE bet_markets SET is_active = 0 WHERE tour = ?
if bets_open:
    generate_round_markets(...)
```
**Не трогает `rounds.is_open`. Не трогает `markets` / `market_selections`.**

### 7.4 Сопоставление с целевой моделью

| Целевое правило | Реализовано? |
|---|---|
| Ставки принимаются **до** открытия тура | Частично: `bets_open` существует, но `place_user_bet` его игнорирует (LINE-01) |
| Открытие тура **закрывает** линию | **НЕТ.** Открытие тура **открывает** линию (`database.py:4453-4457`) |
| После открытия новые ставки запрещены | **НЕТ.** Ставки принимаются именно пока `is_open = 1` |

**Текущая модель — инверсия целевой.** Это не баг реализации, а нереализованное требование. Отмечено как FACT о состоянии, не как дефект кода.

---

## 8. МАТЧИ

### 8.1 Реальные статусы

Требование «не придумывать статусы» соблюдено — ниже только литералы, найденные в коде.

**FACT.** Базовый `CREATE TABLE matches` **не имеет CHECK на status**; `status TEXT NOT NULL DEFAULT 'pending'`. `division_id` / `season_id` добавлены миграцией.

**Статусы, реально записываемые в `matches.status`:**

| Статус | Где пишется | Достижим в проде? |
|---|---|---|
| `pending` | DEFAULT; `reset_match` (`database.py:2573`) | **ДА** |
| `confirmed` | `confirm_and_finalize_match` (2498), `set_technical_result` (2518), `admin_set_match_score` (3123) | **ДА** |
| `cancelled` | `refund_match_bets` (`services/settlement_engine.py:299`) | **ДА** |
| `finished` | параметр по умолчанию `settle_match_predictions` (`settlement_engine.py:47`) — применяется только если текущий статус не `confirmed`/`completed` | **ДА** (краевой) |
| `scheduled`, `live`, `completed`, `postponed` | только `services/live_state_machine.py:146-158` | **НЕТ** — у `transition_match_state` нет ни одного вызывающего в production-коде |
| произвольная строка | `handle_admin_match_correction` (`api/routes_admin_live.py:467-472`) | **ДА** — валидации нет (STATUS-01) |

`open`, `reported`, `disputed`, `suspended`, `voided`, `active` встречаются **только в read-side фильтрах**, никогда не записываются.

`time_status` — отдельная колонка со значениями `proposed` / `accepted` (`database.py:2609, 2618`), к жизненному циклу матча отношения не имеет.

### 8.2 Целостность перехода

**FACT.** `confirm_and_finalize_match` и `set_technical_result` (`database.py:2469-2529`) построены правильно: `raise ValueError` если матча нет; перезапись `match_events`; `UPDATE ... status='confirmed'` с проверкой `if cursor.rowcount != 1: raise RuntimeError`; затем `settle_match_bets(...)` в try/except (падение расчёта не откатывает подтверждение — намеренная развязка, страховка — job каждые 60 s).

---

## 9. СТАВКИ (BETTING)

### 9.1 Все места создания ставок

**FACT.** Точка создания ставки в системе **ровно одна**: `database.place_user_bet` (`database.py:6543-6848`).

| Путь | Вызывающий | idempotency_key |
|---|---|---|
| Telegram | `handlers/betting.py:404` | **нет** |
| Mini App | `api/routes_predictions.py:60` (`POST /api/predictions`) | да |
| Mini App (повтор купона) | `handle_repeat_prediction` | да |

Дублирующих engine-ов создания ставок нет.

### 9.2 Классификация engine-ов

| Модуль | Роль | Классификация |
|---|---|---|
| `services/betting_engine.py` | расчёт 7 кэфов, `generate_round_markets` → пишет **только legacy `bet_markets`** | **CURRENT (legacy schema)** |
| `services/odds_engine.py` | владелец `markets`/`market_selections`, `generate_match_markets`, `set_odds`, `suspend/lock` | **CURRENT (relational schema)** |
| `services/risk_engine.py` | пред-проверка купона | **CURRENT** |
| `services/betting_limits.py` | лимиты пользователя/дивизиона | **CURRENT** |
| `services/exposure_service.py` | экспозиция рынка | **CURRENT** |
| `services/cashout_engine.py` | расчёт оффера кэшаута | **CURRENT** |
| `services/market_settler.py` | чистая детерминированная оценка исхода | **CURRENT** |
| `services/settlement_engine.py` | **единственный** settlement | **CURRENT** |
| `services/market_safety.py` | защита рынка | **CURRENT** |
| `services/live_ingestion.py`, `live_state_machine.py` | LIVE-приём | **UNUSED** (0 вызывающих вне тестов) |
| `bet_markets` (таблица) | плоская legacy-линия | **DUPLICATE, всё ещё в проде** |

**Ключевое:** `bet_markets` и `markets`/`market_selections` — **две параллельные схемы линии, обе живые**, генерируются раздельно (`generate_round_markets` vs `generate_match_markets`) и гасятся раздельно. Именно это расхождение порождает P0-1.

### 9.3 `place_user_bet` — порядок проверок

```
1. Global lockdown       → LOGOVO_LOCKDOWN (кроме global admin)
2. amount < 10           → отказ
3. amount > _MAX_BET (50 000) → MAX_BET_EXCEEDED
4. payload_hash = sha256(amount + отсортированные (match_id, outcome))
5. with _bet_placement_lock, transaction():
   6. idempotency: ключ + сверка payload_hash → IDEMPOTENCY_KEY_REUSED | возврат существующего id
   7. RiskEngine.evaluate_bet(...)        ← try/except с logger.debug (RISK-01)
   8. balance < amount                    → INSUFFICIENT_FUNDS
   9. для каждого исхода:
        match.status not in (scheduled,pending,live,open) → отказ
        if r_num:  round-gate               ← BET-01 (B, C, D)
        резолв кэфа: a) market_id+selection_id
                     b) match_id+selection_key   ← без проверки состояния линии
                     c) legacy bet_markets WHERE is_active = 1
        сверка client_odd (только если передан market_id/selection_id) → ODDS_CHANGED
  10. potential_win > _MAX_PAYOUT (500 000) → MAX_PAYOUT_EXCEEDED
  11. INSERT user_bets (IntegrityError → повторная проверка идемпотентности)
  12. UPDATE user_wallets SET balance = balance - ? WHERE user_id = ? AND balance >= ?
      rowcount == 0 → rollback + отказ
  13. INSERT bet_items; INSERT coin_transactions('bet_placed', -amount, bet_id, new_balance)
```

Шаги 11–13 корректны и атомарны. Условный `UPDATE ... AND balance >= ?` с проверкой `rowcount` — правильная защита от гонки по балансу.

---

## 10. BETTING CUTOFF — ГЛАВНЫЙ РАЗДЕЛ

### ОТВЕТ НА ГЛАВНЫЙ ВОПРОС

> **МОЖНО ЛИ КАК-ТО ПОСТАВИТЬ ПОСЛЕ ЗАКРЫТИЯ ЛИНИИ?**

# ДА. Четыре подтверждённых пути.

---

### Обход A — «Закрыть линию» не действует на Mini App (основной) — FACT

**Точный путь:**

1. Тур N открыт для игры: `rounds.is_open = 1`, `rounds.bets_open = 1`.
2. Админ нажимает **«🚫 Закрыть линию ставок»** (`handlers/admin.py:2069`, callback `admin_bets_close_round_N`) либо использует текстовую команду `/линия` (`handlers/text_commands.py:350`).
3. `admin_toggle_round_bets` (`handlers/admin.py:2094`) → `database.set_round_bets_open(N, False)`.
4. Функция (`database.py:4513-4527`) выполняет ровно два действия:
   - `UPDATE rounds SET bets_open = 0, bets_opened_at = NULL WHERE ... round_number = N`
   - `UPDATE bet_markets SET is_active = 0 WHERE tour = N`
   **Она не сбрасывает `rounds.is_open` и не переводит `markets`/`market_selections` в `closed`.**
5. Пользователь (или прямой HTTP-запрос) шлёт `POST /api/predictions` с `{"match_id": X, "selection_key": "p1"}`.
6. `place_user_bet` доходит до round-gate (`database.py:6676-6683`):
   ```python
   cursor.execute("SELECT is_open, deadline FROM rounds WHERE division_id = ? AND round_number = ?", ...)
   if r_row:
       if not r_row["is_open"]:          # is_open ВСЁ ЕЩЁ 1 → проверка проходит
           return False, "Приём прогнозов закрыт."
   ```
   Колонка `bets_open` в этом SELECT **даже не выбирается**.
7. Резолв кэфа, ветка (b), `database.py:6716`:
   ```sql
   SELECT ms.odds_value, ... FROM market_selections ms
   JOIN markets m ON ms.market_id = m.id
   WHERE m.match_id = ? AND ms.selection_key = ?
   ```
   Ни `m.status`, ни `ms.status`, ни `is_active` не проверяются. `markets.status` по-прежнему `'open'`, `market_selections.status` — `'active'`, потому что **ничто в системе не закрывает реляционные рынки при закрытии линии** (проверено по всему `services/odds_engine.py` — `suspend_market` / `lock_selection` вызываются только вручную из админ-API).
8. `RiskEngine` (`risk_engine.py:201`) пропускает: условие `not (is_open or bets_open)` ложно, так как `is_open = 1`.
9. **Ставка принята.**

**Почему Telegram при этом блокируется:** `_render_tour_matches` (`handlers/betting.py:187`) читает `get_active_bet_markets`, который фильтрует по `bet_markets.is_active = 1` — обнулено на шаге 4. Кнопок нет → путь визуально закрыт. Это и создаёт ложное впечатление, что линия закрыта.

**IMPACT:** админ считает линию закрытой; Mini App продолжает принимать ставки на матчи, которые вот-вот будут сыграны или уже играются в EA FC Mobile.

---

### Обход B — Нет строки в `rounds` ⇒ нет проверки вообще — FACT

`database.py:6675`:
```python
if r_num:                      # NULL или 0 → весь блок пропущен
```
`database.py:6681`:
```python
if r_row:                      # None → весь блок пропущен, отказа НЕТ
```

Если у матча `round_number` равен NULL/0, **или** если ни основной SELECT, ни фолбэк не вернули строку — round-gate не выполняется ни в каком виде. Такой матч остаётся ставочным **навсегда**, независимо от любого состояния линии. `RiskEngine` устроен идентично (`risk_engine.py:187, 200`) и тоже не блокирует.

**Как достижимо:** матч, созданный вне обычного round-robin (товарищеский, кубковый, ручная вставка), или тур, для которого строка в `rounds` не была создана.

---

### Обход C — Кросс-дивизионный фолбэк — FACT

`database.py:6679`:
```sql
SELECT is_open, deadline FROM rounds WHERE round_number = ?
ORDER BY is_open DESC, id DESC LIMIT 1
```

`ORDER BY is_open DESC` **намеренно предпочитает ОТКРЫТУЮ строку из любого дивизиона**. Дивизион, у которого своей строки в `rounds` нет, наследует открытое состояние чужого дивизиона. `risk_engine.py:194-198` содержит ту же конструкцию с добавлением `bets_open DESC` — то есть предпочитает открытость ещё сильнее.

---

### Обход D — Слепота к сезону — FACT

`database.py:6676` и `risk_engine.py:189` фильтруют по `division_id` и `round_number`, **но не по `season_id`**, при `UNIQUE(season_id, division_id, round_number)`. После старта второго сезона `fetchone()` вернёт строку старого сезона (наименьший rowid). Тур нового сезона будет оцениваться по состоянию **прошлогоднего** тура с тем же номером.

**Статус:** THEORETICAL до второго сезона, далее — немедленно активный.

---

### 12 требуемых атак — результаты

| # | Сценарий | Результат | Обоснование |
|---|---|---|---|
| 1 | Ставка **до** открытия тура (ранняя линия) | **ОТКЛОНЕНА** (ошибочно) | `place_user_bet:6682` требует `is_open`, игнорирует `bets_open` → LINE-01 |
| 2 | Ставка **после** открытия тура | **ПРИНИМАЕТСЯ** | текущая модель это разрешает by design |
| 3 | Ставка после закрытия **линии** (тур открыт) | **ПРИНИМАЕТСЯ ❌** | Обход A |
| 4 | Ставка после закрытия **тура** | **ОТКЛОНЕНА ✅** | `is_open = 0` → `place_user_bet:6682` |
| 5 | Повторная отправка старого купона (stale coupon) | **ПРИНИМАЕТСЯ, если тур ещё `is_open`** | купон — это просто список `match_id`+`outcome`, срока жизни нет |
| 6 | Прямой запрос к API мимо UI | **ПРОХОДИТ** | защита только в `place_user_bet`; UI-фильтрация не является контролем |
| 7 | Подмена `round_id` | **н/п** | `round_id` в payload отсутствует; тур выводится из `matches.round_number` |
| 8 | Подмена `match_id` | **ОТКЛОНЕНА ✅** | матч читается из БД, статус и дивизион берутся оттуда |
| 9 | Подмена `market_id` / `selection_id` | **ОТКЛОНЕНА ✅** | `WHERE ms.id = ? AND ms.market_id = ?` + сверка `client_odd` → ODDS_CHANGED |
| 10 | Подмена `division_id` / `season_id` | **ОТКЛОНЕНА ✅** | оба игнорируются в payload, читаются из `matches` |
| 11 | Ставка на закрытый/приостановленный рынок | **ОТКЛОНЕНА ✅** через RiskEngine (`risk_engine.py:262`) — **но обходится**, если RiskEngine упал (RISK-01), т.к. ветка (b) в `place_user_bet:6716` статус рынка не проверяет |
| 12 | Гонка «ставка ↔ открытие тура» | **ЗАЩИЩЕНА ✅** | `_bet_placement_lock` (RLock) + `transaction()`; `update_round_status` идёт тем же `transaction()`, SQLite сериализует запись |

**Суммарно: 4 из 12 сценариев проходят там, где не должны (3, 5, 6, + 11 при деградации RiskEngine); 1 из 12 ошибочно блокируется (1).**

---

## 11. РЫНКИ (MARKETS)

**FACT.** Две схемы:

| | `bet_markets` (legacy) | `markets` + `market_selections` |
|---|---|---|
| Гранулярность | 1 строка на матч, фикс. колонки `odd_p1…odd_btts_no` | N рынков × M исходов |
| Управление | `is_active 0/1` | `status CHECK IN ('open','suspended','closed','settled','voided')`; селекции `('active','locked','voided')` |
| Генератор | `betting_engine.generate_round_markets` | `odds_engine.generate_match_markets` (7 типов) |
| Читает Telegram | **ДА** | нет |
| Читает Mini App | да (`/api/markets/tours`) | **ДА** (`/api/matches/{id}/markets`) |
| Гасится при закрытии линии | **ДА** | **НЕТ** ← корень BET-01 |
| Гасится при settlement | да (`settlement_engine.py` шаг 3) | да (шаг 2) |

**FACT.** `odds_engine.set_odds()` синхронизирует кэф обратно в `bet_markets` через f-string-интерполяцию имени колонки из захардкоженной карты — единственное место интерполяции, аналогичное задокументированному исключению для `SAFE_COLUMNS`. Инъекция невозможна (ключи фиксированы), но это второе место, где схемы связаны неявно.

**Согласованность Telegram / API / Mini App:** `/api/markets/tours` и Telegram показывают **одинаковые** 7 кэфов из `bet_markets`. `/api/matches/{id}/markets` показывает **более широкий** набор из `markets`. Расхождение по составу — by design; расхождение по **состоянию** (открыт/закрыт) — дефект.

---

## 12. КОЭФФИЦИЕНТЫ (ODDS)

**FACT.** `BOOKMAKER_MARGIN = 1.055` (`betting_engine.py`). `calculate_match_odds(team1, team2, division_id, season_id)` — маржа применяется централизованно. `odds_engine.validate_odds` присутствует. История: `odds_history` + `odds_movement` пишутся в `set_odds`.

**FACT.** Защита от «поймать старый кэф» есть, но **условная** (`database.py:6721-6731`):
```python
if (s.get("selection_id") is not None or s.get("market_id") is not None) and client_odd is not None:
    if abs(round(float(client_odd),2) - odd_val) > 0.001:
        return False, {"error": "ODDS_CHANGED", ...}
```
Если клиент присылает только `match_id` + `outcome` (что делает legacy-путь Telegram и допускает API), сверки кэфа **нет** — берётся текущий кэф из БД. Это безопасно для казны (клиент не может навязать свой кэф), но означает, что пользователь может получить кэф, отличный от увиденного, без уведомления. **RISK, не дефект безопасности.**

`RiskEngine` дополнительно имеет проверку `ODDS_STALE` (`risk_engine.py:323`) по `odds_updated_at` — но она теряется при RISK-01.

---

## 13. КУПОН (COUPON)

**FACT.** Telegram: купон живёт в `context.user_data["bet_slip"]` (in-memory PTB). Защита от двойного тапа — `_bet_in_flight` флаг + атомарное изъятие купона перед отправкой с восстановлением при отказе (`handlers/betting.py:399-407`). Реализовано корректно.

**FACT.** Mini App: купон в `store.state.slip`; сохраняемые купоны — `/api/coupons` (`routes_user_extras`), все под `get_authenticated_user`.

**FACT.** Хеш идемпотентности (`database.py:6570-6576`) строится из `amount` + отсортированных пар `(match_id, outcome|selection_key)` — **без кэфов**. Значит повтор с тем же ключом, но изменившимся кэфом, вернёт существующую ставку, а не ошибку. Для retry-семантики это корректное поведение.

**FACT.** У купона нет TTL. Купон, собранный до закрытия линии, остаётся валидным для отправки, пока проходит round-gate (см. атаку 5).

---

## 14. КОШЕЛЁК И ФИНАНСЫ

**FACT.** `user_wallets` (PK `user_id`, `balance DEFAULT 1000`), `coin_transactions` (журнал с `balance_after`).

**Атомарность — корректна:**
- Списание: `UPDATE user_wallets SET balance = balance - ? WHERE user_id = ? AND balance >= ?` + проверка `rowcount == 0` → откат. Отрицательный баланс невозможен.
- Все операции внутри `transaction()` (реентрантного) и под `_bet_placement_lock`.
- `void_user_bet` (`database.py:7274-7279`): `UPDATE ... WHERE id = ? AND status = 'pending' AND settled_at IS NULL` + `rowcount` → двойной возврат невозможен.
- `execute_cashout` (`database.py:6945-6955`): та же схема — идемпотентен по конструкции.
- `settlement_engine`: `WHERE id = ? AND settled_at IS NULL` + `if cursor.rowcount == 0: continue` → повторный расчёт невозможен.

**FACT — недостаток журнала:** `coin_transactions` **не имеет никакого ограничения уникальности или идемпотентности**. Единственная защита от дублирующей проводки — идемпотентность вышестоящей операции. Работает, но журнал не самозащищён. **RISK.**

**FACT.** `execute_cashout` принимает параметр `idempotency_key`, но **нигде его не использует** (`database.py:6851-6980`). Фактической проблемы нет (защищает `WHERE settled_at IS NULL`), но параметр вводит в заблуждение.

**Лимиты:** `_MAX_BET = 50 000`, `_MAX_PAYOUT = 500 000` — жёстко в `database.py`, проверяются **вне** RiskEngine, поэтому переживают RISK-01.

---

## 15. SETTLEMENT

Новый engine не создавался. Проверен существующий: **`services/settlement_engine.py` (357 строк) — единственный.**

`settle_match_predictions(match_id, score1, score2, match_status="finished", ht_score1, ht_score2)`:

| Шаг | Действие |
|---|---|
| 1 | `UPDATE matches` со счётом; `target_status = "confirmed"` если текущий уже `confirmed`/`completed`, иначе `match_status` |
| 2 | Реляционные `markets` → `'settled'`; `market_selections` → `'locked'`/`'voided'` |
| 3 | `UPDATE bet_markets SET is_active = 0 WHERE match_id = ?` |
| 4 | `bet_items` со статусом `'pending'`; `db_item_status = "refunded" if item_result == "voided" else item_result` |
| 5 | `user_bets`: есть `lost` → lost; есть `pending` → пропуск; все `voided` → возврат ставки; иначе won, `effective_odd` = произведение **только выигравших** ног (voided = 1.00), `payout = int(stake * effective_odd)` |

**Оценка: логика корректна.** Экспресс с одной аннулированной ногой правильно пересчитывается, а не сгорает. Идемпотентность обеспечена (`settled_at IS NULL` + `rowcount`).

**FACT.** `services/market_settler.py` — чистая детерминированная функция `evaluate_market_selection`. Fail-safe: неизвестный рынок или исход → `return "voided"` (возврат), а не «проигрыш». `corners` / `cards` всегда `voided`. `cancelled`/`voided` матч → `voided`. Матч не в `('finished','completed')` → `raise ValueError`.

**FACT.** Триггеры расчёта: (а) inline из `confirm_and_finalize_match` / `set_technical_result` в try/except; (б) `settle_finished_bets_job` каждые 60 s → `settle_all_pending_finished_matches()` (матчи в `('confirmed','completed')` с непустым счётом и pending-ставками). Вторая петля страхует первую. **Хорошая конструкция.**

**FACT.** `bet_items.status` имеет `CHECK IN ('pending','won','lost','refunded')` — без `'voided'`. Settlement это **учитывает** и маппит `voided → refunded` (шаг 4). Не дефект.

---

## 16. БЕЗОПАСНОСТЬ И RBAC

### 16.1 Mini App initData — реализовано корректно

**FACT** (`api/auth.py:23-86`):
- `secret_key = HMAC_SHA256("WebAppData", bot_token)` — по спецификации Telegram ✅
- `data_check_string` — сортировка ключей, `hash` извлечён ✅
- `hmac.compare_digest` — сравнение за константное время ✅
- `auth_date` **обязателен**; отсутствие → отказ ✅
- Будущая дата: `auth_date > now + 300` → отказ (допуск 5 мин на расхождение часов) ✅
- Срок жизни: `now - auth_date > 86400` → отказ ✅
- Dev-bypass: требует **одновременно** `ALLOW_DEV_AUTH_BYPASS ∈ {1,true,yes}`, префикс `mock_admin_` и `is_admin(u_id)` ✅

**Production bypass отсутствует.** Это одна из самых аккуратно написанных частей системы.

### 16.2 Трёхуровневая ролевая модель

**FACT** (`handlers/base.py`):
```python
is_admin(id)         # ADMIN_IDS ∪ users.role∈('admin','division_admin') ∪ division_admins
is_global_admin(id)  # ADMIN_IDS; role=='division_admin' или division_id → False;
                     #   role=='admin' → True; строка в division_admins → False;
                     #   иначе → return is_admin(id)   ← фолбэк «для моков в тестах»
is_admin_user(id)    # = is_global_admin(id)
```
`config.ADMIN_IDS` перечитывается динамически — изменение админов без рестарта работает, как предписано.

**RISK.** Финальный фолбэк `return is_admin(telegram_id)` в `is_global_admin` означает: пользователь, не найденный в `users` и не числящийся в `division_admins`, но прошедший `is_admin` по иной ветке, получит **глобальные** права. Комментарий в коде объясняет это поддержкой моков в тестах. Практически недостижимо на согласованных данных (все ветки `is_admin`, кроме `ADMIN_IDS`, требуют строк, которые `is_global_admin` уже проверил), но это тестовая семантика в production-функции авторизации. **THEORETICAL.**

### 16.3 Аутентификация REST API

Программный обход всех 94 хендлеров во всех `routes_*.py` (поиск любых обращений к auth / actor / access / admin / 401 / 403):

**Полностью без аутентификации — FACT:**

| Файл | Хендлер | Маршрут |
|---|---|---|
| `api/routes_live.py` | `handle_get_live_matches` | `GET /api/live/matches` |
| | `handle_get_live_match_detail` | `GET /api/live/matches/{id}` |
| | `handle_get_live_events` | `GET /api/live/matches/{id}/events` |
| | `handle_get_live_stats` | `GET /api/live/matches/{id}/stats` |
| | `handle_get_live_markets` | `GET /api/live/matches/{id}/markets` |
| | `handle_get_odds_movers` | `GET /api/live/movers` |
| | `handle_get_live_intelligence` | `GET /api/live/intelligence` |
| `api/routes_wallet.py:168` | `handle_get_division_leaderboard` | `GET /api/leaderboard/division/{division_id}` |

Все остальные 86 хендлеров защищены — через `get_authenticated_user`, `_get_actor_id`, `_get_admin_actor`, `_authenticate` или `_check_*_access`. Админ-модули (`routes_admin_betting`, `routes_admin_live`, `routes_admin_risk`, `routes_admin_season`) реализуют корректный двухуровневый RBAC: глобальный админ → всё; division-админ → только свои дивизионы через `_check_market_access` / `_can_manage_match` / `_get_division_admin_divisions`.

**Смягчающий фактор:** `lockdown_middleware` (`api/server.py:155-205`) при `LOGOVO_LOCKDOWN=true` требует валидный initData + global admin для **всех** `/api/*`. Но при выключенном lockdown (нормальный режим) LIVE-эндпоинты полностью публичны.

### 16.4 Расхождение проверки доступа

**FACT.** Три разные реализации одного правила:

| Место | Отказ при исключении | Дефолт флага | Принимаемые значения |
|---|---|---|---|
| `api/auth.py:113-125` `check_user_access` | **`return True`** (fail-open) | `"public"` | `public`, `all`, `enabled` |
| `handlers/betting.py:33-44` `_check_betting_access` | `return False` (fail-closed) | нет дефолта | только `public` |
| `main.py:34` | — | `"admin_only"` | только `public` |

При недоступной БД Mini App **откроет** доступ, Telegram — закроет.

### 16.5 Прочее

**FACT.** CORS: `Access-Control-Allow-Origin: "*"` на **каждом** ответе (`api/server.py:148`). Смягчено тем, что аутентификация идёт через заголовок `X-Telegram-Init-Data`, а не cookie — CSRF через браузер невозможен. **RISK низкий.**

**FACT.** `add_static("/static/", WEB_DIR, show_index=True)` и `add_static("/assets/", ASSETS_DIR, show_index=True)` (`api/server.py:356, 360`) — включён листинг директорий. Раскрывает структуру каталогов. `WEB_DIR` содержит только фронтенд.

**FACT.** Захардкоженных секретов нет. `git ls-files` показывает только `.env.example`. `league.db` не отслеживается. В отчёте секретоподобных значений нет — соблюдено требование брифа.

---

## 17. БАЗА ДАННЫХ

**FACT.** 60 таблиц, 66 индексов. WAL, `busy_timeout = 10000`, `foreign_keys = ON`, `row_factory = sqlite3.Row`. Все миграции аддитивные под `CREATE TABLE IF NOT EXISTS` + `schema_migrations`. Переписывания/удаления таблиц нет. Все запросы параметризованы, кроме двух задокументированных мест интерполяции имён колонок из захардкоженных карт.

**Индексы горячего пути — покрытие достаточное:**
`idx_rounds_season_div_round`, `idx_matches_season_div`, `idx_matches_division(division_id,status)`, `idx_bet_markets_tour(tour,is_active)`, `idx_bet_markets_match`, `idx_user_bets_user(user_id,status)`, `idx_bet_items_match(match_id,status)`, `idx_markets_match(match_id,status)`, `idx_selections_market(market_id,status)`, `idx_user_bets_idempotency` (partial unique).

**FACT — пробелы во внешних ключах:**

| Таблица | Отсутствует FK |
|---|---|
| `user_bets` | → `users` |
| `bet_items` | → `markets`, → `market_selections` |
| `coin_transactions` | нет уникальности/идемпотентности |
| `matches.status` | нет `CHECK` (в отличие от `seasons`, `markets`, `user_bets`, `bet_items`) |

При `foreign_keys = ON` эти пробелы означают, что осиротевшие строки ставок технически возможны.

**FACT — критично, засев feature-флага** (`database.py:483-488`):
```sql
INSERT INTO feature_flags (feature_key, status)
VALUES ('betting_market', 'public')
ON CONFLICT(feature_key) DO UPDATE SET status = 'public'
WHERE status != 'disabled'
```
Выполняется в `init_db()` при **каждом старте бота**. Принудительно возвращает флаг в `'public'`, если он не равен точно `'disabled'`. Установленный админом `'admin_only'` молча теряется при ближайшем рестарте.

---

## 18. REST API

**FACT.** 102 маршрута в `api/server.py:225-360`.

**Мёртвых эндпоинтов, используемых фронтендом, нет** — каждый путь `/api/...`, встречающийся в `web/js/*.js`, зарегистрирован на сервере.

**Зарегистрированы, но фронтендом не используются** (не мёртвый код — админский/резервный слой):
- все `/api/live/*` (7) — фронтенд удалён, см. `web/js/api.js:262`
- `/api/bets*` — алиасы к prediction-хендлерам
- `/api/table` → `handle_get_standings`
- `/api/seasons*`, большинство `/api/admin/*` (админ-UI мини-приложения не реализован)

**FACT — блокировка event loop** (`api/routes_markets.py:45, 56`):
```python
for t in open_tours:
    generate_round_markets(r_num, division_id=div_id)      # СИНХРОННО, пишет в БД
    markets = await asyncio.to_thread(database.get_active_bet_markets, ...)
    for m in markets:
        odds_engine.generate_match_markets(m_id, t1, t2)   # СИНХРОННО, пишет в БД
```
Вложенный цикл (туры × матчи) с записью в SQLite выполняется прямо в event loop aiohttp, **общем с Telegram-ботом**. Каждое открытие лобби Mini App подвешивает и API, и бота на время генерации. Соседние вызовы в том же файле корректно используют `asyncio.to_thread` — то есть это упущение, а не стиль.

**FACT.** `handle_get_odds_history` (`routes_markets.py`) вызывает `get_authenticated_user`, но **не** `check_user_access` — в отличие от двух других хендлеров того же файла. Раскрывает историю кэфов при `betting_market != public`.

**FACT.** `routes_gamification.py:159` импортирует `check_user_access` из `api.routes_wallet`, а не из `api.auth`. Работает (реэкспорт), но нарушает единственный источник истины.

**FACT.** `handle_place_prediction` (`routes_predictions.py:50-58`) валидирует `amount` и непустоту `selections`, но **не валидирует структуру элементов**. Элемент не-dict приведёт к `AttributeError` внутри `place_user_bet` → 500 вместо 400. **RISK низкий** (внутри `transaction()`, записи нет).

---

## 19. MINI APP

**FACT.** Проверка целостности DOM: все `getElementById` / `querySelector('#…')` в `app.js`, `ui.js`, `store.js`, `api.js`, `effects.js`, `tg.js` сопоставлены с `id` в `index.html` и с id, создаваемыми в JS-шаблонах.

**Результат: мёртвых кнопок и UI без бэкенда — нет.**

Единственная «висячая» ссылка — `bonus-banner-container` (`ui.js:148`), и это **намеренно**: ежедневный бонус отключён в UI при сохранённом бэкенде, обращение защищено `if (bannerEl)`. То же для `achievements-badge` (`ui.js:142-143`) — принудительно скрыт с комментарием «Награды за достижения отключены».

Идентификаторы `view-lobby`, `view-tournaments`, `view-history`, `view-profile`, `view-match_center` адресуются динамически (`app.js:766`: ``sec.id === `view-${viewName}` ``) — не мёртвые.

**FACT.** Экраны: Лобби, Матч-центр, Турниры, История, Профиль + модалки (рынки матча, лидерборд, успех, изменение кэфа). Купон-drawer с `stake-input`, `btn-submit-prediction`.

**FACT.** Идемпотентность на клиенте: `app.js:590` генерирует `slip-${Date.now()}-${random}` **на каждый клик**. Это ключ retry-безопасности (защищает от повтора одного HTTP-запроса), а не дедупликации — два осознанных клика создадут две ставки. Для купона это, вероятно, желаемое поведение; отмечаю как INFERENCE о намерении.

**FACT.** `ui.js:renderDivisionTabs` при пустом ответе API подставляет захардкоженный список из 5 дивизионов («Дивизион 1…5»). Показывает несуществующие дивизионы при сбое API.

---

## 20. АДМИН-ПАНЕЛЬ

**FACT.** Экран управления туром (`handlers/admin.py:2038-2107`) корректно различает три состояния и показывает их пользователю:
- тур открыт / закрыт;
- «Линия Logovo.bet: 🎰 открыта заранее (тур ещё не открыт для игры)» — при `bets_open and not is_open`;
- открыта / закрыта.

Кнопки: «Закрыть тур», «Напомнить должникам» (если открыт); «Открыть тур», «Открыть/Закрыть линию заранее» (если закрыт); «Смотреть матчи тура»; «Назад». Все имеют обработчики. `admin_toggle_round_bets` возвращает осмысленную ошибку при `ok == False`.

**Проблемы (все — следствие уже описанного):**
1. Список туров строится через `get_all_rounds()` — **захардкожен `division_id = 1`** (`database.py:4553`); туры остальных дивизионов в панели не видны.
2. Состояние берётся через `get_round_info(round_number)` без дивизиона → `LIMIT 1` без `ORDER BY` (`database.py:4357`) — произвольный дивизион.
3. Действия применяются глобально ко всем дивизионам (DIV-01).
4. Кнопка «Закрыть линию ставок» не закрывает линию для Mini App (BET-01).

**FACT.** `handle_admin_match_correction` (`api/routes_admin_live.py:467-472`) записывает `new_status` в `matches.status` **без какой-либо валидации**, а таблица не имеет `CHECK`. Админ (или division-админ своего дивизиона) может записать произвольную строку. Матч со статусом, не входящим ни в один фильтр, выпадает и из ставочного окна, и из settlement, и из таблицы — «зависает». Эндпоинт фронтендом не используется, но зарегистрирован и доступен.

---

## 21. TURNIRЫ / LEGACY

**НИЧЕГО НЕ УДАЛЕНО.** Только карта участия в production flow.

**FACT.** `tournaments` (`type IN ('league','cup','friendly')`), строка id=1 — основная лига. Кубок: `cup_series` (стадия, номер серии, победы сторон, победитель, статус).

**Участие в production flow:**

| Компонент | Статус | Где участвует |
|---|---|---|
| `tournaments` id=1 (лига) | **АКТИВНО** | весь основной флоу |
| `matches.tournament_type` | **АКТИВНО** | фильтры таблицы: `(tournament_type IS NULL OR tournament_type = 'league')` (`database.py:2306`, `3480`) |
| `cup_series` | **АКТИВНО** | продвижение серии внутри `transaction()` при подтверждении матча |
| `/api/tournaments`, `/api/divisions`, `/api/seasons` | зарегистрированы, аутентифицированы | фронтендом Mini App **не вызываются** |
| `bet_markets` (плоская схема) | **АКТИВНО в проде** | единственный источник линии для Telegram |

`bet_markets` — не «legacy-мусор», а несущая конструкция Telegram-пути. Любая её деактивация без переноса Telegram на реляционную схему сломает бот.

---

## 22. LIVE

**НИЧЕГО НЕ УДАЛЕНО.**

**FACT.** LIVE-подсистема **полностью не задействована в production flow**:

| Компонент | Вызывающие вне `tests/` |
|---|---|
| `services/live_state_machine.transition_match_state` | **0** |
| `services/live_ingestion.ingest_live_event` | **0** |
| `services/live_ingestion.ingest_live_statistics` | **0** |
| `get_live_match_state` | 2 (только чтение, `routes_intelligence.py:166, 200`) |
| `sync_live_provider_job` | зарегистрирован, но пишет **только** `provider_sync_state`; при `provider.is_connected == False` выходит; `transition_match_state` не вызывает |
| `api/routes_live.py` (7 эндпоинтов) | зарегистрированы, **без аутентификации**, фронтендом не вызываются |
| `web/js/api.js:262` | «LIVE-центр удалён из мини-приложения» |

**Следствие:** таблицы `live_match_states`, `live_events` в проде не наполняются. `matches.status` никогда не становится `live` / `completed` / `scheduled` / `postponed`. Внешний sports-провайдер (`api-sports.io`) в проде не подключён (`SPORTS_PROVIDER=auto` → Null-провайдер при пустом `SPORTS_API_KEY`), и `services/sports/*` корректно возвращает «ZERO fake data».

Для целевой модели (матчи играются в EA FC Mobile, LIVE не нужен) это **соответствует требованию**. Единственная проблема — открытая наружу неаутентифицированная поверхность.

---

## 23. ЛАБОРАТОРИЯ / ЛАБ

**FACT. Подсистема «Лаборатория» / «ЛАБ» в репозитории ОТСУТСТВУЕТ.**

Единственное упоминание во всей кодовой базе — `purge_old_season.py:369`:
```python
cursor.execute("DELETE FROM system_config WHERE key LIKE 'lab_%'")
```
Ни таблиц, ни хендлеров, ни маршрутов, ни модулей, ни кнопок, ни ключей `lab_*` в схеме. Это остаточная строка очистки от концепции, которая не была реализована.

**Статус раздела: NOT PRESENT.**

---

## 24. УВЕДОМЛЕНИЯ

**FACT.** `services/notification_service.py`: `is_notification_enabled`, `set_notification_preference`, `queue_notification`, `broadcast_match_event`, `get_user_notification_events`, `mark_notification_sent`. Очередь разбирается `process_notification_queue_job` каждые 15 s.

**FACT.** Дисциплинарные уведомления идут по отдельному пути: `job_check_deadlines_and_remind` (30 мин), `job_debt_lifecycle_tracker` (30 мин), `job_post_debts_to_warns` (12 ч). Флаги напоминаний сбрасываются при переустановке дедлайна в `update_round_status` — защита от повторной рассылки при переносе дедлайна.

Дублирования отправки не обнаружено: очередь и дисциплинарные джобы не пересекаются по типам событий. Mini App читает уведомления через `/api/notifications` (аутентифицирован).

---

## 25. ОБРАБОТКА ОШИБОК

**FACT — количественно:**
- `TODO` / `FIXME` / `XXX` / `HACK` в production-коде: **0**
- голых `except:` (без типа): **13**
- `except Exception:` с последующим `pass`: **107**

**Оценка неоднородная.** В фоновых джобах и оформлении сообщений широкие `except` уместны и соответствуют конвенции «падение подсистемы не роняет остальные». Но два места критичны:

1. **`database.py:6638-6639`** — глушение RiskEngine на пути размещения ставки (RISK-01).
2. **`api/auth.py:124-125`** — `check_user_access` fail-open.

Структурированные коды ошибок в API реализованы качественно: `ODDS_CHANGED` → 409, `IDEMPOTENCY_KEY_REUSED` → 409, `MAX_BET_EXCEEDED`/`MAX_PAYOUT_EXCEEDED` → 400, `LOGOVO_LOCKDOWN` → 403, `unauthorized` → 401. Пользовательские сообщения на русском, коды на английском.

---

## 26. ПАРАЛЛЕЛИЗМ И ГОНКИ

| Сценарий | Защита | Оценка |
|---|---|---|
| Две ставки одного юзера одновременно | `_bet_placement_lock` (`threading.RLock`, `database.py:6540`) + `transaction()` | **ЗАЩИЩЕНО** |
| Уход баланса в минус | `UPDATE ... WHERE balance >= ?` + `rowcount` | **ЗАЩИЩЕНО** |
| Двойной расчёт ставки | `WHERE settled_at IS NULL` + `rowcount == 0 → continue` | **ЗАЩИЩЕНО** |
| Двойной cashout | `WHERE settled_at IS NULL AND status='pending'` + `rowcount` | **ЗАЩИЩЕНО** |
| Двойной void | то же | **ЗАЩИЩЕНО** |
| Повторная отправка одного запроса | `idempotency_key` + `payload_hash` + partial unique index; `IntegrityError` → перепроверка | **ЗАЩИЩЕНО** |
| Гонка «ставка ↔ открытие тура» | общий `transaction()`, SQLite сериализует запись | **ЗАЩИЩЕНО** |
| Двойной тап в Telegram | `_bet_in_flight` в `user_data` | **ЗАЩИЩЕНО** |
| Составные операции (подтверждение матча + продвижение кубка) | реентрантный `transaction()`, коммит на внешнем выходе | **ЗАЩИЩЕНО** |

**Ограничение — INFERENCE.** `_bet_placement_lock` — потоковый, а не межпроцессный. Он работает, потому что деплой — один `worker: python main.py`. **При масштабировании на второй процесс/дино защита исчезает,** и останется только SQLite-сериализация записи, которой недостаточно для read-modify-write в `place_user_bet`. Сегодня проблемы нет; при горизонтальном масштабировании она возникает немедленно.

---

## 27. КОНФИГУРАЦИЯ

**FACT.** `config.py` — центральное чтение env, включая динамическое перечитывание `ADMIN_IDS`.

**FACT — нарушение конвенции `CLAUDE.md`** («Every setting must be read here, never via `os.getenv` at a call site»). 7 production-мест читают env напрямую:

| Файл:строка | Переменная |
|---|---|
| `api/auth.py:97` | `ALLOW_DEV_AUTH_BYPASS` ← **влияет на аутентификацию** |
| `services/ai/ai_chat.py:86` | `GEMINI_BASE_URL` |
| `services/ai/ai_recognizer.py:285-293, 351` | `GEMINI_BASE_URL`, `GEMINI_PROXY`, `ALL_PROXY`, `HTTPS_PROXY`, `HTTP_PROXY`, `WARP_PROXY` |
| `services/ai/squad_recognizer.py:105` | `GEMINI_BASE_URL` |
| `services/round_preview.py:301` | `GEMINI_BASE_URL` |

Наиболее значим первый: флаг обхода аутентификации не виден в `config.py` и не документирован в `.env.example` рядом с остальными настройками.

**FACT.** Секреты: захардкоженных нет. `.gitignore` содержит `.env`, `.env.*`, `.env/`, `league.db`, `server_league.db`. Отслеживается только `.env.example`. **Требование брифа о невыводе секретов соблюдено — в отчёте нет ни одного значения.**

---

## 28. ЛОГИРОВАНИЕ И МОНИТОРИНГ

**FACT.** Централизованный `logging.basicConfig` в `main.py:9-12`, уровень INFO, формат с таймстемпом. Пер-модульные `logging.getLogger(__name__)`.

**Аудит-след есть и он хорош:** `bet_audit_log` (индексы `idx_bet_audit_actor(actor_id, created_at DESC)`, `idx_bet_audit_entity`) пишется при `bet_voided`, `match_status_transition`, изменениях рынков и лимитов; `coin_transactions` с `balance_after` даёт полный финансовый след; `odds_history` + `odds_movement` — след движения кэфов; `provider_sync_state` — состояние синка.

**Пробелы:**
- Размещение ставки в `bet_audit_log` **не логируется** (только `coin_transactions`).
- Открытие/закрытие тура и линии в `bet_audit_log` **не логируется** — при глобальном по дивизионам эффекте (DIV-01) восстановить, кто и что закрыл, нельзя.
- Внешней системы мониторинга (Sentry и т. п.) в production-коде нет — только логи процесса.
- `logger.debug` для отказа RiskEngine (RISK-01) означает, что при уровне INFO **обход риск-контроля не оставляет следа вообще**.

---

## 29. ТЕСТЫ

**Прогон выполнен, тесты не изменялись.**

```
634 passed, 3 subtests passed in 42.83s
0 failed
```

`pytest.ini`: `testpaths = tests`, `addopts = -q -n auto --dist loadfile`.
`conftest.py` перенаправляет `LEAGUE_SQLITE_PATH` во временный каталог, даёт каждому модулю свой файл БД и закрывает per-thread соединение вокруг каждого теста. **Реальная `league.db` тестами не затрагивается — прогон безопасен.**

87 файлов тестов. Покрыты: схема дивизионов, ранняя линия, идемпотентность, settlement, кэфы, risk, cashout, RBAC, production-audit, фазы 2/5/6/9.

**FACT — почему тесты не поймали BET-01 и LINE-01:**
`tests/test_early_betting_line.py` — тесты 5 и 6 проверяют **только `RiskEngine.evaluate_bet`**, который принимает `is_open OR bets_open`. Реальная точка отказа — `database.place_user_bet:6682` — этими тестами не вызывается. Ни один тест не проверяет ставку через `place_user_bet` **после** `set_round_bets_open(N, False)` при `is_open = 1`.

**Пробелы покрытия (INFERENCE):**
- нет теста «закрыли линию → ставка через `place_user_bet` должна быть отклонена»;
- нет теста изоляции по сезонам для round-gate;
- нет теста изоляции по дивизионам для `update_round_status` / `set_round_bets_open`;
- нет теста, что `init_db()` не перетирает выставленный админом feature-флаг;
- нет теста аутентификации на `routes_live.py`.

---

## 30. КАЧЕСТВО КОДА

**Сильные стороны:**
- `database.py` — единственный владелец SQL; правило соблюдается строго, обращений к `sqlite3` вне него нет.
- Все запросы параметризованы; два места интерполяции имён колонок — из захардкоженных карт, задокументированы.
- Реентрантный `transaction()` — грамотное решение, используется последовательно.
- Async-дисциплина хорошая: `asyncio.to_thread` вокруг блокирующих вызовов почти везде (исключение — `routes_markets.py`).
- Русские пользовательские строки / английские идентификаторы — соблюдается.
- 0 TODO/FIXME в production-коде.
- Conventional Commits соблюдаются.

**Слабые стороны:**
- `database.py` ~8.7k строк — один модуль совмещает схему, миграции и все репозитории. Не рефакторил (косметический рефакторинг запрещён брифом), но это главный фактор, из-за которого расхождение двух схем линии остаётся незаметным.
- Дублирование логики round-gate между `database.py:6675-6694` и `risk_engine.py:187-223` — два **разошедшихся** экземпляра одного правила. Это и есть механизм LINE-01.
- Три разные реализации проверки доступа к Logovo.bet (раздел 16.4).
- 107 `except Exception: pass` — из них два на критичных путях.

**Расхождения документации с кодом (FACT):**
- `CLAUDE.md`: «There is no `pytest.ini` / `pyproject.toml`» — **`pytest.ini` существует** и задаёт `-n auto --dist loadfile`.
- `CLAUDE.md`: «78 test files» — фактически **87**.
- `CLAUDE.md`: «~200 Python modules» — фактически **105** production-модулей (+87 тестовых).
- `.agents/AGENTS.md` — устаревшая карта модулей, что уже отмечено в `CLAUDE.md`.

---

## 31. БИЗНЕС-ИНВАРИАНТЫ

| # | ИНВАРИАНТ | ТЕКУЩАЯ ЗАЩИТА | РАСПОЛОЖЕНИЕ | СТАТУС |
|---|---|---|---|---|
| 1 | Баланс никогда не отрицательный | `UPDATE ... WHERE balance >= ?` + `rowcount == 0 → rollback` | `database.py:6800-6812` | ✅ **ЗАЩИЩЁН** |
| 2 | Ставка не может быть рассчитана дважды | `WHERE id = ? AND settled_at IS NULL` + `rowcount == 0 → continue` | `settlement_engine.py` шаг 5 | ✅ **ЗАЩИЩЁН** |
| 3 | Один HTTP-запрос → одна ставка | `idempotency_key` + `payload_hash` + partial unique index | `database.py:6570-6620` | ✅ **ЗАЩИЩЁН** |
| 4 | Кэф нельзя навязать с клиента | сверка `client_odd` vs БД → `ODDS_CHANGED` | `database.py:6721-6731` | ⚠️ **ЧАСТИЧНО** — только при переданных `market_id`/`selection_id` |
| 5 | Нельзя ставить на сыгранный матч | `status not in ('scheduled','pending','live','open')` | `database.py:6669` | ✅ **ЗАЩИЩЁН** |
| 6 | Нельзя ставить после закрытия тура | `if not r_row["is_open"]: return False` | `database.py:6682` | ✅ **ЗАЩИЩЁН** |
| 7 | **Нельзя ставить после закрытия ЛИНИИ** | `bets_open` не читается; реляционные рынки не закрываются | `database.py:6676-6682`, `4513-4527` | ❌ **НАРУШЕН** (BET-01) |
| 8 | Ставка до открытия тура (ранняя линия) должна приниматься | `place_user_bet` требует `is_open` | `database.py:6682` | ❌ **НАРУШЕН** (LINE-01) |
| 9 | Ставка проходит риск-контроль | `RiskEngine.evaluate_bet` | `database.py:6633-6639` | ❌ **НАРУШЕН при исключении** (RISK-01) |
| 10 | Операции тура изолированы по дивизиону | все вызывающие передают `division_id=None` | `handlers/admin.py:2094,2198,2326` | ❌ **НАРУШЕН** (DIV-01) |
| 11 | Операции тура изолированы по сезону | round-gate не фильтрует `season_id` | `database.py:6676`, `risk_engine.py:189` | ❌ **НАРУШЕН** (SEASON-01, theoretical) |
| 12 | Feature-флаг сохраняется между рестартами | `ON CONFLICT DO UPDATE SET status='public'` | `database.py:483-488` | ❌ **НАРУШЕН** (FLAG-01) |
| 13 | Экспресс с аннулированной ногой пересчитывается, не сгорает | `effective_odd` = произведение только `won`, voided = 1.00 | `settlement_engine.py` шаг 5 | ✅ **ЗАЩИЩЁН** |
| 14 | Неизвестный рынок не приводит к проигрышу | fail-safe `return "voided"` | `market_settler.py` | ✅ **ЗАЩИЩЁН** |
| 15 | Mini App аутентифицирован криптографически | HMAC-SHA256 + `compare_digest` + `auth_date` + TTL 24 ч | `api/auth.py:23-86` | ✅ **ЗАЩИЩЁН** |
| 16 | Все `/api/*` требуют аутентификации | 8 эндпоинтов её не имеют | `routes_live.py`, `routes_wallet.py:168` | ❌ **НАРУШЕН** (SEC-01, SEC-02) |
| 17 | `matches.status` — ограниченное множество | нет `CHECK`; админ-API пишет произвольную строку | `database.py` схема, `routes_admin_live.py:467` | ❌ **НАРУШЕН** (STATUS-01) |
| 18 | Секреты не попадают в код/логи/коммиты | `.gitignore` + отсутствие литералов | `.gitignore`, весь код | ✅ **ЗАЩИЩЁН** |
| 19 | Одновременные ставки сериализуются | `_bet_placement_lock` (RLock) | `database.py:6540` | ⚠️ **ЧАСТИЧНО** — только внутрипроцессно |
| 20 | Event loop не блокируется | `asyncio.to_thread` везде, кроме генерации рынков | `routes_markets.py:45,56` | ❌ **НАРУШЕН** (API-02) |

**Итог: 8 защищены, 3 частично, 9 нарушены.**

---

## 32. ПРОВЕРКА СКВОЗНЫХ СЦЕНАРИЕВ

### Пользователь

| Шаг | Статус |
|---|---|
| `/start` → главное меню | **WORKING** |
| Просмотр дивизиона / таблицы / бомбардиров | **WORKING** |
| Сдача результата скриншотом (OCR) | **WORKING** |
| Открытие Mini App (menu button) | **WORKING** — при `betting_market = public`; кнопка ставится в `post_init` |
| Просмотр линии в Mini App | **WORKING** — но блокирует event loop (API-02) |
| Сбор купона | **WORKING** |
| Ставка при открытом туре | **WORKING** |
| Ставка по ранней линии (тур ещё не открыт) | **BROKEN** — линия видна в обоих интерфейсах, `place_user_bet` отклоняет (LINE-01) |
| Ставка после закрытия линии из Telegram | **WORKING (корректно заблокировано)** |
| Ставка после закрытия линии из Mini App | **BROKEN** — принимается (BET-01) |
| Cashout | **WORKING** |
| История ставок, кошелёк, бонус | **WORKING** |
| Автоматический расчёт после подтверждения матча | **WORKING** |
| LIVE-центр | **UNREACHABLE** — удалён из фронтенда |
| Кнопка «Поддержка» | **PARTIAL** — намеренная заглушка |

### Администратор дивизиона

| Шаг | Статус |
|---|---|
| Подтверждение результата | **WORKING** |
| Техническое поражение | **WORKING** |
| Открытие/закрытие тура | **PARTIAL** — действует на все дивизионы (DIV-01) |
| Открытие линии заранее | **PARTIAL** — открывается, но ставки не проходят (LINE-01) |
| Закрытие линии | **BROKEN** — не действует на Mini App (BET-01) |
| Просмотр туров в панели | **PARTIAL** — только дивизион 1 (`get_all_rounds`) |
| Управление рынками через API | **WORKING** — RBAC по дивизионам корректен |
| Аннулирование ставки | **WORKING** — идемпотентно, с аудитом |

### Супер-админ

| Шаг | Статус |
|---|---|
| Полный доступ (`ADMIN_IDS`) | **WORKING** |
| Lockdown (`LOGOVO_LOCKDOWN=true`) | **WORKING** — group=-1 guard + `lockdown_middleware` |
| Переключение feature-флага в `admin_only` | **BROKEN** — сбрасывается в `public` при рестарте (FLAG-01) |
| Создание/финализация сезона через API | **WORKING** — RBAC корректен |
| Управление лимитами и экспозицией | **WORKING** |
| Аудит-лог | **PARTIAL** — размещения ставок и операции с турами не логируются |

---

## 33. КРИТИЧЕСКИЕ НАХОДКИ

---

## P0 — КРИТИЧЕСКИЕ

---

### **BET-01 · P0 · Закрытие линии не закрывает приём ставок в Mini App**

**FACT.** Операция «закрыть линию» гасит только legacy-таблицу `bet_markets` и флаг `rounds.bets_open`. Реляционные `markets` / `market_selections` остаются в статусах `'open'` / `'active'`, а `rounds.is_open` — в 1. Round-gate в `place_user_bet` читает **только** `is_open`.

**IMPACT.** После нажатия админом «🚫 Закрыть линию ставок» Mini App продолжает принимать ставки на матчи текущего тура — включая те, что уже играются или сыграны, но не подтверждены. Прямая финансовая уязвимость: игрок, знающий результат своего матча, может поставить на него постфактум, пока матч не подтверждён.

**ТОЧНОЕ РАСПОЛОЖЕНИЕ:**
- `database.py:4513-4527` — `set_round_bets_open`, ветка `not bets_open`
- `database.py:6676` — SELECT без `bets_open`
- `database.py:6682` — `if not r_row["is_open"]`
- `database.py:6716` — резолв кэфа без проверки статуса рынка
- `services/odds_engine.py` — `suspend_market` / `lock_selection` не вызываются из пути закрытия линии

**ПУТЬ ИСПОЛНЕНИЯ:**
`admin_bets_close_round_N` → `handlers/admin.py:2094` → `database.set_round_bets_open(N, False)` → `UPDATE rounds SET bets_open=0` + `UPDATE bet_markets SET is_active=0` → **`markets` не тронуты, `is_open` не тронут** → `POST /api/predictions` → `api/routes_predictions.py:60` → `database.place_user_bet` → round-gate пройден (`is_open=1`) → кэф из `market_selections` (ветка b) → RiskEngine пройден (`is_open or bets_open` истинно) → **INSERT user_bets**.

**ПОЧЕМУ ЭТО ПРОИСХОДИТ.** Система эволюционировала от плоской схемы `bet_markets` к реляционной `markets`/`market_selections`, но контроль состояния линии остался привязан к старой схеме. `set_round_bets_open` написана в терминах `bet_markets` и никогда не обновлялась под новую схему. Telegram читает старую схему и поэтому выглядит корректно — это маскирует дефект.

**ЛОГИКА ВОСПРОИЗВЕДЕНИЯ.** Тур N открыт (`is_open=1`, `bets_open=1`). Вызвать `set_round_bets_open(N, False)`. Проверить: `rounds.is_open` = 1, `rounds.bets_open` = 0, `bet_markets.is_active` = 0, **`markets.status` = 'open'**, **`market_selections.status` = 'active'**. Вызвать `place_user_bet(uid, 100, [{"match_id": X, "selection_key": "p1"}])` для матча тура N → **вернёт `(True, bet_id)`**.

**РЕКОМЕНДАЦИЯ (не применена).** Единый путь закрытия линии должен: (1) переводить `markets` тура в `'closed'` и `market_selections` в `'locked'`; (2) round-gate в `place_user_bet` должен читать и учитывать `bets_open`; (3) ветка (b) резолва кэфа должна проверять `m.status = 'open' AND ms.status = 'active'`. Пункт (3) даёт защиту в глубину даже при рассинхроне флагов.

---

### **BET-02 · P0 · Отсутствие строки в `rounds` полностью отключает контроль линии**

**FACT.** `database.py:6675` (`if r_num:`) и `database.py:6681` (`if r_row:`) — при `round_number` NULL/0 либо при отсутствии строки в `rounds` (включая неудачу кросс-дивизионного фолбэка) round-gate **не выполняется вообще**, и функция продолжает к резолву кэфа. Отказа нет.

**IMPACT.** Такой матч остаётся ставочным бессрочно — ни закрытие линии, ни закрытие тура на него не действуют. Единственная оставшаяся защита — `matches.status`, то есть до момента подтверждения результата.

**ТОЧНОЕ РАСПОЛОЖЕНИЕ:** `database.py:6675`, `database.py:6681`; идентично `services/risk_engine.py:187`, `services/risk_engine.py:200`.

**ПУТЬ ИСПОЛНЕНИЯ:** `place_user_bet` → матч с `round_number IS NULL` → блок 6675 пропущен → сразу резолв кэфа → INSERT.

**ПОЧЕМУ.** Условие написано как «если данные есть — проверь», а не «если данных нет — запрети». Fail-open вместо fail-closed на контроле доступа к ставке.

**ЛОГИКА ВОСПРОИЗВЕДЕНИЯ.** Матч со `status='pending'`, `round_number = NULL`, при наличии активного рынка. `place_user_bet` вернёт успех независимо от состояния любых туров.

**РЕКОМЕНДАЦИЯ (не применена).** Обе ветки должны отклонять ставку: отсутствие привязки к туру = невозможно подтвердить, что линия открыта.

---

### **SEC-01 · P0 · 7 LIVE-эндпоинтов REST API без аутентификации**

**FACT.** Ни один из 7 хендлеров `api/routes_live.py` не вызывает `get_authenticated_user`, `check_user_access` или любую проверку прав. Подтверждено программным обходом всех 94 хендлеров всех `routes_*.py`.

**IMPACT.** При выключенном lockdown (нормальный режим) любой, кто знает URL Mini App, получает без авторизации: список матчей, детали, события, статистику, **рынки и коэффициенты**, движение кэфов и intelligence-выводы. Утечка бизнес-данных и обход `betting_market` feature-флага.

**ТОЧНОЕ РАСПОЛОЖЕНИЕ:** `api/routes_live.py` — `handle_get_live_matches`, `handle_get_live_match_detail`, `handle_get_live_events`, `handle_get_live_stats`, `handle_get_live_markets`, `handle_get_odds_movers`, `handle_get_live_intelligence`. Регистрация — `api/server.py`.

**ПУТЬ ИСПОЛНЕНИЯ:** `GET https://<webapp>/api/live/matches` без заголовков → `cors_middleware` → `lockdown_middleware` (проходит насквозь, т.к. lockdown выключен) → хендлер → 200 + данные.

**ПОЧЕМУ.** Модуль писался как read-only витрина под предположением «данные и так публичны»; когда LIVE-центр удалили из фронтенда, серверную часть оставили без ревизии.

**ЛОГИКА ВОСПРОИЗВЕДЕНИЯ.** Запрос к любому `/api/live/*` без `X-Telegram-Init-Data` при `LOGOVO_LOCKDOWN != true` → 200.

**РЕКОМЕНДАЦИЯ (не применена).** Поскольку LIVE не нужен целевой модели и фронтендом не используется — снять регистрацию маршрутов (код **не удалять**, согласно требованию). Если оставлять — добавить `get_authenticated_user` + `check_user_access`, как в `routes_markets.py`.

---

### **RISK-01 · P0 · Молчаливый полный обход риск-контроля**

**FACT** (`database.py:6633-6639`):
```python
try:
    risk_decision = RiskEngine.evaluate_bet(...)
    if not risk_decision.allowed: ...
except Exception as e:
    logger.debug(f"RiskEngine evaluation fallback: {e}")
```
Любое исключение внутри RiskEngine приводит к продолжению размещения ставки **без единой риск-проверки**, с записью на уровне `debug` (не виден при штатном INFACT-уровне INFO).

**IMPACT.** Теряются: дневные лимиты, защита от rapid-betting, экспозиция рынка, лимиты дивизиона, проверка `ODDS_STALE`, **проверка статуса рынка `suspended`/`closed`/`settled`** и **проверка `bets_open`**. Выживают только `_MAX_BET`, `_MAX_PAYOUT` и проверка баланса. Приостановленный админом рынок при деградации RiskEngine становится доступен, поскольку ветка (b) резолва кэфа статус рынка не проверяет.

**ТОЧНОЕ РАСПОЛОЖЕНИЕ:** `database.py:6638-6639`.

**ПУТЬ ИСПОЛНЕНИЯ:** любое исключение в `RiskEngine.evaluate_bet` (например, `BettingLimitsService` не нашёл конфиг, отсутствует таблица лимитов, ошибка в exposure-запросе) → `except` → `logger.debug` → выполнение продолжается с шага 8.

**ПОЧЕМУ.** Обработчик задумывался как «не ломать ставки, если необязательная подсистема недоступна». Но RiskEngine — не необязательная подсистема, а единственное место, где проверяются статус рынка и `bets_open`.

**ЛОГИКА ВОСПРОИЗВЕДЕНИЯ.** Заставить `RiskEngine.evaluate_bet` бросить исключение (например, недоступностью таблицы лимитов) и разместить ставку на **приостановленный** рынок → ставка пройдёт.

**РЕКОМЕНДАЦИЯ (не применена).** Fail-closed: отказ с понятной ошибкой + `logger.error`. Как минимум — поднять уровень до `error`, чтобы обход оставлял след.

---

### **FLAG-01 · P0 · `init_db()` принудительно сбрасывает feature-флаг в `public` при каждом старте**

**FACT** (`database.py:483-488`):
```sql
INSERT INTO feature_flags (feature_key, status)
VALUES ('betting_market', 'public')
ON CONFLICT(feature_key) DO UPDATE SET status = 'public'
WHERE status != 'disabled'
```
`init_db()` вызывается из `main.py:99` при каждом старте.

**IMPACT.** Админ, ограничивший Logovo.bet до `admin_only`, теряет настройку при ближайшем рестарте/деплое — рынок молча открывается всем. Прямо противоречит `main.py:34` (`get_feature_flag("betting_market", default="admin_only")`) и `handlers/betting.py:41`, которые рассчитаны на консервативный дефолт. Побочно: `post_init` выставит WebApp-кнопку меню глобально всем пользователям.

**ТОЧНОЕ РАСПОЛОЖЕНИЕ:** `database.py:483-488`.

**ПУТЬ ИСПОЛНЕНИЯ:** рестарт → `main.py:99` `init_db()` → `ON CONFLICT DO UPDATE SET status='public'` → флаг `admin_only` перезаписан → `post_init` → `set_chat_menu_button(MenuButtonWebApp)` глобально.

**ПОЧЕМУ.** Строка написана как idempotent-seed для первичной инициализации, но `ON CONFLICT DO UPDATE` превратил её в принудительную перезапись. Условие `WHERE status != 'disabled'` защищает только одно значение из трёх.

**ЛОГИКА ВОСПРОИЗВЕДЕНИЯ.** Установить `betting_market = 'admin_only'`, вызвать `init_db()`, прочитать флаг → `'public'`.

**РЕКОМЕНДАЦИЯ (не применена).** Заменить на `INSERT ... ON CONFLICT DO NOTHING` — засев только при первом создании строки.

---

## P1 — ВЫСОКИЕ

---

### **LINE-01 · P1 · Ранняя линия отображается, но ставки по ней всегда отклоняются**

**FACT.** Два разошедшихся экземпляра одного правила:
- `services/risk_engine.py:201`: `if not (r_row["is_open"] or r_row["bets_open"])` — ранняя линия **разрешена**
- `database.py:6682`: `if not r_row["is_open"]` — ранняя линия **запрещена**; колонка `bets_open` в SELECT на строке 6676 даже не выбирается

**IMPACT.** Функциональность «ранняя линия» нерабочая. Тур отображается в Telegram (`get_open_betting_tours` включает `bets_open = 1`) и в Mini App (`/api/markets/tours` возвращает `is_early: true`), рынки сгенерированы, кнопки активны — но **любая** попытка ставки завершается сообщением «Приём прогнозов на Тур N закрыт». Админ видит в панели «🎰 открыта заранее». Функция, ближайшая к целевой бизнес-модели, не работает.

**ТОЧНОЕ РАСПОЛОЖЕНИЕ:** `database.py:6676` (SELECT), `database.py:6682` (условие); сравнить с `services/risk_engine.py:189, 201`.

**ПУТЬ ИСПОЛНЕНИЯ:** `set_round_bets_open(N, True)` при `is_open=0` → `generate_round_markets` → линия видна → ставка → RiskEngine пропускает → `place_user_bet:6682` отклоняет.

**ПОЧЕМУ.** Миграция `bets_open` (`database.py:378-388`) добавила флаг и обновила `risk_engine`, `get_open_betting_tours` и UI, но `place_user_bet` — не обновила.

**ЛОГИКА ВОСПРОИЗВЕДЕНИЯ.** Тур с `is_open=0`, `bets_open=1` и матчами. `RiskEngine.evaluate_bet(...)` → `allowed=True`. `place_user_bet(...)` на тот же матч → `(False, "Приём прогнозов на Тур N закрыт.")`.

**Примечание:** это **зеркальная** проблема к BET-01 — обе происходят из того, что `place_user_bet:6682` читает не тот флаг.

---

### **DIV-01 · P1 · Открытие/закрытие туров и линий действует на все дивизионы сразу**

**FACT.** Ни один production-вызывающий не передаёт `division_id`:

| Вызывающий | Вызов |
|---|---|
| `handlers/admin.py:2094` | `set_round_bets_open(round_number, opening)` |
| `handlers/admin.py:2198`, `2326` | `update_round_status(...)` |
| `handlers/text_commands.py:350` | `set_round_bets_open(...)` |
| `handlers/text_commands.py:381, 399, 423` | `update_round_status(...)` |

В ветке `division_id is None` (`database.py:4423-4449`, `4519-4522`) все `UPDATE rounds` идут без предиката дивизиона. Плюс три `UPDATE bet_markets SET is_active = 0 WHERE tour = ?` (`database.py:4410, 4441, 4527`) также без дивизиона.

**IMPACT.** Админ дивизиона 2, закрывающий свой тур 5, закрывает тур 5 **во всех дивизионах** и гасит все `bet_markets` тура 5 по всей лиге. Обратное — открытие — тоже глобально. Плюс `update_round_status(is_open=True)` преоткрывает линию тура N+1 (`database.py:4462`) — тоже глобально.

**ТОЧНОЕ РАСПОЛОЖЕНИЕ:** вызывающие выше; `database.py:4410, 4423-4449, 4441, 4519-4522, 4527`.

**ПОЧЕМУ.** Параметр `division_id` был добавлен в сигнатуры функций при переходе на мультидивизионную модель, но ни один вызывающий не обновлён. Ветка `None` сохранила добивизионное поведение.

**ЛОГИКА ВОСПРОИЗВЕДЕНИЯ.** Тур 5 существует в дивизионах 1 и 2, оба открыты. Нажать «Закрыть тур 5» → `rounds.is_open = 0` в обоих.

**Усугубляющий фактор:** это действие не пишется в `bet_audit_log` (см. раздел 28) — восстановить, кто закрыл чужой дивизион, нельзя.

---

### **SEASON-01 · P1 (пока THEORETICAL) · Round-gate слеп к сезону**

**FACT.** `database.py:6676` и `services/risk_engine.py:189` фильтруют по `division_id` + `round_number`, но не по `season_id`, при `rounds UNIQUE(season_id, division_id, round_number)`.

**IMPACT.** После старта второго сезона `fetchone()` вернёт строку старого сезона. Состояние линии нового сезона будет определяться прошлогодним туром: тур нового сезона будет открыт для ставок или закрыт по чужим данным. **Сегодня один активный сезон — не проявляется. В момент старта сезона 2 становится активным P1 без единого изменения кода.**

**ТОЧНОЕ РАСПОЛОЖЕНИЕ:** `database.py:6676`, `services/risk_engine.py:189`. Смежно: `database.py:4559` (`get_all_rounds` теряет фильтр сезона в фолбэке), `database.py:4357` (`get_round_info` — `LIMIT 1` без `ORDER BY`).

**ЛОГИКА ВОСПРОИЗВЕДЕНИЯ.** Создать сезон 2, тур 1 в дивизионе 1 с `is_open=1`; в сезоне 1 тур 1 дивизиона 1 закрыт (`is_open=0`). `place_user_bet` на матч сезона 2 вернёт «Приём прогнозов на Тур 1 закрыт», прочитав строку сезона 1.

---

### **AUTH-01 · P1 · `check_user_access` открывается при ошибке и имеет противоположный дефолт**

**FACT** (`api/auth.py:113-125`): `except Exception: return True` и `get_feature_flag("betting_market", default="public")`. Сравнить: `handlers/betting.py:43-44` — `except Exception: return False`, без дефолта; `main.py:34` — `default="admin_only"`.

**IMPACT.** При недоступности БД или ошибке чтения флага Mini App **открывает** Logovo.bet всем; Telegram в той же ситуации закрывает. Плюс `check_user_access` принимает `public`, `all`, `enabled`, а Telegram — только `public`: возможны состояния, где Mini App открыт, а бот закрыт.

**ТОЧНОЕ РАСПОЛОЖЕНИЕ:** `api/auth.py:122, 124-125`.

**РЕКОМЕНДАЦИЯ (не применена).** Свести три реализации к одной в `handlers/base.py` с fail-closed и дефолтом `admin_only`.

---

### **API-02 · P1 · Генерация рынков блокирует event loop, общий с ботом**

**FACT** (`api/routes_markets.py:45, 56`): `generate_round_markets(...)` и `odds_engine.generate_match_markets(...)` вызываются синхронно во вложенном цикле «туры × матчи», каждый пишет в SQLite. Соседний вызов `get_active_bet_markets` в том же цикле корректно завёрнут в `asyncio.to_thread`.

**IMPACT.** Каждое открытие лобби Mini App блокирует event loop на время генерации всех рынков всех открытых туров. Поскольку aiohttp делит loop с PTB, **на это же время замирает Telegram-бот**. Деградация растёт линейно с числом открытых туров × матчей.

**ТОЧНОЕ РАСПОЛОЖЕНИЕ:** `api/routes_markets.py:43-57`.

**ПОЧЕМУ.** «Ленивая генерация на чтении» — рынки создаются в момент запроса витрины. Это ещё и неверное место: генерация линии должна происходить при открытии линии (что уже делает `set_round_bets_open`), а не при каждом GET.

---

## P2 — СРЕДНИЕ

---

### **STATUS-01 · P2 · `matches.status` не ограничен и записывается админ-API без валидации**

**FACT.** Базовый `CREATE TABLE matches` не имеет `CHECK` на `status` (в отличие от `seasons`, `markets`, `market_selections`, `user_bets`, `bet_items`). `api/routes_admin_live.py:467-472` пишет `new_status` из тела запроса напрямую.

**IMPACT.** Матч с произвольным статусом выпадает из всех фильтров: не попадёт в ставочное окно (`status not in ('scheduled','pending','live','open')`), не будет рассчитан (`settle_all_pending_finished_matches` ищет `('confirmed','completed')`), не попадёт в таблицу. Ставки на него зависнут в `pending` навсегда. Эндпоинт фронтендом не вызывается, но зарегистрирован и доступен любому админу дивизиона для своих матчей.

**ТОЧНОЕ РАСПОЛОЖЕНИЕ:** `api/routes_admin_live.py:467-472`; схема `matches` в `database.py`.

---

### **SEC-02 · P2 · `GET /api/leaderboard/division/{id}` без аутентификации**

**FACT.** `api/routes_wallet.py:168` — `handle_get_division_leaderboard` не вызывает никакой проверки, в отличие от четырёх соседних хендлеров того же файла (`handle_bootstrap`, `handle_claim_bonus`, `handle_leaderboard`, `handle_get_wallet` — все аутентифицированы).

**IMPACT.** Публично раскрывает лидерборд «капперов» с ROI и win-rate по любому `division_id`. Пропуск, а не решение — соседние хендлеры защищены.

---

### **DB-01 · P2 · Пробелы во внешних ключах и идемпотентности журнала**

**FACT.** `user_bets` не имеет FK на `users`; `bet_items` не имеет FK на `markets` / `market_selections`; `coin_transactions` не имеет никакого уникального ограничения.

**IMPACT.** При `foreign_keys = ON` осиротевшие ставки технически возможны. `coin_transactions` — журнал финансовых операций — полагается исключительно на корректность вызывающего кода; собственной защиты от дублирующей проводки у него нет. Сегодня все пути идемпотентны, поэтому дублей не возникает — но журнал не самозащищён.

**ТОЧНОЕ РАСПОЛОЖЕНИЕ:** схема в `database.py`, определения `user_bets`, `bet_items`, `coin_transactions`.

---

### **ROUND-02 · P2 · Админ-панель туров видит только дивизион 1**

**FACT.** `get_all_rounds()` (`database.py:4553`) захардкоживает `AND (division_id = 1 OR division_id IS NULL)`; фолбэк на 4559 при пустом результате отбрасывает и фильтр сезона. `get_round_info(round_number)` без дивизиона (`database.py:4357`) — `LIMIT 1` без `ORDER BY`.

**IMPACT.** Экран «Управление матчами и турами» (`handlers/admin.py:2007-2017`) строит список туров только по дивизиону 1, а состояние (`is_open`, `bets_open`) показывает по произвольной строке. Админ дивизиона 3 не увидит свои туры и будет действовать по чужому состоянию.

---

### **AUDIT-01 · P2 · Ключевые операции не попадают в аудит-лог**

**FACT.** `bet_audit_log` заполняется при `bet_voided`, `match_status_transition`, операциях с рынками и лимитами. **Не заполняется** при: размещении ставки, открытии/закрытии тура, открытии/закрытии линии, изменении feature-флага.

**IMPACT.** В сочетании с DIV-01 (глобальный эффект) и FLAG-01 (тихий сброс) восстановить постфактум, кто закрыл линию или чей рестарт открыл рынок всем, невозможно.

---

### **LIVE-01 · P2 · LIVE-подсистема мертва в проде, но открыта наружу**

**FACT.** `transition_match_state`, `ingest_live_event`, `ingest_live_statistics` — **0 вызывающих** вне тестов. `sync_live_provider_job` пишет только `provider_sync_state`. Таблицы `live_match_states`, `live_events` в проде не наполняются. При этом 7 эндпоинтов зарегистрированы и не аутентифицированы (SEC-01).

**НИЧЕГО НЕ УДАЛЕНО.** Отмечено как карта участия в production flow, согласно требованию брифа.

---

### **CORS-01 · P2 · `Access-Control-Allow-Origin: *` и листинг статических каталогов**

**FACT.** `api/server.py:148` — `*` на каждом ответе. `api/server.py:356, 360` — `show_index=True` для `/static/` и `/assets/`.

**IMPACT.** CSRF через браузер невозможен (аутентификация в заголовке `X-Telegram-Init-Data`, не cookie), поэтому влияние ограничено: любой сайт может читать публичные ответы API, а листинг раскрывает структуру каталогов. **RISK низкий**, но снижает планку для разведки.

---

## P3 — НИЗКИЕ

---

- **CFG-01.** `os.getenv` в 7 production-местах вне `config.py` (`api/auth.py:97`, `services/ai/ai_chat.py:86`, `services/ai/ai_recognizer.py:285-293, 351`, `services/ai/squad_recognizer.py:105`, `services/round_preview.py:301`) — нарушает конвенцию `CLAUDE.md`. Наиболее значим `ALLOW_DEV_AUTH_BYPASS`: флаг, влияющий на аутентификацию, не виден в `config.py`.
- **TG-01.** Telegram-путь размещения ставки не передаёт `idempotency_key` (`handlers/betting.py:404`) — защита только через in-memory `_bet_in_flight`, теряемый при рестарте бота.
- **TG-02.** `callback_data=f"bet_place_{bal}"` (`handlers/betting.py:363`) вшивает баланс на момент отрисовки. Безопасно (`place_user_bet` перепроверяет баланс атомарно), но при устаревшей клавиатуре кнопка «ВСЁ» отправит неверную сумму.
- **WALLET-01.** `execute_cashout` принимает `idempotency_key` и **нигде его не использует** (`database.py:6851-6980`). Фактической проблемы нет — защищает `WHERE settled_at IS NULL` — но параметр вводит в заблуждение.
- **API-03.** `handle_get_odds_history` (`routes_markets.py`) вызывает `get_authenticated_user`, но не `check_user_access`, в отличие от двух других хендлеров того же файла.
- **API-04.** `routes_gamification.py:159` импортирует `check_user_access` из `api.routes_wallet`, а не из `api.auth`.
- **API-05.** `handle_place_prediction` не валидирует структуру элементов `selections` — не-dict приведёт к 500 вместо 400.
- **UI-01.** `ui.js:renderDivisionTabs` при пустом ответе API подставляет захардкоженные «Дивизион 1…5» — показывает несуществующие дивизионы при сбое.
- **UX-01.** `show_support` (`handlers/base.py`) — заглушка «🚧 В разработке». Намеренная, отмечена для полноты.
- **DOC-01.** `CLAUDE.md` расходится с кодом: утверждает отсутствие `pytest.ini` (файл существует и задаёт `-n auto --dist loadfile`), «78 test files» (фактически 87), «~200 Python modules» (фактически 105 production + 87 тестовых).

---

## ФИНАЛЬНАЯ ТАБЛИЦА СТАТУСОВ

| Area | Status | Confidence | Main Finding |
|---|---|---|---|
| Telegram | ✅ WORKING | HIGH | 216 callback_data / 159 паттернов, 0 несопоставленных; порядок регистрации корректен |
| Navigation | ✅ WORKING | HIGH | Мёртвых кнопок нет; catch-all зарегистрирован последним |
| Seasons | ⚠️ PARTIAL | HIGH | Round-gate не фильтрует `season_id` — сломается при старте сезона 2 (SEASON-01) |
| Divisions | ❌ BROKEN | HIGH | Операции с турами и линиями глобальны по всем дивизионам (DIV-01) |
| Rounds | ⚠️ PARTIAL | HIGH | `is_open`/`bets_open` разошлись; модель обратна целевой |
| Matches | ✅ WORKING | HIGH | Жизненный цикл `pending → confirmed → cancelled` корректен; `status` без CHECK (STATUS-01) |
| Betting | ⚠️ PARTIAL | HIGH | Одна точка входа, атомарна и идемпотентна; риск-контроль обходится при исключении (RISK-01) |
| **Betting Cutoff** | ❌ **BROKEN** | **HIGH** | **4 подтверждённых обхода; закрытие линии не действует на Mini App (BET-01)** |
| Markets | ❌ BROKEN | HIGH | Две параллельные схемы; при закрытии линии гасится только legacy |
| Odds | ✅ WORKING | HIGH | Маржа 1.055 централизована; сверка кэфа условна (только при `market_id`/`selection_id`) |
| Coupon | ✅ WORKING | HIGH | Атомарное изъятие + `_bet_in_flight`; TTL отсутствует |
| Wallet | ✅ WORKING | HIGH | Отрицательный баланс невозможен; `coin_transactions` без собственной идемпотентности |
| Settlement | ✅ WORKING | HIGH | Единственный engine; идемпотентен; voided-ноги пересчитываются верно; fail-safe → voided |
| RBAC | ⚠️ PARTIAL | HIGH | initData HMAC образцовый; 8 эндпоинтов без auth (SEC-01, SEC-02) |
| Database | ⚠️ PARTIAL | HIGH | Схема и индексы в порядке; feature-флаг сбрасывается при каждом старте (FLAG-01) |
| API | ⚠️ PARTIAL | HIGH | 102 маршрута, мёртвых нет; блокировка event loop (API-02); 8 без auth |
| Mini App | ✅ WORKING | HIGH | DOM целостен, мёртвых кнопок нет; отключённые блоки закрыты guard-ами |
| Admin | ⚠️ PARTIAL | HIGH | UI корректно различает состояния, но действия глобальны и панель видит только дивизион 1 |
| Tournaments | ✅ WORKING | HIGH | Лига id=1 и `cup_series` активны; `bet_markets` — несущая, не мусор |
| LIVE | ⛔ UNUSED | HIGH | 0 вызывающих в проде; 7 эндпоинтов открыты без auth. **Ничего не удалено** |
| Laboratory | ⛔ NOT PRESENT | HIGH | Существует только строка очистки `lab_%` в `purge_old_season.py:369` |
| Tests | ✅ WORKING | HIGH | 634 passed, 0 failed; прогон безопасен; но BET-01/LINE-01 не покрыты |

---

## ТЕКУЩЕЕ СОСТОЯНИЕ

Logovo.bet — рабочая, зрелая система с надёжным финансовым ядром. Атомарность кошелька, идемпотентность ставок, расчёт экспрессов, криптографическая проверка initData и защита от гонок реализованы на хорошем уровне и подтверждены зелёным test suite.

Все существенные дефекты сосредоточены в **одном месте** — на границе «состояние тура ↔ состояние рынка». Система прошла миграцию с плоской схемы `bet_markets` на реляционную `markets`/`market_selections`, но контроль состояния линии остался привязан к старой схеме, а флаг `bets_open` был добавлен в четыре из пяти мест, где он нужен. Итог: Telegram-путь (читает старую схему) выглядит корректным, Mini App (читает новую) — нет.

Целевая бизнес-модель — ставки до открытия тура, открытие тура закрывает линию — **в коде не реализована**. Текущая модель ей обратна.

---

## ТОП-10 РЕАЛЬНЫХ ПРОБЛЕМ

| # | ID | Severity | Суть |
|---|---|---|---|
| 1 | **BET-01** | P0 | Закрытие линии не закрывает приём ставок в Mini App — можно ставить после закрытия |
| 2 | **BET-02** | P0 | Матч без строки в `rounds` вообще не проходит контроль линии |
| 3 | **RISK-01** | P0 | Исключение в RiskEngine молча отключает весь риск-контроль, включая проверку статуса рынка |
| 4 | **SEC-01** | P0 | 7 LIVE-эндпоинтов доступны без аутентификации |
| 5 | **FLAG-01** | P0 | `init_db()` сбрасывает `betting_market` в `public` при каждом старте бота |
| 6 | **LINE-01** | P1 | Ранняя линия показывается везде, но ставки по ней всегда отклоняются |
| 7 | **DIV-01** | P1 | Открытие/закрытие тура и линии действует на все дивизионы сразу |
| 8 | **API-02** | P1 | Генерация рынков блокирует event loop, общий с Telegram-ботом |
| 9 | **AUTH-01** | P1 | `check_user_access` открывается при ошибке; три расходящихся реализации одного правила |
| 10 | **SEASON-01** | P1 | Round-gate слеп к сезону — сломается в момент старта сезона 2 |

---

## ЧТО УЖЕ РАБОТАЕТ

Перечислено явно, согласно требованию «если всё работает — так и напиши».

- **Атомарность кошелька.** Отрицательный баланс структурно невозможен: `UPDATE ... WHERE balance >= ?` + проверка `rowcount`.
- **Идемпотентность.** Размещение ставки, расчёт, cashout и void — все защищены от повторного выполнения условными UPDATE с проверкой `rowcount`. Partial unique index на `(user_id, idempotency_key)`.
- **Settlement.** Единственный engine, логика корректна, включая пересчёт экспрессов с аннулированными ногами. Fail-safe на неизвестных рынках — возврат, а не проигрыш. Двойная петля (inline + job каждые 60 с).
- **Аутентификация Mini App.** HMAC-SHA256 по спецификации, `compare_digest`, обязательный `auth_date`, отсечка будущих дат с допуском 5 мин, TTL 24 ч, dev-bypass под тройной защитой. Production-обхода нет.
- **Маршрутизация Telegram.** 0 мёртвых кнопок, 0 orphan/duplicate/unreachable хендлеров, порядок регистрации в точности по `CLAUDE.md`, `query.answer()` покрыт.
- **Mini App frontend.** 0 мёртвых кнопок, 0 UI без бэкенда; отключённые блоки (бонус, ачивки) корректно закрыты guard-ами.
- **Защита от гонок.** 9 из 9 проверенных сценариев защищены в рамках одного процесса.
- **Дисциплина БД.** Единственный владелец SQL, полная параметризация, аддитивные миграции, WAL + FK ON, 66 индексов покрывают горячий путь.
- **Секреты.** 0 захардкоженных значений, `.gitignore` корректен, отслеживается только `.env.example`.
- **Изоляция подсистем.** Каждая группа джобов и запуск API-сервера обёрнуты в собственный try/except.
- **Тесты.** 634 passed, 0 failed. `conftest.py` полностью изолирует прогон от реальной БД.
- **RBAC админ-API.** Двухуровневая модель (глобальный / дивизионный админ) реализована корректно во всех четырёх админ-модулях.
- **0 TODO/FIXME** в production-коде.

---

## ЧТО НЕЛЬЗЯ ПРОВЕРИТЬ СТАТИЧЕСКИ

- **Фактическое содержимое `league.db`** — сколько сезонов, дивизионов, туров без строк в `rounds`, матчей с `round_number IS NULL`. От этого зависит, активны ли BET-02, C и D **сегодня** или пока только теоретически.
- **Текущее значение `betting_market`** в проде и как давно был последний рестарт (влияет на FLAG-01).
- **Бросает ли RiskEngine исключения в проде** — RISK-01 логируется на уровне `debug`, поэтому по журналам INFO это невидимо. Нужен грепом прод-лог на `RiskEngine evaluation fallback`.
- **Реальная задержка от API-02** — зависит от числа открытых туров × матчей и скорости диска.
- **Доступность `/api/live/*` снаружи** — зависит от reverse-proxy перед aiohttp: возможно, часть путей уже отфильтрована на уровне инфраструктуры.
- **Значения env в проде** — `LOGOVO_LOCKDOWN`, `ALLOW_DEV_AUTH_BYPASS`, `WEBAPP_URL`, `SPORTS_API_KEY`. Проверялся только код их чтения.
- **Поведение под нагрузкой** — WAL + `busy_timeout=10000` выглядят адекватно, но контention на `_bet_placement_lock` при всплеске ставок не измерялся.
- **Реальные тайминги OCR/Gemini** и их влияние на event loop.

---

## РЕКОМЕНДУЕМЫЙ ПОРЯДОК РАБОТ

**Ничего из перечисленного не выполнено. Это план для следующей команды.**

### Этап 0 — Диагностика на реальных данных (не меняет код)
1. Посчитать в проде: число сезонов; матчи с `round_number IS NULL/0`; туры, у которых нет строки в `rounds`; текущее значение `betting_market`.
2. Грепнуть прод-логи на `RiskEngine evaluation fallback` — установить, активен ли RISK-01.

Это определит, какие из BET-02 / C / D активны сегодня, а какие теоретические.

### Этап 1 — Закрыть возможность ставить после закрытия линии
3. **BET-01/LINE-01 вместе** — они одна проблема, читаемая с двух сторон. Привести round-gate `place_user_bet` в соответствие с `risk_engine`: выбирать `bets_open`, решать по обоим флагам согласно **целевой** модели.
4. **BET-01, защита в глубину** — ветка (b) резолва кэфа (`database.py:6716`) должна требовать `m.status = 'open' AND ms.status = 'active'`. Это одна строка, которая закрывает обход даже при рассинхроне флагов.
5. **BET-01, источник** — `set_round_bets_open(N, False)` должна закрывать реляционные `markets`/`market_selections`, а не только `bet_markets`.
6. **BET-02** — сделать обе ветки fail-closed.
7. Добавить тест: закрыли линию → `place_user_bet` отклоняет. Именно его отсутствие пропустило дефект.

### Этап 2 — Восстановить контроль
8. **RISK-01** — fail-closed либо как минимум `logger.error`.
9. **FLAG-01** — `ON CONFLICT DO NOTHING`.
10. **SEC-01 / SEC-02** — снять регистрацию `/api/live/*` (код **не удалять**) либо добавить auth; добавить auth в `handle_get_division_leaderboard`.
11. **AUTH-01** — свести три реализации проверки доступа к одной, fail-closed.

### Этап 3 — Изоляция
12. **DIV-01** — передавать `division_id` во всех 7 вызывающих; убрать нескоупленные `UPDATE bet_markets ... WHERE tour = ?`.
13. **SEASON-01** — добавить `season_id` в оба round-gate. **Обязательно до старта сезона 2.**
14. **ROUND-02** — `get_all_rounds` / `get_round_info` должны принимать дивизион из контекста админа.

### Этап 4 — Реализация целевой модели
15. Только после этапов 1–3 — переворот семантики: ставки открыты до `is_open`, открытие тура **закрывает** линию. Изменение затрагивает `update_round_status`, `set_round_bets_open`, оба round-gate, `get_open_betting_tours` и UI админки. До устранения дублирования схем этот переворот **не следует делать** — он затронет обе схемы линии и без единой точки контроля создаст новые обходы.

### Этап 5 — Гигиена
16. API-02 (генерация рынков вне event loop), STATUS-01 (CHECK + валидация), DB-01 (FK), AUDIT-01 (логировать размещение ставки и операции с турами), затем P3.

---

**Аудит завершён. Ни одна проблема не исправлена. Ожидаю следующую команду.**
