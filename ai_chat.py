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
                "ты ОБЯЗАН провести аналитический расчёт на основе данных таблицы (очки, сыгранные туры, сколько очков ещё разыгрывается, разница голов и текущая форма) "
                "и НАЗВАТЬ ЧЁТКУЮ ВЕРОЯТНОСТЬ В ПРОЦЕНТАХ (например: 'Шансы на чемпионство: ~12%', 'Шансы победить в матче: 60% на 40%'). "
                "Всегда обосновывай цифры процента фактами: сколько туров осталось, сколько очков разыгрывается и какое отставание от лидера.\n\n"
                f"=== ДАННЫЕ ЛИГИ И ИГРОКА ===\n{context_data}\n===========================\n\n"
                "СТРОГОЕ ПРАВИЛО ОФОРМЛЕНИЯ: НИКОГДА не используй звёздочки (*) и двойные звёздочки (**) для оформления! "
                "Не используй markdown-выделение со звёздочками. Для списков и выделения используй эмодзи или просто чистый текст без символов '*'. "
                "Отвечай коротко, аналитично, дружелюбно, всегда называй вероятности в процентах, если спрашивают про шансы!"
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
            "maxOutputTokens": 800,
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
