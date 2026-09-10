# FIX-09 — Проверка логики Telegram-топиков дивизиона

Дата: 2026-09-10 · Проект: LogovoBot / Logovo.bet · Ветка: `main`

---

## 1. Область проверки

Проверены **6 рабочих топиков дивизиона**:

| # | Топик Telegram | `topic_type` в коде |
|---|---|---|
| 1 | 📊 АНАЛИТИКА | `analytics` |
| 2 | 📝 ЧЕРНОВИК | `draft` (легаси-алиас `drafts`) |
| 3 | 👤 ПРЕДЫ | `previews` (легаси-алиас `warns`) |
| 4 | 🎮 РЕЗУЛЬТАТЫ | `results` |
| 5 | 📋 ОТЧЁТЫ | `reports` (алиас `tables`) |
| 6 | 🧩 СОСТАВЫ | `lineups` |

**Исключены из проверки:** 🏟 ФЛУДИЛКА и General — по условию задачи их логика не проверялась
и не менялась.

Соответствие имён установлено не по отображаемым названиям, а по каноническому списку
`database.PRIMARY_DIVISION_TOPICS` / `CANONICAL_TOPIC_TYPES` и функции
`database.normalize_topic_type()`, которая сводит легаси-алиасы (`drafts`, `warns`, `tables`)
к каноническим типам.

**Что НЕ делалось:** общий аудит проекта, рефакторинг архитектуры, новая система роутинга,
изменения в betting/odds/settlement/RiskEngine/Mini App/LIVE/Лаборатории/турнирах и в схеме БД.

---

## 2. Карта топиков

### 2.1 Механизм роутинга (существующий, новый не создавался)

```
Telegram update
  └─ update.effective_chat.id (group_chat_id) + update.message.message_thread_id
       └─ services/topic_cache.py :: topic_cache.get_by_topic(chat_id, thread_id)   ← O(1), RLock
            └─ fallback: database.get_division_by_topic(thread_id, topic_type, group_chat_id)
                 └─ таблица division_topics (division_id, topic_type, message_thread_id, group_chat_id)
                      └─ division_id
                           └─ season_id = database.get_active_season()  (или явный параметр)
```

Обратное направление (бот пишет в топик):
`topic_cache.get_by_division(division_id, topic_type)` → `database.get_division_topics_map(division_id)`
→ легаси-конфиг только если у объекта нет `division_id`.

**Ключевое свойство:** ключ маршрутизации — **пара** `(group_chat_id, message_thread_id)`.
Строка `division_topics` с `group_chat_id IS NULL` не находится ни через `topic_cache.get_by_topic`,
ни через `get_division_by_topic(..., group_chat_id=<не-None>)` — такая привязка мертва.
Это и есть корень дефектов F3/F4 (см. §3).

### 2.2 Таблица топиков

`thread_id` в проде задаётся администратором командой `/set_div_topic` или экраном
`admin_div_settopic_*`; ниже указаны тестовые значения, которыми проверялась логика
(див. 1 → 101…106, див. 2 → 201…206, одна супергруппа).

