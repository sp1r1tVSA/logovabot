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
                "Ты — дружелюбный и общительный ИИ-комментатор/помощник футбольной лиги по FIFA / EA FC. "
                "Ты можешь общаться с игроками на любые темы, шутить и поддерживать диалог о реальном или виртуальном футболе, "
                "но если тебя спрашивают про турнир, используй следующие данные:\n\n"
                f"=== ДАННЫЕ ЛИГИ И ИГРОКА ===\n{context_data}\n===========================\n\n"
                "Отвечай коротко, дружелюбно, можно использовать эмодзи. Не пиши слишком длинные полотна текста, если тебя не просят."
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
                return text_response.strip()
                
        except urllib.error.HTTPError as e:
            logger.warning(f"AI Chat: Model {model_name} HTTP Error {e.code}: {e.reason}")
            continue
        except Exception as e:
            logger.error(f"AI Chat: Error generating reply with {model_name}: {e}")
            continue
            
    return "Ох, что-то я сейчас не в форме (ошибка API или лимиты), попробуй написать попозже! ⚽"
