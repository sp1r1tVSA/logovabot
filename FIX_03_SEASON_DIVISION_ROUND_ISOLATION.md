# FIX 03 — STRICT SEASON + DIVISION + ROUND ISOLATION

Отчёт по исправлению проблемы №3: изоляция линии Logovo.bet по
`season_id + division_id + round_number`.

**Целевой инвариант**

> ROUND 5 / DIVISION 1 / SEASON 2026 не влияет ни на
> ROUND 5 / DIVISION 2 / SEASON 2026, ни на
> ROUND 5 / DIVISION 1 / SEASON 2027, ни на
> ROUND 6 / DIVISION 1 / SEASON 2026.

---

## 1. Изменённые файлы

| Файл | Что изменено |
|---|---|
| `database.py` | Новый помощник `_round_scope_divisions`; строгий scope в `close_round_betting_line`, `reopen_round_betting_line`, `evaluate_round_betting_gate`; per-scope раскладка в `update_round_status`, `set_round_bets_open`, `open_rounds_batch`; scope в трёх read-путях `get_open_betting_tours`, `get_active_bet_markets`, `get_bet_market_by_match_id`; в `get_matches_by_round` добавлена колонка `m.season_id` |
| `tests/test_round_betting_isolation.py` | **Новый файл**, 10 сценариев изоляции |

Схема БД **не менялась**: миграций не добавлено, таблицы не переписаны,
колонки не добавлены. Ничего не удалено и не отключено.

---

## 2. Фактическая схема, на которую опирается решение

Определена по коду проекта, не предполагалась:

| Таблица | Факт |
|---|---|
| `rounds` | `season_id INTEGER NOT NULL DEFAULT 1`, `division_id INTEGER NOT NULL DEFAULT 1`, **`UNIQUE(season_id, division_id, round_number)`** (перестройка `rounds_v3`, `database.py:204-236`) |
| `matches` | `division_id INTEGER DEFAULT NULL` — **nullable** (`SAFE_COLUMNS`, `database.py:355`); `season_id INTEGER NOT NULL DEFAULT 1` |
| `bet_markets` (legacy, Telegram) | `match_id INTEGER NOT NULL UNIQUE`, `tour INTEGER NOT NULL`; **колонок `division_id` / `season_id` нет** |
| `markets` | `status CHECK IN ('open','suspended','closed','settled','voided')`, связь со scope только через `match_id` |
| `market_selections` | `status CHECK IN ('active','locked','voided')` — статуса `settled`/`suspended` нет, поэтому закрытие = `locked` |

Из этого следуют два ключевых вывода:

1. `UNIQUE(season_id, division_id, round_number)` уже гарантирует, что точная
   выборка строки тура возвращает **не более одной** строки. Точный scope
   достижим **без изменения схемы** — опасная миграция не требуется.
2. `bet_markets` физически не хранит сезон/дивизион. Колонки **не добавлялись
   вслепую**: единственная однозначная связь legacy-рынка со scope — это
   `match_id → matches(round_number, division_id, season_id)`, и закрытие/выборка
   теперь идут через подзапрос по `matches`.

**Соглашение проекта `division_id IS NULL == дивизион 1`** взято не из
предположения, а из существующего production-кода `place_user_bet`
(`database.py:6960`), где матч без дивизиона уже трактуется как дивизион 1.
Поэтому scope матчей выражается как `COALESCE(division_id, 1) = ?`, а не как
отбрасывание таких строк — иначе legacy-матч выпал бы из закрытия линии
и остался бы ставимым после того, как линия «закрыта».

---

## 3. Какие SQL-запросы были опасны

### 3.1 `UPDATE bet_markets SET is_active = 0 WHERE tour = ?` — главная утечка

