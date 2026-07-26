# 🏆 Глубокий QA-аудит бизнес-логики и краевых кейсов — logovobot

> **Роль:** Senior QA Engineer & Lead Backend Developer  
> **Дата аудита:** 26 июля 2026 г.  
> **Объект аудита:** Регламент турнира, краевые сценарии, целостность таблицы и статистики `logovobot`.

---

## 📊 ШАГ 1 — DOMAIN SCORECARD (ТАБЛИЦА ОЦЕНКИ БИЗНЕС-ЛОГИКИ)

| # | Категория бизнес-логики | Оценка | Ключевая бизнес-проблема |
|---|---|---|---|
| 1 | **⚽ Проведение матчей и ввод результатов** | **7 / 10** | Отсутствует проверка дедлайна тура непосредственно при нажатии кнопки «Внести результат» |
| 2 | **🤖 AI-распознавание скриншотов (`ai_recognizer`)** | **8 / 10** | Если AI не распознал нерелевантную картинку (кот/мем), её `file_id` всё равно сохраняется в БД при фоллбеке на ручной ввод |
| 3 | **⚖️ Спорные ситуации (Disputes) и Админ-оверрайд** | **6 / 10** | **Критично**: При админском изменении счёта или назначении техпоражения (3:0) события `match_events` НЕ очищаются — статистика бомбардиров расходится со счётом матчей |
| 4 | **📊 Турнирная таблица и статистика** | **6 / 10** | **Критично**: При удалении участника из лиги `remove_player` его прошлые сыгранные матчи выбывают из подсчёта `get_standings()`, меняя очки соперников |
| 5 | **👥 Профили, трансферы и отвязка игроков** | **8 / 10** | Отличная привязка по `telegram_id` (смена `@username` не ломает профиль), но при переходе игрока старые `match_events` сохраняют старый клуб |
| 6 | **🔒 Границы чатов и команды** | **9 / 10** | Изоляция отличная: `/start` игнорируется в группах, административные действия заблокированы в общих чатах |

---

## 🚨 ШАГ 2 — КРИТИЧЕСКИЕ ЛОГИЧЕСКИЕ ДЫРЫ (CRITICAL BUSINESS FLAWS)

### 🔴 1. Стирание очков лиги при удалении/отвязке участника (`remove_player`)
* **Где найдено:** `database.py:300` (`get_standings`) & `database.py:880` (`remove_player`).
* **Суть проблемы:** В `get_standings()` список пользователей формируется с условием `WHERE team_name IS NOT NULL`. Если администратор удаляет игрока из лиги (или переводит в свободные агенты через `remove_player`), его `team_name` становится `NULL`. В результате в цикле расчёта таблицы `if p1_id in users:` этот игрок игнорируется, а соперники, игравшие с ним, **теряют набранные очки и забитые мячи за прошлые сыгранные матчи**!
* **Влияние на турнир:** Разрушение турнирной таблицы лиги при любом трансфере или удалении игрока посреди сезона.

---

### 🔴 2. Рассинхрон статистики бомбардиров при Админском изменении счёта и техпоражениях
* **Где найдено:** `database.py:683` (`admin_set_match_score`), `database.py:890` (`set_technical_result`), `database.py:1161` (`reset_match`).
* **Суть проблемы:** 
  1. Когда игроки вносят результат, в таблицу `match_events` записываются авторы голов.
  2. Если затем возник спор и админ выставил техпоражение `3:0` (`set_technical_result`) или вручную изменил счёт (`admin_set_match_score`), старые записи из `match_events` **НЕ удаляются**.
  3. В итоге в таблице бомбардиров (`get_top_scorers`) у игрока висят забитые голы из матча, который признан техпоражением `3:0` без реальных голов!
* **Влияние на турнир:** Нарушение честности гонки за «Золотую бутсу» и награды сезонов.

---

### 🔴 3. Утечка картинки-мема в БД при сбое AI-распознавания
* **Где найдено:** `handlers/cabinet.py:1415` (`save_report_photo`).
* **Суть проблемы:** Если пользователь отправляет произвольную картинку (например, мем или нечёткое фото), AI-распознавание выкидывает ошибку, и бот переключает пользователя на ручной ввод голов. Однако `context.user_data["report_photo_id"]` **уже содержит file_id этого нерелевантного фото**. Когда игрок завершает ручной ввод, фото-мем сохраняется в БД как официальный протокол матча.
* **Влияние на турнир:** Засорение базы данных некорректными медиафайлами вместо протоколов матчей.

---

### 🔴 4. Отсутствие проверки дедлайна тура при нажатии «Внести результат»
* **Где найдено:** `handlers/cabinet.py:1021` (`cb_report_choice_auto`) и `cb_report_choice_manual`.
* **Суть проблемы:** Проверка истечения дедлайна проводится только при просмотре карточки матча в `cabinet_view_match`. Если игрок открыл карточку матча за минуту до дедлайна, дождался закрытия тура и только потом нажал «Внести результат» — бот беспрепятственно позволит внести результат в просроченный/закрытый тур.
* **Влияние на турнир:** Игроки могут обходить дедлайны туров без разрешения админа.

---

### 🔴 5. Несоответствие критериев ранжирования (Tie-Breaker) официальному регламенту
* **Где найдено:** `database.py:363` (`get_standings`).
* **Суть проблемы:** Команды со равным количеством очков сортируются по правилу: `(Очки -> Разница голов -> Забитые голы -> Победы)`. Однако в большинстве регламентов футбольных лиг (и в правилах Top 7 Leagues 25/26) первым критерием при равенстве очков являются **Личные встречи (Head-to-Head)**.
* **Влияние на турнир:** Команда может ошибочно занять более высокое место при равенстве очков с победителем личной встречи.

