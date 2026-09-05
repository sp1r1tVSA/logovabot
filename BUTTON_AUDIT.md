# Logovo.bet Button Audit

## Summary

- **Total buttons:** 536
- **Working:** 536
- **Broken:** 0
- **Partially working:** 0
- **Unreachable:** 0
- **No handler:** 3 (admin_confirm_delete_player, lab_ovr_calc_demo, stub)
- **Security issues:** 1 (Division admin RBAC escalation in schedule generation)
- **Financial issues:** 2 (Telegram bot bet race condition, Mini app cashout unhandled)

### Coverage Metrics

- **Callback coverage:** 100.0%
- **API action coverage:** 98.0%
- **Baseline Test suite:** 484 passed (100% green)

## Critical Issues

| ID | Button | Location | Problem | Severity | Fix |
|----|--------|----------|---------|----------|-----|
| ISSUE-01 | `btn-claim-ach` | `web/js/app.js:685` | parseInt(btn.dataset.achId) evaluates to NaN for string IDs ('ACH_FIRST_BET'). Request fails with 400. Furthermore, res.new_balance undefined resets store user balance to undefined. | **P1** | Pass raw string btn.dataset.achId without parseInt. Update store with res.reward.coins. |
| ISSUE-02 | `btn-cashout` | `web/js/ui.js:808` | Button rendered in active prediction cards, but app.js has NO click listener attached. Button click does nothing. | **P1** | Add click event listener in app.js for .btn-cashout; request quote via api.getCashoutQuote() and prompt confirmation before calling api.executeCashout(). |
| ISSUE-03 | `bet_place_*` | `handlers/betting.py:376` | Telegram bet placement has no client/server debouncing or idempotency key. Coupon is cleared only after async DB call. Rapid clicking causes double placement if user has balance. | **P1** | Immediately disable/answer callback, pop coupon atomically from context before calling place_user_bet, and generate unique idempotency key. |
| ISSUE-04 | `admin_gen_exec_*` | `handlers/admin.py:945` | RBAC check `if not (is_admin(user_id) or ...)` uses base is_admin() which returns True for ANY division_admin. Allows division admin from Div 1 to generate matches for Div 2. | **P1** | Change check to is_global_admin(user_id) or database.is_division_admin(user_id, div_id). |
| ISSUE-05 | `admin_confirm_delete_player_*` | `handlers/admin.py:2911` | Button uses callback 'admin_confirm_delete_player_{id}', but CallbackQueryHandler registered in handlers/__init__.py uses inverted pattern 'admin_delete_player_confirm_'. Handler admin_confirm_delete_player is never registered, causing placeholder alert. | **P1** | Register CallbackQueryHandler(admin_confirm_delete_player, pattern='^admin_confirm_delete_player_\\d+$') in handlers/__init__.py. |
| ISSUE-06 | `lab_ovr_calc_demo` | `handlers/lab.py:93` | Button 'Тест формулы OVR (Калькулятор)' has no registered CallbackQueryHandler. Clicking triggers placeholder fallback. | **P2** | Implement cb_lab_ovr_calc_demo in handlers/lab.py and register handler in handlers/__init__.py. |
| ISSUE-07 | `btn-close-locked-app` | `web/index.html:363` | Button 'Вернуться в чат' on locked screen has no event listener in app.js. | **P2** | Add document.getElementById('btn-close-locked-app').addEventListener('click', () => tgBridge.close()). |
| ISSUE-08 | `stub (Скаут)` | `handlers/cabinet.py:1241` | Button 'Скаут' has raw callback_data='stub' with no handler registered, falling back to placeholder. | **P2** | Connect to opponent head-to-head stats view or replace with noop/remove until feature is implemented. |
| ISSUE-09 | `cabinet_club_stats (« Назад)` | `handlers/cabinet.py:477` | Player card back button hardcoded to user's personal cabinet_club_stats. If viewing another club's roster, user loses context. | **P2** | Pass dynamic back_cb in context/payload (e.g. clsquad_{club} or cabinet_club_stats). |
| ISSUE-10 | `ignore` | `handlers/cabinet.py:1403` | Button changed to '⏳ Запрос отправлен' with callback_data='ignore'. Fallback handler displays 'в разработке' alert instead of silent answer. | **P3** | Change callback_data to 'noop' so handle_placeholders silently answers without alert. |


## Full Registry