`close_round_betting_line` гасила legacy-линию **по одному лишь номеру тура**.
Закрытие Тура 5 Дивизиона 1 Сезона 2026 обнуляло `is_active` у рынков
Тура 5 **всех** дивизионов и **всех** сезонов. Это же затрагивало
`update_round_status`, `set_round_bets_open`, `open_rounds_batch` и ветку
истёкшего дедлайна в `get_open_betting_tours` — все они вызывают эту функцию.

### 3.2 Расширяющие `OR ... IS NULL` в выборке матчей

`close_/reopen_round_betting_line` отбирали матчи как
`(? IS NULL OR division_id = ? OR division_id IS NULL)`. Вызов с `division_id=None`
попадал во **все** дивизионы сразу; вызов с конкретным дивизионом дополнительно
захватывал чужие legacy-матчи с `division_id IS NULL`.

### 3.3 Разрешающий fallback в `evaluate_round_betting_gate`

Было:

```sql
WHERE round_number = ?
  AND (division_id = ? OR division_id IS NULL)
  AND (? IS NULL OR season_id = ? OR season_id IS NULL)
ORDER BY (division_id IS NULL) ASC, (season_id IS NULL) ASC, id ASC LIMIT 1
```

При `season_id = None` условие сезона вырождалось в «любой сезон», и
`ORDER BY ... id ASC` выбирал **произвольную** строку того же номера тура.
Открытая линия неактивного сезона могла разрешить ставку в активном.

### 3.4 JOIN `rounds` только по номеру тура в read-путях

* `get_bet_market_by_match_id`: `JOIN rounds r ON m.round_number = r.round_number`
  — открытый тур Дивизиона 2 или прошлого сезона делал матч Дивизиона 1
  «доступным для ставки».
* `get_active_bet_markets`: тот же JOIN с расширением
  `(r.division_id = m.division_id OR r.division_id IS NULL OR m.division_id IS NULL)`
  и **без фильтра по сезону вообще**.
* `get_open_betting_tours`: тот же JOIN; при `season_id=None` фильтра сезона не
  было — в линию попадали туры завершённых сезонов.

### 3.5 Молча не работавший фильтр сезона в генерации линии

`services/betting_engine.generate_round_markets` досеивает матчи по сезону в
Python: `m.get("season_id") in (season_id, None)`. Но `get_matches_by_round` в
ветке с дивизионом **не возвращала колонку `season_id`** — фильтр всегда
пропускал всё, и открытие линии одного сезона переактивировало `bet_markets`
матчей того же тура/дивизиона в другом сезоне.

---

## 4. Как scope обеспечивается теперь

### 4.1 Запись в линию невозможна без явного scope

`close_round_betting_line` (`database.py:4419`) и `reopen_round_betting_line`
(`database.py:4474`) сменили сигнатуру на обязательные `division_id`, `season_id`
и **бросают `ValueError`**, если передан `None`:

```python
if division_id is None or season_id is None:
    raise ValueError(
        "close_round_betting_line requires explicit division_id and season_id: "
        "глобальный scope для операций с линией недопустим"
    )
```

Глобальный scope для betting-операций стал недостижим не по соглашению,
а физически.

### 4.2 Legacy-таблица скоупится через `matches`

```sql
UPDATE bet_markets SET is_active = 0
WHERE match_id IN (
    SELECT id FROM matches
    WHERE round_number = ?
      AND COALESCE(division_id, 1) = ?
      AND COALESCE(season_id, 1) = ?
)
```

Аналогично `markets` (через `match_id`) и `market_selections`
(через `market_id → markets → match_id`). Все запросы параметризованы;
новых подстановок в SQL не добавлено (правило CLAUDE.md).
Рассчитанные рынки `settled` / `voided` по-прежнему не трогаются.

### 4.3 Гейт читает строго свою строку

```sql
SELECT is_open, COALESCE(bets_open, 0) AS bets_open, deadline FROM rounds
WHERE round_number = ? AND division_id = ? AND season_id = ? LIMIT 1
```

