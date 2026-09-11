# Spec: Дивизион-центричная супер-админ панель

Статус: **черновик, ожидает утверждения** · Дата: 2026-09-11 · Ветка: `main`

---

## Objective

**Что строим.** Переносим три функциональных блока — управление матчами, составы команд и
рассылку задолженностей — с глобальной супер-админ панели внутрь карточки конкретного
дивизиона. Главная панель перестаёт быть «плоским» списком инструментов и становится
навигационным хабом; единицей администрирования лиги становится дивизион.

**Зачем.** Сейчас глобальные экраны молча работают только по дивизиону 1:
`database.get_all_rounds()` (`database.py:4724`) жёстко фильтрует `division_id = 1 OR
division_id IS NULL`, а `admin_manage_matches_info` (`handlers/admin.py:2087`) вызывает
`get_round_info(r)` без `division_id`, показывая статус произвольной строки таблицы
`rounds`. Супер-админ, открывающий тур «глобально», фактически открывает тур дивизиона 1 и
не видит туры дивизионов 2–5. Это зафиксировано в `FULL_BOT_AUDIT.md:1216`. Дивизион-центричная
навигация устраняет класс ошибки целиком: любой экран получает `division_id` из callback_data,
и не остаётся пути, по которому админ действует «вообще».

**Пользователь.** Супер-админ (`is_global_admin`). Админ дивизиона (`is_admin`) затронут
косвенно: его панель `show_division_admin_panel` сохраняется без изменений (см. Boundaries).

**Как поймём, что готово.** С главной панели физически невозможно совершить действие над
матчами/составами/долгами без выбора дивизиона; каждый такой экран принимает `div_id` в
callback_data и передаёт его в слой БД; `« Назад` из любого из этих экранов возвращает в
карточку своего дивизиона.

---

## Capability map

Запрос собирает три независимо тестируемые способности поверх общей навигационной оболочки.
Модули строятся строго в этом порядке — оболочка задаёт контракт callback_data, на который
опираются остальные три.

| Module id | Ответственность | Зависит от |
|---|---|---|
| `nav-shell` | Главная панель, хаб дивизионов, карточка дивизиона; контракт `<action>:{div_id}` | — |
| `div-squads` | Составы команд в скоупе дивизиона | `nav-shell` |
| `div-matches` | Туры, генерация, дедлайны, результаты, ТП в скоупе дивизиона | `nav-shell` |
| `div-debts` | ЛС-рассылка долгов участникам дивизиона | `nav-shell` |

Порядок сборки: `nav-shell` → `div-squads` → `div-matches` → `div-debts`

`div-squads` идёт раньше `div-matches`, потому что он самый маленький (переиспользует
существующий `admin_roster_div:{div_id}`) и первым проверяет контракт оболочки на живом
экране. `div-matches` — самый крупный модуль (~12 хендлеров + 2 FSM). `div-debts`
независим от двух предыдущих и может строиться параллельно с `div-matches`, если нужно.

Спецификации всех четырёх модулей собраны в этом файле (раздел «Module specs») вместо
отдельных `SPEC-<module>.md`: все изменения лежат в двух файлах, и дробление на четыре
документа дало бы больше навигации, чем содержания.

---

## Tech Stack

Без изменений относительно проекта:

- Python 3.11+, `python-telegram-bot[job-queue]` v21, полностью async
- SQLite в WAL, доступ только через `database.py` и `transaction()`
- Telegram HTML parse mode, inline-клавиатуры
- `pytest` + `unittest.IsolatedAsyncioTestCase` (dev-зависимость, не в `requirements.txt`)

**Новых зависимостей нет. Миграций схемы нет.** Слой БД уже принимает `division_id` во всех
функциях, которые нужны: `get_round_info` (`database.py:4302`), `update_round_status`
(`database.py:4514`), `open_rounds_batch` (`database.py:3256`), `get_matches_by_round`
(`database.py:3397`), `get_detailed_overdue_matches` (`database.py:6056`),
`get_all_unplayed_league_matches` (`database.py:5882`), `get_division_rounds`
(`database.py:8824`), `get_division_teams` (`database.py:5684`), `get_division_users`
(`database.py:8658`). Работа целиком на уровне хендлеров и callback_data.

