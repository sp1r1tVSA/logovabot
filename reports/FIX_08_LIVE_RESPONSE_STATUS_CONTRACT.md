# FIX-08 — Дублирующийся ключ `status` в `GET /api/matches/{id}/live`

Дата: 2026-09-09
Область: контракт ответа одного endpoint'а
Основание: наблюдение, зафиксированное в `FIX_07_MINIAPP_ACCESS_CALL_SITES.md`

---

## 1. Как выглядел конфликт двух `status`

Ответ собирался одним словарным литералом, в котором ключ `"status"` объявлен дважды:

```python
    return web.json_response({
        "status": "ok",          # ← статус API-операции
        "match_id": match_id,
        "status": m["status"],   # ← статус матча, затирает предыдущее значение
        "live_minute": m["live_minute"],
        "score1": m["player1_score"] or 0,
        "score2": m["player2_score"] or 0,
        "events": events
    })
```

Python при разборе литерала оставляет последнее значение, поэтому `"ok"` не доживало до
сериализации. В JSON уходил ровно один ключ `status`, но со значением статуса матча:
`"pending"`, `"live"`, `"confirmed"`. Значения `"ok"` этот endpoint не отдавал **никогда**.

Важное следствие: два одинаковых ключа схлопывались уже на уровне Python, а не JSON —
поэтому баг нельзя было обнаружить, разглядывая только тело ответа; он виден по тому,
что `status` не принимает значение `"ok"` ни при каком состоянии матча.

---

## 2. Где находился баг

`api/routes_matches.py` → `handle_get_match_live()` — блок `return web.json_response(...)`
(до исправления строки 448-456).

Больше нигде в проекте такого дубля нет: остальные handler'ы `routes_matches.py`
возвращают `"status": "ok"` один раз, а статус матча отдают либо внутри `match`, либо
отдельным полем.

---

## 3. Какие consumers использовали ответ

| Consumer | Строка | Что читает | Влияние бага |
|---|---|---|---|
| `web/js/api.js::getMatchLive()` | 141-143 | сырой `request()` | нет |
| `web/js/app.js::loadMatchCenter()` | 212, 222 | `liveRes.status === 'ok' ? liveRes : null` | **ключевое**: условие никогда не выполнялось, `state.matchLive` всегда оставался `null` |
| `web/js/store.js::setSelectedMatch()` | 143 | `if (live) …` | всегда получал `null` |
| `web/js/ui.js::renderMatchCenter()` | 359-360 | `live?.score1 / live?.score2` | ветка была мёртвой, счёт всегда брался из `matchDetail` |
| `tests/test_betting_api_v2.py` | 128-132 | `score1`, `score2` | не затронут, `status` не проверял |

Ни один consumer не читал статус матча из `response.status` — то есть на буквальный
(ошибочный) контракт никто не опирался. Frontend с самого начала ожидал в `status`
успешность API-операции; именно этого он и не получал.

---

## 4. Новый контракт

```json
{
  "status": "ok",
  "match_id": 123,
  "match_status": "live",
  "live_minute": 57,
  "score1": 1,
  "score2": 0,
  "events": [ ... ]
}
```

- `status` — исключительно статус API-операции (`"ok"`, а в ошибочных ветках этого же
  handler'а — `"error"`, как и раньше при 401 / 400 / 403 / 404).
- `match_status` — статус самого матча. Имя не новое: оно уже используется в
  `api/routes_markets.py:140` (`handle_get_match_markets`), `api/routes_live.py:89`,
  `api/routes_admin_live.py:116`, в параметрах `settlement_engine.settle_match_predictions`
  и на клиенте в `web/js/ui.js:939` (`it.match_status === 'live'`). То есть контракт
  приведён к уже существующему в архитектуре соглашению, а не изобретён.

Остальные поля (`match_id`, `live_minute`, `score1`, `score2`, `events`) не тронуты.

---

## 5. Изменённые файлы

**`api/routes_matches.py`** — `handle_get_match_live()`: второй `"status"` переименован в
`"match_status"`, добавлен поясняющий комментарий. Больше в файле ничего не менялось.

**`web/js/ui.js`** — `renderMatchCenter()`, строки 359-365. Исправление «оживило» ранее
мёртвую ветку: `state.matchLive` впервые стал непустым, и заголовок Match Center начал
брать счёт из live-ответа. У несыгранного матча endpoint отдаёт `score1/score2` как `0`
(`m["player1_score"] or 0`), поэтому прочерк `«- : -»` превратился бы в `«0 : 0»`. Чтобы
исправление контракта не дало видимой регрессии, live-счёт теперь берётся только для
реально идущего матча — через новое поле `match_status`:

```js
    const liveNow = live?.match_status === 'live' ? live : null;
    const s1 = liveNow?.score1 ?? matchDetail.player1_score ?? '-';
    const s2 = liveNow?.score2 ?? matchDetail.player2_score ?? '-';
```

Это адаптация consumer'а к исправленному контракту (прямо предусмотренная разделом 3
брифа), а не изменение логики Match Center: набор источников счёта прежний, поменялось
только условие приоритета. Поля `score1/score2` самого API не трогались.

**`tests/test_miniapp_access_call_sites.py`** — в `_assert_allowed()` снято исключение
для `/live`: теперь, как и все остальные endpoint'ы, он проверяется на `status == "ok"`,
плюс добавлена проверка наличия `match_status`. Assertion усилен, а не ослаблен.

