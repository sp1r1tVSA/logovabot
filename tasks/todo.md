# Задачи: дивизион-центричная супер-админ панель

План: [tasks/plan.md](plan.md) · Спека: [SPEC.md](../SPEC.md)
Порядок: `nav-shell` → `div-squads` → `div-matches` → `div-debts`.

Общее правило для всех задач: после завершения — `python -m pytest tests/ -q` зелёный.
Ни один новый хендлер не пишет собственную проверку прав — только
`_ensure_division_access` / `_ensure_super_admin` из `handlers/admin.py`.

---

## M0 · nav-shell

- [x] **T0.1 — Главная панель супер-админа: убрать 3 кнопки, переименовать 1**
  - Acceptance: клавиатура `show_super_admin_panel` (`handlers/admin.py:169-179`)
    содержит ровно 6 строк из макета SPEC.md; строки `:173`, `:174`, `:175`
    удалены; `:170` переименована в «🏆 Дивизионы» с прежним
    `callback_data="admin_divs_hub"`. Хендлеры `admin_manage_squads`,
    `admin_manage_matches_info`, `admin_broadcast_menu` пока остаются
    зарегистрированными — они становятся недостижимы из UI, удалит их M1/M2/M3.
  - Verify: `pytest tests/test_rbac_division_panels.py -v`; в клавиатуре нет
    `admin_manage_squads`, `admin_manage_matches_info`, `admin_broadcast_menu`.
  - Files: `handlers/admin.py`

- [x] **T0.2 — Карточка дивизиона: функциональный блок сверху + текст хаба**
  - Acceptance: в `admin_div_view` (`:1623`) перед техническим блоком
    (`:1664-1674`) вставлены `[⚔️ Управление матчами → admin_div_manage_matches:{div}]`
    и `[📋 Составы команд → admin_roster_div:{div}]`. Обе цели уже существуют и
    зарегистрированы (`handlers/__init__.py:684`, `:740`). Кнопка долгов **не
    добавляется** — придёт в T3.2 вместе со своим экраном. Хаб `admin_divs_hub`
    (`:1594`) показывает иконку статуса и число участников по каждому дивизиону,
    кнопки «➕ Создать дивизион» и «« Назад в админку».
  - Verify: новый тест в `tests/test_admin_division_navigation.py` —
    `admin_div_view` отдаёт оба callback'а, и они идут **до** `admin_div_topics`.
  - Files: `handlers/admin.py`, `tests/test_admin_division_navigation.py`

---

## M1 · div-squads

- [x] **T1.1 — Перенести утилиты составов в дивизионный скоуп**
  - ⚠️ Отступление: «🖼 Загрузить фото игроков» дивизионной сделать нельзя —
    `admin_fetch_photos` работает от `get_all_unique_players` и скоупа не имеет.
    Кнопка оставлена на экране с честной подписью «(вся лига)».
  - Acceptance: `admin_rosters_for_division` (`:4095`, callback `admin_roster_div:{div}`)
    — точка входа. Две утилитные кнопки из `admin_manage_squads` (`:4086-4087`)
    переносятся сюда и работают по дивизиону; ветка «добавить всем сразу»
    `admin_squad_add_missing_all` (`:4444-4447`) удаляется. Список клубов берётся
    из `get_division_teams` (`database.py:5684`) — чужих клубов на экране нет.
  - Verify: `pytest tests/test_divisions_catalog_and_rosters.py -v`
  - Files: `handlers/admin.py`

- [x] **T1.2 — Переписать back-кнопки ветки составов на карточку дивизиона**
  - Acceptance: все вхождения `callback_data="admin_manage_squads"` в
    `handlers/admin.py` (`:4088, 4130, 4134, 4167, 4213, 4320, 4447, 4453`)
    ведут на `admin_div_view:{div}` либо `admin_roster_div:{div}`. `div_id`
    берётся из `context.user_data["admin_roster_div_id"]`; если его нет —
    fallback `admin_divs_hub`, **не** удаляемый экран. Экран клуба
    (`admin_view_squad`, `:4148`) возвращает на `admin_roster_div:{div}` (`:4167`).
  - Verify: `grep -n 'admin_manage_squads' handlers/admin.py` — остаются только
    определение (`:4061`) и алиас (`:4144`), которые снимет T1.3.
  - Files: `handlers/admin.py`