---

## Commands

```bash
python -m pytest tests/ -q
```

```bash
python -m pytest tests/test_rbac_division_panels.py tests/test_divisions_catalog_and_rosters.py tests/test_division_admin_management.py tests/test_admin_division_panel.py -v
```

```bash
python main.py
```

Линтера и форматтера в проекте нет — стиль поддерживается вручную по соседнему коду.

---

## Project Structure

Затрагиваемые файлы (новых модулей не создаём):

```
handlers/admin.py        → все экраны админки; основной объём правок
handlers/__init__.py     → регистрация CallbackQueryHandler и ConversationHandler
constants.py             → удалить CB_ADMIN_MANAGE_SQUADS, CB_ADMIN_MANAGE_MATCHES_INFO
tests/test_admin_division_panel.py   → НОВЫЙ: карточка дивизиона и её три блока
tests/test_rbac_division_panels.py   → обновить: контракт клавиатур изменился
tests/test_divisions_catalog_and_rosters.py → обновить: admin_manage_squads удалён
```

Порядок регистрации хендлеров из `CLAUDE.md` соблюдается: все новые
`CallbackQueryHandler` добавляются **до** catch-all AI-хендлера в конце
`register_all_handlers()`.

---

## Screens & callback contract

### 1. Главная супер-админ панель (`show_super_admin_panel`, `handlers/admin.py:160`)

```
👑 Админ-панель
Выберите раздел:
[ 🏆 Дивизионы ]                  → admin_divs_hub
[ 👔 Админы дивизионов ]          → admin_div_admins_hub
[ 👥 Управление игроками ]        → admin_manage_players
[ 🔄 Обновить таблицы и стату ]   → admin_force_update
[ 🎭 Режим общения: Темшик 🍺 ]   → admin_toggle_chat_mode
[ « Назад в меню ]                → main_menu
```

Удаляются три строки клавиатуры: `handlers/admin.py:173` (Составы), `:174` (Матчи),
`:175` (Рассылка). Кнопка `:170` переименовывается «🏆 Дивизионы и темы» → «🏆 Дивизионы».

### 2. Хаб дивизионов (`admin_divs_hub`, `handlers/admin.py:1594`)

```
🏆 Управление дивизионами лиги
Выберите дивизион для управления:
[ 🟢 Дивизион 1 (16 игр.) ]      → admin_div_view_1
...
[ ➕ Создать дивизион ]           → admin_div_create_start
[ « Назад в админку ]             → admin_main_menu
```

Клавиатура уже соответствует макету. Меняется только текст: убрать абзац про «настраивать
отдельные темы», оставить «Выберите дивизион для управления:».

### 3. Карточка дивизиона (`admin_div_view`, `handlers/admin.py:1623`)

```
🏆 Дивизион 1
🟢 Активен  •  👥 Участников: 16

📌 Топики группы (3/3):
Драфты и матчи — ✅
Результаты и таблицы — ✅
Общение / Флудилка — ✅

[ ⚔️ Управление матчами ]         → admin_div_manage_matches:{div}
[ 📋 Составы команд ]             → admin_roster_div:{div}
[ 📢 Рассылка задолженностей ]    → admin_div_debts_menu:{div}
────────────────────────────────
[ 🔴 Отключить ] [ ✏️ Переименовать ]
[ 📌 Настроить топики ] [ 👥 Участники ]
────────────────────────────────
[ « К списку дивизионов ]         → admin_divs_hub
```

Три новые кнопки вставляются **перед** существующим блоком технических настроек
(`handlers/admin.py:1664-1674`), порядок остальных кнопок сохраняется.

### 4. Контракт callback_data

Разделитель — двоеточие, как в уже существующих дивизионных хендлерах
(`admin_div_manage_matches:{div_id}`). Легаси-формат с подчёркиванием
(`admin_div_view_{div_id}`) сохраняется там, где он уже используется, — переименование
существующих паттернов в объём не входит.