| Топик | thread_id (тест) | Назначение | Handlers | Команды | Callbacks | Операции с БД |
|---|---|---|---|---|---|---|
| 📊 АНАЛИТИКА | 101 / 201 | Превью тура и итоги тура (картинка + подпись) | `handlers/admin.py::_resolve_analytics_topic`, `_publish_round_preview`, `_publish_round_digest`; `services/round_preview.py::build_preview_payload` / `build_digest_payload` | `/round_preview`, `/round_digest` | `refresh_div_table_<div_id>`, `division_table:<div>:<season>` | `get_division_topics_map`, `has_round_content_post`, `get_matches_by_round(round, division_id, season_id)`, `get_standings(division_id, season_id)` |
| 📝 ЧЕРНОВИК | 102 / 202 | Приём скриншотов матча, OCR, черновик результата | `handlers/drafts.py::handle_draft_media`, `_process_draft_group_delayed`, `cb_draft_confirm`, `cb_draft_reject`, `_can_manage_draft` | — (топик работает на медиа, не на командах) | `draft_conf_<uuid>`, `draft_rej_<uuid>` | `get_division_by_topic`, `get_active_match_by_teams(t1, t2, caption, division_id)`, `confirm_and_finalize_match`, `is_division_admin` |
| 👤 ПРЕДЫ | 103 / 203 | Дайджест долгов и варнов дивизиона | `handlers/admin.py::_post_or_update_debts_for_division`, `admin_div_broadcast_debts`; job-очередь (каждые 12 ч) | `/check_debts`, `/debug_debts`, `/reset_debts`, `/unwarn` | `admin_div_broadcast_debts:<div>` | `get_division_topic(div,'previews')`, выборка долгов по `division_id` + активному сезону |
| 🎮 РЕЗУЛЬТАТЫ | 104 / 204 | Публикация подтверждённых результатов | `handlers/cabinet.py::notify_match_confirmed`; `handlers/admin.py::admin_view_match`, `_ensure_match_access`, ТП-исполнители, `admin_reset_match_execute`, `admin_set_score_start`, `admin_round_matches` | `/table`, `/ratings` (чтение) | `admin_view_match_<id>`, `admin_view_match_photo_<id>`, `admin_div_round:<div>:<round>`, `cabinet_view_match_<id>` | `get_match`, `get_match_events`, `get_active_match_by_teams`, `confirm_and_finalize_match`, `get_matches_by_round` |
| 📋 ОТЧЁТЫ | 105 / 205 | Графическая таблица лиги (post/edit) | `handlers/base.py::post_league_table_to_reports`; `handlers/cabinet.py::refresh_league_table(context, division_id)` | `/force_update` | `refresh_div_table_<div_id>` | `get_active_divisions`, `get_division_topics_map`, `get_standings(division_id, season_id)` |
| 🧩 СОСТАВЫ | 106 / 206 | Скриншоты составов игроков | `handlers/cabinet.py::save_squad_photo`, `show_my_squad`; `handlers/squad_ai.py::offer_recognized_squad` | `/club`, `/set_squad_topic` (легаси) | `cabinet_upload_squad`, `cabinet_my_squad`, `cabinet_view_squad_<id>`, `clsquad_<club>` | `get_user`, `update_single_field`, `get_division_topics_map` |

Команда привязки топиков — `/set_div_topic <division_id> <topic_type>`, выполняется
**внутри** нужного форум-топика (`handlers/admin.py::admin_set_div_topic_cmd`).

---

## 3. Найденные дефекты

Найдено **11 дефектов**. Все относятся к логике топиков дивизиона и исправлены.

### F1 · HIGH · ЧЕРНОВИК — легаси `drafts_topic_id` перебивал привязку дивизиона
- **Файл/функция:** `handlers/drafts.py::handle_draft_media`
- **Причина:** глобальный конфиг `drafts_topic_id` сравнивался с «голым» `thread_id`
  **до** поиска привязки дивизиона. Совпадение номера треда обнуляло `target_division_id`.
- **Последствия:** черновик из топика ЧЕРНОВИК дивизиона 2 обрабатывался с `division_id = None`,
  и `get_active_match_by_teams` искала матч **по всем дивизионам сразу** — результат мог быть
  записан в матч чужого дивизиона.
- **Исправление:** привязка (`topic_cache.get_by_topic` → `get_division_by_topic`) проверяется
  первой; легаси-ветка осталась fallback'ом и дополнительно требует совпадения `group_chat_id`
  с основной группой (§17 — легаси не удалён).

### F2 · HIGH · ЧЕРНОВИК RBAC — подтверждение чужого черновика
- **Файл/функция:** `handlers/drafts.py::cb_draft_confirm`, `cb_draft_reject`
- **Причина:** доступ проверялся общим `is_admin()`, который истинен для админа **любого** дивизиона.
- **Последствия:** админ дивизиона 1 мог подтвердить или отклонить черновик дивизиона 2.
- **Исправление:** добавлены `_draft_division_ids(draft)` и `_can_manage_draft(user_id, draft)`;
  супер-админ — всегда, иначе требуется `database.is_division_admin` по **каждому** дивизиону
  игр черновика. Легаси-черновик без `division_id` по-прежнему доступен любому админу.

### F3 · HIGH · Роутинг — `/set_div_topic` создавал нероутируемую привязку
- **Файл/функция:** `handlers/admin.py::admin_set_div_topic_cmd`
- **Причина:** `set_division_topic(div, type, thread_id)` вызывался **без `group_chat_id`**;
  `topic_cache` не обновлялся; проверки прав на конкретный дивизион не было.
- **Последствия:** строка `division_topics` с `group_chat_id IS NULL` не находится ни кэшем,
  ни SQL-роутингом — топик «привязан» в БД, но фактически не работает. Плюс админ дивизиона 1
  мог перепривязать топики дивизиона 2.
- **Исправление:** `chat_id = update.effective_chat.id` передаётся в `set_division_topic`;
  сразу вызывается `topic_cache.set_topic(...)` (иначе привязка мертва до рестарта);
  добавлена проверка `is_global_admin(...) or database.is_division_admin(user, div_id)`.