- [x] **T1.3 — Удалить глобальный экран составов**
  - Acceptance: `admin_manage_squads` (`:4061`), алиас `admin_manage_rosters`
    (`:4144`) и `admin_squad_add_missing_all` удалены; сняты регистрация и импорт
    в `handlers/__init__.py`; `CB_ADMIN_MANAGE_SQUADS` удалён из `constants.py:22`.
    Асcert'ы в `tests/test_divisions_catalog_and_rosters.py:150-192` и
    `tests/test_rbac_division_panels.py:109` обновлены под новый контракт.
  - Verify: `grep -rn 'admin_manage_squads\|admin_manage_rosters\|CB_ADMIN_MANAGE_SQUADS' handlers/ constants.py tests/`
    — пусто. Затем `pytest tests/ -q`.
  - Files: `handlers/admin.py`, `handlers/__init__.py`, `constants.py`,
    `tests/test_divisions_catalog_and_rosters.py`, `tests/test_rbac_division_panels.py`

---

## M2 · div-matches

- [x] **T2.1 — Экран туров дивизиона: добавить блок действий**
  - Acceptance: `admin_div_manage_matches` (`:1433`) над сеткой туров получает
    «🎲 Сгенерировать матчи» (`admin_gen_div_select:{div}`), «📦 Открыть
    несколько туров» (`admin_batch_open_div:{div}`) и «⏰ Просроченные»
    (`admin_div_overdue:{div}`). Список туров и его источник
    (`get_division_rounds`, `get_round_info(r, div_id)`) не меняются.
  - Verify: тест — клавиатура содержит все три callback'а с нужным `div_id`.
  - Files: `handlers/admin.py`, `tests/test_admin_division_navigation.py`

- [x] **T2.2 — Карточка тура дивизиона**
  - Acceptance: новый `admin_div_round` на `admin_div_round:{div}:{r}` рисует
    паритетный `_render_round_management` (`:2131`) экран — статус, дедлайн,
    состояние линии, кнопки открыть/закрыть, линия ставок, «⚔️ Смотреть матчи
    тура», «« К турам». Все запросы к БД идут с `div_id`:
    `get_round_info(r, div_id)`, `set_round_bets_open(r, flag, div_id)`. Список
    матчей тура (бывший `admin_div_round_matches`, `:1470`) переезжает на
    `admin_div_round_matches:{div}:{r}`, кнопка «« Назад» из него ведёт на
    карточку тура. Паттерн `:685` в `handlers/__init__.py` перенаправляется на
    новый хендлер, список матчей регистрируется отдельным паттерном.
  - Verify: тест — `admin_div_round:{div}:{r}` отдаёт кнопки управления туром,
    а не список матчей; `admin_div_round_matches:{div}:{r}` отдаёт матчи.
  - Files: `handlers/admin.py`, `handlers/__init__.py`, `tests/test_admin_division_navigation.py`

- [x] **T2.3 — Открытие/закрытие тура в скоупе дивизиона**
  - Acceptance: `admin_close_round` (`:2413`) и одиночный FSM открытия
    (`:2251/:2271`) принимают `div_id` и зовут
    `update_round_status(r, is_open, deadline, division_id=div_id)`. `div_id`
    кладётся в `context.user_data` перед ожиданием текста и снимается `pop` в
    обеих ветках выхода — успех и отмена. После действия возврат на
    `admin_div_round:{div}:{r}`.
  - Verify: тест — открытие тура в дивизионе A не меняет `is_open` у тура с тем
    же номером в дивизионе B (проверка через `get_round_info(r, div_b)`).
  - Files: `handlers/admin.py`, `handlers/__init__.py`, `tests/test_admin_division_matches.py`

- [x] **T2.4 — Уведомления об открытии тура — в топик «📞 ОТЧЁТЫ» дивизиона**
  - Acceptance: блоки `:2300-2313` и `:2395-2409` больше не шлют в глобальный
    `reports_topic_id`. Цепочка разрешения повторяет `handlers/base.py:660-669`:
    `topic_cache.get_by_division(div_id, "reports")` → `"tables"` →
    `database.get_division_topics_map(div_id)`. Топик не настроен — уведомление
    не шлётся, админу показывается предупреждение, открытие тура при этом **не
    откатывается**. `post_league_table_to_reports` вызывается с
    `division_id=div_id` (иначе перерисует таблицы всех дивизионов, `base.py:651-658`).
  - Verify: тест с замоканным `context.bot.send_message` — `message_thread_id`
    равен топику дивизиона, а не глобальному.
  - Files: `handlers/admin.py`, `tests/test_admin_division_matches.py`