| Callback | Статус | Экран |
|---|---|---|
| `admin_div_view_{div}` | есть | карточка дивизиона |
| `admin_roster_div:{div}` | есть, меняется `« Назад` | список клубов дивизиона |
| `admin_div_manage_matches:{div}` | есть, расширяется | туры дивизиона |
| `admin_div_round:{div}:{r}` | есть, расширяется | карточка тура |
| `admin_div_gen_confirm:{div}` | **новый** | подтверждение генерации |
| `admin_div_gen_exec:{div}` | **новый** | выполнение генерации |
| `admin_div_open_batch:{div}` | **новый** | FSM массового открытия туров |
| `admin_div_open_round:{div}:{r}` | **новый** | FSM открытия тура с дедлайном |
| `admin_div_close_round:{div}:{r}` | **новый** | закрытие тура |
| `admin_div_bets_open:{div}:{r}` | **новый** | открыть линию ставок тура |
| `admin_div_bets_close:{div}:{r}` | **новый** | закрыть линию ставок тура |
| `admin_div_overdue:{div}` | **новый** | просроченные матчи дивизиона |
| `admin_div_remind:{div}:{r}` | **новый** | напомнить должникам тура |
| `admin_div_debts_menu:{div}` | **новый** | меню рассылки долгов дивизиона |
| `admin_div_debts_dm:{div}` | **новый** | ЛС должникам дивизиона |
| `admin_div_debts_topic:{div}` | **новый** | сводка в топик ПРЕДЫ дивизиона |
| `admin_manage_matches_info` | **удаляется** | — |
| `admin_manage_squads` / `admin_manage_rosters` | **удаляется** | — |
| `admin_broadcast_menu` | **удаляется** | — |

---

## Module specs

### M0 · `nav-shell` — навигационная оболочка

**Объём.** Главная панель, текст хаба, карточка дивизиона. Оболочка делает три глобальных
экрана **недостижимыми из UI**, но не удаляет их: каждый из них удаляется тем модулем,
который заменяет его функциональность и переписывает ведущие на него `« Назад`-кнопки.
Так каждый модуль остаётся самодостаточным срезом, а не «половиной удаления».

| Глобальный экран | Удаляется в модуле |
|---|---|
| `admin_manage_squads` / `admin_manage_rosters` (:4061, :4144) | `div-squads` |
| `admin_manage_matches_info` (:2087) | `div-matches` |
| `admin_broadcast_menu` (:494) | `div-debts` |

**Кнопки карточки.** `nav-shell` добавляет две кнопки, цели которых уже существуют:
`admin_div_manage_matches:{div}` и `admin_roster_div:{div}`. Третья кнопка
(`admin_div_debts_menu:{div}`) добавляется модулем `div-debts` вместе со своим экраном —
карточка ни на одном шаге сборки не содержит кнопки, ведущей в никуда.

**Судьба осиротевших действий.** `admin_send_debts_to_warns` (:573) остаётся
зарегистрированным — на него ссылается экран просроченных (`handlers/admin.py:2239`), и он
же переиспользует `_post_or_update_debts_for_division`, который зовёт фоновый 12-часовой
джоб. `admin_broadcast_all_debts_execute` (:517, ЛС всем дивизионам сразу) удаляется в
`div-debts`: его роль берёт на себя дивизионная рассылка.

**Правило перенаправления back-кнопок** (применяется в M1–M3): `div_id` берётся из данных
самого экрана — карточка матча знает `match["division_id"]`, экран клуба —
`context.user_data["admin_roster_div_id"]`. Если вывести не удалось, fallback —
`admin_divs_hub`, **никогда** не удаляемый глобальный экран.

**Acceptance.**
- Клавиатура главной панели содержит ровно 6 строк из макета.
- Ни одна кнопка главной панели не ведёт на `admin_manage_squads`,
  `admin_manage_matches_info`, `admin_broadcast_menu`.
- `admin_div_view` отдаёт клавиатуру, содержащую `admin_div_manage_matches:{div}` и
  `admin_roster_div:{div}`, а технический блок (вкл/выкл, переименование, топики,
  участники) идёт после них.
- `python -m pytest tests/ -q` — зелёный.

### M1 · `div-squads` — составы в скоупе дивизиона

**Объём.** Кнопка «📋 Составы команд» в карточке ведёт напрямую на
`admin_rosters_for_division` (`handlers/admin.py:4095`), минуя шаг выбора дивизиона.

