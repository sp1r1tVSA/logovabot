# FIX-06 — MINI APP FEATURE ACCESS MUST FAIL CLOSED

**Проблема аудита:** AUTH-01 — проверка доступа Mini App к feature-флагу работала fail-open.

**Вердикт: MINI APP FEATURE ACCESS FAIL-CLOSED: PASS**

---

## 1. Точная найденная fail-open точка

`api/auth.py`, функция `check_user_access` (до правки — строки 113–125), последние две строки:

```python
def check_user_access(user_id: int) -> bool:
    """Check if user has access to Logovo.bet."""
    if not user_id or user_id <= 0:
        return False
    if not is_logovo_access_allowed(user_id):
        return False
    if is_admin(user_id):
        return True
    try:
        flag = database.get_feature_flag("betting_market", default="public")
        return flag in ("public", "all", "enabled")
    except Exception:
        return True          # ← любая внутренняя ошибка = разрешение доступа
```

`except Exception: return True` — единственная конструкция в проекте, где ошибка проверки
доступа превращалась в разрешение. Для сравнения, «телеграмная» половина той же проверки
(`handlers/betting.py:33-44`, `_check_betting_access`) уже была написана правильно
(`except Exception: return False`) — расхождение между двумя половинами одной политики и
было сутью AUTH-01.

## 2. Фактический execution path

```
Mini App (web/js) → HTTP + заголовок X-Telegram-Init-Data
        ↓
get_authenticated_user(init_data)          — HMAC-SHA256, auth_date, freshness
        ↓  (None → 401 unauthorized)
check_user_access(user_id)                 — api/auth.py  ← ЗДЕСЬ был fail-open
        ├─ is_logovo_access_allowed()      — LOGOVO_LOCKDOWN (handlers/base.py:82)
        ├─ is_admin()                      — существующая RBAC
        └─ database.get_feature_flag("betting_market", default="public")
        ↓  (False → 403 access_restricted)
бизнес-операция endpoint'а
```

Все реальные call sites (проверены грепом по `check_user_access` / `is_feature_accessible`
/ `get_feature_flag` / `feature_flags` / `betting_market`):

| Файл | Строка | Контракт при отказе |
|---|---|---|
| `api/routes_predictions.py` | 38, 129 | 403 `access_restricted` |
| `api/routes_markets.py` | 30, 104 | 403 `access_restricted` |
| `api/routes_wallet.py` | 34, 96, 127 | 403 `access_restricted` / поле `has_access` в bootstrap |
| `api/routes_gamification.py` | 34, 160 | 403 `access_restricted` |
| `api/routes_intelligence.py` | 49 | 403 `access_restricted` |

Все они получают `bool` от одной и той же функции, поэтому исправления в одной точке
достаточно — ни один route менять не пришлось.

## 3. Почему exception мог разрешить доступ

`check_user_access` возвращает `bool`, и все endpoint'ы трактуют `True` как «пропускать».
Ветка `except Exception: return True` делала `True` результатом любой внутренней поломки:
`sqlite3.OperationalError` при чтении `feature_flags`, повреждённый файл БД, блокировка
таблицы, отсутствующее соединение, любой неожиданный `RuntimeError`/`TypeError` внутри
чтения флага. Практический сценарий: администратор выставил `betting_market = admin_only`
(или `disabled`), в этот момент чтение флага падает — и вместо запрета обычный пользователь
получает полный доступ к ставкам, кошельку, рынкам и созданию прогнозов. Ошибка при этом
даже не логировалась.

Дополнительно: `is_logovo_access_allowed` и `is_admin` вызывались **вне** `try`, поэтому их
поломка приводила к необработанному исключению в handler'е — не к разрешению доступа, но и
не к нормальному 403, а к 500 с непредсказуемым телом ответа.

## 4. Что изменено

`api/auth.py:113-143` — переписана только обработка ошибок `check_user_access`:

```python
    if not user_id or user_id <= 0:
        return False
    try:
        if not is_logovo_access_allowed(user_id):
            return False
        if is_admin(user_id):
            return True
        flag = database.get_feature_flag("betting_market", default="public")
        return flag in ("public", "all", "enabled")
    except Exception:
        # Никакого permissive fallback: даже если default флага — 'public',
        # НЕ прочитанная проверка не может разрешить доступ.
        logger.exception(
            "FEATURE_ACCESS_UNAVAILABLE: feature access check failed — access denied "
            "(fail-closed). feature_key=betting_market user_id=%s",
            user_id
        )
        return False
```

