# План реализации: дивизион-центричная супер-админ панель

Источник требований: [SPEC.md](../SPEC.md). Порядок сборки зафиксирован картой
способностей: `nav-shell` → `div-squads` → `div-matches` → `div-debts`.

---

## 1. Главный вывод разведки

**Миграция схемы не нужна.** Слой БД уже сквозной по `division_id` — проверено
поимённо:

| Функция | Строка | Параметр |
|---|---|---|
| `open_rounds_batch` | `database.py:3256` | `division_id=None` |
| `get_matches_by_round` | `database.py:3397` | `division_id=None` |
| `get_round_info` | `database.py:4302` | `division_id=None` |
| `update_round_status` | `database.py:4514` | `division_id=None`, ветка `WHERE ... division_id = ?` изолирована |
| `set_round_bets_open` | `database.py:4622` | `division_id=None` |
| `get_all_unplayed_league_matches` | `database.py:5882` | `division_id=None` |
| `division_has_played_matches` | `database.py:6288` | `division_id=None` |
| `get_detailed_overdue_matches` | `database.py:6056` | `division_id=None` |
| `get_division_rounds` | `database.py:8824` | обязательный `division_id` |

Вся работа — слой хендлеров и `callback_data`. Единственное исключение:
`get_all_rounds()` (`database.py:4724`) жёстко зашивает
`AND (division_id = 1 OR division_id IS NULL)`. Эта функция обслуживает только
удаляемый `admin_manage_matches_info`; после `div-matches` она осиротеет.
**Не чиним её — удаляем вместе с её единственным потребителем.**

## 2. Компоненты и зависимости

```
nav-shell  (M0)
  ├── div-squads   (M1)   ← независим от M2/M3
  ├── div-matches  (M2)   ← независим от M1/M3
  └── div-debts    (M3)   ← независим от M1/M2; добавляет 3-ю кнопку в карточку
```

M1, M2 и M3 зависят только от M0 и **не зависят друг от друга** — после сдачи M0
их можно вести параллельно. Общий файл (`handlers/admin.py`) делает параллельную
работу конфликтной на уровне merge, поэтому рекомендуемый режим —
последовательный, но порядок между M1/M2/M3 значения не имеет. Выбранный порядок
`div-squads` → `div-matches` → `div-debts` идёт от самого дешёвого среза к самому
дорогому: M1 — переписывание back-кнопок, M2 — новая логика, M3 — новый экран.

## 3. Ключевое открытие по M2

`admin_div_manage_matches` (`handlers/admin.py:1433`) **уже существует** и уже
рисует список туров дивизиона со статусами. Но кнопка тура ведёт на
`admin_div_round:{div}:{r}`, который обработан `admin_div_round_matches`
(`:1470`) — то есть сразу открывает **список матчей**, минуя карточку тура.
Управления туром (открыть/закрыть/дедлайн/линия/напомнить) в дивизионном контуре
нет вообще.

Отсюда форма M2:

- `admin_div_round:{div}:{r}` перенаправляется на **новую карточку тура**
  (паритет с `_render_round_management`, `:2131`, но со скоупом дивизиона);
- список матчей тура переезжает на новый `admin_div_round_matches:{div}:{r}`;
- существующий хендлер `admin_div_round_matches` переиспользуется, меняется
  только его паттерн регистрации и парсинг префикса.

Это экономит примерно половину M2 и объясняет, почему `div-matches` разбит на 8
мелких задач, а не на 3 крупных.

## 4. Риски и меры

| # | Риск | Мера |
|---|---|---|
| R1 | Удаление глобальных экранов ломает существующие тесты: `tests/test_rbac_division_panels.py:109`, `tests/test_divisions_catalog_and_rosters.py:150-192` | Тесты правятся **в той же задаче**, что и удаление. Задача не считается закрытой, пока `pytest tests/ -q` не зелёный |
| R2 | `admin_cancel_match_action` (`:3320`) зовёт удаляемый `admin_manage_matches_info` (`:3330`) — незаметная точка отказа вне ветки матчей | Явная задача T2.7; в чек-листе M2 — grep по всему `handlers/` |
| R3 | FSM дедлайна (`ADMIN_WAITING_FOR_DEADLINE`, `ADMIN_WAITING_FOR_BATCH_*`) должен пронести `div_id` через ожидание текста | `div_id` кладётся в `context.user_data` (он per-user, утечки между админами нет) и **явно снимается `pop`** в обеих ветках выхода: успех и отмена |
| R4 | Открытие тура сейчас шлёт уведомление в глобальный `reports_topic_id` (`:2300-2313`, `:2395-2409`) — дивизион 3 увидят все | T2.4: цепочка `topic_cache.get_by_division(div, "reports")` → `"tables"` → `get_division_topics_map`, как в `handlers/base.py:660-669` |
| R5 | Там же `post_league_table_to_reports(context)` без аргументов перерисовывает таблицы **всех** дивизионов (`base.py:651-658`) | T2.4: передавать `division_id=div_id` |
| R6 | `callback_data` ограничен 64 байтами | Самый длинный новый — `admin_div_round_matches:{div}:{r}` ≈ 30 символов. Запас есть, отдельная проверка не нужна |
| R7 | Продакшен-строки с `division_id IS NULL` могли бы осиротеть после ухода глобальных экранов. Локальная `league.db` пуста, проверка неинформативна (Resolved Decision 3 в SPEC.md) | **Блокирующее действие перед деплоем:** прогнать на боевой `league.db` `SELECT division_id, COUNT(*) FROM matches GROUP BY division_id` и то же по `rounds`. Найдутся `NULL` — бэкфилл до выкатки |
| R8 | Право доступа теряется при рефакторинге экрана | Ни один новый хендлер не пишет свои проверки: только `_ensure_division_access` (`:99`) / `_ensure_super_admin` (`:116`). `config.ADMIN_IDS` инлайном не сравнивается нигде |

## 5. Контрольные точки

После **каждого** модуля:

```bash
python -m pytest tests/ -q
```

Дополнительно по модулям:

- **После M0** — панель супер-админа отдаёт ровно 6 строк; ни одна кнопка не ведёт
  на `admin_manage_squads` / `admin_manage_matches_info` / `admin_broadcast_menu`.
- **После M1** — `grep -rn "admin_manage_squads\|admin_manage_rosters" handlers/`
  пусто.
- **После M2** — `grep -rn "admin_manage_matches_info" handlers/ constants.py`
  пусто; `get_all_rounds` не имеет вызовов.
- **После M3** — `grep -rn "admin_broadcast_menu\|admin_broadcast_all_debts_execute" handlers/`
  пусто; итоговый grep по всем шести удалённым именам чист.

Финальная приёмка — 10 пунктов Success Criteria в [SPEC.md](../SPEC.md).

## 6. Что не входит в объём

- Рефакторинг `show_division_admin_panel` (Resolved Decision 4: панели остаются
  раздельными). Единственная правка — переименование кнопки в «📋 Сводка долгов в топик».
- Починка `get_all_rounds()` — удаляется вместе с потребителем.
- Любые изменения схемы, `api/`, `web/`, фоновых джобов. 12-часовой джоб долгов
  продолжает звать `_post_or_update_debts_for_division` без изменений.