- [x] **T2.5 — Массовое открытие туров в скоупе дивизиона**
  - Acceptance: FSM `:2317/:2332/:2368` (`ADMIN_WAITING_FOR_BATCH_ROUNDS`,
    `ADMIN_WAITING_FOR_BATCH_DEADLINE`) стартует с `admin_batch_open_div:{div}`,
    зовёт `open_rounds_batch(start, end, deadline, division_id=div_id)`, выход —
    на `admin_div_manage_matches:{div}`. `div_id` в `user_data` с `pop` на обоих
    выходах. Регистрация ConversationHandler'а (`handlers/__init__.py:582-586`)
    обновлена под новый entry point.
  - Verify: `pytest tests/ -q` + тест изоляции по аналогии с T2.3.
  - Files: `handlers/admin.py`, `handlers/__init__.py`, `tests/test_admin_division_matches.py`

- [x] **T2.6 — Генерация матчей без шага выбора дивизиона**
  - Acceptance: вход только через `admin_gen_div_select:{div}` (`:838`) из
    карточки дивизиона; ветка `admin_gen_div_none` (`:825`) удалена, вместе с ней
    — возможность сгенерировать «глобально». Защита
    `division_has_played_matches` (`:919`) и проверка прав (`:849`) сохранены
    без изменений. Возврат — `admin_div_manage_matches:{div}`.
  - Verify: тест — попытка генерации в дивизионе с сыгранными матчами
    по-прежнему отклоняется; `grep -n 'admin_gen_div_none' handlers/` пусто.
  - Files: `handlers/admin.py`, `handlers/__init__.py`, `tests/test_admin_division_matches.py`

- [x] **T2.7 — Просроченные и back-кнопки ветки матчей**
  - Acceptance: `admin_list_overdue` (`:2220`) становится `admin_div_overdue:{div}`
    и зовёт `get_detailed_overdue_matches(division_id=div_id)`. Все вхождения
    `callback_data="admin_manage_matches_info"` (`:827, 921, 957, 987, 2112, 2137,
    2167, 2245, 2293, 2388, 2465, 2500, 2508, 2794, 3330`) переписаны на
    `admin_div_round:{div}:{r}` или `admin_div_manage_matches:{div}`. `div_id`
    берётся из `match["division_id"]` на экранах матча (`admin_view_match`, `:2488`)
    и из callback'а на экранах тура; fallback — `admin_divs_hub`.
    **Отдельно проверить `admin_cancel_match_action` (`:3320`)** — вызов на `:3330`
    ведёт в удаляемый хендлер и легко пропускается, так как лежит вне ветки матчей.
  - Verify: `grep -n 'admin_manage_matches_info' handlers/admin.py` — остаётся
    только определение `:2087`.
  - Files: `handlers/admin.py`

- [x] **T2.8 — Удалить глобальный экран матчей**
  - Acceptance: `admin_manage_matches_info` (`:2087`) и осиротевший
    `_render_round_management` (`:2131`) удалены; сняты регистрация и импорт в
    `handlers/__init__.py`; `CB_ADMIN_MANAGE_MATCHES_INFO` удалён из
    `constants.py:23`. `database.get_all_rounds()` (`:4724`) — потребителей нет;
    удалить вместе с её багом `division_id = 1 OR division_id IS NULL`
    (`:4735`), предварительно подтвердив grep'ом по всему репозиторию, что
    вызовов не осталось.
  - Verify: `grep -rn 'admin_manage_matches_info\|CB_ADMIN_MANAGE_MATCHES_INFO\|get_all_rounds' handlers/ constants.py tests/ scripts/ api/`
    — пусто. Затем `pytest tests/ -q`.
  - Files: `handlers/admin.py`, `handlers/__init__.py`, `constants.py`, `database.py`

### Отклонения от плана M2 (зафиксировано при реализации)

