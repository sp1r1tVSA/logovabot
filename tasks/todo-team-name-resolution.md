# Задачи: корректный резолв имени клуба

План: [plan-team-name-resolution.md](plan-team-name-resolution.md) · Спека:
[SPEC-team-name-resolution.md](../SPEC-team-name-resolution.md)

Правило прохода: задача закрыта только когда её `Verify` зелёный **и** полный
`python -m pytest tests/ -q` не хуже baseline (exit 0, падений нет).

---

## Шаг 1 — Зафиксировать поведение

- [x] **T1. Характеризационные тесты резолвера** — готово. 12 красных / 11 зелёных;
      остальной прогон не задет (exit 0, 0 падений). Находка: `Аякс Амстердам` и
      `Селтик Глазго` держатся на удаляемом подстрочном тире → в T3 им нужны алиасы
      (тест `test_verbose_forms_still_collapse_to_their_club` покраснеет и это поймает).
  - Acceptance:
    - 4 репро-кейса (`Расинг Сантандер`, `Расинг Ланс`, `Спортинг Хихон`, `ПСЖ`) —
      ассерт «резолвится в себя». Сейчас **падают** — это ожидаемо и обязательно.
    - Анти-регресс: каждое из 16 `KPL_TEAMS` и **каждый** ключ `TEAM_ALIASES`
      резолвится ровно в текущее значение. Сейчас **проходят**.
    - Тесты на подставном реестре (monkeypatch), без обращения к БД.
  - Verify: `python -m pytest tests/test_team_name_resolution.py -v` → 4 падения на
    репро, 0 падений на анти-регрессе. Падают **именно** репро-тесты, ничто иное.
  - Files: `tests/test_team_name_resolution.py`

## Шаг 2 — Переезд без смены поведения

- [ ] **T2. `services/club_registry.py` + реэкспорт**
  - Acceptance:
    - `normalize_team_name` и `TEAM_ALIASES` переехали, логика не тронута.
    - `CLUB_REGISTRY` в `config.py` = `KPL_TEAMS ∪ CLUBS` (пока заглушка с TODO;
      реальные 80 имён приезжают в T8).
    - Предпосчитанный индекс `normalized → canonical` + `reload_registry()`.
    - Валидация алиасов на старте: алиас, чья нормализованная форма совпадает с
      каноном **другого** клуба, отбрасывается с `logger.warning`.
    - `database.py` реэкспортирует `normalize_team_name`, `TEAM_ALIASES`,
      `resolve_team_name`, `teams_match`.
  - Verify:
    - `python -c "import database, services.club_registry"` — без ImportError (R4).
    - `python -c "import database as d; print(d.normalize_team_name('Будё-Глимт'))"` → `буде глимт`
    - `python -m pytest tests/ -q` зелёный. **Это чекпоинт реэкспорта** — если красный,
      сломаны 50 вызовов, дальше не идти.
  - Files: `services/club_registry.py`, `config.py`, `database.py`

## Шаг 3 — Собственно фикс

- [ ] **T3. Тиры резолва + `resolve_team_name_ex`**
  - Acceptance:
    - `TeamResolution` (raw, canonical, method, confidence, candidates, is_confident).
    - Тиры 1–5 по спеке; **подстрочный тир удалён**; фаззи с `FUZZY_MIN_LEN=5`,
      `FUZZY_THRESHOLD=0.87`, `FUZZY_MARGIN=0.07`.
    - Неоднозначность на любом тире → `method=NONE`, заполненный `candidates`,
      **без** перехода к следующему тиру.
    - `resolve_team_name` никогда не возвращает пустую строку на непустой вход.
  - Verify:
    - T1 целиком зелёный: репро починены, анти-регресс не тронут.
    - **R3 — отдельный тест:** `detect_teams_from_players` ([database.py:4964](../database.py:4964))
      резолвит отдельные слова подписи. Тест с подписью `"расинг сантандер vs порту"`
      обязан находить оба клуба. Не находит — добавляем алиас, порог не трогаем.
    - `python -m pytest tests/ -q` зелёный.
  - Files: `services/club_registry.py`, `tests/test_team_name_resolution.py`

- [ ] **T4. `teams_match` на полном реестре**
  - Acceptance:
    - Защита от схлопывания читает реестр, а не `config.CLUBS` ([database.py:1601](../database.py:1601)).
    - Проверка «обе стороны — различные зарегистрированные клубы → `False`» стоит
      **до** подстрочного и словесного тиров ([database.py:1613](../database.py:1613)).
    - `teams_match("Расинг", "Расинг Ланс") is False`;
      `teams_match("Атлетик", "Атлетико") is False` (не регрессировало).
  - Verify: `python -m pytest tests/test_team_name_resolution.py -v` + полный прогон.
  - Files: `services/club_registry.py`, `tests/test_team_name_resolution.py`