Что осталось ровно тем же: порядок проверок, семантика статусов, набор допустимых значений
(`public` / `all` / `enabled` → ALLOW), ранний выход по невалидному `user_id`, вызовы
`is_logovo_access_allowed` и `is_admin`, вызов `get_feature_flag` с default `public`.

Изменились две вещи: результат ветки `except` (`True` → `False` + ERROR-лог) и охват `try`
(теперь он покрывает всю проверку, поэтому поломка lockdown/RBAC тоже даёт нормальный
403, а не 500). Логика RBAC при этом не переписана — она вызывается как прежде.

## 5. Как теперь работает ERROR → REJECT

```
FEATURE ACCESS CHECK
        ↓
   ┌────┴────┐
SUCCESS   INTERNAL EXCEPTION
   ↓             ↓
ALLOW/REJECT  logger.exception(ERROR)
                 ↓
              return False
                 ↓
        route → 403 access_restricted
                 ↓
        бизнес-операция НЕ выполняется
```

Итоговая таблица:

| Ситуация | Результат |
|---|---|
| `public` | ALLOW |
| `admin_only`, обычный пользователь | REJECT |
| `admin_only`, глобальный админ | ALLOW (существующая RBAC) |
| `disabled` | REJECT |
| строки флага нет | существующий default проекта (`public`) → ALLOW |
| `sqlite3.OperationalError` | **REJECT** |
| любое неожиданное исключение | **REJECT** |
| `user_id` пустой/отрицательный | REJECT (как и раньше) |

## 6. Как обрабатывается отсутствующий feature flag

Отсутствие строки — **не ошибка**, и это принципиально. `get_feature_flag` в этом случае
не бросает исключение, а штатно возвращает `DEFAULT_FEATURE_FLAGS["betting_market"]`
(`database.py:3189` → `public`). Контракт не менялся: два сценария разведены не по типу
исключения, а по тому, дошёл ли вызов до `return` вообще.

```
MISSING ROW  -> get_feature_flag вернул default -> обычная ветка -> ALLOW
READ ERROR   -> исключение -> ветка except -> REJECT
```

`test_05_missing_flag_uses_existing_default` физически удаляет строку из `feature_flags`,
проверяет её отсутствие сырым SQL и требует прежнего поведения (доступ разрешён). То есть
FIX-06 не «ужесточил» контракт отсутствующего флага и не превратил его в ошибку.

## 7. Как обрабатывается DB error

`sqlite3.OperationalError` (и любой другой `sqlite3.Error`) при чтении `betting_market`
попадает в `except Exception` → ERROR-лог → `return False` → 403. Никакого отката на
`public` не происходит, хотя именно `public` передаётся как `default` в вызов
`get_feature_flag`: этот параметр относится к отсутствию строки, а не к сбою чтения.

Проверяется `test_06_database_error_rejects_access` и — в связке с контрольным прогоном —
`test_14_flag_read_failure_does_not_fall_back_to_public`: один и тот же пользователь при
исправной проверке получает доступ, а при сломанном чтении — нет. Значит отказ вызван
именно fail-closed, а не посторонней причиной.

## 8. Как обрабатывается unexpected exception