**Правки.**
- `handlers/admin.py:4130` и `:4134`: `« Назад к дивизионам` → `« Назад в дивизион`,
  callback `admin_manage_squads` → `admin_div_view_{div_id}`.
- `handlers/admin.py:4167`: fallback `back_cb` при отсутствии `div_id` — `admin_divs_hub`.
- `handlers/admin.py:4213`, `:4320`, `:4447`, `:4453`: те же замены.
- Утилита «🖼 Загрузить фото игроков» (`admin_fetch_photos_cb`) переезжает на экран списка
  клубов дивизиона. Команда `/fetch_photos` сохраняется как была.
- Утилита «➕ Добавить во все клубы игроков из матчей» (`admin_squad_add_missing_all`)
  **удаляется** — решение подтверждено. Вместе с кнопкой уходит ветка
  `if data == "admin_squad_add_missing_all"` (`handlers/admin.py:4444-4447`); per-club
  эквивалент `admin_squad_add_missing_{club}` в карточке клуба остаётся рабочим.
  `database.add_missing_squad_players()` без аргумента теряет вызывающих в `handlers/` —
  саму функцию в `database.py` не трогаем, она остаётся доступной для `scripts/`.

**Acceptance.**
- `admin_rosters_for_division` для дивизиона N возвращает клавиатуру только с клубами
  дивизиона N и кнопкой назад на `admin_div_view_N`.
- Пустой дивизион даёт сообщение «нет зарегистрированных команд» и ту же кнопку назад.
- `admin_view_squad` для клуба дивизиона N ведёт назад на `admin_roster_div:N`.

### M2 · `div-matches` — матчи в скоупе дивизиона

**Объём.** `admin_div_manage_matches` (`handlers/admin.py:1433`) доводится до полного
паритета с удалённым глобальным экраном, но с обязательным `division_id` во всех вызовах БД.

Экран туров дивизиона:

```
⚔️ Матчи дивизиона {name}
[ 🎲 Сгенерировать (Round Robin) ]  → admin_div_gen_confirm:{div}
[ 📅 Открыть туры (массово) ]       → admin_div_open_batch:{div}
[ ⏰ Просроченные ]                  → admin_div_overdue:{div}
[ 🟢 Тур 1 ] [ 🔴 Тур 2 ]           → admin_div_round:{div}:{r}
...
[ « Назад в дивизион ]              → admin_div_view_{div}
```

Карточка тура (`admin_div_round:{div}:{r}`) получает то, что раньше было в
`_render_round_management` (`handlers/admin.py:2131`): статус, дедлайн, состояние линии
Logovo.bet, кнопки открытия/закрытия тура, открытия/закрытия линии, напоминания должникам и
переход к матчам тура.

**Обязательная передача `division_id`:**

| Вызов | Было | Станет |
|---|---|---|
| список туров | `get_all_rounds()` | `get_division_rounds(div_id)` |
| статус тура | `get_round_info(r)` | `get_round_info(r, div_id)` |
| открыть/закрыть | `update_round_status(r, ...)` | `update_round_status(r, ..., division_id=div_id)` |
| массовое открытие | `open_rounds_batch(a, b, dl)` | `open_rounds_batch(a, b, dl, division_id=div_id)` |
| просроченные | `get_detailed_overdue_matches()` | `get_detailed_overdue_matches(division_id=div_id)` |
| матчи тура | `get_matches_by_round(r)` | `get_matches_by_round(r, div_id)` |

**Генерация.** `admin_generate_matches_confirm` (:806) сейчас сам показывает выбор
дивизиона — этот шаг исчезает: из карточки дивизиона сразу открывается экран подтверждения
(бывший `admin_gen_div_select`, :838) с уже известным `div_id`. Защита от повторной
генерации (`division_has_played_matches`, :919) и RBAC-проверка (:849) сохраняются без
изменений. Ветка «🌐 Без дивизиона / Общий» (`admin_gen_div_none`, :825) становится
недостижимой и удаляется вместе с обработкой `target_raw == "none"`.

