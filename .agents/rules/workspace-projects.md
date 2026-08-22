# Workspace Projects Guide & Isolation Rules

Правила взаимодействия с активными проектами в рабочей директории `c:\Users\Ислам\Desktop\Projects`.

---

## 🔒 Общие правила изоляции (Cross-Project Isolation)

1. **Строгая контекстная изоляция**: Каждая задача относится к конкретному проекту. Запрещено модифицировать, удалять или запускать файлы в соседних проектах без прямого указания пользователя.
2. **Изоляция виртуальных окружений и зависимостей**:
   - Для Python-проектов всегда использовать `venv` соответствующего проекта (`<project>/venv/Scripts/...`).
   - Для Node.js-проектов всегда запускать команды внутри целевой папки (`Cwd` = целевая папка).
3. **Безопасность секретов**: Файлы `.env`, базы данных (`.db`, `.sqlite`, `.sql`) и сессионные файлы (`.session`) содержат конфиденциальные токены и ключи — никогда не перезаписывать и не удалять их при рефакторинге.

---

## 📁 Карта проектов

### 1. 🤖 `dbbot` (LPL Football Tournament Bot)
- **Путь**: `c:\Users\Ислам\Desktop\Projects\dbbot`
- **Стек**: Python 3.11+, `python-telegram-bot`, PostgreSQL (`db/postgres.py`), Pillow, asyncio
- **Окружение**: `c:\Users\Ислам\Desktop\Projects\dbbot\venv`
- **Точка входа**: `main.py`
- **Ключевые стандарты**:
  - Следование спецификации `LPL_SPEC.md`.
  - Принцип «Всё по полочкам» (Zero Tech Debt): `bot/` — только обработчики Telegram, `db/` — только Data Access Layer, `utils/` — чистые вспомогательные функции.
  - Обязательные Type Annotations, параметризованные SQL-запросы, аудит-лог `lpl_audit_log`.

### 2. ⚽ `logovobot` (Логово Фифарей / ИИ «Темшик»)
- **Путь**: `c:\Users\Ислам\Desktop\Projects\logovobot`
- **Стек**: Python 3.11+, `python-telegram-bot` v21, SQLite3 (`league.db`), Google Gemini 2.0 Flash (OCR & Чат), Pillow
- **Окружение**: `c:\Users\Ислам\Desktop\Projects\logovobot\venv`
- **Точка входа**: `main.py`
- **Ключевые модули**:
  - `ai_recognizer.py` — Gemini Vision OCR скриншотов статистики матчей.
  - `ai_chat.py` — генеративный ИИ-аналитик «Темшик» (расчет шансов, регламент).
  - `table_generator.py`, `top_stats_generator.py`, `player_card_generator.py` — графический рендеринг инфографики.

### 3. 🏆 `challenge_place` (Tournament Manager Clone)
- **Путь**: `c:\Users\Ислам\Desktop\Projects\challenge_place`
- **Стек**: Next.js (App Router), TypeScript, Tailwind CSS, `ditto.css`
- **Окружение**: Node.js (`npm run dev`, `npm run build`)
- **Ключевые стандарты**:
  - Безопасные зоны редактирования: `src/app/content.ts`, `src/app/components/`, `src/app/sections/`, `src/app/ditto.css`.
  - Не модифицировать системный рантайм `src/app/ditto/` без необходимости.

### 4. 🌐 `tournament-web` (Tournament Management Web App)
- **Путь**: `c:\Users\Ислам\Desktop\Projects\tournament-web`
- **Стек**: Next.js (App Router), TypeScript, Prisma ORM, PostgreSQL
- **Окружение**: Node.js (`npm run dev`, `npx prisma`)

### 5. 🏎️ `strboost` (Straces Automation Script)
- **Путь**: `c:\Users\Ислам\Desktop\Projects\strboost`
- **Стек**: JavaScript (Userscript / Tampermonkey), DOM API

### 6. 🧪 `test1` (Testing & Proctoring Platform)
- **Путь**: `c:\Users\Ислам\Desktop\Projects\test1`
- **Стек**: HTML5, CSS3, Vanilla JavaScript (Quiz & Proctoring engine)
