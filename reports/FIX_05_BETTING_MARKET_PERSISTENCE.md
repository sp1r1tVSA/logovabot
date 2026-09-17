# FIX-05 — BETTING MARKET CONFIG MUST SURVIVE RESTART

**Проблема аудита:** FLAG-01 — `init_db()` перезаписывает значение feature-флага
`betting_market` при каждом перезапуске приложения.

**Вердикт: BETTING MARKET PERSISTENCE: PASS**

---

## 1. Точная найденная причина FLAG-01

Единственный виновник — seed-запрос в `init_db()` (`database.py`, блок создания таблицы
`feature_flags`, до правки строки 490–495).

Хранилище настроек — таблица `feature_flags`:

```sql
CREATE TABLE IF NOT EXISTS feature_flags (
    feature_key TEXT PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT 'admin_only',
    config_json TEXT,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

Сразу после `CREATE TABLE IF NOT EXISTS` шёл **upsert, а не seed**:

```sql
INSERT INTO feature_flags (feature_key, status)
VALUES ('betting_market', 'public')
ON CONFLICT(feature_key) DO UPDATE SET status = 'public'
WHERE status != 'disabled'
```

`ON CONFLICT ... DO UPDATE` срабатывает именно тогда, когда запись **уже существует** —
то есть на каждом втором и последующем запуске. Условие `WHERE status != 'disabled'`
защищало ровно один статус из трёх. Итог:

| Значение до `init_db()` | Значение после `init_db()` |
|---|---|
| (записи нет) | `public` (корректно) |
| `public` | `public` (безвредно) |
| **`admin_only`** | **`public`** ← потеря настройки |
| `disabled` | `disabled` (спасал `WHERE`) |

Практический сценарий: администратор ограничивает Logovo.bet до `admin_only`; при
ближайшем рестарте/деплое `init_db()` молча возвращает `public`. Рынок открывается всем,
а `post_init` в `main.py` дополнительно выставляет WebApp-кнопку меню всем пользователям.
Никакого лога, никакого следа — настройка просто исчезает.

## 2. Какой код выполнял reset

Один-единственный `cursor.execute(...)` внутри `init_db()` — процитирован выше.
Больше никаких записей в `betting_market` в кодовой базе нет (см. §9).

## 3. Что изменено

`database.py:490-501` — upsert заменён на seed-only insert:

```python
        # Seed-only. Значение по умолчанию выставляется ТОЛЬКО при отсутствии записи.
        # Раньше здесь стоял `ON CONFLICT(feature_key) DO UPDATE SET status = 'public'`,
        # из-за чего каждый вызов init_db() (то есть каждый рестарт бота) затирал
        # настройку администратора и молча открывал рынок всем. Теперь init_db()
        # идемпотентен относительно betting_market: существующая запись — в любом
        # статусе — остаётся нетронутой. Менять флаг можно только через
        # set_feature_flag().
        cursor.execute("""
            INSERT INTO feature_flags (feature_key, status)
            VALUES ('betting_market', 'public')
            ON CONFLICT(feature_key) DO NOTHING
        """)