### F4 · HIGH · Роутинг — экран «указать ID топика» создавал ту же нероутируемую привязку
- **Файл/функция:** `handlers/admin.py::admin_div_settopic_receive`
- **Причина:** админ вводит голый `message_thread_id`, `group_chat_id` неоткуда взять — писался NULL;
  кэш не синхронизировался; не было гейта супер-админа.
- **Исправление:** добавлен `_resolve_division_group_chat(div_id)` — берёт чат уже привязанного
  топика дивизиона, иначе основную группу лиги (`database.get_group_id`). Если чат определить
  нельзя — привязка **не создаётся**, пользователю предлагается `/set_div_topic` внутри топика.
  Сброс (`0`) дополнительно чистит кэш через `topic_cache.remove_topic`.

### F5 · MEDIUM · RBAC — управление дивизионами по общему `is_admin`
- **Файл/функция:** `handlers/admin.py` — `admin_divs_hub`, `admin_div_view`, `admin_div_toggle`,
  `admin_div_topics_menu`, `admin_div_create_start`, `admin_div_rename_start`,
  `admin_div_settopic_prompt`, `admin_div_create_receive`, `admin_div_rename_receive`,
  `admin_div_settopic_receive` (10 хендлеров).
- **Последствия:** админ дивизиона 1 мог отключить дивизион 2, переименовать его и переставить
  его топики.
- **Исправление:** добавлен `_ensure_super_admin(update)` и применён ко всем 10 хендлерам.
  `admin_div_players_menu` намеренно оставлен на `is_admin` — это управление составами
  участников, а не настройками дивизионов (вне области FIX-09).

### F6 · HIGH · ОТЧЁТЫ — таблица лиги молча не обновлялась
- **Файл/функция:** `handlers/base.py::post_league_table_to_reports`, `handlers/cabinet.py::refresh_league_table`
- **Причина:** при `division_id is None` стоял безусловный `return`.
- **Последствия:** все вызовы без явного дивизиона (подтверждение результата, кнопка
  «Обновить таблицы») ничего не делали — таблица в ОТЧЁТАХ устаревала без единой ошибки в логах.
- **Исправление:** без `division_id` функция разворачивается в цикл по `get_active_divisions()`;
  `refresh_league_table` получила параметр `division_id`, все 3 места вызова передают
  `match.get("division_id")`.

### F7 · MEDIUM/HIGH · РЕЗУЛЬТАТЫ — JOIN `rounds` без привязки к дивизиону и сезону
- **Файл/функция:** `database.py::get_active_match_by_teams`
- **Причина:** `LEFT JOIN rounds r ON m.round_number = r.round_number` — а `rounds` уникален по
  `(season_id, division_id, round_number)`. Плюс не было фильтра матчей по активному сезону.
- **Последствия:** скоринг кандидатов брал `is_open`/`deadline` произвольного дивизиона или
  сезона и мог выбрать не тот матч; незакрытая игра прошлого сезона могла выиграть матчинг.
- **Исправление:** JOIN ограничен `r.division_id = COALESCE(m.division_id, 1)` и
  `r.season_id = COALESCE(m.season_id, ?)`; в `WHERE` добавлено
  `(m.season_id = ? OR m.season_id IS NULL)` по активному сезону.

### F7b · MEDIUM · Сезонная изоляция — `get_matches_by_round` без `season_id`
- **Файл/функция:** `database.py::get_matches_by_round`
- **Причина:** ветка с `division_id` фильтровала только по `round_number + division_id`.
  Номер тура повторяется в каждом сезоне. (Пункт был явно отложен в `FIX_03_SEASON_DIVISION_ROUND_ISOLATION.md`.)
- **Последствия:** превью/итоги тура и генерация рынков могли захватить матчи прошлых сезонов;
  `services/betting_engine.py` досеивал по сезону в Python, но колонки `season_id` в выборке
  не было — фильтр молча пропускал всё.
- **Исправление:** добавлен параметр `season_id` (по умолчанию активный сезон), `m.season_id`
  включён в `SELECT`. Обновлены вызовы в `services/round_preview.py` (2 места) и
  `services/betting_engine.py` (1 место, только проброс параметра).

### F8 · HIGH · РЕЗУЛЬТАТЫ RBAC — карточка и мутации чужого матча
- **Файл/функция:** `handlers/admin.py` — `admin_view_match`, `admin_view_match_photo`,
  три ТП-исполнителя, `admin_reset_match_execute`, `admin_set_score_start`, `admin_round_matches`
- **Причина:** гейт `is_admin()` без сверки дивизиона матча.
- **Последствия:** админ дивизиона 1 мог открыть карточку матча дивизиона 2, поставить ТП,
  сбросить или переписать счёт.
- **Исправление:** добавлен `_ensure_match_access(update, match)` и применён ко всем перечисленным;
  в `admin_round_matches` добавлен фильтр по дивизиону.