**FSM.** Два `ConversationHandler` (`handlers/__init__.py:564-567` и `:582-586`) получают
`div_id` через `context.user_data["admin_round_div_id"]`, выставляемый в entry point.
Финальные сообщения FSM (`handlers/admin.py:2293`, `:2388`) ведут назад на
`admin_div_manage_matches:{div_id}`.

**Уведомление об открытии тура → топик «📞 ОТЧЁТЫ» своего дивизиона.** Сейчас
`handlers/admin.py:2300-2313` и `:2395-2409` шлют в `database.get_group_id()` с
`message_thread_id` из глобального конфига `reports_topic_id` — то есть открытие тура
дивизиона 3 видят все дивизионы. Новый хелпер `_resolve_division_reports_topic(div_id)` в
`handlers/admin.py` повторяет цепочку разрешения из
`handlers/base.py::post_league_table_to_reports` (`base.py:660-669`):

```
topic_cache.get_by_division(div_id, "reports")
  → topic_cache.get_by_division(div_id, "tables")
  → database.get_division_topics_map(div_id)["reports"] or [...]["tables"]
```

`group_chat_id` и `message_thread_id` берутся из найденной привязки. Если у дивизиона не
привязан ни `reports`, ни `tables` — fallback в общую группу `get_group_id()` **без**
`message_thread_id`, с именем дивизиона в тексте сообщения, плюс `logger.warning`. Тихо
пропускать нельзя: админ должен увидеть, что тур открыт, даже если топики не настроены.

Текст уведомления дополняется именем дивизиона:
`🟢 Открыт {N}-й Тур — {division_name}!`

Вызов `post_league_table_to_reports(context)` при открытии первого тура (`:2309`, `:2404`)
получает аргумент `division_id=div_id` — иначе он перерисовывает таблицы всех активных
дивизионов.

**Карточка матча.** `admin_view_match` (:2488), простановка счёта (:3177), ТП
(`admin_tp_home/away/draw_*`), сброс (:2780) — логика не меняется, `_ensure_match_access`
(:140) уже проверяет права по `match["division_id"]`. Меняются только back-кнопки:
`:2500`, `:2508`, `:2794` → `admin_div_round:{div}:{round}` с `div_id` из самого матча.

**Acceptance.**
- Для дивизиона N экран туров показывает ровно `get_division_rounds(N)`, а иконка
  🟢/🔴 каждого тура соответствует `get_round_info(r, N)["is_open"]`.
- Открытие тура 5 в дивизионе 2 не меняет `is_open` тура 5 в дивизионах 1 и 3.
- Уведомление об открытии тура уходит только в топик дивизиона 2.
- Массовое открытие 1–3 в дивизионе 2 затрагивает ровно 3 строки `rounds` с
  `division_id = 2`.
- Экран просроченных дивизиона N не содержит матчей других дивизионов.
- Генерация из карточки дивизиона N затрагивает только матчи дивизиона N (уже покрыто
  `clear_matches_by_division`).

### M3 · `div-debts` — рассылка долгов дивизиона

**Объём.** Кнопка «📢 Рассылка задолженностей» в карточке открывает дивизионное меню
рассылки с **двумя** явно названными действиями. Это разрешение двусмысленности из
Open Question 2: супер-админ получает обе операции, и имена кнопок больше не совпадают
с кнопкой панели админа дивизиона.

```
📢 Рассылка задолженностей — {name}
Несыгранных матчей в дивизионе: N
Участников с долгами: M

[ 📩 ЛС должникам дивизиона ]     → admin_div_debts_dm:{div}
[ 📋 Сводка в топик дивизиона ]   → admin_div_debts_topic:{div}
[ « Назад в дивизион ]            → admin_div_view_{div}
```

Счётчики в шапке считаются заранее через `get_all_unplayed_league_matches(division_id=div_id)`
— админ видит масштаб до нажатия, а не после.

**`admin_div_debts_dm` — персональные ЛС.** Производный от
`admin_broadcast_all_debts_execute` (`handlers/admin.py:517`), но:
- `database.list_users()` → `database.get_division_users(div_id)`;
- ранний выход по `get_all_unplayed_league_matches(division_id=div_id)`;
- текст ЛС и кнопка «📋 Мои матчи в кабинете» сохраняются без изменений;
- `safe_send_notification` уже глушит ошибки доставки — цикл не должен падать на одном
  заблокировавшем бота пользователе.

