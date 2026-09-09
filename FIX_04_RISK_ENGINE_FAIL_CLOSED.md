# FIX 04 — RISK ENGINE MUST FAIL CLOSED

Отчёт по устранению проблемы №4 production-аудита Logovo.bet (RISK-01).

---

## 1. Найденная точка fail-open

**`database.py`, `place_user_bet()` — блок риск-проверки (до правки строки 6888–6927).**

```python
        # Phase 9: Risk Engine Evaluation
        try:
            from services.risk_engine import RiskEngine
            ...
            risk_decision = RiskEngine.evaluate_bet(...)
            if not risk_decision.allowed:
                ...  # маппинг бизнес-отказов
        except Exception as e:
            logger.debug(f"RiskEngine evaluation fallback: {e}")

        wallet = get_or_create_wallet(user_id)      # ← исполнение продолжалось отсюда
        ...
        INSERT INTO user_bets ...
        UPDATE user_wallets SET balance = balance - ? ...
        INSERT INTO bet_items ...
        INSERT INTO coin_transactions ...
```

Это **единственная** точка вызова риск-движка в продовом коде. Поиск по проекту
(`RiskEngine`, `evaluate_bet`, `risk_result`, `risk_check`) даёт ровно один
исполняемый call site — `database.py:6914`; остальные вхождения находятся в
документах аудита и в тестах. Оба продовых входа в размещение ставки
(`api/routes_predictions.py:60` для Mini App/REST и `handlers/betting.py:404`
для Telegram) идут через `database.place_user_bet`, поэтому одна точка отказа
покрывает оба пути.

## 2. Почему это позволяло ставке продолжаться

`except Exception` перехватывал **любое** исключение риск-движка и не менял
поток исполнения: после `logger.debug` управление просто переходило к
следующей строке — получению кошелька, расчёту коэффициента, `INSERT INTO
user_bets` и списанию монет. Внутренняя поломка обязательной проверки
трактовалась как её успешное прохождение.

Последствия при деградации RiskEngine:

- терялись дневные лимиты, лимиты пользователя и дивизиона, защита от
  rapid-betting, лимиты экспозиции рынка, проверка `ODDS_STALE`;
- терялась проверка статуса рынка (`suspended` / `closed` / `settled`) в той
  ветке резолва коэффициента, где `place_user_bet` её сам не делает;
- запись шла на уровне `debug`, то есть при штатном уровне логирования INFO
  обход риск-контроля **не оставлял следа вообще**;
- дополнительно `except` накрывал и сам маппинг отказов: исключение внутри
  ветки `INSUFFICIENT_BALANCE` (вызов `get_or_create_wallet`) тоже приводило к
  продолжению размещения ставки.

## 3. Изменённые файлы

| Файл | Что изменено |
|---|---|
| `database.py` | `place_user_bet`: блок риск-проверки переведён в fail-closed (строки 6888–6963) |
| `tests/test_risk_engine_fail_closed.py` | **новый** файл, 12 тестов |

Новый RiskEngine не создавался, вторая система risk checks не создавалась,
существующие проверки не дублировались, `services/risk_engine.py` не менялся.
Схема БД, миграции, settlement, cashout, кошелёк, odds engine, odds history,
Mini App UI, Telegram-меню, LIVE и Laboratory не тронуты.

## 4. Как обрабатывается исключение сейчас

Разделены два случая:

- **A. Пользователь нарушил правило риска.** `RiskEngine` возвращает
  `RiskDecision(allowed=False, reason=...)`. Обработка вынесена **из** `try`
  в самостоятельный блок `if not risk_decision.allowed:` — это обычный
  бизнес-отказ с прежними кодами (`MIN_STAKE`, `MAX_STAKE`, `MAX_PAYOUT`,
  `INSUFFICIENT_BALANCE`, `MARKET_SUSPENDED`, `INVALID_MARKET`, `RISK_LIMIT`,
  `DAILY_LIMIT`, `ODDS_STALE`, `EXPOSURE_LIMIT`). Поведение не изменилось.
- **B. Риск-движок сломался.** Любое исключение при получении контекста матча,
  импорте движка или самом `evaluate_bet` (ошибка БД, `KeyError`, `TypeError`,
  `RuntimeError`, битые внутренние данные) — это **внутренняя ошибка риска**,
  и она отклоняет операцию:

```python
        try:
            from services.risk_engine import RiskEngine
            ...
            risk_decision = RiskEngine.evaluate_bet(...)
            if risk_decision is None or not hasattr(risk_decision, "allowed"):
                raise RuntimeError("RiskEngine returned a malformed decision object")
        except Exception:
            _risk_match_ids = [s.get("match_id") for s in selections if isinstance(s, dict)]
            logger.exception(
                "RISK_CHECK_UNAVAILABLE: RiskEngine failed during place_user_bet — "
                "bet rejected (fail-closed). "
                f"user_id={user_id} amount={amount} match_ids={_risk_match_ids} "
                f"round_number={risk_ctx_round} division_id={div_id} season_id={risk_ctx_season}"
            )
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            return False, {
                "error": "RISK_CHECK_UNAVAILABLE",
                "message": "Не удалось проверить прогноз. Ставка не принята, монеты не списаны. Попробуйте позже."
            }
```

Пользователь получает обычный безопасный отказ. Ни stack trace, ни текст
исключения, ни имя внутреннего класса наружу не уходят — тесты проверяют
отсутствие подстрок `Traceback`, `RuntimeError`, `KeyError`, `OperationalError`
и текста исходного исключения в ответе.

**Про код ошибки.** Подходящего существующего кода для «обязательная проверка
не смогла выполниться» в проекте не было: все имеющиеся коды
(`MARKET_SUSPENDED`, `RISK_LIMIT`, `MAX_BET_EXCEEDED`, `ODDS_CHANGED`,
`IDEMPOTENCY_KEY_REUSED`, `LOGOVO_LOCKDOWN`, …) описывают конкретные
бизнес-причины и их переиспользование дезинформировало бы и пользователя, и
логи. Введён один код — `RISK_CHECK_UNAVAILABLE`.

**Про HTTP-контракт.** Отдельная ветка в `api/routes_predictions.py` не
добавлялась: там уже есть общая ветка для структурированных ошибок
(`routes_predictions.py:93`), которая отдаёт `HTTP 400` с полем `message`.
Это и есть «безопасный бизнес-ответ по существующему контракту» — 500 со
стеком не возвращается. Telegram-обработчик (`handlers/betting.py:409-418`)
так же берёт `res.get("message", err_code)` и показывает алерт, а купон
восстанавливает в `context.user_data`.

## 5. Логирование ошибки

- Уровень **ERROR**, через `logger.exception(...)` — то есть с исходным
  traceback в логе сервера (`record.exc_info` не пуст; проверяется тестом).
- Логгер — `database` (`logging.getLogger(__name__)` в `database.py`).
- Запись однозначно идентифицирует сбой риск-движка и операцию:
  `RISK_CHECK_UNAVAILABLE: RiskEngine failed during place_user_bet — bet
  rejected (fail-closed). user_id=… amount=… match_ids=[…] round_number=…
  division_id=… season_id=…`.
- Контекст тура/дивизиона/сезона собирается заранее в переменные
  (`risk_ctx_round`, `div_id`, `risk_ctx_season`), инициализированные `None`,
  поэтому лог не падает, даже если исключение произошло до их заполнения.
- **Не логируется:** Telegram `initData`, токены, пароли, платёжные секреты,
  персональные данные. В запись попадают только числовые технические
  идентификаторы (`user_id` — это Telegram ID, уже используемый как ключ во
  всех логах проекта) и сумма ставки.

## 6. Чем гарантируется отсутствие списания и ставки

1. **Порядок операций.** В `place_user_bet` риск-проверка идёт **до** всего
   финансового: до `get_or_create_wallet`, до валидации исходов, до
   `INSERT INTO user_bets`, до `UPDATE user_wallets`, до `INSERT INTO
   bet_items` и `INSERT INTO coin_transactions`. Ветка исключения делает
   `return` из этой точки — физически ни один из этих запросов не выполняется.
   Архитектура проекта (RISK CHECK → BET CREATION → WALLET DEBIT) не
   переписывалась, проверено только то, что исключение невозможно протащить
   до успешного списания.
2. **Транзакция.** Вся функция работает внутри `with _bet_placement_lock,
   transaction() as conn:`. Перед отказом выполняется `conn.rollback()`, что
   снимает любые побочные записи, которые риск-движок мог успеть сделать до
   падения (например, `risk_alerts`). Тот же приём уже используется в этой
   функции при недостатке средств (`database.py:7132`).
3. **Idempotency.** Запись идемпотентности создаётся только вместе со строкой
   `user_bets` (`idempotency_key` + `idempotency_payload_hash` — колонки той же
   строки). Раз `INSERT` не выполняется, ложного успешного idempotency-состояния
   не появляется, и тот же ключ остаётся пригодным для повторной попытки.
   Существующий idempotency-движок не переписывался.