```

Изменена ровно одна SQL-операция. Ни таблица, ни миграции, ни другие флаги, ни
`get_feature_flag` / `set_feature_flag` / `get_all_feature_flags` / `is_feature_accessible`
не тронуты.

## 4. Почему существующая настройка теперь сохраняется

`ON CONFLICT(feature_key) DO NOTHING` — стандартный upsert-no-op SQLite. При конфликте по
первичному ключу строка **не изменяется вообще**: ни `status`, ни `config_json`, ни
`updated_at`. Ветка записи существует только для случая «строки нет»:

```
NOT EXISTS -> INSERT DEFAULT
EXISTS     -> DO NOTHING
```

Это делает `init_db()` идемпотентным относительно `betting_market` при любом числе
вызовов и при любом хранимом статусе. Тест 5 проверяет это жёстче требуемого: он
сравнивает `updated_at` до и после `init_db()` и требует побайтового совпадения — то
есть строка не переписывается даже тем же значением.

## 5. Какой default используется

`public` — ровно то значение, которое использовал код до правки. Оно совпадает с
`DEFAULT_FEATURE_FLAGS["betting_market"] = "public"` (`database.py:3189`), к которому
`get_feature_flag` откатывается, когда строки в таблице нет. Новых значений не введено.

Допустимых статусов в проекте ровно три — `disabled`, `admin_only`, `public`
(`database.is_feature_accessible`). Отдельного значения `private` в проекте не существует;
роль «ограничительной админской настройки» из ТЗ здесь играет `admin_only`, и именно оно
проверяется в главном тесте FIX-05. Значение `private` не выдумывалось и не добавлялось.

## 6. Как проверяется первый запуск

`test_01_default_created_on_first_initialization`: создаётся пустой временный файл БД,
`database.DB_PATH` переключается на него, вызывается `init_db()`. Проверяется, что строка
`betting_market` создана (`COUNT(*) = 1`), её `status` прочитан **напрямую из таблицы**
(без подмешивания `DEFAULT_FEATURE_FLAGS`) и равен `public`, и что `get_feature_flag`
возвращает то же самое.

Чтение сырого значения из таблицы принципиально: иначе тест нельзя было бы отличить от
фолбэка на словарь дефолтов.

## 7. Как проверяется restart

Два уровня.

*Повторный вызов функции* — `test_02`, `test_04` (три `init_db()` подряд), `test_06`,
`test_07`.

*Настоящий restart-цикл* — `test_08_full_restart_cycle_preserves_admin_value` и хелпер
`_simulate_restart()`:

1. создать БД → `init_db()`;
2. изменить `betting_market`;
3. `database.close_thread_connection()` — соединение процесса закрывается;
4. `init_db()` — «новый процесс» инициализирует ту же БД и открывает новое соединение;
5. `close_thread_connection()` — снова разрыв;
6. чтение через новое соединение.

Цикл выполняется дважды подряд. `database._thread_connection()` кэширует соединение по
`DB_PATH`, поэтому явное закрытие гарантирует, что читается состояние с диска, а не
переживший тест хендл.

## 8. Как проверяется admin-set value

Админский механизм изменения флага в проекте — `database.set_feature_flag(key, status)`
(`database.py:3204`, `REPLACE INTO feature_flags ...`). Отдельного admin-handler'а или
API-эндпоинта для `betting_market` в коде нет — флаг меняется через эту функцию
(скрипты/консоль/тесты). Функция не изменялась.

- `test_03_admin_value_survives_restart` — ядро FIX-05: `set_feature_flag('betting_market',
  'admin_only')` → `init_db()` → значение осталось `admin_only`.
- `test_07_admin_update_still_works` — `public → admin_only` через штатный механизм,
  проверка, `init_db()`, снова проверка; затем обратный переход `admin_only → public`,
  `init_db()`, проверка. Админский flow работает в обе стороны.
- `test_11_flag_consumers_read_admin_value_after_restart` — после рестарта админское
  значение видят все реальные потребители с их разными дефолтами: `main.py:34`
  (`default="admin_only"`), `api/auth.py:122` (`default="public"`), `handlers/betting.py:41`
  (без default), а также `get_all_feature_flags()`. Дополнительно проверяется `disabled`.

## 9. Какие файлы изменены

| Файл | Изменение |
|---|---|
| `database.py` | Одна SQL-операция в `init_db()`: upsert → seed-only `DO NOTHING` (строки 490–501) |
| `tests/test_betting_market_persistence.py` | **Новый** файл, 11 тестов |
| `FIX_05_BETTING_MARKET_PERSISTENCE.md` | **Новый** отчёт |

Ревизия всех операций записи по `feature_flags` после правки:

```
database.py:498   INSERT INTO feature_flags (feature_key, status) ... ON CONFLICT DO NOTHING   -- seed, init_db()
database.py:3209  REPLACE INTO feature_flags (feature_key, status, updated_at) VALUES (?,?,...) -- set_feature_flag(), админский путь
```

Ни `UPDATE feature_flags`, ни `DELETE FROM feature_flags`, ни `DROP TABLE feature_flags` в
проекте нет. Единственное упоминание `betting_market` в SQL внутри `init_db()` — seed;
второе вхождение слова в функции находится в комментарии. Это зафиксировано машинно в
`test_10_init_db_contains_no_unconditional_write`, который парсит исходник `init_db()` и
падает, если в seed-запросе появятся `DO UPDATE`, `REPLACE` или `DELETE`.

**Backward compatibility.** Миграций нет, таблица не пересоздаётся, существующие строки не
удаляются и не переписываются. Продовая БД, где `betting_market` уже есть, после деплоя
сохранит своё текущее значение — каким бы оно ни было.

## 10. Какие тесты добавлены

`tests/test_betting_market_persistence.py` — 11 тестов (плюс 3 subtest'а):

| Тест | Что проверяет |
|---|---|
| `test_01_default_created_on_first_initialization` | чистая БД → создан default `public` |
| `test_02_init_db_is_idempotent` | повторный `init_db()` не меняет значение и не плодит строки |
| `test_03_admin_value_survives_restart` | **главный тест FIX-05**: `admin_only` переживает `init_db()` |
| `test_04_admin_value_survives_multiple_restarts` | три `init_db()` подряд |
| `test_05_existing_public_is_not_rewritten` | `public` не переписывается; `updated_at` не меняется |
| `test_06_every_valid_configured_value_survives` | subtest по каждому реальному статусу: `disabled`, `admin_only`, `public` |
| `test_07_admin_update_still_works` | админский `set_feature_flag` работает в обе стороны и переживает `init_db()` |
| `test_08_full_restart_cycle_preserves_admin_value` | полный цикл с закрытием и переоткрытием соединения |
| `test_09_other_feature_flags_are_not_reset` | `init_db()` не трогает другие флаги, включая кастомный |
| `test_10_init_db_contains_no_unconditional_write` | статический контроль SQL внутри `init_db()` |
| `test_11_flag_consumers_read_admin_value_after_restart` | все потребители видят сохранённое значение, а не свой default |

**Проверка невакуумности.** SQL временно возвращён к старому виду
(`ON CONFLICT ... DO UPDATE SET status = 'public' WHERE status != 'disabled'`) и прогон
повторён: **7 failed, 5 passed** — падают `test_03`, `test_04`, `test_06` (subtest
`admin_only`), `test_07`, `test_08`, `test_10`, `test_11`. Тесты действительно ловят
исходный баг, а не проходят «сами по себе». После проверки файл восстановлен из бэкапа и
прогон повторён — снова 11 passed.

## 11. Targeted test result

```
python -m pytest tests/test_betting_market_persistence.py
→ 11 passed, 3 subtests passed in 4.16s
```

Связанные области (инициализация БД, настройки, feature flags, betting market, betting):

```
python -m pytest tests/test_feature_flags_and_cards.py tests/test_betting_engine.py \
                 tests/test_betting_api_v2.py tests/test_phase4_betting_experience.py \
                 tests/test_phase5_advanced_betting.py tests/test_schema_migration.py \
                 tests/test_purge_old_season.py