Без `OR ... IS NULL`, без `ORDER BY`, без «подходящей» строки.
`UNIQUE(season_id, division_id, round_number)` гарантирует единственность.

**Fail-safe при отсутствии строки.** Если точного scoped-тура нет —
`ROUND_NOT_FOUND`, ставка отклоняется. Чужой активный тур не подставляется
никогда. Разрешающего fallback в гейте не существует.

**Fail-safe при незаданном сезоне.** `season_id = None` → активный сезон
(`get_active_season()`), а не «любой сезон». Это сужение, не расширение:
раньше отсутствие сезона открывало доступ ко всем, теперь — ровно к одному.

### 4.4 Глобальные админ-операции раскладываются по scope

**Назначение `division_id=None` было проверено, а не исправлено механически.**
Глобальная админ-панель туров (`admin_manage_round_*`, `admin_bets_open_round_*`,
`admin_open_batch_*` в `handlers/admin.py`) не имеет селектора дивизиона и
объявляет результат в основную группу — это **намеренная общелиговая операция**
над состоянием игры. Дивизионный путь существует отдельно
(`admin_div_manage_matches`, `handlers/admin.py:1390`), но он матчи только
просматривает. Требовать дивизион в этих хендлерах означало бы менять
Telegram-меню, что запрещено брифом.

Принятое решение: **операция остаётся глобальной, но записи в линию — нет.**
Новый помощник `_round_scope_divisions(cursor, round_number, season_id)`
(`database.py:4388`) разворачивает такую операцию в конкретный список
дивизионов **внутри одного сезона** (по строкам `rounds` и по матчам тура,
с учётом `COALESCE(division_id, 1)`), и линия закрывается/открывается отдельно
по каждому `(season, division, round)`:

```python
for scope_div_id in _round_scope_divisions(cursor, round_number, s_id):
    close_round_betting_line(cursor, round_number, division_id=scope_div_id, season_id=s_id)
```

Применено в `update_round_status` (`database.py:4667`), `open_rounds_batch`
(`database.py:3376`), `set_round_bets_open` (`database.py:4761`).
Чужие сезоны не затрагиваются ни при каких обстоятельствах.

### 4.5 Read-пути привязаны к полному ключу

* `get_open_betting_tours` (`database.py:6672`) — JOIN
  `m.round_number = r.round_number AND COALESCE(m.division_id,1) = r.division_id
  AND COALESCE(m.season_id,1) = r.season_id`; сезон по умолчанию — активный;
  фильтр дивизиона сужен до `r.division_id = ?`.
* `get_active_bet_markets` (`database.py:6748`) — тот же JOIN; добавлен
  параметр `season_id` (по умолчанию активный сезон); фильтр дивизиона —
  `COALESCE(m.division_id, 1) = ?`.
* `get_bet_market_by_match_id` (`database.py:6800`) — строка тура берётся
  строго по scope самого матча.

### 4.6 Фильтр сезона в генерации линии заработал

В `get_matches_by_round` (`database.py:3468`) в выборку добавлена колонка
`m.season_id`. Логика функции не менялась — восстановлена работоспособность
уже написанного, но бездействовавшего фильтра сезона в
`generate_round_markets`. Сам odds engine не тронут.

---

## 5. Добавленные тесты

`tests/test_round_betting_isolation.py` — 10 сценариев. Каждый работает на
отдельном временном файле БД (`database.DB_PATH` подменяется и восстанавливается
в `tearDown`), поэтому два сезона создаются свободно и не задевают league.db.
Фикстура — четыре независимых scope с общими номерами туров:
`SA/D1/R5`, `SA/D2/R5`, `SB/D1/R5`, `SA/D1/R6`.