- **Callback генерации.** Вместо нового `admin_gen_div_select:{div}` оставлен уже
  зарегистрированный `admin_gen_div_{div}` / `admin_gen_exec_{div}`: контракт
  рабочий, переименование дало бы только лишнюю правку в тестах и паттернах.
- **Fallback back-кнопок — `admin_main_menu`, а не `admin_divs_hub`.** Хаб закрыт
  `_ensure_super_admin`, поэтому админа дивизиона он отошьёт; `admin_main_menu`
  роутит по роли через `show_admin_panel`. То же ограничение есть у `_roster_back_cb`
  из M1 — оставлено как есть, разобрать в T4.1.
- **`database.get_all_rounds()` не удалена.** Потребители остались:
  `database.get_active_round_number` (`database.py:6436`) и контекст ИИ-чата
  (`handlers/chat.py:94`). Её баг `division_id = 1 OR division_id IS NULL` теперь
  затрагивает только эти два места — вынесено за скоуп M2.
- **Глобальные `admin_round_matches` / `admin_remind_round` живы.** На первый ведёт
  `handlers/cabinet.py:2745`, второй ключуется одним номером тура. Обоим переписаны
  только back-кнопки — через `_round_back_cb(context, r)`, который берёт дивизион из
  `context.user_data["admin_round_div_id"]`.

---

## M3 · div-debts

- [x] **T3.1 — Меню рассылки долгов дивизиона**
  - Acceptance: новый `admin_div_debts_menu` на `admin_div_debts_menu:{div}`
    показывает два явно названных действия — «✉️ ЛС должникам дивизиона»
    (`admin_div_debts_dm:{div}`) и «📋 Сводка в топик дивизиона»
    (`admin_div_broadcast_debts:{div}`, уже существует, `:1508`) — плюс «« Назад»
    на `admin_div_view:{div}`. Развязка двух одинаково подписанных кнопок из
    Resolved Decision 2 в SPEC.md.
  - Verify: тест — меню отдаёт оба callback'а и кнопку назад на карточку.
  - Files: `handlers/admin.py`, `handlers/__init__.py`, `tests/test_admin_division_debts.py`

- [x] **T3.2 — ЛС должникам дивизиона + третья кнопка в карточке**
  - Acceptance: `admin_div_debts_dm` собирает долги через
    `get_detailed_overdue_matches(division_id=div_id)`, шлёт ЛС **только**
    участникам этого дивизиона (`get_division_users`, `database.py:8658`), и
    отчитывается числом доставленных/недоставленных. Ошибка отправки одному
    получателю не прерывает рассылку остальным. В `admin_div_view` добавляется
    третья кнопка `[📢 Рассылка задолженностей → admin_div_debts_menu:{div}]`,
    завершая блок из T0.2.
  - Verify: тест — при двух дивизионах ЛС уходят только `div_a`; замоканный
    `send_message`, падающий на первом получателе, не мешает второму.
  - Files: `handlers/admin.py`, `tests/test_admin_division_debts.py`

- [x] **T3.3 — Удалить глобальную рассылку, переименовать кнопку дивадмина**
  - Acceptance: `admin_broadcast_menu` (`:494`) и
    `admin_broadcast_all_debts_execute` (`:517`) удалены; сняты регистрации
    (`handlers/__init__.py:612`) и импорты. `admin_send_debts_to_warns` (`:573`)
    **сохраняется** — на него ссылается экран просроченных (`:2239`) и он зовёт
    `_post_or_update_debts_for_division`, который использует 12-часовой джоб; его
    back-кнопки (`:569, 587, 612`) переводятся на `admin_div_debts_menu:{div}`
    или `admin_divs_hub`. Кнопка в `show_division_admin_panel` (`:242`)
    переименована в «📋 Сводка долгов в топик» — `callback_data` не меняется.
  - Verify: `grep -rn 'admin_broadcast_menu\|admin_broadcast_all_debts_execute' handlers/ constants.py tests/`
    — пусто; `pytest tests/test_divisions_catalog_and_rosters.py -v` (покрывает
    `_post_or_update_debts_for_division` на `:221`).
  - Files: `handlers/admin.py`, `handlers/__init__.py`, `tests/test_rbac_division_panels.py`

