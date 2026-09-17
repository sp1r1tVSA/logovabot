# FIX-07 — Закрытие пропущенных Mini App feature-access call sites

Дата: 2026-09-09
Область: `api/routes_markets.py`, `api/routes_matches.py`
Основание: два пробела, зафиксированные в отчёте `FIX_06_MINIAPP_FEATURE_ACCESS_FAIL_CLOSED.md`

---

## 1. Проблема

FIX-06 сделал саму `check_user_access()` fail-closed, но проверка защищает только там,
где её вызывают. В двух местах Mini App API она не вызывалась вовсе: handler'ы получали
пользователя через `get_authenticated_user()` (HMAC-SHA256) и сразу переходили к
бизнес-логике.

Практическое последствие: **аутентификация подменяла авторизацию**. Любой обычный
пользователь с валидной `initData` получал данные, закрытые политикой `betting_market`,
даже при `admin_only` и при `disabled`:

| Endpoint | Что утекало при закрытом рынке |
|---|---|
| `GET /api/markets/{id}/odds-history` | Полная история движения коэффициентов по селекции |
| `GET /api/matches` | Список матчей **вместе с линией** (`odds`: p1/x/p2/ТБ/ТМ/обе забьют) |
| `GET /api/matches/{id}` | Карточка матча |
| `GET /api/matches/{id}/stats` | Форма команд, средние голы, % ТБ 2.5, % «обе забьют» |
| `GET /api/matches/{id}/h2h` | История личных встреч и сводка |
| `GET /api/matches/{id}/insights` | Статистические тренды («Тотал Больше», серии) |
| `GET /api/matches/{id}/live` | Live-счёт, минута, таймлайн событий |
| `GET /api/recommendations` | Персональные рекомендации ставок |

Отдельно: в `api/routes_matches.py` `check_user_access` уже был **импортирован** (строка 15),
но ни разу не вызван — импорт создавал ложное впечатление, что модуль защищён.

---

## 2. Что изменено

### 2.1 `api/routes_markets.py` — `handle_get_odds_history()`

Между блоком 401 и разбором `market_id` (то есть **до** `odds_engine.get_odds_history(...)`)
добавлен ровно тот же блок, что уже стоит в соседних handler'ах этого файла
(`handle_get_tours` — строки 29-35, `handle_get_match_markets` — строки 103-105):

```python
    user_id = user_info["id"]
    if not check_user_access(user_id):
        return web.json_response({
            "status": "error",
            "error": "access_restricted",
            "message": "Logovo.bet временно недоступен."
        }, status=403)
```

Семантика теперь идентична остальным endpoint'ам `routes_markets.py`. Ни HMAC, ни формат
`initData`, ни RBAC, ни feature-флаги, ни `odds_engine`, ни SQL получения истории, ни
структура ответа `history` не тронуты.

### 2.2 `api/routes_matches.py` — защищённые Mini App endpoints

Добавлен один общий помощник ответа (не второй механизм проверки — проверку по-прежнему
выполняет существующая `check_user_access()`):

```python
def _access_restricted_response() -> web.Response:
    """
    Единый ответ политики доступа Logovo.bet — тот же контракт, что в
    routes_markets / routes_predictions / routes_wallet / routes_gamification.
    Проверку выполняет существующая check_user_access(); здесь только её ответ.
    """
    return web.json_response({
        "status": "error",
        "error": "access_restricted",
        "message": "Logovo.bet временно недоступен."
    }, status=403)
```

и в каждый защищённый handler, сразу после существующего блока 401, вставлено:

```python
    if not check_user_access(user_info["id"]):
        return _access_restricted_response()
```

Точки вставки (текущие номера строк):

| Handler | Маршрут | Строка проверки |
|---|---|---|
| `handle_get_matches` | `GET /api/matches` | 43 |
| `handle_get_match_detail` | `GET /api/matches/{id}` | 132 |
| `handle_get_match_stats` | `GET /api/matches/{id}/stats` | 186 |
| `handle_get_match_h2h` | `GET /api/matches/{id}/h2h` | 270 |
| `handle_get_match_insights` | `GET /api/matches/{id}/insights` | 348 |
| `handle_get_match_live` | `GET /api/matches/{id}/live` | 425 |
| `handle_get_recommendations` | `GET /api/recommendations` | 492 |