4. **Settlement / payout.** Расчёт и резервирование выплаты происходят от
   `bet_id`; ставки нет — значит нет ни settlement-записи, ни резерва.

Тесты проверяют это как снимок состояния: `user_bets`, `bet_items`,
`coin_transactions` и баланс кошелька до и после операции должны совпадать.

## 7. Купон / экспресс

`RiskEngine.evaluate_bet` вызывается **один раз на весь купон** и сам
проходит по всем исходам. Исключение на любой ноге прерывает всю оценку и
попадает в обработчик выше — то есть отклоняется **весь купон целиком**, а не
отдельная нога. Цикл после ошибки риск-движка не продолжается, частичного
размещения не бывает. Дополнительно всё размещение атомарно за счёт
существующей `transaction()`. Сценарий «первая нога прошла, вторая уронила
проверку изнутри цикла» покрыт тестом 6.

## 8. Добавленные тесты

`tests/test_risk_engine_fail_closed.py` — 12 тестов, изолированная временная БД
на каждый тест (`database.DB_PATH` восстанавливается в `tearDown`).

| Тест | Что проверяет |
|---|---|
| `test_01_risk_allow_places_bet` | ALLOW → ставка создана, баланс уменьшился |
| `test_02_risk_reject_creates_no_bet` | REJECT → бизнес-отказ, ставки нет, деньги на месте |
| `test_03_runtime_error_rejects_and_logs_error` | `RuntimeError("simulated risk failure")` → отказ, нет ставки/списания/транзакции, ERROR-запись с `exc_info` и контекстом, без утечки внутренних деталей, нет idempotency-записи |
| `test_04_key_error_rejects_without_side_effects` | `KeyError` → REJECT / NO BET / NO DEBIT |
| `test_05_database_error_rejects_without_side_effects` | `sqlite3.OperationalError` → REJECT / NO BET / NO DEBIT |
| `test_06_express_partial_failure_places_nothing` | Экспресс A→ok, B→исключение внутри цикла: ничего не размещено даже частично |
| `test_07_rest_control_bet_is_accepted` | Контроль REST: штатная ставка через `POST /api/predictions` → 200 (защита от «зелёного по посторонней причине») |
| `test_08_rest_risk_exception_is_safe_rejection` | REST при сбое риска → HTTP 400 по существующему контракту, без stack trace, ставка не создана |
| `test_09_telegram_path_gets_safe_rejection` | Telegram-путь (`place_user_bet` напрямую, как в `handlers/betting.py:404`) → безопасное сообщение отказа |
| `test_10_fix01_line_gate_survives_permissive_risk_engine` | FIX-01: при полностью разрешающем RiskEngine закрытая линия и открытый для игры тур всё равно отклоняют ставку; исключение риска — тоже |
| `test_11_fix03_scope_isolation_survives_permissive_risk_engine` | FIX-03: открытый тур чужого сезона и чужого дивизиона с тем же номером не разрешает ставку даже при разрешающем RiskEngine |
| `test_12_fail_open_detector` | Прямой детектор fail-open: `bet_count_before == bet_count_after`, `balance_before == balance_after`, операция отклонена; повтор с тем же ключом после восстановления движка проходит |

**Тесты не вакуумны.** Контрольная проверка: код `place_user_bet` был временно
возвращён к старой fail-open семантике (исключение → `logger.debug` →
`risk_decision = ALLOW` → продолжение) и новый файл прогнан на нём:

```
FAILED test_03_runtime_error_rejects_and_logs_error
FAILED test_04_key_error_rejects_without_side_effects
FAILED test_05_database_error_rejects_without_side_effects
FAILED test_06_express_partial_failure_places_nothing
FAILED test_09_telegram_path_gets_safe_rejection
FAILED test_12_fail_open_detector
FAILED test_08_rest_risk_exception_is_safe_rejection
7 failed, 5 passed
```

То есть требуемый детектор (`test_12`) и остальные сценарии внутренней ошибки
действительно падают на старой реализации. После восстановления правки —
12 passed.

Существующие тесты не удалялись, не отключались и не ослаблялись. Тестов,
утверждающих «RiskEngine exception → bet allowed», в проекте не нашлось —
править было нечего.

## 9. Результаты целевых прогонов

```
python -m pytest tests/test_risk_engine_fail_closed.py
→ 12 passed, 84 warnings in 16.44s
```

Целевой набор из задания (файла `tests/test_phase9_wallet.py` в проекте нет,
использованы фактически существующие файлы):

