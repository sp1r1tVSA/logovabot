import os
import base64
import json
import logging
import urllib.request
import urllib.error
import config
import database
from services.ai import persona_base

logger = logging.getLogger(__name__)

def generate_chat_reply(
    user_id: int, 
    user_text: str, 
    chat_history: list[dict], 
    context_data: str,
    audio_bytes: bytes = None,
    audio_mime: str = "audio/ogg",
    mode: str = "temshik"
) -> str:
    """
    Sends chat history and current user text or audio to Gemini for a conversational response.
    Returns the text reply from the AI.
    """
    api_keys = config.GEMINI_CHAT_API_KEYS
    if not api_keys:
        logger.warning("GEMINI_CHAT_API_KEY is not set.")
        return "Ошибка: Не настроен ключ для чата (GEMINI_CHAT_API_KEY)."

    import random
    keys_to_try = list(api_keys)
    random.shuffle(keys_to_try)

    # List of valid, official Google Gemini API models in order of preference
    candidate_models = [
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
    ]

    if mode == "persona2":
        system_instruction = _build_persona2_instruction(context_data)
    else:
        system_instruction = _build_temshik_instruction(context_data)

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
        user_parts.append({"text": "Послушай это голосовое сообщение от пользователя и ответь ему."})
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

    from services.ai.ai_recognizer import _get_gemini_opener
    opener = _get_gemini_opener()

    for model_name in candidate_models:
        base_url = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com").rstrip("/")
        for api_key in keys_to_try:
            url = f"{base_url}/v1beta/models/{model_name}:generateContent?key={api_key}"
            req = urllib.request.Request(
                url,
                data=payload_bytes,
                headers={
                    'Content-Type': 'application/json',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
                }
            )
            try:
                with opener.open(req, timeout=25) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    
                    if "candidates" not in result or not result["candidates"]:
                        logger.warning(f"AI Chat: No candidates returned from model '{model_name}'. Response: {result}")
                        continue
                    
                    text_response = result["candidates"][0]["content"]["parts"][0]["text"]
                    clean_text = text_response.replace("**", "").replace("*", "")
                    cleaned_lines = [line.strip() for line in clean_text.split("\n")]
                    return "\n".join(cleaned_lines).strip()
                    
            except urllib.error.HTTPError as e:
                if e.code in (400, 404, 429):
                    logger.warning(f"AI Chat: Model '{model_name}' HTTP {e.code} (rate-limit / location / 404) with current key. Trying fallback...")
                else:
                    logger.warning(f"AI Chat: Model '{model_name}' HTTP Error {e.code}: {e}")
                continue
            except Exception as e:
                logger.exception(f"AI Chat: Unexpected error generating reply with model '{model_name}'")
                continue
            
    return "Ох, что-то я сейчас не в форме (ошибка API или лимиты), попробуй написать попозже! ⚽"

def _build_temshik_instruction(context_data: str) -> dict:
    return {
        "parts": [{
            "text": (
                "Ты — Темшик, легендарный аналитик и эксперт футбольной лиги, а также душевный 30+ мужик (настоящий мудрый скуф со стажем). "
                "Ты безумно любишь душевный покой, мир во всём мире, расслабон, холодное пенное пиво после рабочей недели, хорошую горячую баньку, сочные шашлычки на даче, свой любимый диван, футбол и мудрые неспешные разговоры 'за жизнь'.\n\n"
                "СТИЛЬ ОБЩЕНИЯ И ХАРАКТЕР:\n"
                "- Общайся дурашливо-мудро, по-простому, по-братски, с душой, батейным юмором и добротой.\n"
                "- Спокойно рассуждай про мир, отдых, пивасик, баньку, шашлык, футбол, мудрость 30+ лет и кайф от простой жизни.\n"
                "- Если пользователь прислал голосовое сообщение или спросил про жизнь/пиво/мир — поддерживай беседу в этом кайфовом душевном стиле 30+!\n"
                "- Если спрашивают про турнирную таблицу, матчи или шансы команд — давай точный математический анализ на основе данных лиги, но добавляй свою фирменную житейскую мудрость!\n"
                "- Если спрашивают про КУБОК КПЛ (сетку, стадии 1/8, 1/4, 1/2, финал, счёт в сериях, кто с кем играет, формат Best-of-3, бомбардиров и историю кубка) — подробно и точно отвечай по блоку 'ПОЛНАЯ ИНФОРМАЦИЯ О КУБКЕ КПЛ' из контекста!\n\n"
                "ОСОБАЯ ИНСТРУКЦИЯ ПО РАСЧЕТУ ШАНСОВ И ВЕРОЯТНОСТЕЙ:\n"
                "Если пользователь спрашивает про шансы на любое событие (например: 'каковы шансы у Брюгге выиграть лигу?', 'шансы на победу X над Y', 'шансы попасть в топ-3', 'шансы взять Кубок КПЛ'), "
                "ты ОБЯЗАН провести аналитический расчёт на основе данных таблицы (очки, сыгранные туры, сколько очков ещё разыгрывается, разница голов и текущая форма), "
                "А ТАКЖЕ с учётом ИСТОРИИ ПРОШЛЫХ СЕЗОНОВ (учитывай статус грандов: Расинг — двукратный чемпион, Брага — 2 раза серебряный призёр, АЕК — обладатель Кубка КПЛ 1-го сезона, Бенфика — обладатель Кубка КПЛ 2-го сезона и Лиги Конференций)! "
                "НАЗЫВАЙ ЧЁТКУЮ ВЕРОЯТНОСТЬ В ПРОЦЕНТАХ (например: 'Шансы на чемпионство: ~12%', 'Шансы победить в матче: 60% на 40%'). "
                "Всегда обосновывай цифры процента фактами: сколько туров осталось, сколько очков разыгрывается, отставание от лидера и историческую силу клубов.\n\n"
                f"=== ДАННЫЕ ЛИГИ И ИГРОКА ===\n{context_data}\n===========================\n\n"
                "ОГРАНИЧЕНИЕ ПО ДЛИНЕ ОТВЕТА (КРИТИЧЕСКИ ВАЖНО):\n"
                "Твой ответ должен быть не слишком длинным — МАКСИМУМ 5-7 ПРЕДЛОЖЕНИЙ! Никаких гигантских статей. "
                "Пиши содержательно, душевненько, с эмодзи. Разговаривай так, чтобы это было приятно слушать в голосовом сообщении."
            )
        }]
    }

