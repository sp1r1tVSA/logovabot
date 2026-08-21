import requests
import os
from pathlib import Path

SERVER_IP = "192.168.137.228"
PORT = "11434"
PROJECT_PATH = r"C:\Users\Ислам\Desktop\Projects\logovobot"

CODE_EXTENSIONS = ['.py', '.md', '.txt', '.yml', '.yaml', '.json', '.toml']
EXCLUDED_FOLDERS = {'venv', '__pycache__', '.git', '.claude', 'players_cache', 'assets', 'node_modules', '.idea', '.vscode'}
EXCLUDED_FILES = {'channel_posts.txt', 'chat_dump.txt', 'db_b64.txt', 'telegram_dump_session.session', 'requirements.txt'}

# Предустановленные "модули" проекта для быстрого анализа
PROJECT_MODULES = {
    "1": ("Весь проект (только маленькие файлы)", None),
    "2": ("AI-модули", ["ai_chat.py", "ai_recognizer.py", "persona_base.py"]),
    "3": ("Handlers (обработчики команд)", ["handlers/base.py", "handlers/chat.py", "handlers/drafts.py", "handlers/text_commands.py", "handlers/__init__.py"]),
    "4": ("Генераторы контента", ["player_card_generator.py", "table_generator.py", "top_stats_generator.py", "player_photos.py"]),
    "5": ("Парсеры и сервисы", ["schedule_parser.py", "services/cup_bracket_generator.py"]),
    "6": ("Конфигурация и константы", ["config.py", "constants.py", "main.py"]),
    "7": ("Тесты", ["tests/test_debt_lifecycle.py"]),
    "8": ("README и документация", ["README.md"]),
}

# Большие файлы для отдельного анализа
LARGE_FILES = {
    "A": ("database.py", "database.py"),
    "B": ("admin.py", "admin.py"),
    "C": ("cabinet.py", "cabinet.py"),
}


def read_files(file_list):
    """Читает список файлов и возвращает их содержимое"""
    content = []
    total_size = 0
    
    for file_name in file_list:
        file_path = Path(PROJECT_PATH) / file_name
        if not file_path.exists():
            print(f"  ⚠️  Не найден: {file_name}")
            continue
        
        try:
            size = file_path.stat().st_size
            if size > 80_000:  # ~80 КБ максимум на файл
                print(f"  ️  Пропуск {file_name} ({size // 1024} КБ) — слишком большой")
                continue
            
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()
                content.append(f"\n{'='*50}\n📄 {file_name}\n{'='*50}\n{text}")
                total_size += len(text)
                print(f"  ✅ {file_name} ({size // 1024} КБ)")
        except Exception as e:
            print(f"  ❌ Ошибка {file_name}: {e}")
    
    print(f"\n📊 Загружено: ~{total_size // 1024} КБ")
    return "\n".join(content)


def read_all_small_files():
    """Читает все маленькие файлы проекта (исключая большие)"""
    files = []
    project = Path(PROJECT_PATH)
    
    for file_path in project.rglob("*"):
        if file_path.is_dir():
            continue
        if any(part in EXCLUDED_FOLDERS for part in file_path.parts):
            continue
        if file_path.name in EXCLUDED_FILES:
            continue
        if file_path.suffix.lower() not in CODE_EXTENSIONS:
            continue
        if file_path.stat().st_size > 40_000:  # Только файлы до 40 КБ
            continue
        
        files.append(str(file_path.relative_to(project)))
    
    return read_files(files)


def ask_ai(prompt, context=""):
    url = f"http://{SERVER_IP}:{PORT}/api/generate"
    
    if context:
        full_prompt = f"КОНТЕКСТ КОДА:\n{context}\n\nЗАДАЧА:\n{prompt}\n\nОтвечай на русском, приводи примеры кода."
    else:
        full_prompt = prompt
    
    payload = {
        "model": "qwen2.5-coder:3b",
        "system": "Ты — опытный Python-разработчик, специализирующийся на Telegram-ботах. Анализируй код, находи баги, предлагай улучшения. Отвечай на русском, пиши чистый код с комментариями.",
        "prompt": full_prompt,
        "stream": False
    }
    
    print("\n🔄 Отправка запроса (модель думает, это может занять 1-3 минуты)...")
    try:
        response = requests.post(url, json=payload, timeout=300)  # 5 минут
        response.raise_for_status()
        return response.json().get("response", "Пустой ответ")
    except requests.exceptions.ReadTimeout:
        return "⏱️  Таймаут! Модель не успела ответить. Попробуйте уменьшить контекст или упростить вопрос."
    except requests.exceptions.RequestException as e:
        return f"❌ Ошибка: {e}"


def show_menu():
    print("\n" + "="*60)
    print("📂 ВЫБЕРИТЕ МОДУЛЬ ДЛЯ АНАЛИЗА:")
    print("="*60)
    for key, (name, _) in PROJECT_MODULES.items():
        print(f"  [{key}] {name}")
    print("\n📄 БОЛЬШИЕ ФАЙЛЫ (анализ по одному):")
    for key, (name, _) in LARGE_FILES.items():
        print(f"  [{key}] {name}")
    print("\n  [Q] Выход")
    print("="*60)


if __name__ == "__main__":
    print("="*60)
    print("🤖 ПОМОЩНИК ДЛЯ ПРОЕКТА LOGOVOBOT (v2)")
    print("="*60)
    
    if not os.path.exists(PROJECT_PATH):
        print(f"❌ Папка не найдена: {PROJECT_PATH}")
        exit()
    
    while True:
        show_menu()
        choice = input("\nВаш выбор: ").strip().upper()
        
        if choice == 'Q':
            print("👋 До свидания!")
            break
        
        context = ""
        module_name = ""
        
        if choice in PROJECT_MODULES:
            module_name, files = PROJECT_MODULES[choice]
            print(f"\n📂 Загрузка: {module_name}")
            if files is None:
                context = read_all_small_files()
            else:
                context = read_files(files)
        elif choice in LARGE_FILES:
            module_name, file_name = LARGE_FILES[choice]
            print(f"\n Загрузка большого файла: {file_name}")
            context = read_files([file_name])
        else:
            print("❌ Неверный выбор")
            continue
        
        if not context:
            print("⚠️  Не удалось загрузить файлы")
            continue
        
        print(f"\n💡 Модуль '{module_name}' загружен. Задавайте вопросы.")
        print("   Примеры: 'Найди баги', 'Как улучшить?', 'Объясни код'")
        print("   [BACK] — вернуться к меню\n")
        
        while True:
            user_input = input("Ваш запрос: ").strip()
            
            if not user_input:
                continue
            if user_input.upper() in ['BACK', 'НАЗАД', 'B']:
                break
            if user_input.lower() in ['выход', 'exit', 'quit', 'q']:
                print("👋 До свидания!")
                exit()
            
            result = ask_ai(user_input, context)
            
            print("\n" + "─"*60)
            print("🤖 ОТВЕТ:")
            print("─"*60)
            print(result)
            print("─"*60)