```
python -m pytest tests/test_risk_engine_fail_closed.py tests/test_phase9_risk_engine.py \
                 tests/test_round_betting_cutoff.py tests/test_round_betting_isolation.py \
                 tests/test_betting_engine.py tests/test_phase5_advanced_betting.py \
                 tests/test_phase9_atomic_betting.py tests/test_phase9_concurrency.py
→ 85 passed, 84 warnings in 13.38s
```

Дополнительно — кошелёк, экспозиция, cashout, Mini App, безопасность,
лимиты и конкурентность:

```
python -m pytest tests/test_early_betting_line.py tests/test_phase4_betting_experience.py \
                 tests/test_phase9_exposure.py tests/test_phase9_cashout.py \
                 tests/test_phase9_miniapp.py tests/test_phase9_security.py \
                 tests/test_phase9_division_season.py tests/test_phase10_concurrency.py \
                 tests/test_betting_api_v2.py tests/test_production_audit.py tests/test_lockdown.py
→ 93 passed, 337 warnings in 19.77s
```

## 10. Полный прогон

```
python -m pytest tests/
→ 670 passed, 815 warnings, 3 subtests passed in 44.02s
```

До FIX-04 суммарно было 658 passed; +12 — ровно новые тесты. Ни один
существующий тест не сломался.

## 11. FIX-01 сохранён

Второй round gate не создавался. Единственная реализация правила приёма
прогнозов — `database.evaluate_round_betting_gate`, её вызывают и
`place_user_bet` (`database.py:7003`), и `RiskEngine.evaluate_bet`
(`services/risk_engine.py:188`). Правка FIX-04 находится выше по коду и гейта
не касается.

Инварианты подтверждены прогонами `tests/test_round_betting_cutoff.py`,
`tests/test_early_betting_line.py` и новым `test_10`:

| `is_open` | `bets_open` | Результат |
|---|---|---|
| 0 | 1 | ALLOWED |
| 0 | 0 | REJECTED |
| 1 | 0 | REJECTED |
| 1 | 1 | REJECTED |
| строки тура нет | — | REJECTED |
| дедлайн истёк | — | REJECTED |
| исключение RiskEngine | — | **REJECTED** (новое) |

`test_10` дополнительно доказывает независимость: даже с полностью
разрешающим (замоканным на ALLOW) риск-движком закрытая линия и открытый для
игры тур ставку отклоняют.

## 12. FIX-03 сохранён

Строгая изоляция `season_id + division_id + round_number` не ослаблена.
Область видимости по-прежнему выводится из самого матча
(`division_id`, `round_number`, `season_id` строки `matches`) и передаётся в
гейт; при отсутствии корректной scoped-строки тура ставка отклоняется.

Новый запрос контекста для лога (`SELECT division_id, round_number, season_id
FROM matches WHERE id = ?`) читает те же поля первого исхода купона и
используется только для логирования и для аргумента `division_id`
риск-движка — ровно как раньше; на выбор тура он не влияет.

Подтверждено прогоном `tests/test_round_betting_isolation.py` (10 тестов),
`tests/test_phase9_division_season.py` и новым `test_11`, где открытый тур
чужого сезона и чужого дивизиона с тем же номером не разрешает ставку даже
при разрешающем риск-движке.

---

## Что осознанно не менялось

- **`services/risk_engine.py`** — не тронут. `evaluate_bet` по-прежнему
  выбрасывает исключение наружу, а не «проглатывает» его внутри: единственный
  ответственный за политику отказа — call site, где принимается финансовое
  решение. Так ошибка не может быть тихо поглощена в источнике.
- **Глобальная замена `except Exception`** не производилась. Изменён ровно
  один обработчик — тот, который был способен разрешить финансовую операцию.
- **`services/betting_limits.py:101-113`** (`get_limit`: `except Exception:
  pass` → значение по умолчанию) оставлен как есть. Это не пропуск проверки:
  проверка выполняется, но на дефолтных лимитах вместо кастомных. Отмечаю как
  отдельное наблюдение на будущее — если админ выставил **более строгий**
  лимит, а чтение `risk_limits_config` упало, применится более мягкий дефолт.
  Функция используется и админскими read-путями, поэтому её поведение выходит
  за рамки FIX-04.
- `api/routes_predictions.py` и `handlers/betting.py` не изменялись — их
  существующие ветки обработки структурированных ошибок уже дают безопасный
  ответ для нового кода.

---

**RISK ENGINE FAIL-CLOSED: PASS**