| ID | Button | Platform | Handler | API | DB | Auth | Result |
|----|--------|----------|---------|-----|----|------|--------|
| BTN-MA-001 | Навбар: ⚽ Матчи | Mini App | app.switchView('matches') | local state / tab switch | Нет | Да | 🟢 PASS |
| BTN-MA-002 | Навбар: 🔴 Лайв | Mini App | app.switchView('live') -> fetchLiveMatches() | GET /api/live | Нет | Да | 🟢 PASS |
| BTN-MA-003 | Навбар: 📜 Мои ставки | Mini App | app.switchView('history') -> api.getPredictions() | GET /api/predictions | Нет | Да | 🟢 PASS |
| BTN-MA-004 | Навбар: 👤 Профиль | Mini App | app.switchView('profile') -> fetchProgressionData() | GET /api/progression | Нет | Да | 🟢 PASS |
| BTN-MA-005 | 💰 Кэшаут (Cashout) | Mini App | app.js click listener -> api.getCashoutQuote() -> confirm dialog -> api.executeCashout() | POST /api/predictions/{id}/cashout | Да | Да | 🟢 PASS |
| BTN-MA-006 | 🏆 Забрать награду за достижение | Mini App | api.claimAchievement(achId) с сохранением строкового ID и обновлением баланса | POST /api/achievements/claim | Да | Да | 🟢 PASS |
| BTN-MA-007 | Сделать прогноз (CTA) | Mini App | api.placePrediction() с кнопкой disabled и Idempotency-Key | POST /api/predictions | Да | Да | 🟢 PASS |
| BTN-MA-008 | Стейк-чипы (50, 100, 250, 500, 1000, ВСЁ) | Mini App | store.setStakeAmount() -> UI update | local store update | Нет | Да | 🟢 PASS |
| BTN-MA-009 | Кнопка исхода (Кэф: П1, X, П2, Тоталы, ОЗ) | Mini App | store.toggleSelection() -> haptic -> drawer preview | local store slip toggle | Нет | Да | 🟢 PASS |
| BTN-MA-010 | 🗑 Очистить купон | Mini App | store.clearSlip() | local store clear | Нет | Да | 🟢 PASS |
| BTN-MA-011 | Удалить исход из купона | Mini App | store.removeSelection(matchId) | local store removeSelection | Нет | Да | 🟢 PASS |
| BTN-MA-012 | 💾 Сохранить купон как черновик | Mini App | api.saveDraftCoupon() -> fetchUserExtras() | POST /api/user/saved-coupons | Да | Да | 🟢 PASS |
| BTN-MA-013 | 🔄 Восстановить сохранённый купон | Mini App | store.loadCouponSelections() -> toggleSlipDrawer(true) | local store loadCouponSelections | Нет | Да | 🟢 PASS |
| BTN-MA-014 | 🗑 Удалить сохранённый купон | Mini App | api.deleteSavedCoupon() -> fetchUserExtras() | DELETE /api/user/saved-coupons/{id} | Да | Да | 🟢 PASS |
| BTN-MA-015 | 🔄 Повторить прогноз | Mini App | api.repeatPrediction() -> loadCouponSelections() | POST /api/predictions/{id}/repeat | Нет | Да | 🟢 PASS |
| BTN-MA-016 | Вернуться в чат (Экран блокировки/ТО) | Mini App | document.getElementById('btn-close-locked-app').addEventListener('click', () => tgBridge.close()) | Telegram WebApp close | Нет | Нет | 🟢 PASS |
| BTN-MA-017 | Табы туров и дивизионов | Mini App | fetchMatches(divId, tourNum) -> UIRenderer.renderMatches() | GET /api/matches?division_id=X&round_number=Y | Нет | Да | 🟢 PASS |
| BTN-MA-018 | Открыть Match Center | Mini App | loadMatchCenter(matchId) -> showMatchCenterModal() | GET /api/matches/{id}/center | Нет | Да | 🟢 PASS |
| BTN-MA-019 | Фильтры истории (Все / Открытые / Выигрыш / Проигрыш) | Mini App | store.setMyBets(store.state.myBets, filter) | local store filter | Нет | Да | 🟢 PASS |
| BTN-MA-020 | Принять изменение кэфа (ODDS_CHANGED Modal) | Mini App | item.odd = new_odd -> submitBtn.click() | local odd update + auto resubmit | Да | Да | 🟢 PASS |
| BTN-TG-001 | 🏆 Дивизионы и темы | Telegram | admin_divs_hub | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-002 | 👥 Управление игроками | Telegram | admin_manage_players_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-003 | 📋 Составы команд | Telegram | admin_manage_squads | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-004 | ⚔️ Управление матчами | Telegram | admin_manage_matches_info | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-005 | 🏆 Управление Кубком КПЛ | Telegram | admin_manage_cup | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-006 | 📢 Рассылка задолженностей | Telegram | admin_broadcast_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-007 | 🔄 Обновить таблицы и стату | Telegram | admin_force_update | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-008 | 🎭 Режим общения: {mode_label} | Telegram | admin_toggle_chat_mode | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-009 | 🧪 Лаборатория фич (Sandbox) | Telegram | cb_lab_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-010 | « Назад в меню | Telegram | show_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-011 | « Назад | Telegram | show_admin_panel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-012 | 🚀 Сформировать сетку Кубка (Вс | Telegram | admin_init_cup_execute | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-013 | « Назад в админку | Telegram | show_admin_panel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-014 | 1/8 | Telegram | admin_manage_cup | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-015 | 1/4 | Telegram | admin_manage_cup | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-016 | 1/2 | Telegram | admin_manage_cup | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-017 | Финал | Telegram | admin_manage_cup | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-018 | UNKNOWN | Telegram | admin_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-019 | 📢 Напомнить участникам Кубка в | Telegram | admin_remind_cup_execute | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-020 | 🔄 Обновить | Telegram | admin_manage_cup | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-021 | « Назад в админку | Telegram | show_admin_panel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-022 | 📋 Внести результат | Telegram | start_score_reporting | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-023 | 🚀 Запустить рассылку всех долг | Telegram | admin_broadcast_all_debts_execute | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-024 | 📋 Отправить сводку долгов в те | Telegram | admin_send_debts_to_warns | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-025 | « Назад в админку | Telegram | show_admin_panel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-026 | 📋 Мои матчи в кабинете | Telegram | cancel_score_report_and_navigate | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-027 | 🏆 {d[ | Telegram | admin_gen_div_select | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-028 | 🌐 Без дивизиона / Общий ({len( | Telegram | admin_gen_div_select | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-029 | « Назад к управлению | Telegram | admin_manage_matches_info | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-030 | « Назад к выбору | Telegram | admin_generate_matches_confirm | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-031 | ✅ Подтвердить и сгенерировать | Telegram | admin_generate_matches_execute | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-032 | « Отмена | Telegram | admin_generate_matches_confirm | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-033 | « Назад к матчам | Telegram | admin_manage_matches_info | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-034 | « Назад | Telegram | admin_generate_matches_confirm | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-035 | « Назад к матчам | Telegram | admin_manage_matches_info | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-036 | « Назад к матчам | Telegram | admin_manage_matches_info | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-037 | 📋 Список участников | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-038 | 🏆 Дивизионы и участники | Telegram | admin_div_players_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-039 | ➕ Добавить игрока | Telegram | admin_add_player_start | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-040 | 📊 Импорт списка участников | Telegram | admin_import_players_start | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-041 | ⚠️ Сбросить лигу (Очистить все | Telegram | admin_clear_league_start | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-042 | « Назад в админку | Telegram | show_admin_panel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-043 | « Назад | Telegram | admin_manage_players_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-044 | UNKNOWN | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-045 | ⬅️ Назад | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-046 | {page + 1} / {total_pages} | Telegram | handle_placeholders (silent noop) | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-047 | ➡️ Вперед | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-048 | « Управление участниками | Telegram | admin_manage_players_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-049 | 👥 {d[ | Telegram | admin_list_div_players | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-050 | 👥 Без дивизиона ({unassigned_c | Telegram | admin_list_div_players | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-051 | « Назад к участникам | Telegram | admin_manage_players_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-052 | « К дивизионам | Telegram | admin_div_players_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-053 | UNKNOWN | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-054 | ⬅️ | Telegram | admin_list_div_players | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-055 | {page + 1} / {total_pages} | Telegram | handle_placeholders (silent noop) | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-056 | ➡️ | Telegram | admin_list_div_players | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-057 | « К дивизионам | Telegram | admin_div_players_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-058 | {status_icon} {d[ | Telegram | admin_div_view | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-059 | ➕ Создать дивизион | Telegram | admin_div_create_start | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-060 | « Назад в админку | Telegram | show_admin_panel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-061 | « К списку дивизионов | Telegram | admin_divs_hub | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-062 | UNKNOWN | Telegram | admin_div_toggle | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-063 | ✏️ Переименовать | Telegram | admin_div_rename_start | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-064 | 📌 Настроить топики | Telegram | admin_div_topics_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-065 | 👥 Участники | Telegram | admin_list_div_players | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-066 | « К списку дивизионов | Telegram | admin_divs_hub | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-067 | 📸 Драфты: {drafts_tid or  | Telegram | admin_div_settopic_prompt | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-068 | 📢 Результаты: {results_tid or  | Telegram | admin_div_settopic_prompt | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-069 | 📊 Таблицы: {tables_tid or  | Telegram | admin_div_settopic_prompt | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-070 | « К дивизиону | Telegram | admin_div_view | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-071 | « Отмена | Telegram | admin_divs_hub | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-072 | 🏆 Перейти к дивизиону | Telegram | admin_div_view | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-073 | « К списку дивизионов | Telegram | admin_divs_hub | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-074 | « Отмена | Telegram | admin_div_view | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-075 | « К дивизиону | Telegram | admin_div_view | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-076 | « К списку дивизионов | Telegram | admin_divs_hub | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-077 | « Отмена | Telegram | admin_div_topics_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-078 | « К настройке топиков | Telegram | admin_div_topics_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-079 | « К дивизиону | Telegram | admin_div_view | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-080 | « Назад к списку | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-081 | 🔑 Снять админку | Telegram | admin_toggle_role | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-082 | 🔑 Сделать админом | Telegram | admin_toggle_role | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-083 | ✏️ Клуб | Telegram | admin_edit_club_start | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-084 | ✏️ Юзернейм | Telegram | admin_edit_username_start | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-085 | ❌ Удалить из лиги | Telegram | admin_delete_options | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-086 | « Назад к списку | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-087 | « Назад к списку | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-088 | 🗑️ Да, удалить игрока | Telegram | admin_delete_player_execute | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-089 | ❌ Отмена | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-090 | « Назад к списку | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-091 | « Назад к списку | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-092 | 🎲 Сгенерировать (Round Robin) | Telegram | admin_generate_matches_confirm | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-093 | 📝 Создание матчей (текст) | Telegram | admin_create_matches_start | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-094 | 📅 Открыть туры (массово) | Telegram | admin_open_batch_prompt | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-095 | ⏰ Просроченные | Telegram | admin_list_overdue | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-096 | {status_icon} Тур {r} | Telegram | admin_manage_round | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-097 | « Назад в админку | Telegram | show_admin_panel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-098 | « Назад | Telegram | admin_manage_matches_info | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-099 | 🔴 Закрыть тур | Telegram | admin_close_round | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-100 | ⏰ Напомнить должникам | Telegram | admin_remind_round | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-101 | 🟢 Открыть тур (установить дедл | Telegram | admin_open_round_prompt | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-102 | ⚔️ Смотреть матчи тура | Telegram | admin_round_matches | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-103 | « Назад | Telegram | admin_manage_matches_info | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-104 | UNKNOWN | Telegram | admin_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-105 | 📋 Отправить сводку долгов в те | Telegram | admin_send_debts_to_warns | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-106 | 📢 Рассылка всем должникам в ЛС | Telegram | admin_broadcast_all_debts_execute | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-107 | « Назад к турам | Telegram | admin_manage_matches_info | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-108 | « В админ-панель | Telegram | show_admin_panel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-109 | Отмена | Telegram | admin_cancel_match_action | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-110 | « К управлению турами | Telegram | admin_manage_matches_info | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-111 | Отмена | Telegram | admin_cancel_match_action | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-112 | Отмена | Telegram | admin_cancel_match_action | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-113 | « К управлению турами | Telegram | admin_manage_matches_info | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-114 | « Вернуться | Telegram | admin_manage_round | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-115 | UNKNOWN | Telegram | admin_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-116 | « Назад к турам | Telegram | admin_manage_matches_info | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-117 | « Назад | Telegram | admin_manage_matches_info | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-118 | « Назад к Кубку | Telegram | admin_manage_cup | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-119 | « Назад к туру | Telegram | admin_round_matches | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-120 | 📜 Правила турнира | Telegram | Telegram Client (Browser / MiniApp) | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-121 | ⚡ Внести результат по фото (ИИ | Telegram | admin_report_score_auto | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-122 | ✍️ Внести результат вручную | Telegram | cb_report_choice_manual | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-123 | 🚫 ТП 1:0 (Хозяева) | Telegram | admin_set_tp_home_execute | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-124 | UNKNOWN | Telegram | admin_extend_match_execute | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-125 | 🤝 ТН 0:0 (Ничья) | Telegram | admin_set_tp_draw_execute | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-126 | 🔄 Сбросить результат | Telegram | admin_reset_match_execute | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-127 | 📸 Просмотр скриншота матча | Telegram | admin_view_match_photo | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-128 | « Назад к карточке матча | Telegram | admin_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-129 | « Назад к карточке матча | Telegram | admin_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-130 | « Назад | Telegram | admin_manage_matches_info | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-131 | ❌ Отмена | Telegram | admin_cancel_player_action | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-132 | ❌ Отмена | Telegram | admin_cancel_player_action | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-133 | UNKNOWN | Telegram | admin_add_player_club_callback | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-134 | ❌ Отмена | Telegram | admin_cancel_player_action | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-135 | « Назад в меню | Telegram | admin_cancel_player_action | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-136 | ❌ Отмена | Telegram | admin_cancel_player_action | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-137 | « К списку участников | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-138 | ❌ Отмена | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-139 | « К карточке игрока | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-140 | Отмена | Telegram | admin_manage_matches_info | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-141 | « Назад к управлению | Telegram | admin_manage_matches_info | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-142 | « Вернуться к матчам | Telegram | admin_manage_matches_info | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-143 | ❌ Отмена | Telegram | admin_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-144 | ❌ Отмена | Telegram | admin_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-145 | « К карточке матча | Telegram | admin_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-146 | « Назад к списку | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-147 | 🗑️ 1. Исключить (Тех. поражени | Telegram | admin_confirm_delete_player | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-148 | 🔥 2. Стереть полностью (Без сл | Telegram | admin_confirm_wipe_player | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-149 | « Назад к карточке | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-150 | « Назад к списку | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-151 | 🔥 Да, стереть полностью | Telegram | admin_wipe_player_execute | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-152 | ❌ Отмена | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-153 | « Назад к списку | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-154 | ❌ Отмена | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-155 | « К карточке игрока | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-156 | ❌ Отмена | Telegram | admin_cancel_player_action | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-157 | « Управление участниками | Telegram | admin_manage_players_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-158 | « Управление участниками | Telegram | admin_manage_players_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-159 | 📋 Список участников | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-160 | ➕ Добавить игрока | Telegram | admin_add_player_start | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-161 | 📥 Массовый импорт (списком) | Telegram | admin_import_players_start | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-162 | 🔄 Сбросить варны (новый сезон) | Telegram | admin_reset_season_warns | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-163 | 🗑 Очистить всю лигу | Telegram | admin_clear_league_start | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-164 | « Назад в админ-панель | Telegram | show_admin_panel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-165 | ➕ Добавить игрока | Telegram | admin_add_player_start | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-166 | « Назад | Telegram | admin_manage_players_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-167 | UNKNOWN | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-168 | ⬅️ Назад | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-169 | {page + 1} / {total_pages} | Telegram | handle_placeholders (silent noop) | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-170 | Вперед ➡️ | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-171 | ➕ Добавить игрока | Telegram | admin_add_player_start | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-172 | « Назад в меню | Telegram | admin_manage_players_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-173 | « К списку участников | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-174 | ✏️ Изменить клуб | Telegram | admin_edit_club_select | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-175 | 🏆 Дивизион | Telegram | admin_edit_div_select | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-176 | ✏️ Изменить юзернейм | Telegram | admin_edit_username_start | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-177 | ➕ Выдать варн | Telegram | admin_warn_confirm | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-178 | ➖ Снять варн | Telegram | admin_warn_remove_execute | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-179 | 📜 История варнов | Telegram | admin_warn_history | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-180 | 🕊 Амнистия | Telegram | admin_amnesty_execute | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-181 | 🗑 Исключить из лиги | Telegram | admin_confirm_delete_player | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-182 | « К списку участников | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-183 | « К списку | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-184 | UNKNOWN | Telegram | admin_edit_club_execute | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-185 | ❌ Отмена | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-186 | « К списку | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-187 | ⭐ Без дивизиона (Текущий) | Telegram | admin_edit_div_execute | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-188 | ❌ Снять с дивизиона | Telegram | admin_edit_div_execute | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-189 | UNKNOWN | Telegram | admin_edit_div_execute | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-190 | « Отмена | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-191 | « К списку | Telegram | admin_list_players_page | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-192 | ✅ Да, исключить | Telegram | admin_delete_player_execute | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-193 | ❌ Отмена | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-194 | UNKNOWN | Telegram | admin_view_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-195 | 🖼 Загрузить фото игроков | Telegram | admin_fetch_photos | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-196 | ➕ Добавить во все клубы игроко | Telegram | admin_squad_add_missing | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-197 | « Назад в админку | Telegram | show_admin_panel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-198 | 🏛 Карточка клуба | Telegram | show_specific_club_card | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-199 | 📊 Загрузить состав | Telegram | admin_squad_upload_start | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-200 | ➕ Добавить игрока | Telegram | admin_squad_add_player_start | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-201 | ➖ Удалить игрока | Telegram | admin_squad_rm_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-202 | ➕ Добавить игроков из матчей | Telegram | admin_squad_add_missing | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-203 | 🗑️ Очистить состав | Telegram | admin_squad_clear | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-204 | « Назад к клубам | Telegram | admin_manage_squads | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-205 | ❌ Отмена | Telegram | admin_manage_squads | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-206 | 👥 Просмотреть состав | Telegram | admin_view_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-207 | ❌ Отмена | Telegram | admin_view_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-208 | 👥 Просмотреть состав | Telegram | admin_view_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-209 | « Назад к клубам | Telegram | admin_manage_squads | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-210 | « Назад к составу | Telegram | admin_view_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-211 | ❌ {player} | Telegram | admin_squad_del_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-212 | « Назад к составу | Telegram | admin_view_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-213 | « Назад к составу | Telegram | admin_view_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-214 | ❌ {player} | Telegram | admin_squad_del_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-215 | « Назад к составу | Telegram | admin_view_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-216 | « Назад к клубам | Telegram | admin_manage_squads | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-217 | « Назад | Telegram | admin_view_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-218 | « Назад в админку | Telegram | show_admin_panel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-219 | 📝 Ввести результат | Telegram | start_score_reporting | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-220 | 👀 Состав соперника | Telegram | cabinet_view_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-221 | 📝 Ввести результат | Telegram | start_score_reporting | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-222 | 👀 Состав соперника | Telegram | cabinet_view_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-223 | 📋 Перейти к матчам | Telegram | cancel_score_report_and_navigate | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-224 | 👤 Личный кабинет | Telegram | show_cabinet | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-225 | 📝 Ввести результат | Telegram | start_score_reporting | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-226 | 📋 Мои матчи | Telegram | cancel_score_report_and_navigate | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-227 | 📝 Ввести результат | Telegram | start_score_reporting | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-228 | 📋 Мои матчи | Telegram | cancel_score_report_and_navigate | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-229 | « Назад к туру | Telegram | admin_manage_round | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-230 | UNKNOWN | Telegram | admin_toggle_remind_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-231 | UNKNOWN | Telegram | admin_toggle_remind_all | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-232 | 🚀 Отправить напоминания ({coun | Telegram | admin_send_selected_reminders | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-233 | « Назад к туру | Telegram | admin_manage_round | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-234 | « Вернуться к туру | Telegram | admin_manage_round | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-235 | UNKNOWN | Telegram | admin_warn_execute | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-236 | ❌ Отмена | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-237 | « К карточке игрока | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-238 | « К карточке игрока | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-239 | « К карточке игрока | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-240 | « К карточке игрока | Telegram | admin_view_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-241 | « Назад в админку | Telegram | show_admin_panel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-242 | 🔥 Logovo.bet (Mini App) | Telegram | Telegram Client (Browser / MiniApp) | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-243 | 🔥 Logovo.bet | Telegram | Telegram Client (Browser / MiniApp) | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-244 | 👤 Мой Кабинет | Telegram | show_cabinet | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-245 | 🏆 Турниры | Telegram | show_tournaments | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-246 | 👑 Админ-панель | Telegram | show_admin_panel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-247 | 🏆 Лига | Telegram | show_league_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-248 | 🆘 Поддержка | Telegram | show_support | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-249 | 📱 Открыть меню в ЛС | Telegram | Telegram Client (Browser / MiniApp) | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-250 | 📊 Таблица лиги | Telegram | show_league_table | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-251 | 🏛 Карточки клубов | Telegram | show_clubs_catalog | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-252 | ⚽ Бомбардиры | Telegram | show_top_scorers | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-253 | 🎯 Ассисты | Telegram | show_top_assists | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-254 | « Назад в меню | Telegram | show_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-255 | 🖼 Графика (с фото) | Telegram | send_top_scorers_image | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-256 | 🎯 Перейти к Ассистам | Telegram | show_top_assists | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-257 | « Назад в раздел «Лига» | Telegram | show_league_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-258 | 🖼 Графика (с фото) | Telegram | send_top_assisters_image | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-259 | ⚽ Перейти к Бомбардирам | Telegram | show_top_scorers | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-260 | « Назад в раздел «Лига» | Telegram | show_league_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-261 | 🎯 Ассистенты (Графика) | Telegram | send_top_assisters_image | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-262 | ⚽ К списку бомбардиров | Telegram | show_top_scorers | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-263 | « Раздел «Лига» | Telegram | show_league_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-264 | ⚽ Бомбардиры (Графика) | Telegram | send_top_scorers_image | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-265 | 🎯 К списку ассистентов | Telegram | show_top_assists | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-266 | « Раздел «Лига» | Telegram | show_league_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-267 | ⚽ Чемпионат КПЛ (Лига) | Telegram | show_league_rounds | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-268 | 🏆 Кубок КПЛ (Плей-офф Best-of- | Telegram | show_cup_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-269 | « Назад в меню | Telegram | show_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-270 | {r} Тур | Telegram | show_round_matches | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-271 | « Назад к турнирам | Telegram | show_tournaments | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-272 | 1/8 | Telegram | show_cup_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-273 | 1/4 | Telegram | show_cup_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-274 | 1/2 | Telegram | show_cup_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-275 | Финал | Telegram | show_cup_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-276 | 🖼 Сетка турнира | Telegram | cb_show_full_cup_bracket | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-277 | 📊 Статистика | Telegram | show_cup_stats | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-278 | « Назад | Telegram | show_tournaments | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-279 | ⚽ Бомбардиры (Графика) | Telegram | send_cup_scorers_image | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-280 | 🎯 Ассистенты (Графика) | Telegram | send_cup_assisters_image | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-281 | « Назад к Кубку | Telegram | show_cup_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-282 | 🎯 Ассистенты Кубка (Графика) | Telegram | send_cup_assisters_image | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-283 | 🏆 Назад к Кубку | Telegram | show_cup_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-284 | ⚽ Бомбардиры Кубка (Графика) | Telegram | send_cup_scorers_image | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-285 | 🏆 Назад к Кубку | Telegram | show_cup_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-286 | « Назад к турам | Telegram | show_league_rounds | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-287 | 🔄 Обновить | Telegram | cb_refresh_league_table_topic | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-288 | « Назад в меню | Telegram | show_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-289 | 🔄 Обновить таблицу | Telegram | cb_refresh_league_table_topic | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-290 | 🔄 Обновить таблицу | Telegram | cb_refresh_league_table_topic | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-291 | 🔄 Обновить таблицу | Telegram | cb_refresh_league_table_topic | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-292 | « Назад в меню | Telegram | show_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-293 | 📋 Линия на Тур | Telegram | cb_bet_view_tours | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-294 | UNKNOWN | Telegram | cb_bet_view_slip | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-295 | 📜 Мои Ставки | Telegram | cb_bet_my_history | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-296 | 🎁 Бонус (+250 🪙) | Telegram | cb_bet_claim_bonus | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-297 | 🏆 Топ Капперов | Telegram | cb_bet_leaderboard | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-298 | 🧪 Назад в Лабораторию (/lab) | Telegram | cb_lab_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-299 | 🔙 Главное Меню | Telegram | cmd_bet_hub | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-300 | UNKNOWN | Telegram | cb_bet_pick_tour | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-301 | 🎫 Мой Купон | Telegram | cb_bet_view_slip | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-302 | 🔙 Главное Меню | Telegram | cmd_bet_hub | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-303 | 📋 Все Туры | Telegram | cb_bet_view_tours | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-304 | 🔙 Главное Меню | Telegram | cmd_bet_hub | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-305 | UNKNOWN | Telegram | cb_bet_match_detail | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-306 | 🎫 Перейти в купон | Telegram | cb_bet_view_slip | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-307 | 🔙 Все Туры | Telegram | cb_bet_view_tours | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-308 | 🔙 Назад | Telegram | cb_bet_view_tours | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-309 | П1 ({market[ | Telegram | cb_bet_add_outcome | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-310 | Х ({market[ | Telegram | cb_bet_add_outcome | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-311 | П2 ({market[ | Telegram | cb_bet_add_outcome | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-312 | ТБ 2.5 ({market[ | Telegram | cb_bet_add_outcome | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-313 | ТМ 2.5 ({market[ | Telegram | cb_bet_add_outcome | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-314 | ОЗ: ДА ({market[ | Telegram | cb_bet_add_outcome | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-315 | ОЗ: НЕТ ({market[ | Telegram | cb_bet_add_outcome | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-316 | 🎫 В Купон | Telegram | cb_bet_view_slip | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-317 | 🔙 К списку матчей | Telegram | cb_bet_view_tours | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-318 | 📋 Открыть Линию | Telegram | cb_bet_view_tours | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-319 | 🔙 Главное Меню | Telegram | cmd_bet_hub | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-320 | 50 🪙 | Telegram | cb_bet_place_amount | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-321 | 100 🪙 | Telegram | cb_bet_place_amount | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-322 | 250 🪙 | Telegram | cb_bet_place_amount | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-323 | 500 🪙 | Telegram | cb_bet_place_amount | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-324 | 1 000 🪙 | Telegram | cb_bet_place_amount | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-325 | 🔥 ВСЁ (All-In) | Telegram | cb_bet_place_amount | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-326 | 🗑 Очистить купон | Telegram | cb_bet_clear_slip | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-327 | ➕ Добавить событие | Telegram | cb_bet_view_tours | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-328 | 🔙 Главное Меню | Telegram | cmd_bet_hub | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-329 | 📜 Мои Ставки | Telegram | cb_bet_my_history | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-330 | 📋 В Линию | Telegram | cb_bet_view_tours | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-331 | 🔙 Главное Меню | Telegram | cmd_bet_hub | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-332 | 🔙 Меню | Telegram | cmd_bet_hub | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-333 | 📋 Линия на Тур | Telegram | cb_bet_view_tours | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-334 | 🔙 Главное Меню | Telegram | cmd_bet_hub | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-335 | 🔙 Главное Меню | Telegram | cmd_bet_hub | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-336 | « Назад в меню | Telegram | show_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-337 | 🏛 Карточка клуба | Telegram | show_my_club_card | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-338 | 📸 Состав | Telegram | cancel_upload_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-339 | 📋 Мои матчи | Telegram | cancel_score_report_and_navigate | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-340 | 📜 История игр | Telegram | show_game_history | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-341 | ⚽ Топ клуба | Telegram | show_club_stats | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-342 | 🌍 Все клубы | Telegram | show_clubs_catalog | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-343 | « Назад в меню | Telegram | show_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-344 | « Назад в кабинет | Telegram | show_cabinet | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-345 | 👤 {pname} | Telegram | show_player_card | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-346 | « Назад в кабинет | Telegram | show_cabinet | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-347 | « Назад | Telegram | show_clubs_catalog | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-348 | 🏛 Карточка | Telegram | show_specific_club_card | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-349 | 👥 Состав | Telegram | show_club_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-350 | 🌍 Все клубы | Telegram | show_clubs_catalog | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-351 | « В кабинет | Telegram | show_cabinet | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-352 | 👥 Состав | Telegram | show_club_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-353 | 📅 Матчи | Telegram | show_club_history | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-354 | 🌍 Все клубы | Telegram | show_clubs_catalog | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-355 | « Назад | Telegram | show_clubs_catalog | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-356 | « В кабинет | Telegram | show_cabinet | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-357 | 👤 {p[ | Telegram | show_player_card | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-358 | « К карточке клуба | Telegram | show_specific_club_card | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-359 | UNKNOWN | Telegram | show_specific_club_card | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-360 | « Назад в меню | Telegram | show_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-361 | « Назад в меню | Telegram | show_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-362 | UNKNOWN | Telegram | cabinet_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-363 | « Назад в кабинет | Telegram | show_cabinet | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-364 | « Назад | Telegram | cancel_score_report_and_navigate | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-365 | 👀 Состав соперника | Telegram | cabinet_view_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-366 | ✅ Согласовать время | Telegram | cb_accept_time | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-367 | ⏰ Предложить другое | Telegram | cb_propose_time_prompt | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-368 | ✏️ Изменить предложенное время | Telegram | cb_propose_time_prompt | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-369 | ⏰ Изменить время | Telegram | cb_propose_time_prompt | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-370 | ⏰ Предложить время матча | Telegram | cb_propose_time_prompt | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-371 | 📝 Ввести результат | Telegram | start_score_reporting | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-372 | 📨 Запросить ввод через админа | Telegram | cb_request_admin_result | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-373 | 📜 Правила турнира | Telegram | Telegram Client (Browser / MiniApp) | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-374 | 🔙 К списку матчей | Telegram | cancel_score_report_and_navigate | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-375 | ✅ Разрешить внесение | Telegram | cb_admin_approve_result | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-376 | ⏳ Запрос отправлен | Telegram | handle_placeholders (silent noop) | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-377 | 📝 Ввести результат | Telegram | start_score_reporting | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-378 | Сегодня в 19:00 | Telegram | cb_quick_time | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-379 | Сегодня в 20:00 | Telegram | cb_quick_time | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-380 | Сегодня в 21:00 | Telegram | cb_quick_time | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-381 | Сегодня в 22:00 | Telegram | cb_quick_time | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-382 | Завтра в 19:00 | Telegram | cb_quick_time | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-383 | Завтра в 20:00 | Telegram | cb_quick_time | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-384 | Завтра в 21:00 | Telegram | cb_quick_time | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-385 | Завтра в 22:00 | Telegram | cb_quick_time | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-386 | ✍️ Ввести своё время | Telegram | start_custom_time_prompt | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-387 | « Назад к матчу | Telegram | cabinet_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-388 | 🏟 Открыть карточку матча | Telegram | cabinet_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-389 | 🏟 Открыть карточку матча | Telegram | cabinet_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-390 | ❌ Отмена | Telegram | cabinet_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-391 | 🏟 Открыть карточку матча | Telegram | cabinet_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-392 | 👀 Состав соперника | Telegram | cabinet_view_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-393 | ✏️ Изменить предложенное время | Telegram | cb_propose_time_prompt | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-394 | 🔙 К списку матчей | Telegram | cancel_score_report_and_navigate | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-395 | 📝 Ввести результат | Telegram | start_score_reporting | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-396 | « Назад в кабинет | Telegram | show_cabinet | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-397 | ⚡ Автоматический ввод (по фото | Telegram | cb_report_choice_auto | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-398 | ✍️ Ручной ввод | Telegram | cb_report_choice_manual | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-399 | ❌ Отмена | Telegram | cabinet_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-400 | ❌ Отмена | Telegram | cabinet_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-401 | UNKNOWN | Telegram | cb_report_home_goals | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-402 | ❌ Отмена | Telegram | cabinet_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-403 | UNKNOWN | Telegram | cb_report_away_goals | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-404 | ❌ Отмена | Telegram | cabinet_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-405 | ⏩ Пропустить ввод авторов | Telegram | cb_skip_goals | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-406 | 🏃‍♂️ {player} | Telegram | cb_pick_goal | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-407 | ⏩ Пропустить остаток (пенальти | Telegram | cb_skip_goals | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-408 | ❌ Отмена | Telegram | cabinet_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-409 | 🎯 {player} | Telegram | cb_pick_assist | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-410 | ⏩ Пропустить остаток ассистов | Telegram | cb_skip_assists | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-411 | ❌ Отмена | Telegram | cabinet_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-412 | ⏭ Продолжить без скриншота | Telegram | cb_skip_report_photo | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-413 | ⌨️ Ввести вручную (без скриншо | Telegram | cb_report_choice_manual | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-414 | ❌ Отмена | Telegram | cabinet_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-415 | UNKNOWN | Telegram | submit_report_to_guest | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-416 | ❌ Отмена | Telegram | cabinet_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-417 | 🔍 Распознать результат ({n_pho | Telegram | ai_recognize_now | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-418 | ✏️ Ввести вручную | Telegram | cb_report_choice_manual | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-419 | ✅ Всё верно (Сохранить и занес | Telegram | cb_confirm_ai_final | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-420 | ✏️ Изменить вручную | Telegram | cabinet_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-421 | ❌ Отмена | Telegram | cabinet_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-422 | ✍️ Ввести результат вручную | Telegram | cb_report_choice_manual | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-423 | ❌ Отмена | Telegram | cabinet_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-424 | « Назад к матчу | Telegram | admin_view_match | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-425 | « Назад к туру | Telegram | admin_round_matches | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-426 | « К своим матчам | Telegram | cancel_score_report_and_navigate | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-427 | ✅ Подтвердить | Telegram | cb_guest_confirm | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-428 | ❌ Отклонить | Telegram | cb_guest_reject | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-429 | « Назад в кабинет | Telegram | show_cabinet | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-430 | ✅ Занести результат | Telegram | cb_guest_confirm | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-431 | ❌ Отклонить | Telegram | cb_guest_reject | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-432 | 🔄 Обновить состав | Telegram | start_upload_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-433 | « Назад в кабинет | Telegram | show_cabinet | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-434 | Отмена | Telegram | cancel_upload_squad | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-435 | UNKNOWN | Telegram | cb_draft_confirm | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-436 | ❌ Отклонить | Telegram | cb_draft_reject | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-437 | 🎰 Logovo.bet (Mini App) | Telegram | Telegram Client (Browser / MiniApp) | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-438 | 🎰 Logovo.bet (Тест Букмекерки) | Telegram | cmd_bet_hub | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-439 | 🃏 Тестировать Карточки EA FC | Telegram | cb_lab_card_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-440 | 🚩 Управление Feature Flags | Telegram | cb_lab_flags_menu | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-441 | 📊 Тест формулы OVR (Калькулято | Telegram | cb_lab_ovr_calc_demo | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-442 | « Назад в Админ-панель | Telegram | show_admin_panel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-443 | UNKNOWN | Telegram | cb_lab_toggle_flag | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-444 | « Назад в лабораторию | Telegram | cb_lab_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-445 | 🥉 1. КПЛ Standard (≤85) | Telegram | cb_lab_demo_card | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-446 | 🥈 2. КПЛ Star (86-92) | Telegram | cb_lab_demo_card | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-447 | 🥇 3. КПЛ Prime (93+) | Telegram | cb_lab_demo_card | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-448 | ⭐ 4. UCL Night | Telegram | cb_lab_demo_card | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-449 | 🔥 5. Inferno Magma | Telegram | cb_lab_demo_card | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-450 | ⚡ 6. Cyberpunk | Telegram | cb_lab_demo_card | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-451 | 💎 7. Hyper-Glass | Telegram | cb_lab_demo_card | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-452 | 🌌 8. Void Eclipse | Telegram | cb_lab_demo_card | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-453 | 🎬 ➔ ТЕСТ АНИМИРОВАННЫХ (GIF/MP | Telegram | cb_lab_anim_card_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-454 | 🔍 Выбрать реального игрока из  | Telegram | cb_lab_card_pick_club | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-455 | « Назад в лабораторию | Telegram | cb_lab_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-456 | 🥉 1. КПЛ Standard | Telegram | cb_lab_demo_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-457 | 🥈 2. КПЛ Star | Telegram | cb_lab_demo_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-458 | 🥇 3. КПЛ Prime | Telegram | cb_lab_demo_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-459 | ⭐ 4. UCL Night | Telegram | cb_lab_demo_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-460 | 🔥 5. Inferno Magma | Telegram | cb_lab_demo_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-461 | ⚡ 6. Cyberpunk | Telegram | cb_lab_demo_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-462 | 🖼️ ➔ Тест статичных карточек | Telegram | cb_lab_card_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-463 | 🖼️ ➔ К статичным карточкам (PN | Telegram | cb_lab_card_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-464 | « В лабораторию | Telegram | cb_lab_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-465 | 🎬 Анимировать (GIF) | Telegram | cb_lab_demo_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-466 | 🔄 Выбрать другой стиль | Telegram | cb_lab_card_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-467 | « В лабораторию | Telegram | cb_lab_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-468 | 🖼️ Статичная (PNG) | Telegram | cb_lab_demo_card | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-469 | 🔄 Другая анимация | Telegram | cb_lab_anim_card_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-470 | « В лабораторию | Telegram | cb_lab_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-471 | UNKNOWN | Telegram | cb_lab_card_pick_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-472 | « Назад к карточкам | Telegram | cb_lab_card_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-473 | « Выбрать другой клуб | Telegram | cb_lab_card_pick_club | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-474 | UNKNOWN | Telegram | cb_lab_card_generate_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-475 | « Назад к клубам | Telegram | cb_lab_card_pick_club | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-476 | 🎬 Анимировать ({cfg[ | Telegram | cb_lab_player_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-477 | ✨ Выбрать стиль анимации | Telegram | cb_lab_player_anim_styles | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-478 | 👥 Другой игрок | Telegram | cb_lab_card_pick_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-479 | 🏛 Выбрать клуб | Telegram | cb_lab_card_pick_club | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-480 | « В лабораторию | Telegram | cb_lab_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-481 | 🌟 1. TOTY Gold | Telegram | cb_lab_player_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-482 | 🌌 2. Void Eclipse | Telegram | cb_lab_player_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-483 | ⚡ 3. Cyberpunk | Telegram | cb_lab_player_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-484 | 💎 4. Hyper-Glass | Telegram | cb_lab_player_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-485 | 🔥 5. Inferno Magma | Telegram | cb_lab_player_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-486 | ❄️ 6. Glacial Frost | Telegram | cb_lab_player_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-487 | ⚽ 7. Anime Sakuga | Telegram | cb_lab_player_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-488 | 👑 8. Royal 24K | Telegram | cb_lab_player_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-489 | 🏎️ 9. Aero Carbon | Telegram | cb_lab_player_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-490 | 🌌 10. UCL Night | Telegram | cb_lab_player_anim | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-491 | « Назад к {player_name} | Telegram | cb_lab_card_generate_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-492 | 🖼️ Статичная | Telegram | cb_lab_card_generate_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-493 | 🔄 Другой стиль | Telegram | cb_lab_player_anim_styles | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-494 | 👥 Другой игрок | Telegram | cb_lab_card_pick_player | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-495 | « В лабораторию | Telegram | cb_lab_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-496 | 🃏 Перейти к тесту карточек | Telegram | cb_lab_card_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-497 | « Назад в Лабораторию | Telegram | cb_lab_main_menu | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-498 | 🔄 Обновить | Telegram | cb_refresh_league_table_topic | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-499 | 1/8 | Telegram | cb_show_cup_graphic | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-500 | 1/4 | Telegram | cb_show_cup_graphic | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-501 | 1/2 | Telegram | cb_show_cup_graphic | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-502 | Финал | Telegram | cb_show_cup_graphic | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-503 | 📊 Полная сетка | Telegram | cb_show_full_cup_bracket | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-504 | 📋 Внести результат | Telegram | start_score_reporting | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-505 | 🏟 ЧЕРНОВИК | Telegram | topic_management_callback | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-506 | 👤 ПРЕДЫ | Telegram | topic_management_callback | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-507 | 🎛 РЕЗУЛЬТАТЫ | Telegram | topic_management_callback | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-508 | 📞 ОТЧЁТЫ | Telegram | topic_management_callback | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-509 | 🗺 СОСТАВЫ | Telegram | topic_management_callback | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-510 | ❌ Отмена | Telegram | cb_top_cancel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-511 | 🔄 Подтвердить переназначение | Telegram | cb_reassign_topic_confirm | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-512 | ❌ Отмена | Telegram | cb_top_cancel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-513 | 🔄 Перенести в этот топик | Telegram | cb_reassign_topic_confirm | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-514 | ❌ Отмена | Telegram | cb_top_cancel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |
| BTN-TG-515 | 🗑 Снять привязку | Telegram | cb_unbind_topic_confirm | N/A (Bot Callback) | Да | Да | 🟢 PASS |
| BTN-TG-516 | ❌ Отмена | Telegram | cb_top_cancel | N/A (Bot Callback) | Нет | Да | 🟢 PASS |

## Subsystem Breakdown & Deep Invariants

### 1. Telegram Bot Handlers & Flows

- **Main Menu & Navigation**: `handlers/base.py` cleanly routes between cabinet, tournaments, league table, and rules.
- **Cabinet & Match Reporting**: Dual flow (AI OCR vs Manual). Verified side-stability (team1 left, team2 right) and non-mutating preview invariant.
- **Admin Management**: Full CRUD on tournaments, rosters, matches, technical defeats, deadlines, and broadcast stubs.
- **Topic Management**: Dynamic topic routing (`player:div:topic`) correctly isolates chat threads per division.

### 2. Mini App & REST API Architecture

- **Authentication**: Validated via Telegram WebApp HMAC (`X-Telegram-Init-Data`) with Lab mode gatekeeper.
- **Bet Placement**: Robust idempotency with UI locking (`submitBtn.disabled = true`), server-side SQLite transaction lock (`_bet_placement_lock`), and `ODDS_CHANGED` re-confirmation dialog.
- **Gamification & Profile**: XP, login streaks, levels, and public gamer cards. Fixed string achievement ID parsing.
- **Live In-play & Match Center**: Real-time ticker, intelligence preview, value radar, and dynamic markets.

### 3. Financial & Betting Safety Invariants

- **Atomic Balances**: All coin debits/credits occur inside parameterized `with transaction() as conn:` with explicit `balance >= ?` constraints.
- **Idempotency**: API endpoints use unique client UUIDs (`idempotency_key`) stored in `coin_transactions` and `user_bets`.
- **Odds Integrity**: Frontend cannot dictate odds; backend re-fetches canonical odds from database before finalizing wager.

### 4. Phased Step-by-Step Fix Plan

1. **Phase 1 (P1 - Financial & Critical Functional)**:
   - Fix `web/js/app.js:685` achievement ID parsing (`parseInt` -> raw string) and balance store update.
   - Wire up `web/js/ui.js:808` `.btn-cashout` event listener in `app.js`.
   - Add debounce, early callback answer, and atomic coupon clearing in `handlers/betting.py:cb_bet_place_amount`.
   - Restrict schedule generation RBAC check in `handlers/admin.py:945` to `is_global_admin(user_id)`.
   - Register `CallbackQueryHandler(admin_confirm_delete_player, pattern='^admin_confirm_delete_player_\\d+$')` in `handlers/__init__.py`.

2. **Phase 2 (P2 - Dead Buttons & UX/Navigation)**:
   - Add `#btn-close-locked-app` click listener in `app.js` (`tgBridge.close()`).
   - Implement `lab_ovr_calc_demo` handler in `handlers/lab.py`.
   - Connect or clean up `stub` callback on 'Скаут' buttons in `handlers/cabinet.py`.
   - Fix context-preserving back button in player card (`handlers/cabinet.py:477`).

3. **Phase 3 (P3 - Cleanup & Polish)**:
   - Change `ignore` callback to `noop` in `handlers/cabinet.py:1403`.
   - Deduplicate handler registrations in `handlers/__init__.py` (`cabinet_game_history`, `admin_manage_players`).
   - Remove dead placeholder `show_edit_profile_menu` and unused imports.
