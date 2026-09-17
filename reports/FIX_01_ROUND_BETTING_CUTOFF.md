# FIX 01 — CORRECT ROUND BETTING CUTOFF

Отчёт по исправлению **PROBLEM 1**. Исправлена ровно одна проблема: приём ставок
на тур не заканчивался в момент открытия тура для игры.

Целевой жизненный цикл:

```
LINE OPEN → USERS CAN BET → ROUND OPENS → BETTING LINE CLOSES → MATCHES PLAYED → RESULT → SETTLEMENT
```

| Фаза | `rounds.is_open` | `rounds.bets_open` | Ставка |
|---|---|---|---|
| Линия выставлена, тур не начался | `0` | `1` | **разрешена** |
| Линия ещё не выставлена | `0` | `0` | запрещена |
| Тур открыт для игры | `1` | `0` | запрещена |
| `1` + `1` | — | — | **недостижимо**; если подделано в БД — запрещена |

---

## 1. Какие файлы изменены

Продакшн-код — **2 файла**:

| Файл | Что изменено |
|---|---|
| `database.py` | Добавлены 3 функции-помощника; переписан round-gate в `place_user_bet`; закрытие линии в `update_round_status`, `set_round_bets_open`, `open_rounds_batch`, `get_open_betting_tours`; нормализующая миграция `011_round_betting_cutoff` |
| `services/risk_engine.py` | Собственная проверка тура заменена вызовом общего гейта из `database.py` |

Тесты — **1 новый файл** (`tests/test_round_betting_cutoff.py`, 14 тестов),
**18 существующих файлов**, в которых поправлены только фикстуры состояния тура,
и **1 файл** (`tests/test_early_betting_line.py`), где по отдельному согласованию
переписан тест, кодировавший старое правило (подробности — п. 10).

Не тронуты: wallet, settlement, cashout, расчёт коэффициентов, Telegram-меню,
Mini App UI, LIVE, Laboratory, legacy `bet_markets` (схема сохранена и
синхронизируется как раньше), архитектура БД.

---

## 2. Какие функции изменены

**Новые (`database.py`):**

| Функция | Строка | Назначение |
|---|---|---|
| `close_round_betting_line(cursor, round_number, division_id, season_id)` | `database.py:4377` | Закрывает линию тура **в обеих схемах** сразу |
| `reopen_round_betting_line(cursor, round_number, division_id, season_id)` | `database.py:4417` | Обратная операция при повторном открытии линии |
| `evaluate_round_betting_gate(cursor, round_number, division_id, season_id)` | `database.py:4452` | **Единственный** источник правды о том, принимается ли ставка на тур |

**Изменённые:**

| Функция | Строка | Изменение |
|---|---|---|
| `init_db` (блок миграций) | `database.py:379` | Убран бэкфилл `bets_open = 1 WHERE is_open = 1`; добавлена одноразовая миграция `011_round_betting_cutoff` |
| `open_rounds_batch` | `database.py:3327` | Открытие туров пачкой закрывает их линии; захват `_bet_placement_lock` |
| `update_round_status` | `database.py:4501` | Всегда `bets_open = 0, bets_opened_at = NULL` + закрытие линии; убрана генерация линии на открываемый тур; захват `_bet_placement_lock` |
| `set_round_bets_open` | `database.py:4604` | Запрет открывать линию на тур с `is_open = 1`; закрытие/переоткрытие обеих схем; захват `_bet_placement_lock` |
| `get_open_betting_tours` | `database.py:6582` | Условие выборки приведено к правилу гейта; при истёкшем дедлайне закрываются обе схемы |
| `place_user_bet` | `database.py:6704` | Инлайновая проверка тура заменена вызовом `evaluate_round_betting_gate` |
| `RiskEngine.evaluate_bet` | `services/risk_engine.py:188` | Собственная проверка тура заменена вызовом того же гейта |

---

## 3. Как теперь определяется возможность поставить ставку

Единственным правилом, реализованным один раз — в
`evaluate_round_betting_gate` (`database.py:4452`). Функция выполняется
**внутри той же транзакции**, что и создание ставки, непосредственно перед
вставкой, и возвращает `(allowed, reason, message)`:

1. `round_number` отсутствует у матча → `ROUND_UNKNOWN`, **отказ**.
2. Строка тура не найдена → `ROUND_NOT_FOUND`, **отказ**.
   Это принципиально: старый код при отсутствии строки тура **пропускал
   проверку целиком**. Разрешающего fallback больше нет.