→ 82 passed in 23.23s
```

*Примечание:* `tests/test_lab_and_cards.py` в проекте отсутствует (остался только `.pyc` в
`__pycache__`); в набор включены реально существующие файлы.

## 12. Полный pytest result

```
python -m pytest tests/
→ 681 passed, 815 warnings, 6 subtests passed in 54.44s
```

До FIX-05 было 670 passed / 3 subtests. Прирост ровно +11 тестов и +3 subtest'а — это
новый файл целиком. Ни один существующий тест не сломан, ни один не удалён, skip не
добавлен, assertions не ослаблены.

## 13. Подтверждение FIX-01 (round betting cutoff)

Архитектура гейта не тронута: `evaluate_round_betting_gate` и его вызов в
`place_user_bet` не изменялись, второго гейта не создано.

```
python -m pytest tests/test_round_betting_cutoff.py tests/test_round_betting_isolation.py \
                 tests/test_phase9_atomic_betting.py tests/test_phase9_division_season.py \
                 tests/test_settlement_engine.py tests/test_phase10_seasons.py
→ 43 passed in 17.32s
```

Инварианты `is_open=0 + bets_open=1 → ALLOWED`, остальные комбинации → `REJECTED`,
закрытая линия → `REJECT`, полный цикл LINE OPEN → BET → ROUND OPENS → LINE CLOSES →
RESULT → SETTLEMENT сохранены (`tests/test_early_betting_line.py`, входит в прогон §14).

## 14. Подтверждение FIX-03 (season + division + round isolation)

Скоуп гейта по-прежнему определяется тройкой `season_id + division_id + round_number`,
выводимой из матча; код изоляции не изменялся.

```
python -m pytest tests/test_risk_engine_fail_closed.py tests/test_early_betting_line.py \
                 tests/test_phase9_risk_engine.py tests/test_phase9_security.py
→ 33 passed in 11.85s
```

Сюда входят `test_11_fix03_scope_isolation_survives_permissive_risk_engine` и весь набор
`test_round_betting_isolation.py` из §13.

## 15. Подтверждение FIX-04 (RiskEngine fail-closed)

`database.place_user_bet()`, `RiskEngine`, код `RISK_CHECK_UNAVAILABLE`, round betting
gate и списание из кошелька не изменялись — правка FIX-05 находится на 6300+ строк выше и
касается только seed-запроса `feature_flags`.

```
python -m pytest tests/test_risk_engine_fail_closed.py
→ 12 passed
```

(в составе прогона §14; отдельный прогон файла также зелёный)

---

## Что осознанно не менялось

- `set_feature_flag()` — админский механизм, работает как прежде (`REPLACE INTO`, это
  осознанная перезапись по явной команде администратора, а не при старте).
- Другие feature-флаги и `DEFAULT_FEATURE_FLAGS` — не тронуты; `init_db()` их и раньше не
  сеял, они живут только в словаре дефолтов.
- Схема `feature_flags`, миграции, `system_config`, auth/RBAC, betting/odds/settlement/
  wallet engines, divisions, seasons, tournaments, Mini App, Telegram UI, LIVE, Laboratory,
  RiskEngine — без изменений.

## Наблюдение (за рамками FIX-05, не исправлялось)

Три потребителя `betting_market` используют три разных дефолта на случай отсутствия
записи: `main.py:34` — `admin_only`, `api/auth.py:122` — `public`,
`handlers/betting.py:41` — без явного default (то есть `admin_only` из сигнатуры
`get_feature_flag`). После FIX-05 это перестаёт быть проблемой на практике, потому что
`init_db()` гарантированно создаёт строку на первом же запуске и больше её не трогает —
фолбэк на default недостижим в рабочей БД. Но само расхождение дефолтов остаётся
источником неоднозначности и заслуживает отдельной унификации.
