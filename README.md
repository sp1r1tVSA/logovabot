<div align="center">

  <h1>⚽ LOGOVOBOT (Логово Фифарей)</h1>
  <p><b>Умная система автоматизации футбольных киберспортивных лиг по EA FC / FIFA</b></p>
  <p><i>Автоматическое ИИ-распознавание скриншотов матчей • Виртуальный аналитик «Темшик» • Управление турнирной таблицей</i></p>

  <p>
    <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"></a>
    <a href="https://github.com/python-telegram-bot/python-telegram-bot"><img src="https://img.shields.io/badge/Telegram_Bot_API-v21.0-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" alt="Telegram Bot API"></a>
    <a href="https://deepmind.google/technologies/gemini/"><img src="https://img.shields.io/badge/Google_Gemini-2.0_Flash-8E75B2?style=for-the-badge&logo=googlecloud&logoColor=white" alt="Google Gemini AI"></a>
    <a href="https://sqlite.org"><img src="https://img.shields.io/badge/Database-SQLite3-003B57?style=for-the-badge&logo=sqlite&logoColor=white" alt="SQLite"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.style=for-the-badge" alt="License"></a>
  </p>

</div>

---

## 🌟 Ключевые Возможности

### 📸 1. ИИ-Распознавание Скриншотов Матчей (AI Vision)
- **Мгновенный ввод результатов:** Игроку достаточно прикрепить 1 или 2 скриншота послематчевой статистики из EA FC / FIFA.
- **Умное распознавание зеркальных таблиц:** Учитывает специфику интерфейса EA FC — считывает голы и ассисты с разделением по левой (`Г | А`) и правой (`А | Г`) половинам экрана.
- **Индивидуальная статистика:** Автоматически заносит голы и ассисты конкретных футболистов в баз данных лиги.

### 🤖 2. Виртуальный ИИ-Аналитик и Собеседник «Темшик»
- **Интерактивное общение:** Активируется в ЛС и общих чатах по кодовому слову `темшик` (например: *"Темшик, какие шансы у Брюгге на топ-3?"*).
- **Математический расчёт шансов:** Анализирует набранные очки, оставшиеся туры, разницу голов и текущую форму (`W-D-L`), выдавая аналитические вероятности в процентах.
- **Память и регламент:** Знает историю прошлых сезонов (чемпионов, призёров), правила турнира, запрещённые финты и контакты судьи.

### 🏆 3. Автоматизированная Турнирная Система
- **Динамическая таблица:** Мгновенный пересчёт очков, побед, ничьих, поражений, забитых и пропущенных мячей.
- **Двухэтапная верификация:** Подтверждение результатов соперником в ЛС с защитой от застревания (авто-подтверждение при закрытых сообщениях).
- **Гонка бомбардиров и ассистентов:** Полный учёт личных показателей всех футболистов лиги.

### ⚙️ 4. Панель Администратора
- Управление турами, дедлайнами и закрытием раундов.
- Технические поражения, дисквалификации и ручная корректировка результатов.
- Редактирование составов клубов и участников лиги.

---

## 🛠 Технологический Стек

| Компонент | Технология | Назначение |
| :--- | :--- | :--- |
| **Language** | Python 3.11+ | Основной язык разработки |
| **Framework** | `python-telegram-bot` (v21) | Асинхронное взаимодействие с Telegram Bot API |
| **AI Engine** | Google Gemini (v1beta API) | OCR скриншотов (`gemini-2.0-flash`) и генеративный чат |
| **Database** | SQLite3 | Хранение пользователей, матчей, ивентов и составов |
| **Configuration** | `python-dotenv` | Безопасное управление токенами и ключами |

---

## 📂 Структура Проекта

```text
logovobot/
├── main.py                # Точка входа, инициализация и запуск Telegram-бота
├── config.py              # Загрузка переменных окружения и настроек
├── database.py            # SQLite база данных, транзакции и аналитические запросы
├── ai_recognizer.py       # Модуль распознавания скриншотов (Gemini Vision OCR)
├── ai_chat.py             # Модуль генеративного общения (ИИ Темшик)
├── handlers/
│   ├── __init__.py        # Регистрация всех хэндлеров и роутинг сообщений
│   ├── cabinet.py         # Личный кабинет игрока, отчёты о матчах
│   ├── chat.py            # Обработчик свободного общения с ИИ Темшиком
│   └── admin.py           # Панель администратора и управление лигой
├── requirements.txt       # Список внешних зависимостей
└── .env                   # Конфигурационный файл окружения (не коммитится)
```

---

## 🚀 Быстрый Запуск

### 1. Клонирование и Установка Зависимостей

```bash
git clone https://github.com/Test996817/logovabot.git
cd logovabot

# Создание и активация виртуального окружения
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

pip install -r requirements.txt
```

### 2. Настройка Переменных Окружения (`.env`)

Создайте файл `.env` в корневой директории:

```env
TELEGRAM_BOT_TOKEN=7777777777:AAA_your_telegram_bot_token
ADMIN_IDS=123456789,987654321
LEAGUE_SQLITE_PATH=league.db

# Ключ Gemini API для распознавания скриншотов
GEMINI_API_KEY=AIzaSy_your_primary_gemini_key

# Второй ключ Gemini API для свободной беседы (изоляция лимитов)
GEMINI_CHAT_API_KEY=AIzaSy_your_secondary_gemini_key
```

### 3. Локальный Запуск

```bash
python main.py
```

---

## 🐧 Деплоймент на Сервер (Systemd Linux Service)

Для бесперебойной работы бота на VPS создайте `systemd` сервис:

```bash
sudo nano /etc/systemd/system/logovobot.service
```

Вставьте следующую конфигурацию:

```ini
[Unit]
Description=LogovoBot Telegram Bot Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/logovobot
ExecStart=/root/logovobot/venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Перезапустите менеджер сервисов и активируйте автозапуск:

```bash
sudo systemctl daemon-reload
sudo systemctl enable logovobot
sudo systemctl start logovobot
```

Просмотр логов в реальном времени:
```bash
journalctl -u logovobot -f
```

---

## 💬 Команды Бот-Системы

### 👤 Команды Игрока
- `/start` — Запуск бота, регистрация профиля и выбор клуба.
- `👤 Мой кабинет` — Панель управления игрока, список доступных матчей и внесение результатов.
- `📊 Таблица лиги` — Актуальная турнирная таблица, гонка бомбардиров и ассистентов.
- `темшик [ваш вопрос]` — Общение с ИИ-аналитиком (рассчет шансов, правила, история лиги).

### ⚙️ Команды Администратора
- `⚙️ Админ-панель` — Открытие административной панели.
- Внесение результатов за любую пару участников без ожидания подтверждения.
- Управление открытыми и закрытыми турами, установление дедлайнов.
- Назначение техничесикх поражений и редактирование составов команд.

---

<div align="center">
  <p>Разработано с ❤️ для сообщества <b>Логово Фифарей</b></p>
</div>