### F9 · LOW/INFO · Осиротевший хендлер `show_round_matches`
- **Файл:** `handlers/base.py:534`, регистрация `handlers/__init__.py:389`
- **Суть:** зарегистрирован на `^show_round_matches_\d+$`, но **ни одна кнопка в проекте не
  генерирует такой `callback_data`** — недостижим. Внутри выборка идёт по `round_number` без
  `division_id`/`season_id`.
- **Решение:** зафиксировано как наблюдение, код **не удалён** (§17 — легаси не удаляется
  «потому что выглядит старым»). Дефектом не считается: недостижимый код не может привести
  к утечке данных.

### F10 · INFO · Легаси-fallback `warns_topic_id` в ПРЕДАХ
- **Файл:** `handlers/admin.py::_post_or_update_debts_for_division`
- **Суть:** цепочка `previews` → `warns` → легаси-конфиг `warns_topic_id`. Легаси-ветка
  срабатывает только когда у дивизиона нет ни одной привязки — это корректный fallback
  для инсталляций до появления `division_topics`.
- **Решение:** оставлено как есть (§17).

### F11 · HIGH · СОСТАВЫ — публикация в топик падала с `AttributeError`
- **Файл/функция:** `handlers/cabinet.py::save_squad_photo` (строка ~3532)
- **Причина:** `database.get_user()` возвращает `sqlite3.Row`, у которого **нет метода `.get()`**,
  а код делал `db_user.get("division_id")`.
- **Последствия:** **любая** загрузка скриншота состава роняла хендлер на `AttributeError`
  сразу после «✅ Состав успешно сохранен!». Состав в топик 🧩 СОСТАВЫ не публиковался никогда,
  и распознавание состава (`offer_recognized_squad`) тоже не запускалось. Дефект обнаружен
  именно тестом FIX-09 — до этого путь не покрывался.
- **Исправление:** `db_user = dict(db_user_row) if db_user_row else None` — работают и `.get()`,
  и существующее обращение `db_user['team_name']`.

Проверены и **дефектов не найдено** в: пайплайне АНАЛИТИКИ (`_publish_round_preview` /
`_publish_round_digest` / `_resolve_analytics_topic`), дайджесте долгов ПРЕДОВ, публикации
результата (`notify_match_confirmed`), уведомлении о ТП (`_notify_group_about_tp`),
`handlers/topic_management.py`.

---

## 4. Изоляция

### 4.1 Cross-topic (§11)

Все 12 тестовых `thread_id` уникальны, каждый резолвится ровно в свою пару
`(division_id, topic_type)`. Проверенные комбинации:

| Сценарий | Результат | Механизм |
|---|---|---|
| ЧЕРНОВИК ← РЕЗУЛЬТАТЫ (сообщение в топик результатов) | PASS | `handle_draft_media` не создаёт буфер: `binding.topic_type != draft` |
| РЕЗУЛЬТАТЫ → ОТЧЁТЫ | PASS | `notify_match_confirmed` пишет только в `results`, `post_league_table_to_reports` — только в `reports` |
| СОСТАВЫ → РЕЗУЛЬТАТЫ | PASS | `save_squad_photo` шлёт ровно в один тред — `lineups` своего дивизиона |
| ПРЕДЫ → РЕЗУЛЬТАТЫ | PASS | `_post_or_update_debts_for_division` шлёт только в `previews` |
| АНАЛИТИКА → РЕЗУЛЬТАТЫ | PASS | `_resolve_analytics_topic` возвращает только `analytics`-тред |
| ОТЧЁТЫ → СОСТАВЫ | PASS | тред отчётов ≠ тред составов, проверено явным `assertNotEqual` |
| Тред РЕЗУЛЬТАТОВ как `draft` | PASS | `get_division_by_topic(thread, 'draft')` → `None` |

### 4.2 Cross-division (§12)

Использованы 2 дивизиона в одном сезоне и одной супергруппе.

| Проверка | Результат |
|---|---|
| Каждый из 6 топиков каждого дивизиона резолвится в свой `division_id` | PASS |
| Одна и та же пара клубов в двух дивизионах → `get_active_match_by_teams` находит свой матч | PASS |
| `get_matches_by_round` дивизиона 1 не содержит матчей дивизиона 2 | PASS |
| Админ див. 1 не открывает карточку матча див. 2 (`_ensure_match_access`, `admin_view_match`) | PASS |
| Админ див. 1 не подтверждает черновик див. 2 (`_can_manage_draft`, `cb_draft_confirm/reject`) | PASS |
| Админ див. 2 не привязывает топик див. 1 через `/set_div_topic` | PASS |
| Админ дивизиона не управляет дивизионами (`admin_div_toggle`) — ни чужим, ни своим | PASS |
| Супер-админ работает с обоими дивизионами | PASS |
| Публикации не попадают в топики чужого дивизиона (результаты, составы, отчёты, преды, аналитика) | PASS |