**`admin_div_debts_topic` — сводка в топик.** Тонкая обёртка над существующим
`_post_or_update_debts_for_division(context, div_id, div_name)` (`handlers/admin.py:666`).
Новой логики нет; функция уже шлёт в `previews`/`warns` своего дивизиона и покрыта
`tests/test_divisions_catalog_and_rosters.py:221` и `tests/test_telegram_topics_logic.py:516`.

Экран результата (общий для обоих действий):

```
✅ {Рассылка выполнена | Сводка отправлена}
Дивизион: {name}
Найдено долгов: N
Уведомлено участников: K из M        ← только для ЛС
[ 🔄 Повторить ]           → то же действие
[ « К рассылке ]           → admin_div_debts_menu:{div}
```

**Кнопка в панели админа дивизиона.** `admin_div_broadcast_debts` (`:1508`) остаётся как
есть по поведению, но подпись меняется на «📋 Сводка долгов в топик»
(`handlers/admin.py:242`) — чтобы две разные операции не назывались одинаково.

**Acceptance.**
- При долгах в дивизионах 1 и 2 запуск ЛС для дивизиона 1 вызывает `bot.send_message`
  только для `telegram_id` участников дивизиона 1.
- Дивизион без долгов даёт сообщение «Долгов нет» и не шлёт ни одного ЛС.
- Один участник, заблокировавший бота, не прерывает рассылку остальным.
- `admin_div_debts_topic` шлёт ровно в `previews`-тред своего дивизиона (регрессия уже
  покрыта существующим тестом).
- Меню показывает счётчики, совпадающие с `get_all_unplayed_league_matches(division_id=N)`.

---

## Code Style

Дивизионный хендлер строится по образцу уже существующих в файле: парс `div_id` из
callback через `_parse_div_arg`, проверка доступа через `_ensure_division_access`, блокирующие
вызовы БД — через `asyncio.to_thread`, отрисовка — через `_send_panel`.

```python
@admin_only
async def admin_div_overdue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Просроченные матчи одного дивизиона — без шага «Выберите дивизион»."""
    query = update.callback_query
    div_id = _parse_div_arg(query, "admin_div_overdue")
    if div_id is None:
        await _deny_access(update, "⛔ Дивизион не определён")
        return
    if not await _ensure_division_access(update, div_id):
        return

    div = await asyncio.to_thread(database.get_division, div_id)
    div_name = div["name"] if div else f"#{div_id}"
    overdue = await asyncio.to_thread(database.get_detailed_overdue_matches, div_id)

    keyboard = [
        [InlineKeyboardButton(
            f"Тур {m['round_number']}: {m.get('player1_team') or 'К1'} vs {m.get('player2_team') or 'К2'}",
            callback_data=f"admin_view_match_{m['id']}",
        )]
        for m in overdue
    ]
    keyboard.append([InlineKeyboardButton("« К турам", callback_data=f"admin_div_manage_matches:{div_id}")])

    if overdue:
        text = f"⏰ <b>Просроченные матчи — {html.escape(str(div_name))}</b> ({len(overdue)}):\n\nВыберите матч:"
    else:
        text = f"⏰ <b>Просроченных матчей в «{html.escape(str(div_name))}» нет.</b>"

    await _send_panel(update, context, text, InlineKeyboardMarkup(keyboard))
```

Конвенции, которые соблюдаем:

- Пользовательские строки — русские, идентификаторы и docstring'и — английские; комментарии
  в `handlers/admin.py` смешанные, пишем в тон соседнему коду.
- `parse_mode="HTML"`, все подстановки пользовательских данных — через `html.escape`.
  Markdown в переносимых экранах (`_render_round_management` использовал `"Markdown"`)
  переводится на HTML, чтобы имена дивизионов с `_` и `*` не ломали разметку.
- Ни одного прямого сравнения с `config.ADMIN_IDS` — только `is_global_admin` / `is_admin` /
  `_ensure_division_access`.
- Ни одного SQL вне `database.py`.
- `div_id` проходит через `int()` на входе и подставляется в callback_data как число —
  f-строка в callback безопасна только потому, что значение уже провалидировано.