## Шаг 4 — Производительность

- [ ] **T5. LRU-кэш резолва + инвалидация**
  - Acceptance:
    - Кэш на `resolve_team_name_ex`, сбрасывается в `reload_registry()`.
    - Функция остаётся синхронной и без I/O (спека, Boundaries).
    - Тест: после `reload_registry()` с изменённым реестром ответ меняется —
      кэш не отдаёт устаревшее.
  - Verify: `python -m pytest tests/test_team_name_resolution.py -v`; грубый замер —
    80 имён × 1000 резолвов укладывается в секунду.
  - Files: `services/club_registry.py`, `tests/test_team_name_resolution.py`

## Шаг 5 — Доказать исходную жалобу

- [ ] **T6. Интеграционный тест `get_standings`**
  - Acceptance:
    - Фикстура сеет дивизион и 3 клуба с коллизионными именами + `uuid4`-суффиксами
      (как `tests/test_chat_division_scope.py`), плюс подтверждённые матчи.
    - `get_standings(division_id=...)` → **3** строки, у каждой свой `telegram_id`,
      суммы очков сходятся.
    - Фикстура убирает за собой; прогон под `-n auto` стабилен.
  - Verify: `python -m pytest tests/test_standings_name_collision.py -v`, затем то же
    с `-n0`. Оба зелёные.
  - Files: `tests/test_standings_name_collision.py`

## Шаг 6 — Инструмент для живой БД

- [ ] **T7. `scripts/audit_team_resolution.py`**
  - Acceptance:
    - Строго read-only: ни одного `INSERT`/`UPDATE`/`DELETE`.
    - Отчёт: коллизии (2+ имени → 1 канон), клубы вне реестра, дрейф реестр↔БД,
      распределение фаззи-скоров (для проверки порога, R8).
    - Exit code 1 при любой находке, 0 при чистом прогоне.
    - `--emit-config` печатает готовый блок `CLUB_REGISTRY` для вставки в `config.py`.
  - Verify: локально на пустой `league.db` → «0 клубов», exit 0. Затем **вы** прогоняете
    на VPS и присылаете вывод.
  - Files: `scripts/audit_team_resolution.py`

## Шаг 7 — Заполнить реестр ⛔ БЛОКИРОВАНО

- [ ] **T8. Реальные 80 клубов + страж дрейфа**
  - **Блокирует:** вывод `--emit-config` с VPS (T7). Локально данных нет.
  - Acceptance:
    - `config.CLUB_REGISTRY` содержит реальные имена; заглушка и TODO убраны.
    - `verify_registry_against_db()` в `database.py` + тест-страж: если в
      `users.team_name` есть клуб вне реестра — тест падает.
    - Инвариант тотальности прогнан на реальном списке: попарно различные каноны
      дают различные результаты.
  - Verify: `python -m pytest tests/ -q`; `python scripts/audit_team_resolution.py`
    на VPS → 0 коллизий, exit 0.
  - Files: `config.py`, `database.py`, `tests/test_team_name_resolution.py`

## Шаг 8 — Документация

- [ ] **T9. Обновить описания**
  - Acceptance:
    - `CLAUDE.md:142-150` — абзац с ⚠️ описывает этот баг как действующий и запрещает
      добавлять вызовы для клубов вне 16. После фикса запрет снимается, вместо него —
      описание реестра и правило «новый клуб → обновить `CLUB_REGISTRY`».
    - `Project_Audit_Report.md` — отметка, что первая часть P3-7 закрыта, и что именно
      осталось (`KPL_TEAMS` в ветке `get_standings`, пересечение с P3-3).
    - `.agents/AGENTS.md:44` — упоминание резолва актуализировано.
  - Verify: чтение; `grep -n "0.65" CLAUDE.md` пусто.
  - Files: `CLAUDE.md`, `Project_Audit_Report.md`, `.agents/AGENTS.md`

---

## Порядок и параллельность

```
T1 → T2 → T3 → T4 → T5 → T9
           │           ↘
           ├→ T6 (независим от T4/T5)
           └→ T7 → T8 ⛔ ждёт прогона на VPS
```

Коммиты по Conventional Commits, по одному на задачу:
`test(database):`, `refactor(database):`, `fix(database):`, `feat(scripts):`, `docs:`.