Логика `check_user_access` внутри handler'ов не дублируется — вызывается существующая функция.

---

## 3. Порядок выполнения: проверка строго перед бизнес-операцией

Во всех восьми точках проверка стоит **до** первого обращения к БД или сервису:

- `handle_get_odds_history` — до `odds_engine.get_odds_history(...)`;
- `handle_get_matches` / `_detail` / `_stats` / `_h2h` / `_insights` / `_live` — до
  первого `with database.transaction()`;
- `handle_get_recommendations` — сразу после `user_id = user_info["id"]` и до
  `get_user_recommendations(...)` (и до импорта движка рекомендаций).

То есть реализована схема `check_user_access() → DENIED → return 403`, а не
`get_odds_history() → потом проверка`. Это проверяется тестом 04 через мок-детектор:
`odds_engine.get_odds_history` подменяется на `side_effect=AssertionError` и должен
остаться невызванным (`spy.assert_not_called()`).

---

## 4. Что осознанно НЕ закрыто и почему

**`handle_get_hot_matches` (`GET /api/matches/hot`) оставлен без проверки.**

Это единственный handler `routes_matches.py`, который **вообще не аутентифицирован**: он
не читает заголовок `X-Telegram-Init-Data`, не вызывает `get_authenticated_user()` и не
имеет `user_id`. По исходной архитектуре это публичный витринный endpoint (рейтинг
«горячих» матчей по live/движению линии/объёму/H2H), а не часть защищённого Mini App
функционала за `check_user_access()`. Брифом прямо запрещено механически добавлять
проверку туда, где endpoint намеренно публичный/служебный.

Чтобы закрыть его, потребовалось бы сначала ввести в него аутентификацию — то есть
изменить контракт endpoint'а. Это выходит за рамки FIX-07 и фиксируется здесь как
отдельное наблюдение для отдельного решения.

---

## 5. Наблюдение вне области FIX-07 (ничего не менялось)

Быстрая проверка соседних модулей `api/` показала, что схема «есть
`get_authenticated_user()`, нет `check_user_access()`» встречается и за пределами двух
исправленных файлов — в частности в `routes_predictions.py` (`handle_get_prediction_detail`,
`handle_repeat_prediction`, `handle_get_cashout_quote`, `handle_execute_cashout`),
`routes_tournaments.py`, `routes_user_extras.py`, `routes_wallet.py::handle_get_wallet`.

Часть из них (турнирная таблица, результаты, бомбардиры) по смыслу к политике
`betting_market` не относится; часть (cashout, повтор купона) выглядит спорной. Бриф
FIX-07 явно ограничивает работу двумя зафиксированными в FIX-06 пробелами и запрещает
новый аудит, поэтому **ни один из этих файлов не изменён**. Пункт оставлен как материал
для отдельного решения, а не как выполненная работа.

---

## 6. Тесты