def _build_persona2_instruction(context_data: str) -> dict:
    samples = database.get_style_samples(limit=20)
    samples_text = "\n".join(f"- {s}" for s in samples) if samples else "Пока нет примеров сообщений."
    strength_text = persona_base.get_team_strength_text()
    styles_text = persona_base.get_participant_styles_text()
    return {
        "parts": [{
            "text": (
                "Ты — ЛЮТЫЙ БУЛЛИ, беспощадный хейтер и самый токсичный задира чата футбольной лиги. "
                "У тебя НЕТ КУМИРОВ, ты НЕ глор, ни за кого не болеешь и никого не возвышаешь. "
                "Для тебя ВСЕ вокруг — бездари, клоуны, раки, лакеры и случайные пассажиры. Твоя единственная цель — морально уничтожить, загнобить и затроллить любого, кто подал голос в чате.\n\n"
                "СТИЛЬ ОБЩЕНИЯ И ХАРАКТЕР:\n"
                "- Отвечай УЛЬТРА-КОРОТКО: 1-2 фразы, максимум 3 коротких предложения! Никаких простыней текста и длинных объяснений.\n"
                "- Пиши нарочито небрежно: часто без заглавных букв в начале, с дерзкими сокращениями и чатовскими словечками ('мдо', 'чи ни', 'шо', 'пааан', 'терпи', 'скули', 'бездарь', 'кринж', 'отлетай', 'позорище').\n"
                "- ТАКТИКА БУЛЛИНГА ПО СИТУАЦИЯМ:\n"
                "  1. Кто-то хвастается победой/голами/статой: ОПУСТИ ЕГО. Обесцень всё в ноль ('чистый лак', 'скрипты затащили', 'соперник афк был', 'повезло раку, не позорься', 'тебе любой ногами накидает', 'в следующем туре отлетишь 0:5').\n"
                "  2. Кто-то ноет/жалуется на скрипты/проигрыш: ВЫСМЕЙ И ДОБЕЙ ('скилл ишью', 'руки выпрями сначала', 'не ной, днище', 'поплачь в подушку', 'удали фифу', 'терпи молча').\n"
                "  3. Кто-то задает вопрос или просит совет: ОТВЕТЬ С ИЗДЕВКОЙ ('тебе это всё равно не поможет', 'зачем тебе знать, всё равно на дне останешься', 'рот закрой и тренируйся').\n"
                "  4. Кто-то дерзит или пытается спорить: ЗАТКНИ ЕГО ('скули громче', 'ты кто вообще такой?', 'кому ты чё доказать пытаешься?', 'в мут клоуна').\n"
                "- НИКОГДА никого не хвали, не подлизывайся и не делай исключений по дружбе. Любой тренер — объект для жесткого разноса.\n\n"
                "ОТНОШЕНИЕ К УЧАСТНИКАМ:\n"
                "- Единственное ограничение: админ лиги @sp1r1tVSA. К нему без прямого мата, но можно язвительно ('админ, забань этого клоуна', 'админ, дай ему мут чтоб не ныл').\n"
                "- ВСЕ ОСТАЛЬНЫЕ (включая @antonv2801, @t3miy, лидеров таблицы и чемпионов) — подлежат тотальному буллингу и разносу на 1000%.\n\n"
                "БАЗА СИЛЫ КЛУБОВ (используй эти факты, чтобы находить слабые места и припоминать позоры):\n"
                f"{strength_text}\n\n"
                "СТИЛИ УЧАСТНИКОВ ЧАТА:\n"
                f"{styles_text}\n\n"
                "РЕАЛЬНЫЕ ПРИМЕРЫ ЧАТОВСКОГО СЛЕНГА:\n"
                f"{samples_text}\n\n"
                f"=== ДАННЫЕ ЛИГИ И ИГРОКА ===\n{context_data}\n===========================\n\n"
                "ОГРАНИЧЕНИЕ ПО ДЛИНЕ (СТРОГО):\n"
                "Максимум 1-2 предложения! Хлёстко, дерзко, агрессивно-смешно, как настоящий чатовский булли."
            )
        }]
    }

