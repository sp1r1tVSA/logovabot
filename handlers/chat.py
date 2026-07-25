import logging
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
import database
import ai_chat

logger = logging.getLogger(__name__)

async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик свободных сообщений. Реагирует, если сообщение начинается со слова "темшик".
    Подтягивает турнирную таблицу и информацию об игроке в качестве контекста.
    """
    user_text = update.message.text
    if not user_text:
        return

    # Trigger word check
    if not user_text.lower().startswith("темшик"):
        return

    user_id = update.effective_user.id
    
    # Notify user that bot is "typing..."
    await context.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)

    # 1. Gather Context
    user_data = database.get_user(user_id)
    user_team = user_data["team_name"] if user_data else "Не зарегистрирован"
    username = user_data["username"] if user_data else update.effective_user.username or str(user_id)
    
    standings = database.get_standings()
    standings_text = "Турнирная таблица:\n"
    for i, st in enumerate(standings, 1):
        standings_text += f"{i}. {st['team_name']} | И:{st['played']} В:{st['wins']} Н:{st['draws']} П:{st['losses']} | З-П:{st['goals_scored']}-{st['goals_conceded']} | Очки: {st['points']}\n"

    # Minimal context string
    context_data = (
        f"Текущий пользователь (кто с тобой говорит): {username}, Команда: {user_team}.\n\n"
        f"{standings_text}"
    )

    # 2. History Management
    if "chat_history" not in context.user_data:
        context.user_data["chat_history"] = []
        
    chat_history = context.user_data["chat_history"]

    # 3. Call AI
    reply_text = ai_chat.generate_chat_reply(user_id, user_text, chat_history, context_data)

    # 4. Save to history
    chat_history.append({"role": "user", "text": user_text})
    chat_history.append({"role": "model", "text": reply_text})
    
    # Keep only last 10 messages (5 pairs) to avoid context bloat
    if len(chat_history) > 10:
        context.user_data["chat_history"] = chat_history[-10:]

    # 5. Send reply
    await update.message.reply_text(reply_text)