---

## Testing Strategy

Фреймворк — `pytest` поверх `unittest.IsolatedAsyncioTestCase`, как во всех 78 существующих
файлах. Тесты живут в `tests/`, один файл на область. Изоляция БД — по образцу
`tests/test_rbac_division_panels.py`.

**Уровни.**

| Уровень | Что проверяем | Где |
|---|---|---|
| Контракт клавиатур | набор `callback_data` каждого экрана, наличие/отсутствие кнопок | `test_admin_division_panel.py` (новый) |
| Изоляция дивизионов | действие в дивизионе A не меняет состояние дивизиона B | `test_admin_division_panel.py` |
| RBAC | супер-админ vs админ дивизиона vs посторонний | `test_rbac_division_panels.py` (обновить) |
| Регрессия удаления | удалённые callback'и нигде не встречаются | `test_admin_division_panel.py` |

**Обязательные новые тесты:**

1. `test_super_panel_has_no_global_blocks` — клавиатура `show_super_admin_panel` не содержит
   `admin_manage_squads`, `admin_manage_matches_info`, `admin_broadcast_menu`; кнопка
   дивизионов подписана «🏆 Дивизионы».
2. `test_division_card_exposes_three_blocks` — `admin_div_view` отдаёт три новых callback'а.
3. `test_div_rounds_are_isolated` — два дивизиона с разными наборами туров; экран каждого
   показывает только свои.
4. `test_open_round_scoped_to_division` — открытие тура в A оставляет `is_open = 0` у тура с
   тем же номером в B.
5. `test_div_overdue_excludes_other_divisions`.
6. `test_div_debts_dm_targets_only_division_members` — мок `context.bot`, сверка множества
   `chat_id`.
7. `test_squads_back_returns_to_division_card`.
8. `test_removed_callbacks_are_unregistered` — сборка `register_all_handlers` на мок-app и
   проверка, что ни один паттерн не матчит удалённые строки.
9. `test_round_open_notifies_division_reports_topic` — открытие тура в дивизионе A шлёт
   сообщение в `reports`-тред A и ни в один тред B.
10. `test_round_open_falls_back_to_group_without_topic` — дивизион без `reports`/`tables`:
    сообщение уходит в общую группу без `message_thread_id`, с именем дивизиона в тексте.
11. `test_div_debts_menu_shows_both_actions` — меню отдаёт `admin_div_debts_dm:{div}` и
    `admin_div_debts_topic:{div}`.

**Порог.** Каждый из четырёх модулей считается готовым только при зелёном
`python -m pytest tests/ -q` целиком — не только новых файлов. Существующие тесты
`test_divisions_catalog_and_rosters.py:150-192` и `test_rbac_division_panels.py:105-188`
завязаны на удаляемые callback'и и обновляются в рамках того же модуля, который их ломает,
а не «потом».

---

## Boundaries

**Always do**

- Прогонять `python -m pytest tests/ -q` перед каждым коммитом; коммиты — Conventional
  Commits, по одному на модуль (`refactor(admin): ...`, `feat(admin): ...`).
- Передавать `division_id` в каждый вызов БД на этих экранах. Вызов
  `get_round_info(r)` без второго аргумента в новом коде — дефект, а не «пока так».
- Проверять доступ через `_ensure_division_access` / `_ensure_super_admin` первым делом
  в каждом новом хендлере.
- Регистрировать новые `CallbackQueryHandler` **до** catch-all AI-хендлера.
- `html.escape` для любых имён дивизионов, клубов и игроков.

**Ask first**

- Любое изменение схемы БД или сигнатур функций в `database.py` — по текущему анализу не
  требуется; если понадобится, это сигнал, что что-то понято неверно.
- Изменение **поведения** `show_division_admin_panel` (панель админа дивизиона). Решено:
  оставляем раздельной с карточкой супер-админа; из правок допустима только смена подписи
  кнопки на «📋 Сводка долгов в топик».
- Изменение текста или маршрутизации уведомлений, уходящих участникам в ЛС и в топики
  групп, — кроме уже решённого переноса уведомления об открытии тура в топик «📞 ОТЧЁТЫ»
  своего дивизиона (M2).
