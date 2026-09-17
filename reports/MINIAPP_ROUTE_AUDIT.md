# Аудит маршрутов Telegram Mini App — Logovo.bet

**Дата:** 2026-09-16 · **Ветка:** `claude/telegram-mini-app-audit-a371e6`
**Объём:** 105 серверных маршрутов `/api/*`, 6 экранов + 6 модалок клиента, 62 обёртки `ApiClient`.

Все статусы в отчёте — **реальные HTTP-ответы**, снятые с поднятого `create_app()`
(`tests/test_api_route_matrix.py` + одноразовые дамп-скрипты), а не выводы из чтения кода.

---

## Сводка

| Проверка | Результат |
|---|---|
| Fail-closed без `initData` | **94 / 105** маршрутов отдают `401`. 10 публичны by design, 1 отдаёт `403` вместо `401`. |
| Fail-closed с подделанной HMAC-подписью | Идентично отсутствию `initData` — расхождений нет. |
| Подпись чужим токеном | `401` — отвергается. |
| RBAC `/api/admin/*` для игрока | **25 / 25** маршрутов отдают `403`. Нарушений нет. |
| RBAC `/api/admin/*` для глобального админа | `200` (GET) / `400` на пустом теле (POST, PUT) — доступ работает. |
| Изоляция пользователей (IDOR) | Чужой купон не читается, не обналичивается, не повторяется; чужой профиль отдаёт только публичные поля. **Нарушений нет.** |
| Битый JSON в теле | Ни одного `5xx` — все `400`/`403`/`404`. |
| Парность фронт → бэк | **0** вызовов в несуществующие эндпоинты. |
| Парность бэк → фронт | 43 маршрута без обёртки + 28 обёрток без вызовов ≈ **62 из 105 маршрутов недостижимы из UI**. |
| Устойчивость UI к 4xx/5xx | Белого экрана нет; но ошибки с не-JSON телом теряют `status`/`code` (Д-6). |

**Критических дыр в авторизации не найдено.** Основные риски — публичные
`/api/live/*`, `/api/odds/movers`, `/api/matches/hot`, `/api/matches/{id}/photo`,
`/api/leaderboard/division/{division_id}` и пять расхождений контракта, из которых
два полностью ломают функциональность (кэшаут и таблица лидеров).

---

## Этап 1. Инвентаризация

### 1.1 Клиент (`web/`)

Роутера нет — переключение через `data-view` и `switchView()` в `web/js/app.js`.
Hash/URL-параметры не читаются нигде, deep-link в конкретный экран невозможен.

| Экран | id секции | Под-вкладки | Данные |
|---|---|---|---|
| Лобби | `view-lobby` (index.html:126) | вкладки дивизионов | `/api/bootstrap`, `/api/divisions`, `/api/markets/tours`, `/api/matches/hot`, `/api/odds/movers`, `/api/recommendations` |
| Матч-центр | `view-match_center` (:159) | — | `/api/matches/{id}` + `/stats`, `/h2h`, `/insights`, `/live`, `/markets`, `/api/intelligence/matches/{id}/preview` |
| Турниры | `view-tournaments` (:174) | Таблица / Результаты / Бомбардиры (:184–186) | `/api/standings`, `/api/results`, `/api/tournaments/{id}/top-scorers` |
| История | `view-history` (:197) | — | `/api/predictions`, `/api/predictions/{id}/repeat`, `/cashout-quote`, `/cashout` |
| Профиль | `view-profile` (:224) | — | `/api/progression`, `/api/achievements`, `/api/stats/me`, `/api/profile/tournament-stats`, `/api/saved-coupons` |
| Мой клуб | `view-my_club` (:269) | Мои матчи / Состав / История (:279–281) | `/api/cabinet/overview`, `/cabinet/matches`, `/cabinet/squad`, `/cabinet/match-time` |

Модалки: `match-markets-modal` (:379), `leaderboard-modal` (:390), `match-time-modal` (:404),
`match-protocol-modal` (:434), `general-success-modal` (:445), `odds-changed-modal` (:455).

Весь HTTP-трафик клиента идёт только через `web/js/api.js` — в `ui.js`, `store.js`,
`tg.js` и `index.html` нет ни одного `fetch(` или `api.`-вызова.

### 1.2 Сервер (`api/`)

105 маршрутов, зарегистрированных в `api/server.py`. Цепочка middleware
(`api/server.py:280`): `cors → rate_limit → lockdown`. CORS намеренно снаружи,
чтобы `401` и `429` тоже уходили с CORS-заголовками — это верно.