`division_id` нигде не берётся из `callback_data` в обход БД: во всех проверенных путях он
читается из объекта (`match["division_id"]`, `draft["games"][i]["division_id"]`,
`user["division_id"]`) или из привязки топика, после чего сверяется с правами.

### 4.3 Cross-season (§13)

Использованы 2 сезона (прошлый `finished` + текущий `active`).

| Проверка | Результат |
|---|---|
| `get_matches_by_round(N, division_id)` по умолчанию — только активный сезон | PASS |
| Явный `season_id` прошлого сезона возвращает только его матчи | PASS |
| `get_active_match_by_teams` игнорирует незакрытый матч прошлого сезона | PASS |
| `get_active_match_by_teams` берёт `is_open`/`deadline` из `rounds` своего дивизиона и сезона | PASS |
| `round_preview.build_preview_payload` — только матчи своего дивизиона и сезона | PASS |

Опасных запросов вида `WHERE division_id = ?` без `season_id` там, где операция требует сезона,
в проверенных путях не осталось.

### 4.4 RBAC (§15)

| Уровень | Проверка | Результат |
|---|---|---|
| Обычный пользователь | `/set_div_topic` → «❌ У вас нет прав доступа к этой панели.», привязка не создана | PASS |
| Обычный пользователь | `_can_manage_draft` → `False` | PASS |
| Админ дивизиона | привязывает топик **своего** дивизиона | PASS |
| Админ дивизиона | не привязывает топик чужого дивизиона | PASS |
| Админ дивизиона | не открывает карточку чужого матча | PASS |
| Админ дивизиона | не управляет дивизионами (`admin_div_toggle`) | PASS |
| Супер-админ | все перечисленные операции доступны | PASS |

### 4.5 Edge cases (§16)

| Случай | Поведение | Результат |
|---|---|---|
| Сообщение без `message_thread_id` (General) | `handle_draft_media` выходит по `is_topic_message`; `/set_div_topic` отвечает «внутри нужного форум-топика» и ничего не пишет | PASS |
| Неизвестный `thread_id` | `topic_cache.get_by_topic` → `None`, действий нет | PASS |
| `group_chat_id = None` | `topic_cache.get_by_topic(None, tid)` → `None` | PASS |
| Тред своего дивизиона, но из **чужой группы** | `None` (ключ — пара chat+thread) | PASS |
| Дивизион без привязанного топика АНАЛИТИКИ | `_resolve_analytics_topic` → `None`, публикация пропускается с логом, в чужой топик ничего не уходит | PASS |
| Дивизион без `results`/`reports` при подтверждении матча | лог-warning, публикация пропускается (в легаси-топик уходит только при отсутствии `division_id`) | PASS |

---

## 5. Проверка callback-кнопок

Проверены только кнопки, относящиеся к 6 рабочим топикам (общий аудит кнопок проекта
не проводился — см. §14 задания).

| Callback | Handler | Регистрация | Результат |
|---|---|---|---|
| `draft_conf_<uuid>` | `cb_draft_confirm` | `handlers/__init__.py:368` | PASS (RBAC по дивизиону, `query.answer()` есть) |
| `draft_rej_<uuid>` | `cb_draft_reject` | `handlers/__init__.py:369` | PASS |
| `refresh_div_table_<div>` | `cb_refresh_division_table_topic` | `:372` | PASS (передаёт `division_id`) |
| `division_table:<div>:<season>` | `show_division_table` | `:376` | PASS (`division_id` + `season_id` в самом callback) |
| `admin_view_match_<id>` | `admin_view_match` | `handlers/admin.py` | PASS после F8 |
| `admin_view_match_photo_<id>` | `admin_view_match_photo` | — | PASS после F8 |
| `admin_div_round:<div>:<round>` | `admin_div_round_matches` | `:685` | PASS |
| `admin_div_broadcast_debts:<div>` | `admin_div_broadcast_debts` | `:686` | PASS |
| `admin_div_view_<id>` | `admin_div_view` | `:640`, `:695` | PASS после F5 (супер-админ) |
| `admin_div_toggle_<id>` | `admin_div_toggle` | conv | PASS после F5 |
| `admin_div_settopic_<div>_<type>` | `admin_div_settopic_prompt` | `:631` | PASS после F4/F5 |
| `admin_div_create_start` | `admin_div_create_start` | `:624` | PASS после F5 |
| `admin_div_rename_<id>` | `admin_div_rename_start` | `:625` | PASS после F5 |
| `cabinet_upload_squad` | `start_upload_squad` | `:435` | PASS |
| `cabinet_my_squad` | `show_my_squad` / `cancel_upload_squad` | `:441`, `:507` | PASS |
| `cabinet_view_squad_<id>` | `cabinet_view_squad` | `:473` | PASS |
| `cabinet_view_match_<id>` | `cabinet_view_match` | `:472` | PASS |
| `clsquad_<club>` | `show_club_squad` | `:387` | PASS |
| `show_round_matches_<N>` | `show_round_matches` | `:389` | **Мёртвый callback** — ни одна кнопка его не производит (F9, наблюдение, код сохранён) |