Точно так же: `RuntimeError("simulated feature access failure")` из любой из трёх
внутренних операций (`is_logovo_access_allowed`, `is_admin`, `get_feature_flag`) даёт
REJECT. Это покрыто `test_07` и детектором `test_16_fail_open_detector`, который ломает
каждый шаг по очереди (три subtest'а) и требует `False` во всех случаях.

## 9. HTTP contract

Новый контракт не вводился. `check_user_access` по-прежнему возвращает `bool`, а
существующие route'ы отдают уже принятый в проекте ответ:

```
HTTP 403
{"status": "error", "error": "access_restricted", "message": "Logovo.bet временно недоступен."}
```

Пользователь видит обычный отказ. В теле ответа нет ни stack trace, ни текста исключения,
ни SQL-ошибки, ни путей к файлам — это проверяется машинно в
`test_09_rest_feature_error_response_is_safe` (поиск подстрок `Traceback`, `RuntimeError`,
`OperationalError`, `sqlite3`, текста симулированного исключения и `api\auth.py`).
Отдельно зафиксировано, что ответ именно 403, а не 500.

## 10. Logging

Используется существующий `logger = logging.getLogger(__name__)` модуля `api.auth`.
Уровень — ERROR, через `logger.exception(...)`, поэтому traceback сохраняется на сервере,
но не уходит клиенту.

Контекст в сообщении: маркер `FEATURE_ACCESS_UNAVAILABLE`, `feature_key=betting_market`,
`user_id=<id>` и сам traceback (в нём видно, на каком именно шаге проверка сломалась и из
какого endpoint'а она была вызвана).

**Не логируется:** initData, auth hash, bot token, любые секреты, пароли, персональные
данные. `test_10_internal_error_is_logged_at_error_level` проверяет уровень записи, наличие
`exc_info`, наличие feature key и user_id — и отдельно то, что в сообщении **нет** `TOKEN`,
`initData`, `init_data`, `hash=`.

## 11. Какие файлы изменены

| Файл | Изменение |
|---|---|
| `api/auth.py` | `check_user_access`: `except Exception: return True` → ERROR-лог + `return False`; `try` расширен на всю проверку (строки 113–143) |
| `tests/test_feature_access_fail_closed.py` | **Новый** файл, 16 тестов |
| `FIX_06_MINIAPP_FEATURE_ACCESS_FAIL_CLOSED.md` | **Новый** отчёт |

Ничего больше не тронуто: HMAC-валидация, `auth_date`/expiration, initData, dev bypass,
`ADMIN_IDS`, IDOR-защита, RBAC (`is_admin` / `is_global_admin` / `is_admin_user` /
`division_admins`), `get_feature_flag`, `set_feature_flag`, `is_feature_accessible`,
семантика статусов, схема БД, route'ы, Mini App UI. Новых middleware, движков, таблиц и
систем permissions не создавалось. Глобальной замены `except Exception` не делалось.

**Ревизия остальных мест, где ошибка теоретически могла бы разрешить доступ:**

- `handlers/betting.py:33-44` (`_check_betting_access`) — уже fail-closed
  (`except Exception: return False`). Не изменялся.
- `database.is_feature_accessible` (`database.py:3224`) — верхнего `try` нет, исключение
  пробрасывается наверх, то есть разрешением стать не может. Внутренний
  `except Exception: pass` относится только к чтению роли пользователя и завершается
  `return False`. Не изменялся.
- `main.py:34` — не проверка доступа, а выбор кнопки меню при старте. Не изменялся.

## 12. Какие тесты добавлены

`tests/test_feature_access_fail_closed.py` — 16 тестов (+3 subtest'а), два класса:
unit-уровень самой проверки и реальный Mini App REST-путь (`AioHTTPTestCase` +
`create_app()` + валидный HMAC initData).

| Тест | Что проверяет |
|---|---|
| `test_01_public_allows_access` | `public` → ALLOW |
| `test_02_disabled_rejects_access` | `disabled` → REJECT |
| `test_03_admin_only_rejects_regular_user` | `admin_only` + обычный пользователь → REJECT |
| `test_04_admin_only_allows_admin` | `admin_only` + глобальный админ → ALLOW (RBAC не изменён) |
| `test_05_missing_flag_uses_existing_default` | строки нет → существующий fallback, доступ разрешён |
| `test_06_database_error_rejects_access` | `sqlite3.OperationalError` → REJECT |
| `test_07_unexpected_exception_rejects_access` | `RuntimeError` → REJECT |
| `test_08_rest_feature_error_runs_no_business_operation` | 403 + `place_user_bet` не вызывается, ставки нет, баланс не тронут |
| `test_09_rest_feature_error_response_is_safe` | 403 `access_restricted`, без stack trace и внутренностей |
| `test_10_internal_error_is_logged_at_error_level` | ERROR + `exc_info` + feature key + user_id, без секретов |
| `test_11_rest_public_allows_business_operation` | контроль: `public` → 200, ставка создана, баланс списан |
| `test_12_rest_admin_only_rejects_regular_user` | REST: `admin_only` → 403, ничего не создано |
| `test_13_rest_disabled_rejects` | REST: `disabled` → 403, ничего не создано |
| `test_14_flag_read_failure_does_not_fall_back_to_public` | контроль ALLOW + сбой REJECT: отката на `public` нет |
| `test_15_fix05_persistence_compatibility` | `admin_only` переживает `init_db()` и виден проверке доступа |
| `test_16_fail_open_detector` | прямой детектор: поломка каждого из трёх шагов → REJECT (3 subtest'а) |

**Проверка невакуумности.** `check_user_access` временно возвращена к старой реализации
(`except Exception: return True`, `try` только вокруг чтения флага), прогон повторён:
**9 failed, 10 passed** — падают `test_06`, `test_07`, `test_10`, `test_14`, `test_08`,
`test_09` и все три subtest'а `test_16`. Тесты действительно ловят исходный баг. После
проверки файл восстановлен из бэкапа, прогон повторён — снова 16 passed.

Особо: `test_08` не просто смотрит на HTTP-код, а подменяет `database.place_user_bet` на
`AssertionError`, то есть падает, если бизнес-операция вообще будет достигнута после сбоя
проверки доступа.

## 13. Targeted test result

```
python -m pytest tests/test_feature_access_fail_closed.py
→ 16 passed, 3 subtests passed in 13.40s
```

Существующие тесты feature flags / auth / Mini App / betting API / security / RBAC:

```
python -m pytest tests/test_phase9_security.py tests/test_phase4_betting_experience.py \
                 tests/test_feature_flags_and_cards.py tests/test_betting_api_v2.py \
                 tests/test_betting_engine.py tests/test_rbac_division_panels.py \
                 tests/test_betting_market_persistence.py
→ 71 passed, 6 subtests passed in 25.70s
```

## 14. Полный pytest result

```
python -m pytest tests/
→ 697 passed, 875 warnings, 9 subtests passed in 54.15s
```

До FIX-06 было 681 passed / 6 subtests. Прирост ровно +16 тестов и +3 subtest'а — это новый
файл целиком. Ни один существующий тест не сломан, ни один не удалён, skip не добавлен,
assertions не ослаблены.

## 15. Подтверждение FIX-01 (round betting cutoff)

`evaluate_round_betting_gate` и его вызов в `place_user_bet` не изменялись; второго гейта не
создано. Инварианты `is_open=0 + bets_open=1 → ALLOWED`, остальные комбинации → `REJECTED`,
отсутствующий тур → `REJECTED`, истёкший дедлайн → `REJECTED`.

```
python -m pytest tests/test_risk_engine_fail_closed.py tests/test_early_betting_line.py \
                 tests/test_round_betting_cutoff.py tests/test_round_betting_isolation.py \
                 tests/test_phase9_risk_engine.py tests/test_phase9_division_season.py
→ 55 passed in 20.92s
```

## 16. Подтверждение FIX-03 (season + division + round isolation)

Скоуп гейта — по-прежнему тройка `season_id + division_id + round_number`, выводимая из
матча; код изоляции не изменялся. Покрыто `tests/test_round_betting_isolation.py`,
`tests/test_phase9_division_season.py` и
`test_11_fix03_scope_isolation_survives_permissive_risk_engine` — все входят в прогон §15.

## 17. Подтверждение FIX-04 (RiskEngine fail-closed)

`database.place_user_bet()`, `RiskEngine`, код `RISK_CHECK_UNAVAILABLE`, round betting gate
и списание из кошелька не изменялись — правка FIX-06 находится в `api/auth.py`, до вызова
бизнес-логики.

```
python -m pytest tests/test_risk_engine_fail_closed.py
→ 12 passed
```

(в составе прогона §15)

## 18. Подтверждение FIX-05 (betting market persistence)

Seed-запрос `INSERT ... ON CONFLICT(feature_key) DO NOTHING` в `init_db()` не изменялся.

```
python -m pytest tests/test_betting_market_persistence.py
→ 11 passed, 3 subtests passed
```

(в составе прогона §13). Дополнительно `test_15_fix05_persistence_compatibility` из нового
файла соединяет оба фикса: `admin_only` → `init_db()` → проверка доступа по-прежнему видит
`admin_only`, обычный пользователь получает отказ, админ — доступ.

---

## Наблюдение (за рамками FIX-06, не исправлялось)

`handle_get_odds_history` (`api/routes_markets.py`) вызывает `get_authenticated_user`, но —
в отличие от двух других хендлеров того же файла — **не** вызывает `check_user_access`.
Это не fail-open в проверке (проверки там просто нет), а отдельная находка аудита о
пропущенном call site: история коэффициентов доступна любому аутентифицированному
пользователю даже при `betting_market != public`. Исправление означало бы добавление новой
проверки в endpoint, что выходит за рамки FIX-06 («ошибка → отказ»), поэтому оставлено как
отдельная задача. Схожим образом `api/routes_matches.py` импортирует `check_user_access`,
но не использует его.