### Отклонения от плана M3 (зафиксировано при реализации)

- **`admin_send_debts_to_warns` удалён, а не сохранён.** План оставлял его ради
  экрана просроченных, но экран стал скоупом дивизиона (T2.7), а сама функция
  рассылала сводку по **всем** дивизионам сразу — с карточки одного дивизиона это
  утечка. Две быстрые кнопки на экране просроченных заменены на
  `admin_div_broadcast_debts:{div}` и `admin_div_debts_dm:{div}`.
  `_post_or_update_debts_in_warns` **не тронут** — у него ~15 потребителей
  (джобы, `cabinet.py:3197`, `text_commands.py:531/569/614`).
- **Кнопка дивадмина перенаправлена, а не переименована.** В
  `show_division_admin_panel` она теперь ведёт на общий `admin_div_debts_menu:{div}`,
  а не просто меняет подпись при прежнем `admin_div_broadcast_debts`. Так у
  дивадмина и супер-админа одинаковый набор действий, и Resolved Decision 2
  закрывается в одном месте, а не двумя разными подписями.
- **`tests/test_rbac_division_panels.py:106`** обновлён под новый callback панели
  дивадмина; проверка подделки чужого дивизиона по-прежнему бьёт в
  `admin_div_broadcast_debts` — обработчик жив.

---

## Финал

- [x] **T4.1 — Сквозная приёмка**
  - Acceptance: все 10 пунктов Success Criteria в [SPEC.md](../SPEC.md) выполнены.
    Итоговый grep по шести удалённым именам чист. Регистрация в
    `register_all_handlers()` — все новые `CallbackQueryHandler` стоят **до**
    catch-all AI-обработчика текста/голоса, иначе они не сработают.
  - Verify: `python -m pytest tests/ -q`, затем
    `grep -rn 'admin_manage_squads\|admin_manage_rosters\|admin_manage_matches_info\|admin_broadcast_menu\|admin_broadcast_all_debts_execute\|admin_squad_add_missing_all' handlers/ constants.py tests/`
  - Files: —

- [x] **T4.2 — Блокирующая проверка перед деплоем (R7)**
  - Acceptance: на **боевой** `league.db` выполнены
    `SELECT division_id, COUNT(*) FROM matches GROUP BY division_id` и то же по
    `rounds`. Строк с `division_id IS NULL` нет — либо они забэкфилены до
    выкатки. Локальная БД пуста, поэтому локальная проверка ничего не
    доказывает (Resolved Decision 3 в SPEC.md).
  - Verify: вывод обоих запросов приложен к отчёту о деплое.
  - Files: —

### Приёмка T4.1 (результат)

| # | Success Criteria (SPEC.md) | Статус |
|---|---|---|
| 1 | 6 кнопок главной панели, «🏆 Дивизионы» | ✅ `handlers/admin.py:169-176` |
| 2 | Хаб → карточка, три новых блока сверху | ✅ `tests/test_admin_division_navigation.py` |
| 3 | Клубы / туры / меню долгов дивизиона N | ✅ M1–M3, три тест-файла |
| 4 | «« Назад» ведёт в карточку дивизиона N | ✅ `_div_home_cb`, `_round_back_cb`, `_roster_back_cb` |
| 5 | Экраны не зовут `get_all_rounds()` | ⚠️ админских вызовов нет; остались `database.py:6436` и контекст ИИ-чата `handlers/chat.py:94` — вне скоупа (отклонение M2) |
| 6 | Изоляция действий дивизиона N | ✅ тесты изоляции в M2/M3 |
| 7 | Открытие тура → топик «📞 ОТЧЁТЫ» своего дивизиона | ✅ `_announce_rounds_opened` |
| 8 | Два действия по долгам разведены | ✅ реализовано как общее меню, формулировка в SPEC.md обновлена |
| 9 | `pytest tests/ -q` зелёный | ✅ `PYTEST_EXIT=0` |
| 10 | Grep по шести удалённым именам | ✅ в проде чисто, остались только negative-assert'ы в тестах |

Дополнительно закрыт долг M1: `_roster_back_cb` при пустой сессии уводил на
`admin_divs_hub` (закрыт `_ensure_super_admin` — тупик для админа дивизиона);
теперь fallback — `admin_main_menu`, как у остальных веток.