| # | Тест | Проверяет |
|---|---|---|
| 1 | `test_01_opening_round_for_play_closes_only_its_division_line` | Открытие Тура 5 Д1 для игры гасит линию Д1; линия Д2 (тот же тур, тот же сезон) остаётся открытой — включая `bet_markets.is_active` и `markets.status` |
| 2 | `test_02_opening_round_for_play_closes_only_its_season_line` | То же между сезонами: Сезон A закрыт, Сезон B нетронут |
| 3 | `test_03_closing_line_touches_only_its_own_scope` | Закрытие линии `SA/D1/R5` не трогает `SA/D2/R5`, `SB/D1/R5`, `SA/D1/R6` — по `bets_open`, `bet_markets`, `markets`, `market_selections` |
| 4 | `test_04_reopening_line_touches_only_its_own_scope` | Повторное открытие одного scope не открывает остальные (все четыре предварительно закрыты) |
| 5 | `test_05_legacy_markets_isolated_between_divisions_of_same_round` | Legacy `bet_markets` изолированы; `get_active_bet_markets` не отдаёт чужой дивизион и чужой сезон; `get_bet_market_by_match_id` не «оживает» из-за открытого тура другого сезона |
| 6 | `test_06_market_selections_isolated_between_scopes` | `market_selections` соседних scope остаются `active`, свой — `locked` |
| 7 | `test_07_betting_gate_isolated_between_divisions` | Гейт: Д1 → `LINE_CLOSED`, Д2 → разрешено; то же на реальном `place_user_bet` |
| 8 | `test_08_betting_gate_isolated_between_seasons` | Гейт между сезонами; плюс: при `season_id=None` гейт берёт активный сезон и **не** подставляет открытый тур неактивного |
| 9 | `test_09_missing_scoped_round_rejects_and_does_not_borrow_other_scope` | `SA/D2/R5` открыт, строки `SA/D1/R5` нет → гейт `ROUND_NOT_FOUND`, `place_user_bet` отклоняет, чужой scope не изменён |
| 10 | `test_10_concurrent_line_opening_keeps_scopes_separate` | Одновременное открытие двух scope из разных потоков: оба открылись, чужие — нет; последующее закрытие одного не задевает второй |

### 5.1 Проверка, что тесты не «зелёные вхолостую»

Каждое ключевое исправление временно откатывалось, тесты запускались, файл
восстанавливался:

| Откат | Результат |
|---|---|
| `UPDATE bet_markets ... WHERE tour = ?` | **4 failed** (тесты 01, 02, 03, 05) |
| Старая выборка гейта с `(? IS NULL OR season_id = ?...)` + `ORDER BY` | **1 failed** (тест 08) |
| `JOIN rounds r ON m.round_number = r.round_number` в `get_bet_market_by_match_id` | **1 failed** (тест 05) |

---

## 6. Результат целевого набора тестов

```
python -m pytest tests/test_round_betting_isolation.py
10 passed in 6.35s
```

```
python -m pytest tests/test_round_betting_cutoff.py tests/test_round_betting_isolation.py \
                 tests/test_betting_engine.py tests/test_early_betting_line.py \
                 tests/test_odds_engine.py tests/test_phase5_advanced_betting.py \
                 tests/test_phase9_division_season.py tests/test_phase9_risk_engine.py
80 passed in 17.53s
```

Дополнительно прогнаны смежные файлы, читающие линию по дивизионам
(`test_phase4_1_acceptance.py`, `test_phase4_betting_experience.py`) — 121 passed.

---

## 7. Полный прогон

```
python -m pytest tests/
658 passed, 731 warnings, 3 subtests passed in 62.85s
```

Ноль падений. До FIX-03 полный прогон давал 648 passed; +10 — ровно новые тесты
изоляции. Ни один существующий тест не удалён, не отключён и не ослаблен.

### 7.1 Инварианты FIX-01 сохранены

Проверено `tests/test_round_betting_cutoff.py` и `tests/test_early_betting_line.py`
(оба зелёные, без правок в этом фиксе):