Создан `tests/test_miniapp_access_call_sites.py` (8 тестов, 44 subtest'а). Все идут через
реальный HTTP-путь: `AioHTTPTestCase` + `create_app()`, валидная `initData`, временная БД.

| # | Тест | Что проверяет |
|---|---|---|
| 01 | `test_01_odds_history_disabled_rejects` | `disabled` → 403 `access_restricted` |
| 02 | `test_02_odds_history_admin_only_rejects_regular_user` | `admin_only` + обычный пользователь → 403 |
| 03 | `test_03_odds_history_public_returns_history` | `public` → 200, прежняя структура (`status`, `market_id`, `selection_key`, `history: list`) |
| 04 | `test_04_odds_history_access_failure_runs_no_business_operation` | сбой проверки → 403 **и** `odds_engine.get_odds_history()` не вызван (`assert_not_called`) |
| 05 | `test_05_protected_match_endpoints_disabled_reject` | все 7 защищённых endpoint'ов при `disabled` → 403 |
| 06 | `test_06_protected_match_endpoints_public_keep_working` | контроль: при `public` прежнее успешное поведение |
| 07 | `test_07_protected_match_endpoints_admin_only` | `admin_only`: обычный пользователь → 403, глобальный админ → 200 |
| 08 | `test_08_denied_responses_leak_nothing` | ответы отказа не содержат `Traceback`, `RuntimeError`, `OperationalError`, `sqlite3`, `initData`, `hash=`, путей файлов, токена бота |

Замечание по контракту: у `GET /api/matches/{id}/live` в ответе ключ `"status"`
объявлен дважды в одном литерале, и статус матча перезаписывает `"ok"`. Это существующее
поведение; менять его в рамках FIX-07 не разрешено, поэтому тест для `/live` проверяет
`match_id`, а не `status`. Ослаблением assertion это не является — успешность запроса
по-прежнему проверяется кодом 200.

---

## 7. Результаты прогонов

Все команды выполнены реально, без `skip` и без изменения существующих assertion'ов.

```
python -m pytest tests/test_miniapp_access_call_sites.py
    8 passed, 44 subtests passed in 11.57s

python -m pytest tests/test_feature_access_fail_closed.py            (FIX-06)
    16 passed, 3 subtests passed in 9.91s

python -m pytest tests/test_feature_access_fail_closed.py \
                tests/test_feature_flags_and_cards.py \
                tests/test_betting_market_persistence.py \
                tests/test_betting_api_v2.py \
                tests/test_phase9_security.py
    42 passed, 6 subtests passed in 17.90s

python -m pytest tests/
    705 passed, 53 subtests passed in 30.22s
```

Базовая линия до FIX-07 — 697 passed; сейчас 705 = 697 + 8 новых тестов. Ни один
существующий тест не удалён, не помечен `skip` и не ослаблен.

### Проверка на неложность (non-vacuity probe)

Добавленные проверки временно удалены из обоих файлов скриптом (7 блоков в
`routes_matches.py`, 1 блок в `routes_markets.py`), тесты прогнаны, файлы восстановлены
из резервных копий:

```
без исправления: 33 failed, 5 passed, 14 subtests passed
```

Падают ровно тесты 01, 02, 04, 05, 07, 08 (и их subtest'ы). Проходят только контрольные
03 и 06, которые описывают разрешённый доступ и обязаны проходить в обеих версиях. После
восстановления — снова `8 passed, 44 subtests passed`. Тесты действительно ловят
исправленный дефект.

---

## 8. Что НЕ изменялось

Не тронуты: HMAC-SHA256 и валидация `initData`, `auth_date` / срок жизни, dev-bypass
(`ALLOW_DEV_AUTH_BYPASS`), `LOGOVO_LOCKDOWN`, `ADMIN_IDS`, `is_admin()` / `is_global_admin()`
/ RBAC / `division_admins`, IDOR-защита, семантика feature-флагов и `get_feature_flag`,
сама `check_user_access()` (после FIX-06), betting engine, RiskEngine, settlement,
round betting gate (FIX-01), изоляция season/division/round (FIX-03), схема БД, архитектура
рынков, генерация коэффициентов, Telegram-handlers и меню, фронтенд Mini App, LIVE,
Лаборатория, турниры. Новых middleware, feature-flag-движков, auth-движков, таблиц и
систем permissions не создано. Рефакторингов «заодно» нет.

---

## Verdict: PASS

Ни один Mini App endpoint из числа закрытых политикой `betting_market` больше не доступен
обычному пользователю только потому, что прошла HMAC-аутентификация. Проверка выполняется
существующей `check_user_access()` до бизнес-операции, отказ отдаёт существующий контракт
403 `access_restricted` без утечки внутренних деталей, разрешённый доступ работает как
прежде, полный набор тестов зелёный (705 passed), и удаление исправления воспроизводимо
роняет новые тесты.