### 1.3 Parity & Drift

* **Фронт → бэк:** расхождений нет, все 60 нормализованных вызовов имеют маршрут.
* **Бэк → фронт:** 43 маршрута вообще не имеют обёртки в `api.js`, ещё 28 обёрток
  никогда не вызываются. Часть — намеренно (`renderBonusBanner` в `web/js/ui.js:160`
  сознательно отключил ежедневный бонус, LIVE-центр удалён — комментарий `api.js:266`).
  Остальное — админские маршруты без админ-UI и легаси-дубли (`POST /api/bets`
  рядом с `POST /api/predictions`).
* **Формат полей:** 5 расхождений, см. Д-2 … Д-5 и колонку «Контракт валиден?».

---

## Этап 4. Матрица маршрутов

Колонка «HTTP Статус» — фактические коды на запрос без `initData` / от обычного
игрока / от глобального админа.

| Маршрут | Метод | Источник (UI/API) | Auth/Роль | HTTP Статус | Контракт валиден? | Статус проверки |
|---|---|---|---|---|---|---|
| `/api/favorites/{id}` | DELETE | API — обёртки в api.js нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/saved-coupons/{id}` | DELETE | UI — Профиль (app.js:624) | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/achievements` | GET | UI — Профиль (app.js:167) | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/admin/audit-log` | GET | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `200` | ✅ | 🟢 ОК |
| `/api/admin/bets` | GET | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `200` | ✅ | 🟢 ОК |
| `/api/admin/intelligence/overview` | GET | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `200` | ✅ | 🟢 ОК |
| `/api/admin/live/overview` | GET | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `200` | ✅ | 🟢 ОК |
| `/api/admin/markets` | GET | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `200` | ✅ | 🟢 ОК |
| `/api/admin/risk/alerts` | GET | API — обёртка в api.js есть, вызовов нет | admin | anon `401` / player `403` / admin `200` | ✅ | 🟢 ОК |
| `/api/admin/risk/exposure` | GET | API — обёртка в api.js есть, вызовов нет | admin | anon `401` / player `403` / admin `200` | ✅ | 🟢 ОК |
| `/api/admin/risk/limits` | GET | API — обёртка в api.js есть, вызовов нет | admin | anon `401` / player `403` / admin `200` | ✅ | 🟢 ОК |
| `/api/admin/season` | GET | API — обёртка в api.js есть, вызовов нет | admin | anon `401` / player `403` / admin `200` | ✅ | 🟢 ОК |
| `/api/admin/sports/health` | GET | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `200` | ✅ | 🟢 ОК |
| `/api/bets` | GET | API — обёртки в api.js нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/bets/{id}` | GET | API — обёртки в api.js нет | player | anon `401` / player `404` / admin `404` | ✅ | 🟢 ОК |
| `/api/bootstrap` | GET | UI — старт (app.js:67) | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/cabinet/matches` | GET | UI — Мой клуб (app.js:232, 252) | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/cabinet/overview` | GET | UI — Мой клуб (app.js:224) | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/cabinet/squad` | GET | UI — Мой клуб (app.js:233) | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/divisions` | GET | UI — Лобби (app.js:90) | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/favorites` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/intelligence/history` | GET | API — обёртки в api.js нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/intelligence/hot` | GET | API — обёртки в api.js нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/intelligence/matches` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/intelligence/matches/{id}` | GET | API — обёртки в api.js нет | player | anon `401` / player `404` / admin `404` | ✅ | 🟢 ОК |
| `/api/intelligence/matches/{id}/insights` | GET | API — обёртки в api.js нет | player | anon `401` / player `404` / admin `404` | ✅ | 🟢 ОК |
| `/api/intelligence/matches/{id}/prediction` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `404` / admin `404` | ✅ | 🟢 ОК |
| `/api/intelligence/matches/{id}/preview` | GET | UI — Матч-центр (app.js:365) | player | anon `401` / player `404` / admin `404` | ✅ | 🟢 ОК |
| `/api/intelligence/movers` | GET | API — обёртки в api.js нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/intelligence/performance` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/intelligence/value` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/leaderboard` | GET | UI — модалка лидеров (app.js:773) | player | anon `403` / player `200` / admin `200` | ❌ — отдаёт `leaders`/`entries`/`user_pin`, app.js:775 читает `leaderboard`/`my_rank` | 🔴 401 подменён на 403 |
| `/api/leaderboard/division` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/leaderboard/division/{division_id}` | GET | API — обёртка в api.js есть, вызовов нет | публичный | anon `200` / player `200` / admin `200` | ✅ | 🔴 нет проверки initData |
| `/api/leaderboard/season` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/live` | GET | API — обёртки в api.js нет | публичный | anon `200` / player `200` / admin `200` | ✅ | 🔴 нет проверки initData |
| `/api/live/{id}` | GET | API — обёртки в api.js нет | player | anon `404` / player `404` / admin `404` | ✅ | 🟢 ОК |
| `/api/live/{id}/events` | GET | API — обёртки в api.js нет | публичный | anon `200` / player `200` / admin `200` | ✅ | 🔴 нет проверки initData |
| `/api/live/{id}/intelligence` | GET | API — обёртки в api.js нет | player | anon `404` / player `404` / admin `404` | ✅ | 🟢 ОК |
| `/api/live/{id}/markets` | GET | API — обёртки в api.js нет | публичный | anon `200` / player `200` / admin `200` | ✅ | 🔴 нет проверки initData |
| `/api/live/{id}/stats` | GET | API — обёртки в api.js нет | публичный | anon `200` / player `200` / admin `200` | ✅ | 🔴 нет проверки initData |
| `/api/markets/tours` | GET | UI — Лобби (app.js:104, 404) | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/markets/{id}/odds-history` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/matches` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/matches/hot` | GET | UI — Лобби (app.js:149) | публичный | anon `200` / player `200` / admin `200` | ❌ — отдаёт `matches`, app.js:153 читает `hot_matches` | 🔴 нет проверки initData |
| `/api/matches/{id}` | GET | UI — Матч-центр, протокол (app.js:342, 362) | player | anon `401` / player `404` / admin `404` | ✅ | 🟢 ОК |
| `/api/matches/{id}/h2h` | GET | UI — Матч-центр (app.js:364) | player | anon `401` / player `404` / admin `404` | ✅ | 🟢 ОК |
| `/api/matches/{id}/insights` | GET | UI — Матч-центр (app.js:365) | player | anon `401` / player `404` / admin `404` | ✅ | 🟢 ОК |
| `/api/matches/{id}/live` | GET | UI — Матч-центр (app.js:366) | player | anon `401` / player `404` / admin `404` | ✅ | 🟢 ОК |
| `/api/matches/{id}/markets` | GET | UI — Матч-центр, модалка рынков (app.js:367, 505) | player | anon `401` / player `404` / admin `404` | ✅ | 🟢 ОК |
| `/api/matches/{id}/photo` | GET | API — обёртка в api.js есть, вызовов нет | публичный | anon `200` / player `200` / admin `200` | ✅ | 🔴 нет проверки initData |
| `/api/matches/{id}/stats` | GET | UI — Матч-центр (app.js:363) | player | anon `401` / player `404` / admin `404` | ✅ | 🟢 ОК |
| `/api/notifications` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/odds/movers` | GET | UI — Лобби (app.js:150) | публичный | anon `200` / player `200` / admin `200` | ❌ — отдаёт `biggest_*`, app.js:154 читает `movers` | 🔴 нет проверки initData |
| `/api/player/{id}/public` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/predictions` | GET | UI — История, купон (app.js:706, 717, 813, 951) | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/predictions/{id}` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `404` / admin `404` | ✅ | 🟢 ОК |
| `/api/predictions/{id}/cashout-quote` | GET | UI — История, кэшаут (app.js:794) | player | anon `401` / player `200` / admin `200` | ❌ — вложено в `quote{available,offer}`, app.js:796/799 читает `cashout_available`/`amount` | 🔴 дрейф контракта |
| `/api/profile` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/profile/analytics` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/profile/stats` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/profile/tournament-stats` | GET | UI — Профиль (app.js:211) | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/profile/{user_id}` | GET | API — обёртки в api.js нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/progression` | GET | UI — Профиль (app.js:163) | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/recommendations` | GET | UI — Лобби (app.js:151) | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/results` | GET | UI — Турниры (app.js:192) | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/saved-coupons` | GET | UI — Профиль (app.js:210, 594) | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/season` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/season/rewards` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/seasons` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/seasons/{id}` | GET | API — обёртки в api.js нет | player | anon `401` / player `404` / admin `404` | ✅ | 🟢 ОК |
| `/api/standings` | GET | UI — Турниры (app.js:191) | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/stats/me` | GET | UI — Профиль (app.js:209) | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/table` | GET | API — обёртки в api.js нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/tournaments` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/tournaments/{id}/results` | GET | API — обёртки в api.js нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/tournaments/{id}/standings` | GET | API — обёртки в api.js нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/tournaments/{id}/top-scorers` | GET | UI — Турниры (app.js:193) | player | anon `401` / player `200` / admin `200` | ⚠️ — ключи совпадают, но app.js:193 подставляет division_id в путь tournament_id | 🟡 неверный id в пути |
| `/api/wallet` | GET | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/achievements/claim` | POST | API — обёртки в api.js нет | player | anon `401` / player `400` / admin `400` | ✅ | 🟢 ОК |
| `/api/admin/bets/{id}/void` | POST | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `400` | ✅ | 🟢 ОК |
| `/api/admin/live/markets/{id}/close` | POST | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `400` | ✅ | 🟢 ОК |
| `/api/admin/live/markets/{id}/resume` | POST | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `400` | ✅ | 🟢 ОК |
| `/api/admin/live/markets/{id}/suspend` | POST | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `400` | ✅ | 🟢 ОК |
| `/api/admin/live/markets/{id}/void` | POST | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `400` | ✅ | 🟢 ОК |
| `/api/admin/live/matches/{id}/correction` | POST | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `400` | ✅ | 🟢 ОК |
| `/api/admin/live/matches/{id}/refresh` | POST | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `200` | ✅ | 🟢 ОК |
| `/api/admin/markets/{id}/transition` | POST | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `400` | ✅ | 🟢 ОК |
| `/api/admin/risk/alerts/{id}/ack` | POST | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `200` | ✅ | 🟢 ОК |
| `/api/admin/risk/alerts/{id}/resolve` | POST | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `200` | ✅ | 🟢 ОК |
| `/api/admin/risk/limits` | POST | API — обёртка в api.js есть, вызовов нет | admin | anon `401` / player `403` / admin `400` | ✅ | 🟢 ОК |
| `/api/admin/risk/suspend` | POST | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `400` | ✅ | 🟢 ОК |
| `/api/admin/season` | POST | API — обёртка в api.js есть, вызовов нет | admin | anon `401` / player `403` / admin `400` | ✅ | 🟢 ОК |
| `/api/admin/season/finalize` | POST | API — обёртка в api.js есть, вызовов нет | admin | anon `401` / player `403` / admin `400` | ✅ | 🟢 ОК |
| `/api/admin/season/rewards` | POST | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `400` | ✅ | 🟢 ОК |
| `/api/bets` | POST | API — обёртки в api.js нет | player | anon `401` / player `400` / admin `400` | ✅ | 🟢 ОК |
| `/api/bonus/claim` | POST | API — обёртки в api.js нет | player | anon `401` / player `400` / admin `400` | ✅ | 🟢 ОК |
| `/api/cabinet/match-time` | POST | UI — Мой клуб, модалка времени (app.js:319, 859) | player | anon `401` / player `400` / admin `400` | ✅ | 🟢 ОК |
| `/api/favorites` | POST | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/notifications/read` | POST | API — обёртка в api.js есть, вызовов нет | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/predictions` | POST | UI — История, купон (app.js:706, 717, 813, 951) | player | anon `401` / player `400` / admin `400` | ✅ | 🟢 ОК |
| `/api/predictions/{id}/cashout` | POST | UI — История, кэшаут (app.js:806) | player | anon `401` / player `400` / admin `400` | ❌ — вложено в `result{balance,payout}`, app.js:811/814 читает `new_balance`/`payout` | 🔴 дрейф контракта |
| `/api/predictions/{id}/repeat` | POST | UI — История (app.js:572) | player | anon `401` / player `404` / admin `404` | ✅ | 🟢 ОК |
| `/api/saved-coupons` | POST | UI — Профиль (app.js:210, 594) | player | anon `401` / player `200` / admin `200` | ✅ | 🟢 ОК |
| `/api/admin/markets/{id}/odds` | PUT | API — обёртки в api.js нет | admin | anon `401` / player `403` / admin `400` | ✅ | 🟢 ОК |
**Легенда:** 🟢 — проверка пройдена · 🟡 — работает, но некорректно по смыслу ·
🔴 — дефект. `player` = любой аутентифицированный пользователь, `admin` = глобальный
админ из `ADMIN_IDS`, `публичный` = отдаёт данные без `initData`.

---

## Этап 2–3. Дефекты и уязвимости

Упорядочено по серьёзности.

> **Статус устранения (2026-09-16).** Закрыты: **Д-1, Д-2, Д-3, Д-4, Д-6, Д-13**.
> Описания и `file:line` ниже отражают состояние на момент аудита, до правок.
> Соответственно устарели и статусы в сводке и матрице: `/api/live/*`,
> `/api/odds/movers` больше не публичны, `/api/leaderboard` без `initData`
> отдаёт `401`. Открытыми остаются Д-5, Д-7 … Д-12, Д-14.

### Д-1 🔴 Блокирующий — кэшаут невозможно инициировать из UI

`api/routes_predictions.py:266` кладёт котировку в `quote`, а `:301` — результат в
`result`. Клиент читает поля с верхнего уровня:

* `web/js/app.js:796` — `quoteRes.cashout_available` → всегда `undefined` → всегда
  показывается «Кэшаут в данный момент недоступен». **Функция мертва для всех
  пользователей.**
* `web/js/app.js:799` — `quoteRes.amount` → `undefined` в тексте подтверждения.
* `web/js/app.js:811` — `execRes.new_balance` → баланс в сторе становится `undefined`.
* `web/js/app.js:814` — `execRes.payout` → `+undefined 🪙` в модалке успеха.

Фактический ответ сервера: `{status, quote:{available, bet_id, offer, stake,
potential_win, reason}}` и `{status, result:{bet_id, status, cashout_payout, payout,
balance, message}}` (`services/cashout_engine.py:149`, `database.py:7566`).

### Д-2 🔴 Высокий — модалка таблицы лидеров всегда пуста

`web/js/app.js:775` читает `data.leaderboard` и `data.my_rank`. Сервер
(`api/routes_gamification.py:143`) отдаёт `leaders`, `capper_leaders`, `entries`,
`user_pin` — ни одного из ожидаемых ключей нет. `renderLeaderboardModal` получает
`undefined`, срабатывает guard `if (!x || x.length === 0) return;`, модалка
открывается пустой без единой ошибки в консоли.

### Д-3 🔴 Высокий — `/api/leaderboard` подменяет `401` на `403`

`api/routes_gamification.py:148-154`: результат `_get_auth_user()` отбрасывается, и
**любая** ошибка — включая отсутствие и подделку `initData` — превращается в `403
access_restricted`. Это единственный маршрут во всём API, который нарушает
fail-closed-контракт «нет подписи → 401». Практический вред: клиент не может
отличить «сессия протухла, перелогинься» от «доступ закрыт локдауном», а мониторинг
видит всплеск `403` вместо `401`.

Дополнительно `:156-163` повторно вызывает `check_user_access(user_id)`, который уже
отработал внутри `_get_auth_user` — мёртвая ветка, плюс импорт `check_user_access`
из `api.routes_wallet`, хотя функция живёт в `api/auth.py`.

Подтверждено тестом: `tests/test_api_route_matrix.py::test_missing_init_data_is_401`
и `::test_tampered_hash_is_401` падают ровно на этой строке.

### Д-4 🔴 Высокий — 7 LIVE-маршрутов полностью без авторизации

`api/routes_live.py` не содержит ни одного вызова `get_authenticated_user` или
`check_user_access`. Все обработчики (`:32`, `:76`, `:112`, `:130`, `:149`, `:188`,
`:205`) идут напрямую в `database.transaction()` и провайдеры.

Проверено: `GET /api/live`, `/api/live/{id}/events`, `/api/live/{id}/stats`,
`/api/live/{id}/markets`, `/api/odds/movers` отдают `200` анонимно.

Это обходит локдаун: `LOGOVO_LOCKDOWN=true` закрывает бота и остальной API, но
котировки, рыночные линии и движение коэффициентов остаются публичными. Плюс прямой
расход квоты внешнего sports-провайдера кем угодно из интернета.

### Д-5 🟠 Средний — ещё три публичных маршрута

| Маршрут | Файл | Что утекает |
|---|---|---|
| `GET /api/matches/hot` | `api/routes_matches.py:587` | список матчей с коэффициентами |
| `GET /api/leaderboard/division/{division_id}` | `api/routes_wallet.py:169` | ROI, win rate и ставки игроков дивизиона |
| `GET /api/matches/{id}/photo` | `api/routes_matches.py:210` | скриншоты матчей |

`/api/leaderboard/division/{division_id}` особенно неприятен: соседний
`GET /api/leaderboard/division` (без параметра в пути) авторизацию **требует** —
то есть те же данные доступны и с проверкой, и без неё, в зависимости от формы URL.

### Д-6 🟠 Средний — отзыв прав админа не действует до рестарта API

`api/routes_admin_betting.py:19`, `api/routes_admin_live.py:25`,
`api/routes_admin_risk.py:26`, `api/routes_admin_season.py:18` делают
`from config import ADMIN_IDS` на уровне модуля и сравнивают с этим **снимком**
(`api/routes_admin_risk.py:43-44`).

CLAUDE.md требует обратного: «`config.py` re-reads `config.ADMIN_IDS` dynamically
inside these helpers, so admin changes take effect without a restart. Keep that
behaviour». Хелперы `handlers/base.py:75/96` это правило соблюдают —
эти четыре модуля нет.

Последствие в обе стороны: выданные права не появляются до рестарта, **и отозванные
не исчезают**. Второе — это окно, в котором уволенный админ сохраняет доступ к
`/api/admin/risk/suspend`, `/api/admin/bets/{id}/void` и правке коэффициентов.

Обнаружено экспериментально: в тесте подмена `config.ADMIN_IDS` даёт `403` на 24 из
25 админских маршрутов и `200` на `/api/admin/intelligence/overview` — единственном,
который импортирует `ADMIN_IDS` внутри функции.

### Д-7 🟠 Средний — ошибка с не-JSON телом теряет `status` и `code`

`web/js/api.js:43` вызывает `await res.json()` **до** проверки `res.ok`. aiohttp
отдаёт часть ошибок текстом:

```
GET  /api/does-not-exist   -> 404 text/plain  '404: Not Found'
POST /api/bootstrap        -> 405 text/plain  '405: Method Not Allowed'
GET  /api/matches/abc/photo-> 400 text/plain  'Invalid match id'
```

(проверено на живом приложении; необработанное исключение в хендлере даст такой же
`500: Internal Server Error` текстом)

В этих случаях `res.json()` бросает `SyntaxError` до строки `if (!res.ok)`.
Наружу уходит ошибка без `err.status`, `err.code` и `err.data`, а ветка
`LOGOVO_LOCKDOWN` (`api.js:50-62`) не отрабатывает. Белого экрана нет — все
`fetch*`-методы обёрнуты в `try/catch` — но диагностика теряется полностью.

### Д-8 🟡 Низкий — `api.getLeaderboard` объявлен дважды

`web/js/api.js:95` и `web/js/api.js:362`. В JS побеждает второе объявление, первое
(без параметров) — мёртвый код, который молча затирается. Опасность в том, что
правка версии на `:95` не даст никакого эффекта.

### Д-9 🟡 Низкий — `division_id` подставляется в путь `tournament_id`

`web/js/app.js:193` вызывает `api.getTopScorers(targetDiv)`, где `targetDiv` —
**id дивизиона**. Обёртка `web/js/api.js:199` подставляет его и в путь
(`/api/tournaments/${tournamentId}/top-scorers`), и в query (`?division_id=...`).
Дивизион 1..5 случайно совпадает с турниром 1 «Основная Лига» только для
`targetDiv === 1`; для остальных дивизионов запрашивается несуществующий турнир.

### Д-10 🟡 Низкий — открытый редирект и чтение локального файла в `/photo`

`api/routes_matches.py:234` — `raise web.HTTPFound(photo_id)` перенаправляет на
произвольный URL из БД. `api/routes_matches.py:250-252` — `os.path.exists(photo_id)`
и `open(photo_id, "rb")` читают произвольный локальный путь из БД.

Источник `photo_id` — пайплайн бота, не HTTP-вход, поэтому эксплуатация требует
записи в БД. Но маршрут при этом анонимный (Д-5), так что цепочка «любая запись в
`matches.photo_id`» → «читаемый файл с диска сервера» замыкается без авторизации.

### Д-11 🟡 Низкий — инлайновое сравнение с `ADMIN_IDS`

`api/routes_intelligence.py:418-419`:

```python
from config import ADMIN_IDS
is_global = (user_id in ADMIN_IDS)
```

CLAUDE.md: «use these helpers, never compare against `config.ADMIN_IDS` inline».
Функционально этот вариант как раз корректен (импорт внутри функции = динамическое
чтение), но он дублирует логику `is_global_admin`, минуя проверку `division_admins`.

### Д-12 🟡 Низкий — мёртвый `handle_leaderboard`

`api/routes_wallet.py:120` импортируется в `api/server.py:16`, но не регистрируется
ни на один маршрут. Внутри — `user_id = user_info.get("id") if user_info else 0`
(`:127`): при отсутствии подписи вместо отказа подставляется `0` и запрос идёт
дальше в `check_user_access(0)`. Сейчас безвреден, но это заряженный паттерн — его
достаточно один раз зарегистрировать, чтобы получить обход аутентификации.

### Д-13 🟡 Низкий — тесты API падают на чистом дереве

Без `.env` `config.TOKEN is None`, и `validate_telegram_init_data`
(`api/auth.py:31`) отвергает всё. `tests/test_miniapp_api.py` подписывает initData
через `config.TOKEN or "123456:..."`, то есть фолбэком, которого валидатор не знает.

```
python -m pytest tests/test_miniapp_api.py tests/test_lockdown.py \
  tests/test_phase9_miniapp.py tests/test_phase10_api.py -q -n0
→ 18 failed
```

Тот же прогон с плейсхолдер-токеном в окружении → **0 failed**. Правильный образец
уже есть в репозитории — `tests/test_player_cabinet_api.py` присваивает
`config.TOKEN` в `asyncSetUp` и восстанавливает после.

### Д-14 🟡 Низкий — токен бота может попасть в лог

`api/routes_matches.py:264` формирует `https://api.telegram.org/bot{config.TOKEN}/...`,
а `:279` логирует исключение через f-строку. Исключения aiohttp, которые несут URL
(`InvalidURL`, `ClientResponseError`), запишут токен в лог целиком. CLAUDE.md:
«`TELEGRAM_BOT_TOKEN` … must never appear in code, logs, tests, or commits».

---

## Что проверено и нарушений не найдено

* **Изоляция пользователей.** `api/routes_predictions.py` и
  `api/routes_player_cabinet.py` везде берут `user_id` из подписанной `initData` и
  скоупят выборку через `database.get_user_bet_by_id(user_id, bet_id)`. Тесты
  `test_foreign_bet_is_not_readable`, `test_foreign_bet_is_not_cashable`,
  `test_foreign_profile_exposes_public_fields_only` — зелёные.
* **Приватность профиля.** `api/routes_gamification.py:85-88` сравнивает
  `target_uid == auth_uid` и переключается на `get_public_player_profile` — кошелёк
  чужому не отдаётся.
* **RBAC админки.** Все 25 `/api/admin/*` отдают `403` игроку. Дивизионные админы
  ограничены своими дивизионами (`api/routes_admin_risk.py:89-98`).
* **Эталон модуля** — `api/routes_player_cabinet.py`: единый `_auth()` (401, затем
  403), идентичность клуба всегда выводится из `telegram_id`, участие в матче
  проверяется через `_is_match_participant`.
* **Fail-closed `check_user_access`** (`api/auth.py`) — `except: return False`,
  как и требует CLAUDE.md.
* **Порядок маршрутов.** `/api/profile/stats` не перехватывается
  `/api/profile/{user_id}` — проверено, отдаёт `200`.
* **Параметризация SQL.** Новых интерполяций в `api/` нет.
* **Устойчивость UI.** `renderHotMatches`, `renderOddsMovers`,
  `renderRecommendations`, `renderLeaderboardModal` начинаются с
  `if (!x || x.length === 0) return;` — дрейф контракта деградирует в пустую
  секцию, а не в белый экран.

---

## Минимальные безопасные патчи

### П-1 → Д-1 (кэшаут)

`web/js/app.js`, заменить обращения на вложенные объекты:

```js
const quoteRes = await api.getCashoutQuote(betId);
const quote = quoteRes.quote || {};
if (quoteRes.status !== 'ok' || !quote.available) {
  tgBridge.showAlert(quoteRes.message || "Кэшаут в данный момент недоступен для этого прогноза.");
  return;
}
const quoteAmount = quote.offer;
// ...
const result = execRes.result || {};
store.setUser({ ...store.state.user, balance: result.balance });
this.showSuccessModal('💰 Кэшаут выполнен!', `Зачислено: +${result.payout} 🪙.`);
```

### П-2 → Д-2 (таблица лидеров)

`web/js/app.js:775`:

```js
UIRenderer.renderLeaderboardModal(data.entries || data.leaders, data.user_pin);
```

### П-3 → Д-3 (401 вместо 403)

`api/routes_gamification.py:148-163` — вернуть настоящую ошибку и убрать дубль:

```python
    user_info, err = _get_auth_user(request)
    if err is not None:
        return err
    user_id = user_info["id"]
```

### П-4 → Д-4/Д-5 (публичные маршруты)

В начало каждого обработчика `api/routes_live.py`, а также
`handle_get_hot_matches` (`api/routes_matches.py:587`),
`handle_get_division_leaderboard` (`api/routes_wallet.py:169`) и
`handle_get_match_photo` (`api/routes_matches.py:210`) — тот же `_auth()`-гейт, что
уже используется в `api/routes_player_cabinet.py`:

```python
user_info = get_authenticated_user(request.headers.get("X-Telegram-Init-Data", ""))
if not user_info or "id" not in user_info:
    return web.json_response({"status": "error", "error": "unauthorized"}, status=401)
if not check_user_access(user_info["id"]):
    return web.json_response({"status": "error", "error": "access_restricted"}, status=403)
```

Для `/photo` — авторизация обязательна, а `raise web.HTTPFound(photo_id)`
(`:234`) заменить на проксирование того же вида, что уже сделано для Telegram
`file_id` ниже по коду, либо на белый список хостов.

После патча соответствующие строки убрать из `PUBLIC_BY_DESIGN` в
`tests/test_api_route_matrix.py` — тест сразу начнёт держать границу.

### П-5 → Д-6 (снимок `ADMIN_IDS`)

В четырёх `routes_admin_*.py` убрать `from config import ADMIN_IDS` и заменить
локальный `_is_global_admin` делегированием на канонический хелпер:

```python
from handlers.base import is_global_admin

def _is_global_admin(actor_id: int) -> bool:
    return is_global_admin(actor_id)
```

Это заодно закрывает Д-11, если применить и к `api/routes_intelligence.py:418`.

### П-6 → Д-7 (не-JSON ошибки)

`web/js/api.js:43`:

```js
const raw = await res.text();
let data;
try { data = raw ? JSON.parse(raw) : {}; }
catch { data = { status: 'error', message: raw || `HTTP ${res.status}` }; }
if (!res.ok) { /* ... без изменений ... */ }
```

### П-7 → Д-8, Д-9, Д-12, Д-14

* Удалить `getLeaderboard()` на `web/js/api.js:95`.
* `web/js/app.js:193` → `api.getTopScorers(store.state.tournamentId || 1, targetDiv)`
  с раздельными аргументами; обёртку `api.js:199` — принимать два параметра.
* Удалить `handle_leaderboard` (`api/routes_wallet.py:120-166`) и его импорт
  (`api/server.py:16`).
* `api/routes_matches.py:279` — логировать без интерполяции объекта исключения,
  содержащего URL: `logger.warning("Could not proxy Telegram photo for match #%s: %s", match_id, type(e).__name__)`.

### П-8 → Д-13 (тесты)

В `tests/test_miniapp_api.py`, `tests/test_lockdown.py`,
`tests/test_phase9_miniapp.py`, `tests/test_phase10_api.py` — присваивать
`config.TOKEN` плейсхолдер в `asyncSetUp` и восстанавливать в `asyncTearDown`, как
в `tests/test_player_cabinet_api.py`. Либо вынести это в общий `tests/conftest.py`
(сейчас его нет).

---

## Добавленный тест

`tests/test_api_route_matrix.py` — параметризованная матрица, берущая инвентарь
маршрутов **из самого `create_app()`**, поэтому новый маршрут попадает под проверки
без правки теста. Покрывает: `401` без `initData`, `401` с подделанной подписью,
`401` при подписи чужим токеном, `403` для игрока на всех `/api/admin/*`,
отсутствие `5xx` на битом JSON, `200` на 12 личных маршрутах игрока, три
IDOR-сценария и стража «список публичных маршрутов не растёт молча».

```bash
python -m pytest tests/test_api_route_matrix.py -q -n0
```

Текущий результат: **8 passed, 2 failed** — оба падения указывают на Д-3
(`GET /api/leaderboard -> 403`). Тест умышленно оставлен красным: он станет зелёным
ровно тогда, когда дефект будет исправлен.

Токен в тесте — заведомо недействительный плейсхолдер из документации Telegram
(`123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`), тот же, что уже используется в
`tests/test_player_cabinet_api.py`. Настоящих секретов ни в тесте, ни в отчёте нет.