- Удаление или переименование любого callback'а, не перечисленного в таблице контракта.
- Трогать фоновые джобы долгов (30-минутный трекер, 12-часовой дайджест).

**Never do**

- Удалять или «чинить» падающие тесты вместо кода.
- Писать SQL вне `database.py` или открывать соединения в хендлерах.
- Сравнивать `user.id` с `config.ADMIN_IDS` напрямую.
- Оставлять экран, совершающий действие над матчами/составами/долгами без явного
  `division_id`.
- Коммитить `.env`, `league.db`, токены.
- Ломать `LOGOVO_LOCKDOWN`-гард на group=-1 или fail-closed-логику `api/auth.py`.

---

## Success Criteria

1. Главная супер-панель — ровно 6 кнопок из макета; «🏆 Дивизионы» вместо «🏆 Дивизионы и темы».
2. Хаб дивизионов открывает карточку любого из дивизионов; карточка содержит три новых блока
   над техническими настройками.
3. Из карточки дивизиона N доступны: список клубов N, туры N, меню рассылки долгов N
   с двумя действиями (ЛС и сводка в топик).
4. `« Назад` из любого экрана этих трёх веток возвращает в карточку дивизиона N.
5. Ни один экран не вызывает `get_all_rounds()`; функция либо удалена, либо не имеет
   вызывающих в `handlers/`.
6. Открытие/закрытие тура, генерация, дедлайн, ТП и рассылка в дивизионе N не меняют
   состояние и не шлют сообщений в дивизионы ≠ N — подтверждено тестами 3–6, 9, 11.
7. Уведомление об открытии тура дивизиона N приходит в топик «📞 ОТЧЁТЫ» дивизиона N;
   при отсутствии привязки — в общую группу с именем дивизиона и `logger.warning`.
8. Два ранее одноимённых действия разведены явными подписями внутри одного меню
   `admin_div_debts_menu:{div}`: «✉️ ЛС должникам дивизиона» и «📋 Сводка в топик
   дивизиона». Меню одинаково доступно супер-админу (из карточки дивизиона) и
   админу дивизиона (из его панели) — *реализовано так вместо исходной формулировки
   «кнопки названы по-разному в разных панелях», см. отклонения M3 в `tasks/todo.md`.*
9. `python -m pytest tests/ -q` зелёный, включая обновлённые существующие файлы.
10. Grep по `handlers/` не находит `admin_manage_matches_info`, `admin_manage_squads`,
    `admin_manage_rosters`, `admin_broadcast_menu`, `admin_squad_add_missing_all`.

---

## Resolved Decisions

1. **`admin_squad_add_missing_all` — удаляется.** Per-club эквивалент остаётся в карточке
   клуба; `database.add_missing_squad_players()` сохраняется в `database.py` для `scripts/`.
2. **Рассылка долгов — вариант (в): супер-админу обе операции.** Кнопка в карточке
   открывает меню с «✉️ ЛС должникам дивизиона» и «📋 Сводка в топик дивизиона».
   *Уточнено при реализации M3:* кнопка в панели админа дивизиона не переименована,
   а переведена на то же меню — у обеих ролей одинаковый набор действий, а
   развязка двух смыслов живёт в одном месте.
3. **Легаси-строки `division_id IS NULL` — проверено, результат неинформативен.**
   `SELECT COUNT(*) FROM matches WHERE division_id IS NULL` на локальной `league.db`
   вернул `0`, но `SELECT division_id, COUNT(*) FROM matches GROUP BY division_id` вернул
   **пустой результат** — то есть таблица `matches` пуста целиком, как и `rounds` и `users`.
   Локальная БД — чистая dev-заготовка и ничего не говорит о проде. **Действие перед
   выкаткой:** прогнать те же два запроса на боевой `league.db`. Если легаси-строки есть —
   нужен разовый скрипт привязки в `scripts/`, и это отдельная задача вне текущего объёма.
4. **Уведомление об открытии тура — в топик «📞 ОТЧЁТЫ» своего дивизиона.** Цепочка
   разрешения и fallback описаны в M2.

## Open Questions

Пока нет — все развилки закрыты. Новые записывать сюда по ходу реализации.