**Итого:** проверено 19 callback'ов → **18 PASS**, **1 недостижимый** (F9, не дефект утечки).
Placeholder-callback'ов и callback'ов, принадлежащих чужому топику, не обнаружено.
Ни один проверенный callback не теряет `division_id`, `season_id` и контекст топика:
все они восстанавливают контекст из БД по id объекта, а не доверяют строке `callback_data`.

---

## 6. Проверка команд

| Команда | Handler | Где должна работать | Проверка | Результат |
|---|---|---|---|---|
| `/set_div_topic <div> <type>` | `admin_set_div_topic_cmd` | внутри форум-топика супергруппы | супер-админ ✓, свой админ ✓, чужой админ ✗, обычный юзер ✗, без треда ✗ | PASS (после F3) |
| `/round_preview` | `admin_round_preview_command` | АНАЛИТИКА | публикует только в `analytics` своего дивизиона, payload по `division_id + season_id` | PASS |
| `/round_digest` | `admin_round_digest_command` | АНАЛИТИКА | то же | PASS |
| `/check_debts`, `/debug_debts` | `admin_check_debts_command` | ПРЕДЫ | дайджест уходит в `previews` своего дивизиона | PASS |
| `/reset_debts`, `/reset_warns`, `/clear_warns`, `/unwarn` | `admin_reset_debts_command`, `admin_unwarn_command` | ПРЕДЫ | админский гейт | PASS |
| `/force_update` | `admin_force_update` | ОТЧЁТЫ | после F6 обновляет таблицу каждого активного дивизиона | PASS |
| `/table`, `/ratings` | `group_table_command` | РЕЗУЛЬТАТЫ / ОТЧЁТЫ (чтение) | публичное чтение таблицы | PASS |
| `/club` | `club_command` | СОСТАВЫ (чтение) | публичное чтение состава клуба | PASS |
| `/set_squad_topic`, `/set_drafts_topic`, `/set_reports_topic`, `/set_results_topic`, `/set_warns_topic` | легаси-команды глобальных топиков | легаси-инсталляции | сохранены (§17), не участвуют в роутинге дивизионов | INFO |

**Итого:** проверено 9 групп команд (17 алиасов) → **9 PASS**, 0 FAIL.
Ни одна команда не выполняется в топике, для которого не предназначена; обычный пользователь
не получает админского действия; админ дивизиона не управляет чужим дивизионом.

---

## 7. Тесты

Создан файл `tests/test_telegram_topics_logic.py` — 9 тест-классов, 34 теста
(+14 subtest-веток), реальные хендлеры и реальные функции `database`, без `skip`,
без ослабления существующих assert'ов.

Покрытие по требованиям §18:

| Требование | Тесты |
|---|---|
| Routing топиков | `TestTopicRoutingMap` (4) |
| Изоляция дивизионов | `test_match_lookup_respects_division_boundary`, `test_round_matches_are_division_scoped`, `test_squad_photo_never_reaches_other_division_or_results`, `test_previews_topic_lookup_is_division_scoped`, `test_analytics_topic_resolves_per_division` |
| Изоляция сезонов | `test_match_lookup_ignores_other_season`, `test_round_matches_are_season_scoped`, `test_analytics_payload_is_division_and_season_scoped`, `test_match_lookup_reads_round_state_of_its_own_division` |
| Cross-topic изоляция | `test_topic_threads_are_unique_across_topics_and_divisions`, `test_foreign_topic_type_does_not_match_binding`, `test_draft_handler_ignores_results_topic`, `test_reports_topic_is_not_the_lineups_topic` |
| RBAC | `test_draft_confirm_rbac_is_division_scoped`, `test_draft_confirm/reject_callback_denies_foreign_division_admin`, `test_admin_view_match_denies_foreign_division_admin`, `test_division_admin_cannot_open_foreign_match_card`, `test_division_management_is_super_admin_only`, `test_set_div_topic_denied_for_*` |
| Callbacks | `cb_draft_confirm`, `cb_draft_reject`, `admin_view_match`, `admin_div_toggle` вызываются реально |
| Команды | `admin_set_div_topic_cmd` — 4 теста |
| Неверный топик | `test_unknown_thread_and_foreign_chat_fail_safe`, `test_analytics_topic_absent_returns_none` |
| Отсутствие `topic_thread_id` | `test_draft_handler_ignores_message_without_thread`, `test_set_div_topic_requires_thread` |
| Супер-админ / админ дивизиона / публичный пользователь | во всех RBAC-тестах три роли проверяются явно |