3. `rounds.is_open = 1` → `ROUND_STARTED`, **отказ**.
4. `COALESCE(rounds.bets_open, 0) = 0` → `LINE_CLOSED`, **отказ**.
5. Дедлайн тура истёк (`_parse_round_deadline`) → `DEADLINE_PASSED`, **отказ**.
6. Иначе — разрешено.

Выборка строки тура детерминирована и скоупится дивизионом и сезоном:

```sql
SELECT is_open, COALESCE(bets_open, 0) AS bets_open, deadline FROM rounds
WHERE round_number = ?
  AND (division_id = ? OR division_id IS NULL)
  AND (? IS NULL OR season_id = ? OR season_id IS NULL)
ORDER BY (division_id IS NULL) ASC, (season_id IS NULL) ASC, id ASC
LIMIT 1
```

Прежний межсезонный/междивизионный fallback
`ORDER BY is_open DESC, bets_open DESC, id DESC` удалён: он мог подобрать
чужой открытый тур и разрешить ставку.

UI ни на что не влияет — проверка целиком серверная и выполняется на уровне
репозитория, а не хендлера.

---

## 4. Что происходит при открытии тура

`update_round_status(N, is_open=True, ...)` (`database.py:4501`) в одной
транзакции под `_bet_placement_lock`:

1. `UPDATE rounds SET is_open = 1 ...`
2. `UPDATE rounds SET bets_open = 0, bets_opened_at = NULL ...` — **всегда**,
   и при открытии, и при закрытии тура.
3. `close_round_betting_line(...)` — закрытие линии в обеих схемах.
4. Из пост-транзакционного блока **удалён** вызов
   `generate_round_markets(N, ...)`: он заново активировал только что
   закрытую линию. Поведение «открытие тура N автоматически открывает раннюю
   линию на тур N+1» (`set_round_bets_open(N + 1, True)`) сохранено.

То же самое делает `open_rounds_batch` (`database.py:3327`) для диапазона туров.

Обратное направление закрыто в `set_round_bets_open` (`database.py:4604`):
если у тура `is_open = 1`, попытка открыть его линию возвращает `False` и
ничего не меняет. Итог: состояние `is_open = 1 AND bets_open = 1` недостижимо
ни одним штатным путём. Для уже развёрнутых баз, где это состояние создал
старый бэкфилл миграции, добавлена одноразовая нормализация
`011_round_betting_cutoff` (`database.py:387`):
`UPDATE rounds SET bets_open = 0, bets_opened_at = NULL WHERE is_open = 1`.

---

## 5. Как закрываются рынки

`close_round_betting_line` (`database.py:4377`) — один вызов вместо трёх
разрозненных мест:

```sql
-- legacy-схема (Telegram) — поведение сохранено без изменений
UPDATE bet_markets SET is_active = 0 WHERE tour = ?;

-- реляционная схема (Mini App)
UPDATE markets SET status = 'closed'
WHERE status IN ('open', 'suspended')
  AND match_id IN (SELECT id FROM matches WHERE round_number = ? AND <div/season scope>);
```

Уже рассчитанные рынки (`settled`, `voided`) не трогаются. Все параметры
связаны через placeholders — интерполяции имён/значений в SQL не добавлено.

Вызывается из: `update_round_status`, `open_rounds_batch`,
`set_round_bets_open(..., False)`, `get_open_betting_tours` (ветка истёкшего
дедлайна).

---

## 6. Как закрываются исходы

Тем же вызовом, вторым оператором:

```sql
UPDATE market_selections SET status = 'locked'
WHERE status = 'active'
  AND market_id IN (SELECT id FROM markets WHERE match_id IN (
        SELECT id FROM matches WHERE round_number = ? AND <div/season scope>));
```

`'locked'` — единственное корректное значение: CHECK-ограничение
`market_selections.status` допускает только `active | locked | voided`.

Обратная операция — `reopen_round_betting_line` (`database.py:4417`) — нужна
потому, что `services/odds_engine.get_or_create_market` / `get_or_create_selection`
используют обычный `INSERT` и не сбрасывают статус существующей строки. Без неё
повторно открытая линия была бы видна, но неставима. Переоткрытие затрагивает
только `markets.status = 'closed'` → `'open'` и `market_selections.status =
'locked'` → `'active'` у ещё не сыгранных матчей; `settled`, `voided` и
выставленный админом `suspended` не трогаются.

---

## 7. Какие API-пути защищены

Все создающие ставку пути сходятся в одну функцию, поэтому инвариант один, а
не три:

```
Telegram         handlers/betting.py:404  → asyncio.to_thread(database.place_user_bet, ...)
Mini App / REST  POST /api/predictions    → api/server.py:246 ─┐
Mini App / REST  POST /api/bets           → api/server.py:254 ─┴→ api/routes_predictions.py:60
                                                                  → database.place_user_bet(...)
```

`database.place_user_bet` (`database.py:6704`) вызывает
`evaluate_round_betting_gate` для каждого исхода купона.
`RiskEngine.evaluate_bet` (`services/risk_engine.py:188`), который отрабатывает
раньше в том же вызове, использует **ту же** функцию и отдаёт отказ под
существующим кодом `MARKET_SUSPENDED` — новых кодов решений не заведено.

`api/routes_predictions.py` не менялся: он целиком делегирует
`place_user_bet`, поэтому оба маршрута закрыты автоматически.
`handle_repeat_prediction` ставку не создаёт — он возвращает черновик купона,
который затем проходит тот же гейт при отправке.

В `place_user_bet` при разрешении коэффициента, помимо гейта тура, как и
раньше проверяются `markets.status` и `market_selections.status` для обеих
реляционных веток (`market_id + selection_id` и `match_id + selection_key`), а
legacy-ветка читает только `bet_markets.is_active = 1`, который теперь гасится
синхронно.

---

## 8. Как обработан race condition

Гонка «пользователь ставит» ↔ «админ открывает тур» закрыта **уже
существующим** примитивом — `_bet_placement_lock` (`threading.RLock`,
`database.py:6701`), который до этого захватывал только `place_user_bet` и
`execute_cashout`. Теперь его же захватывают все мутаторы состояния тура:

- `update_round_status` (`database.py:4519`)
- `set_round_bets_open` (`database.py:4628`)
- `open_rounds_batch` (`database.py:3341`)

Каждый — в форме `with _bet_placement_lock, transaction() as conn:`, то есть
замок берётся снаружи транзакции и держится до коммита. Следствие: открытие
тура не может вклиниться между проверкой гейта и вставкой ставки — либо ставка
целиком проходит до отсечки, либо гейт уже видит `is_open = 1`.

Новых блокировок, новых транзакционных обёрток и изменений в
`transaction()`/`get_connection()` не вводилось. Re-entrant-семантика
`transaction()` сохранена: `close_round_betting_line` и
`evaluate_round_betting_gate` принимают уже открытый `cursor` и не открывают
своих транзакций.

---

## 9. Какие тесты добавлены

Новый файл `tests/test_round_betting_cutoff.py` — 14 тестов, покрывающих все
11 обязательных сценариев:

| № | Тест | Сценарий |
|---|---|---|
| 1 | `test_01_closed_round_open_line_accepts_bet` | `is_open=0, bets_open=1` → ставка принята |
| 2 | `test_02_closed_round_closed_line_rejects_bet` | `is_open=0, bets_open=0` → отказ |
| 3 | `test_03_open_round_closed_line_rejects_bet` | `is_open=1, bets_open=0` → отказ |
| 4 | `test_04_forbidden_state_still_rejects_bet` | `is_open=1, bets_open=1` подделано в БД → отказ |
| 4b | `test_04b_forbidden_state_unreachable_through_public_api` | запрещённое состояние недостижимо штатными вызовами |
| 5 | `test_05_admin_opening_round_closes_line_everywhere` | админ открыл тур → `markets='closed'`, `market_selections='locked'`, `bet_markets.is_active=0`, последующая ставка отклонена |
| 6 | `test_06_closed_market_rejects_bet` | `markets.status='closed'` → отказ |
| 7 | `test_07_locked_selection_rejects_bet` | `market_selections.status='locked'` → отказ |
| 8 | `test_08_telegram_path_blocked_after_cutoff` | Telegram-путь после закрытия линии → отказ |
| 9 | `test_09_api_path_blocked_after_cutoff` | `POST /api/predictions` после закрытия линии → HTTP 400, ставки нет |
| 9b | `test_09b_api_path_accepts_bet_while_line_open` | тот же endpoint при открытой линии → HTTP 200 (правило не ломает штатный путь) |
| 10 | `test_10_stale_coupon_rejected` | купон собран до отсечки, отправлен после → отказ |
| 11 | `test_11_race_bet_versus_round_opening` | 8 параллельных ставок против открытия тура |
| 11b | `test_11b_round_opening_serialises_with_bet_placement` | открытие тура ждёт освобождения замка приёма ставок |

