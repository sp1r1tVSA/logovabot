import base64
import json
import logging
import urllib.request
import urllib.error
import config

logger = logging.getLogger(__name__)

def generate_chat_reply(
    user_id: int, 
    user_text: str, 
    chat_history: list[dict], 
    context_data: str,
    audio_bytes: bytes = None,
    audio_mime: str = "audio/ogg"
) -> str:
    """
    Sends chat history and current user text or audio to Gemini for a conversational response.
    Returns the text reply from the AI.
    """
    api_key = config.GEMINI_CHAT_API_KEY
    if not api_key:
        logger.warning("GEMINI_CHAT_API_KEY is not set.")
        return "Ошибка: Не настроен ключ для чата (GEMINI_CHAT_API_KEY)."

    # List of valid, official Google Gemini API models in order of preference
    candidate_models = [
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-flash-latest",
        "gemini-flash-lite-latest",
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
    ]

    system_instruction = {
        "parts": [{
            "text": (
                "Ты — Темшик, легендарный аналитик и эксперт футбольной лиги, а также душевный 30+ мужик (настоящий мудрый скуф со стажем). "
                "Ты безумно любишь душевный покой, мир во всём мире, расслабон, холодное пенное пиво после рабочей недели, хорошую горячую баньку, сочные шашлычки на даче, свой любимый диван, футбол и мудрые неспешные разговоры 'за жизнь'.\n\n"
                "СТИЛЬ ОБЩЕНИЯ И ХАРАКТЕР:\n"
                "- Общайся дурашливо-мудро, по-простому, по-братски, с душой, батейным юмором и добротой.\n"
                "- Спокойно рассуждай про мир, отдых, пивасик, баньку, шашлык, футбол, мудрость 30+ лет и кайф от простой жизни.\n"
                "- Если пользователь прислал голосовое сообщение или спросил про жизнь/пиво/мир — поддерживай беседу в этом кайфовом душевном стиле 30+!\n"
                "- Если спрашивают про турнирную таблицу, матчи или шансы команд — давай точный математический анализ на основе данных лиги, но добавляй свою фирменную житейскую мудрость!\n\n"
                "ОСОБАЯ ИНСТРУКЦИЯ ПО РАСЧЕТУ ШАНСОВ И ВЕРОЯТНОСТЕЙ:\n"
                "Если пользователь спрашивает про шансы на любое событие (например: 'каковы шансы у Брюгге выиграть лигу?', 'шансы на победу X над Y', 'шансы попасть в топ-3'), "
                "ты ОБЯЗАН провести аналитический расчёт на основе данных таблицы (очки, сыгранные туры, сколько очков ещё разыгрывается, разница голов и текущая форма), "
                "А ТАКЖЕ с учётом ИСТОРИИ ПРОШЛЫХ СЕЗОНОВ (учитывай статус грандов: Расинг — двукратный чемпион, Брага — 2 раза серебряный призёр, АЕК — 2 раза бронзовый призёр, Бенфика — обладатель Кубка КПЛ и Лиги Конференций)! "
                "НАЗЫВАЙ ЧЁТКУЮ ВЕРОЯТНОСТЬ В ПРОЦЕНТАХ (например: 'Шансы на чемпионство: ~12%', 'Шансы победить в матче: 60% на 40%'). "
                "Всегда обосновывай цифры процента фактами: сколько туров осталось, сколько очков разыгрывается, отставание от лидера и историческую силу клубов.\n\n"
                f"=== ДАННЫЕ ЛИГИ И ИГРОКА ===\n{context_data}\n===========================\n\n"
                "ОГРАНИЧЕНИЕ ПО ДЛИНЕ ОТВЕТА (КРИТИЧЕСКИ ВАЖНО):\n"
                "Твой ответ должен быть не слишком длинным — МАКСИМУМ 5-7 ПРЕДЛОЖЕНИЙ! Никаких гигантских статей. "
                "Пиши содержательно, душевненько, с эмодзи. Разговаривай так, чтобы это было приятно слушать в голосовом сообщении."
            )
        }]
    }

    contents = []
    for msg in chat_history:
        contents.append({
            "role": msg["role"],
            "parts": [{"text": msg["text"]}]
        })
    
    user_parts = []
    if audio_bytes:
        user_parts.append({
            "inline_data": {
                "mime_type": audio_mime,
                "data": base64.b64encode(audio_bytes).decode('utf-8')
            }
        })
        user_parts.append({"text": "Послушай это голосовое сообщение от пользователя и ответь ему как Темшик."})
    else:
        user_parts.append({"text": user_text})

    contents.append({
        "role": "user",
        "parts": user_parts
    })

    payload = {
        "system_instruction": system_instruction,
        "contents": contents,
        "generationConfig": {
            "temperature": 0.8,
            "maxOutputTokens": 500,
        }
    }

    
    payload_bytes = json.dumps(payload).encode('utf-8')

    for model_name in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        req = urllib.request.Request(url, data=payload_bytes, headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                result = json.loads(response.read().decode('utf-8'))
                
                if "candidates" not in result or not result["candidates"]:
                    logger.warning(f"AI Chat: No candidates returned from model '{model_name}'. Response: {result}")
                    continue
                
                text_response = result["candidates"][0]["content"]["parts"][0]["text"]
                clean_text = text_response.replace("**", "").replace("*", "")
                cleaned_lines = [line.strip() for line in clean_text.split("\n")]
                return "\n".join(cleaned_lines).strip()
                
        except urllib.error.HTTPError as e:
            if e.code == 429:
                logger.warning(f"AI Chat: Model '{model_name}' rate-limited (429). Trying fallback model...")
            elif e.code == 404:
                logger.warning(f"AI Chat: Model '{model_name}' not found (404). Trying fallback model...")
            else:
                logger.warning(f"AI Chat: Model '{model_name}' HTTP Error {e.code}: {e}")
            continue
        except Exception as e:
            logger.exception(f"AI Chat: Unexpected error generating reply with model '{model_name}'")
            continue
            
    return "Ох, что-то я сейчас не в форме (ошибка API или лимиты), попробуй написать попозже! ⚽"