---

## 6. Тесты

Создан `tests/test_live_response_contract.py` — 6 тестов, 9 subtest'ов, всё через
реальный HTTP-путь (`AioHTTPTestCase` + `create_app()`, валидная `initData`, временная БД,
три матча: `pending`, `live` с минутой 57 и событием, `confirmed`).

| # | Тест | Что проверяет |
|---|---|---|
| 01 | `test_01_successful_response_status_is_ok` | HTTP 200 и `status == "ok"` для всех трёх статусов матча |
| 02 | `test_02_match_status_matches_database` | `match_status` присутствует и равен статусу матча в БД; `match_id` корректен |
| 03 | `test_03_status_key_appears_exactly_once` | детектор дублей через `json.loads(object_pairs_hook=…)` + `"status":` встречается в теле ровно один раз (регексп `(?<!_)"status"\s*:` не считает `match_status`) |
| 04 | `test_04_live_match_contract` | идущий матч: `status == "ok"`, `match_status == "live"`, `live_minute == 57`, счёт 1:0, событие в таймлайне |
| 05 | `test_05_finished_match_contract` | завершённый матч: `status == "ok"`, `match_status == "confirmed"`, счёт 3:1 |
| 06 | `test_06_consumer_contract_is_preserved` | воспроизводит логику `app.js` (`res.status === 'ok' ? res : null`) — ответ принимается, статус матча доступен отдельно; статически проверяет, что `app.js` по-прежнему гейтит на `status`, а `ui.js` читает `match_status`; ошибочная ветка (404) по-прежнему отдаёт `"status": "error"` |

Существующие тесты не удалялись, `skip` не добавлялся, assertions не ослаблялись.

### Проверка на неложность

Handler и правка `ui.js` временно возвращены к старому виду, тесты прогнаны, файлы
восстановлены из резервных копий:

```
без исправления: 9 failed, 3 passed, 3 subtests passed
```

Падают 01, 02, 04, 05, 06 (и их subtest'ы) — ровно те, что описывают новый контракт.
Тест 03 в старой версии проходит, и это ожидаемо: дубль схлопывается ещё в Python, до
сериализации, поэтому JSON-детектор дублей его увидеть не может — он страхует от
будущего регресса другого рода. После восстановления снова `6 passed, 9 subtests passed`.

---

## 7. Targeted test result

```
python -m pytest tests/test_live_response_contract.py
    6 passed, 9 subtests passed in 7.79s
```

Связанные Mini App тесты:

```
python -m pytest tests/test_live_response_contract.py \
                tests/test_miniapp_access_call_sites.py \
                tests/test_feature_access_fail_closed.py \
                tests/test_betting_api_v2.py \
                tests/test_betting_market_persistence.py \
                tests/test_phase9_security.py \
                tests/test_feature_flags_and_cards.py
    56 passed, 59 subtests passed in 19.28s
```

---

## 8. Full pytest result

```
python -m pytest tests/
    711 passed, 62 subtests passed in 28.62s
```

Базовая линия после FIX-07 — 705 passed; сейчас 711 = 705 + 6 новых тестов. Падений и
пропусков нет.

---

## 9. Подтверждение FIX-01 — FIX-07

```
python -m pytest tests/test_round_betting_cutoff.py \
                tests/test_round_betting_isolation.py \
                tests/test_risk_engine_fail_closed.py \
                tests/test_betting_market_persistence.py \
                tests/test_feature_access_fail_closed.py \
                tests/test_miniapp_access_call_sites.py \
                tests/test_live_response_contract.py
    77 passed, 59 subtests passed in 10.71s
```

| Фикс | Механизм | Статус |
|---|---|---|
| FIX-01 | round betting cutoff (`is_open = 0 AND bets_open = 1`) | не тронут, тесты зелёные |
| FIX-03 | изоляция season + division + round | не тронута, тесты зелёные |
| FIX-04 | RiskEngine fail-closed | не тронут, тесты зелёные |
| FIX-05 | `betting_market` переживает `init_db()` | не тронут, тесты зелёные |
| FIX-06 | `check_user_access()` fail-closed | не тронут, тесты зелёные |
| FIX-07 | вызовы `check_user_access()` в Mini App | не тронуты; проверка в `handle_get_match_live` осталась на месте и до бизнес-логики |

Не изменялись: получение live-данных и SQL этого handler'а, Match Center как подсистема,
live-провайдер и `NullSportsDataProvider`, betting engine, odds engine, settlement,
RiskEngine, аутентификация и feature access, Telegram-слой, турниры, лаборатория, схема БД.
Новых response wrapper'ов, сериализаторов, middleware и массовых переименований нет.

---

## Verdict: PASS

- `"status"` присутствует в ответе ровно один раз;
- `"status"` означает успешность API-операции (`"ok"` / `"error"`);
- статус матча доступен отдельным полем `"match_status"`, имя согласовано с уже
  существующими endpoint'ами;
- frontend не сломан — наоборот, `app.js` впервые начал принимать ответ, а `ui.js`
  адаптирован так, чтобы отображение счёта не изменилось для несыгранных матчей;
- все тесты проходят (711 passed), новые тесты воспроизводимо падают на старом коде.