---

## 10. Результат целевых тестов

```
python -m pytest tests/test_round_betting_cutoff.py -q
..............                                                           [100%]
14 passed
```

Затем — существующие тесты betting / market / coupon / wallet / settlement:

```
python -m pytest tests/test_betting_engine.py tests/test_early_betting_line.py \
  tests/test_odds_engine.py tests/test_phase4_betting_experience.py \
  tests/test_phase4_1_acceptance.py tests/test_phase5_advanced_betting.py \
  tests/test_phase6_odds.py tests/test_production_audit.py \
  tests/test_phase3_operations.py tests/test_round_betting_cutoff.py -q

132 passed
```

### Правки фикстур в существующих тестах

В 18 файлах изменено **только состояние тура в setUp/фикстурах**, ни одно
утверждение (`assert*`) не изменено и не удалено. Две категории:

**(а) `is_open = 1` как синоним «ставки принимаются»** → заменено на
`is_open = 0, bets_open = 1`:
`test_betting_engine.py`, `test_phase4_1_acceptance.py`,
`test_phase4_betting_experience.py`, `test_phase5_advanced_betting.py`,
`test_phase6_odds.py`, `test_p0_nameerror_regressions.py`,
`test_phase9_atomic_betting.py`, `test_phase9_cashout.py`,
`test_phase9_concurrency.py`, `test_phase9_division_season.py`,
`test_phase9_exposure.py`, `test_phase9_odds.py`, `test_phase9_risk_engine.py`.

**(б) строки тура не было вовсе** — тесты опирались на старый разрешающий
fallback «нет тура → проверка пропускается». Добавлена явная строка тура с
открытой линией: `test_phase3_operations.py`, `test_production_audit.py`,
`test_phase6_1_redteam.py`, `test_phase9_miniapp.py`, `test_phase9_security.py`.

Категория (б) — прямое подтверждение исправляемой дыры: эти тесты ставили
ставки на матчи, у которых вообще не было записи тура.

### Существующий тест, кодировавший старое правило

`tests/test_early_betting_line.py`, тест
`test_08_opening_and_closing_round_syncs_line`, содержал утверждение:

```python
database.update_round_status(ROUND_PLAY, is_open=True, division_id=1, season_id=1)
self.assertEqual(info["is_open"], 1)
self.assertEqual(info["bets_open"], 1)   # ← СТАРОЕ правило
```

Это была не фикстура, а утверждение: тест требовал, чтобы открытие тура для
игры **открывало** его линию (`is_open = 1 AND bets_open = 1`) — ровно то
состояние, которое новое правило объявляет недостижимым. То же было записано в
докстринге файла, пункт 8.

Работа была остановлена, конфликт вынесен на решение — и по вашему
подтверждению тест приведён к новой бизнес-логике (переименован в
`test_08_opening_round_closes_its_betting_line`), а пункт 8 докстринга
переформулирован. Тест не ослаблен, а усилен: теперь он сначала выставляет
линию через `set_round_bets_open` и **проверяет предусловие**
(`bets_open == 1`), затем открывает тур и требует `bets_open == 0`, после чего
закрывает тур и требует, чтобы линия обратно **не** открылась. В прежней
редакции предусловия не было вовсе.

Остальная часть файла — тесты 1–7 и 9, включая «открытие тура N автоматически
открывает раннюю линию на тур N+1» — не изменялась и проходит.

---

## 11. Результат полного pytest

```
python -m pytest tests/

648 passed, 731 warnings, 3 subtests passed in 78.60s
```

Базовая линия до изменений: `634 passed, 3 subtests passed, 0 failed`.
Стало: `648 passed, 3 subtests passed, 0 failed` — те же 634 теста плюс 14
новых. Ни один тест не удалён и не отключён, ни одно утверждение не ослаблено.

---

## ВЕРДИКТ

Отсечка реализована и проверена: все 14 целевых тестов проходят, наборы
betting / market / coupon / wallet / settlement проходят, полный `pytest`
зелёный.

Достигнутый инвариант: ставка принимается **только** при
`rounds.is_open = 0 AND rounds.bets_open = 1`, проверка выполняется на сервере
внутри той же транзакции, что и создание ставки, одной функцией для Telegram,
Mini App и REST; открытие тура закрывает линию в обеих схемах; состояние
`is_open = 1 AND bets_open = 1` недостижимо, а если подделано в БД — ставка всё
равно отклоняется.

**ROUND BETTING CUTOFF: PASS**