---

## ⚠️ ШАГ 3 — КРАЕВЫЕ СЦЕНАРИИ (EDGE CASES AUDIT)

1. **Матч сам с собой (`player1_id == player2_id`)**:
   - При автоматической генерации туров команда не играет сама с собой. Но если админ вручную создаст матч или привяжет одного игрока к двум командам, проверка `CHECK (player1_id != player2_id)` в SQLite отсутствовала.
2. **Ввод аномального счёта при ручном вводе**:
   - В `admin_set_score_text` регулярное выражение `(\d+)` принимает любые числа (например, `999999:0`). Отсутствует ограничение `MAX_GOALS = 50`.
3. **Параллельная отправка отчёта двумя игроками**:
   - Если Player 1 и Player 2 одновременно отправляют AI-скриншот или подтверждают результат, благодаря добавленному `match['status'] != 'pending'` второй запрос отменяется с уведомлением *"Результат уже зафиксирован!"*. Это работает корректно.

---

## 🛠️ ШАГ 4 — КОНКРЕТНЫЕ ИСПРАВЛЕНИЯ В КОДЕ («БЫЛО ➔ СТАЛО»)

---

### Исправление #1: Корректный учёт матчей в таблице лиги (`database.py`)
Гарантирует, что даже если игрок удалён или отвязан от клуба, его сыгранные матчи остаются в очковом балансе лиги.

**БЫЛО (`database.py:299-301`):**
```python
# Игроки без клуба игнорировались, а их матчи пропадали из таблицы
cursor.execute("SELECT telegram_id, team_name, username FROM users WHERE team_name IS NOT NULL")
```

**СТАЛО:**
```python
# Учитываем всех участников, у которых есть сохранённый snapshot команды в матчах
cursor.execute("""
    SELECT telegram_id, team_name, username FROM users
    UNION
    SELECT player1_id AS telegram_id, player1_team AS team_name, player1_nickname AS username FROM matches WHERE player1_id IS NOT NULL
    UNION
    SELECT player2_id AS telegram_id, player2_team AS team_name, player2_nickname AS username FROM matches WHERE player2_id IS NOT NULL
""")
```

---

### Исправление #2: Очистка `match_events` при Админском изменении счёта / технаре (`database.py`)

**БЫЛО (`database.py:683`):**
```python
def admin_set_match_score(match_id: int, player1_score: int, player2_score: int) -> None:
    with transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE matches SET player1_score = ?, player2_score = ?, status = 'confirmed', played_at = CURRENT_TIMESTAMP WHERE id = ?",
            (player1_score, player2_score, match_id)
        )
        # ❌ Старые события голов НЕ удалялись!
```

**СТАЛО:**
```python
def admin_set_match_score(match_id: int, player1_score: int, player2_score: int) -> None:
    with transaction() as conn:
        cursor = conn.cursor()
        # ✅ Удаляем события голов при административном изменении счёта/технаре
        cursor.execute("DELETE FROM match_events WHERE match_id = ?", (match_id,))
        cursor.execute(
            "UPDATE matches SET player1_score = ?, player2_score = ?, status = 'confirmed', played_at = CURRENT_TIMESTAMP WHERE id = ?",
            (player1_score, player2_score, match_id)
        )
```

---

### Исправление #3: Очистка `report_photo_id` при сбое AI-распознавания (`handlers/cabinet.py`)

**БЫЛО (`handlers/cabinet.py:1415`):**
```python
except Exception:
    logger.exception("AI Vision processing error")
    await update.message.reply_text("⚠️ Не удалось распознать скриншот. Переходим к ручному вводу.")
    # ❌ photo_id оставался в context.user_data["report_photo_id"]
    await cb_report_choice_manual(update, context)
```

**СТАЛО:**
```python
except Exception:
    logger.exception("AI Vision processing error")
    context.user_data.pop("report_photo_id", None)  # ✅ Сбрасываем нерелевантное фото
    await update.message.reply_text("⚠️ Не удалось распознать скриншот. Переходим к ручному вводу.")
    await cb_report_choice_manual(update, context)
```

---

### Исправление #4: Проверка дедлайна при запуск ввода результата (`handlers/cabinet.py`)

**БЫЛО (`handlers/cabinet.py`):**
```python
async def cb_report_choice_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ❌ Не проверялся дедлайн тура!
```

**СТАЛО:**
```python
async def cb_report_choice_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    match_id = context.user_data.get("reporting_match_id")
    if match_id:
        m = await asyncio.to_thread(database.get_match, match_id)
        if m:
            r_info = await asyncio.to_thread(database.get_round_info, m['round_number'])
            if r_info and r_info.get("deadline"):
                try:
                    dt = datetime.datetime.strptime(r_info["deadline"], "%d.%m.%Y %H:%M")
                    if datetime.datetime.now() > dt:
                        await query.answer("🔴 Дедлайн тура истёк! Запросите разрешение у админа.", show_alert=True)
                        return
                except ValueError:
                    pass
```

---

## 📈 ИТОГИ АУДИТА И РЕКОМЕНДАЦИИ

1. Применить исправления #1–#4 для предотвращения рассинхрона статистики бомбардиров и таблицы лиги.
2. Архитектура бота в целом демонстрирует **высокий уровень изоляции прав и безопасности**.
3. Все найденные замечания касаются специфических бизнес-сценариев турнира и устраняются в течение 1–2 часов рефакторинга.