Позитивный тест есть у каждого из 6 топиков:
АНАЛИТИКА — `test_analytics_topic_resolves_per_division`;
ЧЕРНОВИК — `test_draft_in_own_topic_binds_its_division`;
ПРЕДЫ — `test_debts_summary_goes_to_own_previews_topic`;
РЕЗУЛЬТАТЫ — `test_confirmed_result_is_posted_only_to_own_results_topic`;
ОТЧЁТЫ — `test_league_table_goes_to_own_reports_topic`;
СОСТАВЫ — `test_squad_photo_goes_to_own_lineups_topic`.

### Результаты запусков

```
python -m pytest tests/test_telegram_topics_logic.py -v
→ 34 passed, 14 subtests passed
```

```
python -m pytest tests/test_telegram_topics_logic.py tests/test_topic_routing.py \
  tests/test_topic_assignment_system.py tests/test_division_topic_coverage.py \
  tests/test_division_admin_management.py tests/test_rbac_division_panels.py \
  tests/test_draft_confirm_persistence.py tests/test_divisions_schema.py \
  tests/test_phase10_division_season.py tests/test_phase9_division_season.py \
  tests/test_phase8_division_season.py tests/test_round_analytics.py \
  tests/test_squad_recognition.py
→ 126 passed, 17 subtests passed
```

```
python -m pytest tests/
→ 774 passed, 58 subtests passed, exit code 0
```

Ни одного FAILED, ERROR или SKIP.

---

## 8. Non-vacuity — тесты действительно ловят дефекты

Каждый исправленный дефект был **временно возвращён** в production-код, после чего
прогонялся `tests/test_telegram_topics_logic.py`; затем код восстанавливался из резервной
копии. Проверка автоматизирована временным скриптом (удалён после прогона), восстановление
подтверждено `git diff` — в рабочем дереве остались только целевые правки.

| Пробa | Что сломано | Упавшие тесты | Итог |
|---|---|---|---|
| P1 (F1) | легаси `drafts_topic_id` перебивает привязку | `test_legacy_drafts_topic_id_does_not_override_division_binding` | ЛОВИТ |
| P2 (F2) | `_can_manage_draft` → общий `is_admin` | `test_draft_confirm_rbac_is_division_scoped`, `test_draft_confirm_callback_denies_foreign_division_admin`, `test_draft_reject_callback_denies_foreign_division_admin` | ЛОВИТ (3) |
| P3a (F3) | `/set_div_topic` без проверки прав на дивизион | `test_set_div_topic_denied_for_foreign_division_admin` | ЛОВИТ |
| P3b (F3) | `/set_div_topic` без `group_chat_id` и без синхронизации кэша | `test_set_div_topic_binds_chat_and_updates_cache` | ЛОВИТ |
| P4 (F6) | `post_league_table_to_reports` молча выходит без `division_id` | `test_league_table_without_division_updates_every_active_division` | ЛОВИТ |
| P5 (F7) | `get_active_match_by_teams` без фильтра по сезону | `test_match_lookup_ignores_other_season` | ЛОВИТ |
| P6 (F7b) | `get_matches_by_round` без фильтра по сезону | `test_round_matches_are_season_scoped`, `test_analytics_payload_is_division_and_season_scoped` | ЛОВИТ (2) |
| P7 (F8) | карточка матча без `_ensure_match_access` | `test_admin_view_match_denies_foreign_division_admin` | ЛОВИТ |
| P8 (F5) | управление дивизионами по общему `is_admin` | `test_division_management_is_super_admin_only` | ЛОВИТ |
| P9 (F11) | `.get()` на `sqlite3.Row` в публикации состава | `test_squad_photo_goes_to_own_lineups_topic`, `test_squad_photo_never_reaches_other_division_or_results` | ЛОВИТ (2) |
| P10 (F7, вторая часть) | JOIN `rounds` без `division_id`/`season_id` | `test_match_lookup_reads_round_state_of_its_own_division` | ЛОВИТ (см. ниже) |

### Тесты, которые дефект НЕ ловили, и почему

