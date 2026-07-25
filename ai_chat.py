import json
import logging
import urllib.request
import urllib.error
import config

logger = logging.getLogger(__name__)

def generate_chat_reply(user_id: int, user_text: str, chat_history: list[dict], context_data: str) -> str:
    """
    Sends chat history and current user text to Gemini for a conversational response.
    Returns the text reply from the AI.
    """
    api_key = config.GEMINI_CHAT_API_KEY
    if not api_key:
        logger.error("GEMINI_CHAT_API_KEY is not set.")
        return "Ошибка: Не настроен ключ для чата (GEMINI_CHAT_API_KEY)."

    # We will use the same flash-lite model, or fallback to standard flash.
    candidate_models = [
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash-lite",
        "gemini-1.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    ]

    # Build the conversation payload
    # Add the system instruction with the context
    system_instruction = {
        "parts": [{
            "text": (
                "Ты — аналитик, математик и эксперт-комментатор футбольной лиги по FIFA / EA FC по имени Темшик. "
                "Ты можешь общаться с игроками на любые темы, шутить и поддерживать диалог о реальном или виртуальном футболе.\n\n"
                "ОСОБАЯ ИНСТРУКЦИЯ ПО РАСЧЕТУ ШАНСОВ И ВЕРОЯТНОСТЕЙ:\n"
                "Если пользователь спрашивает про шансы на любое событие (например: 'каковы шансы у Брюгге выиграть лигу?', 'шансы на победу X над Y', 'шансы попасть в топ-3'), "
                "ты ОБЯЗАН провести аналитический расчёт на основе данных таблицы (очки, сыгранные туры, сколько очков ещё разыгрывается, разница голов и текущая форма), "
                "А ТАКЖЕ с учётом ИСТОРИИ ПРОШЛЫХ СЕЗОНОВ (учитывай статус грандов: Расинг — двукратный чемпион, Брага — 2 раза серебряный призёр, АЕК — 2 раза бронзовый призёр, Бенфика — обладатель Кубка КПЛ и Лиги Конференций)! "
                "НАЗЫВАЙ ЧЁТКУЮ ВЕРОЯТНОСТЬ В ПРОЦЕНТАХ (например: 'Шансы на чемпионство: ~12%', 'Шансы победить в матче: 60% на 40%'). "
                "Всегда обосновывай цифры процента фактами: сколько туров осталось, сколько очков разыгрывается, отставание от лидера и историческую силу клубов.\n\n"
                f"=== ДАННЫЕ ЛИГИ И ИГРОКА ===\n{context_data}\n===========================\n\n"
                "ЗНАНИЕ РЕГЛАМЕНТА И ПРАВИЛ:\n"
                "У тебя есть полный регламент турнира (запрещённые финты и навесы, судья @onvamneVSAplayer, главные админы, лимиты тренировок до 20, максимум 6 спешл-карт, правила каналов клуба и т.д.). "
                "Если пользователь спрашивает про правила, нарушения, контакты судьи или прокачку — отвечай строго по регламенту из блока данных!\n\n"
                "ОГРАНИЧЕНИЕ ПО ДЛИНЕ ОТВЕТА (КРИТИЧЕСКИ ВАЖНО):\n"
                "Твой ответ должен быть не слишком длинным — МАКСИМУМ 7-8 ПРЕДЛОЖЕНИЙ! Никаких гигантских статей. "
                "Пиши содержательно, динамично, с эмодзи. Если спрашивают про шансы — давай цифру процентов и краткое фактологическое обоснование."
            )
        }]
    }

    # Format history for Gemini API: roles can be "user" or "model"
    contents = []
    for msg in chat_history:
        contents.append({
            "role": msg["role"],
            "parts": [{"text": msg["text"]}]
        })
    
    # Add current user message
    contents.append({
        "role": "user",
        "parts": [{"text": user_text}]
    })

    payload = {
        "system_instruction": system_instruction,
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 500,
        }
    }
    
    payload_bytes = json.dumps(payload).encode('utf-8')

    for model_name in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        req = urllib.request.Request(url, data=payload_bytes, headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))
                
                # Check for prompt feedback block if output is empty
                if "candidates" not in result or not result["candidates"]:
                    logger.warning(f"AI Chat: No candidates returned from {model_name}. Response: {result}")
                    continue
                
                text_response = result["candidates"][0]["content"]["parts"][0]["text"]
                # Clean any asterisks from markdown
                clean_text = text_response.replace("**", "").replace("*", "")
                cleaned_lines = [line.strip() for line in clean_text.split("\n")]
                return "\n".join(cleaned_lines).strip()
                
        except urllib.error.HTTPError as e:
            logger.warning(f"AI Chat: Model {model_name} HTTP Error {e.code}: {e.reason}")
            continue
        except Exception as e:
            logger.error(f"AI Chat: Error generating reply with {model_name}: {e}")
            continue
            
    return "Ох, что-то я сейчас не в форме (ошибка API или лимиты), попробуй написать попозже! ⚽"