| Состояние | Ожидание | Статус |
|---|---|---|
| `is_open=0 + bets_open=1` | ALLOWED | ✅ |
| `is_open=0 + bets_open=0` | REJECTED | ✅ |
| `is_open=1 + bets_open=0` | REJECTED | ✅ |
| `is_open=1 + bets_open=1` | REJECTED | ✅ |
| строка тура отсутствует | REJECTED | ✅ |
| дедлайн истёк | REJECTED | ✅ |
| `_bet_placement_lock` вокруг смены состояния тура | сохранён | ✅ |

Бизнес-логика FIX-01 не менялась — менялся только scope, в котором она
применяется.

---

## 8. Что намеренно НЕ менялось

1. **Схема БД.** Ни одной миграции. `season_id` / `division_id` в `bet_markets`
   не добавлялись: связь со scope выражена через `matches`, чего достаточно.
2. **Общий рефакторинг betting-системы.** Две параллельные схемы линии
   (legacy `bet_markets` + реляционные `markets`/`market_selections`) оставлены
   как есть; исправлен scope, а не архитектура.
3. **Mini App UI, Telegram-меню, LIVE, settlement, wallet, odds engine,
   Laboratory.** Ни один файл `web/`, `api/routes_*.py`, `services/odds_engine.py`,
   `services/settlement*.py`, `handlers/*.py` не изменён.
4. **Глобальные админ-операции над туром.** `admin_manage_round_*`,
   `admin_bets_open_round_*`, `admin_open_batch_*` остались общелиговыми —
   это их назначение (нет селектора дивизиона, объявление в основную группу).
   Изменён не контракт хендлера, а способ записи: раскладка по scope
   внутри `database.py`.
5. **`(season_id = ? OR season_id IS NULL)` в UPDATE по таблице `rounds`.**
   Это унаследованные ветки для строк, существовавших до перестройки `rounds_v3`.
   После неё обе колонки `NOT NULL`, ветка мертва и изоляцию не нарушает
   (сезон в условии всё равно указан явно). Механически не вычищалась —
   удаление дало бы нулевой эффект при ненулевом риске.
6. **`RiskEngine` (`services/risk_engine.py:182-197`).** Уже извлекает
   `round_number` / `division_id` / `season_id` из строки матча и вызывает общий
   гейт — правку не требовал и не получил.
7. **`api/routes_predictions.py`.** Полностью делегирует `place_user_bet`;
   не изменён.
8. **`handlers/betting.py:187`** вызывает `get_active_bet_markets(tour_num)` без
   дивизиона — Telegram по существующему контракту показывает линию всех
   дивизионов активного сезона. Контракт не менялся; безопасность обеспечена
   тем, что при размещении ставки гейт всё равно проверяет scope конкретного
   матча.
9. **Существующие тесты.** Ни одной правки: ни удалений, ни `skip`,
   ни ослабленных assertions.

---

## 9. Архитектурные ограничения, требующие отдельного решения

Опасных миграций не потребовалось, но два момента стоит зафиксировать явно —
они **не** блокируют изоляцию, однако относятся к следующим этапам:

1. **`bet_markets` остаётся без собственного scope.** Изоляция держится на
   `match_id → matches`. Пока `matches.division_id` nullable, корректность
   опирается на соглашение «NULL = дивизион 1». Соглашение уже действовало в
   `place_user_bet` до этого фикса; при желании ужесточить его нужно отдельное
   решение (backfill `matches.division_id` + `NOT NULL`), а это изменение схемы,
   которое здесь сознательно не делалось.

2. **`get_matches_by_round(round, division_id=...)` не фильтрует по сезону
   на уровне SQL.** Для betting это закрыто (фильтр в `generate_round_markets`
   заработал после добавления колонки), но у функции есть и не-betting
   потребители — админские списки матчей. Добавление сезона в её `WHERE`
   изменило бы их поведение и выходит за рамки FIX-03. Участок:
   `database.py:3472-3489`.

---

## Вердикт

**SEASON + DIVISION + ROUND ISOLATION: PASS**