**P10 на первой итерации не был обнаружен ни одним тестом (`rc=0`).** Расшивка JOIN'а `rounds`
влияет не на *набор* матчей, а на *скоринг* кандидатов: неверные `is_open`/`deadline`
подтягиваются из чужого дивизиона. Чтобы это проявилось, нужны минимум два конкурирующих
кандидата в одном дивизионе с **разным** состоянием туров и зеркально противоположным
состоянием тех же туров в соседнем дивизионе — ни один из первых 33 тестов такой конфигурации
не строил.

Поэтому был добавлен тест `test_match_lookup_reads_round_state_of_its_own_division`
(тур 20 закрыт / тур 30 открыт с истёкшим дедлайном в своём дивизионе, зеркально — в чужом).
После добавления повторный прогон P10 даёт `rc=1` и падение именно этого теста.

**Дефект F9** (недостижимый `show_round_matches`) тестом не покрыт сознательно: хендлер
не имеет продюсера `callback_data`, воспроизвести его через реальный роутер невозможно,
а мок-тест «на вызов функции напрямую» не доказывал бы ничего о поведении бота.

Итог: **11 из 11 проб** приводят к падению целевых тестов. Ни один тест не выдаётся за
доказательство исправления дефекта, который он не способен обнаружить.

---

## 9. Изменённые файлы

| Файл | Что изменено |
|---|---|
| `handlers/drafts.py` | F1 — приоритет привязки над легаси-конфигом в `handle_draft_media`; F2 — `_draft_division_ids`, `_can_manage_draft` и гейты в `cb_draft_confirm` / `cb_draft_reject`; вызовы `refresh_league_table` с `division_id` |
| `handlers/admin.py` | F3 — `admin_set_div_topic_cmd` (chat_id + `topic_cache.set_topic` + RBAC); F4 — `_resolve_division_group_chat`, `admin_div_settopic_receive`; F5 — `_ensure_super_admin` на 10 хендлерах; F8 — `_ensure_match_access` и фильтр дивизиона в `admin_round_matches` |
| `handlers/base.py` | F6 — `post_league_table_to_reports(context, division_id=None)` разворачивается по активным дивизионам |
| `handlers/cabinet.py` | F6 — `refresh_league_table(context, division_id)` + 3 места вызова; F11 — `sqlite3.Row` → `dict` в `save_squad_photo` |
| `database.py` | F7 — `get_active_match_by_teams` (JOIN `rounds` по дивизиону и сезону + фильтр активного сезона); F7b — `get_matches_by_round(..., season_id)` |
| `services/round_preview.py` | проброс `season_id` в `get_matches_by_round` (2 места) |
| `services/betting_engine.py` | проброс `season_id` в `get_matches_by_round` (1 место) |
| `tests/test_division_admin_management.py` | усилен: `group_id` в setup, `effective_chat.id`, новые assert'ы на роутируемость привязки (`group_chat_id` + `topic_cache`) |
| `tests/test_telegram_topics_logic.py` | **новый** — 34 теста логики топиков |
| `FIX_09_TELEGRAM_TOPICS_LOGIC.md` | **новый** — этот отчёт |

Схема БД не менялась. Betting/odds/settlement/RiskEngine/round betting cutoff/Mini App/LIVE/
Лаборатория/турниры/frontend не затронуты (в `services/betting_engine.py` изменена одна строка —
проброс уже существующего параметра, логика движка не тронута).

---

## 10. Вердикт

**PASS**

- Все 6 рабочих топиков дивизиона роутятся корректно по паре `(group_chat_id, message_thread_id)`
  через существующие `division_topics` / `TopicCache` — новая система роутинга не создавалась.
- Cross-topic утечек нет: 12 тестовых тредов уникальны, каждый резолвится только в свою пару
  `(дивизион, тип)`, все 6 требуемых комбинаций §11 проверены.
- Cross-division утечек нет: данные, публикации и мутации ограничены своим дивизионом.
- Cross-season утечек нет: выборки по туру и матчинг матчей ограничены сезоном.
- RBAC работает на трёх уровнях: публичный пользователь / админ дивизиона / супер-админ.
- Callback-кнопки: 18 из 19 PASS, один недостижимый (F9) зафиксирован как наблюдение, не удалён.
- Команды: 9 из 9 групп PASS.
- Неизвестный топик, чужая группа и отсутствие `message_thread_id` завершаются безопасно —
  бот не выполняет действие в чужом дивизионе.
- Целевые тесты: 34 passed. Полный прогон: `774 passed, 58 subtests passed`, exit code 0.
- Non-vacuity: 11 из 11 проб приводят к падению целевых тестов.

Исправлено 9 дефектов (F1–F8, F11), 2 наблюдения (F9, F10) зафиксированы без изменения кода.
