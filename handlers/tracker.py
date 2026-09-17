"""
handlers/tracker.py

Команды /tracker и /app — вход в мобильное приложение Logovo Tracker.

Бот выдаёт одноразовый 4-значный ПИН, который приложение меняет на токен
сессии (POST /api/tracker/auth/pair, см. api/routes_tracker.py). Код живёт
10 минут и действует один раз.
"""

import asyncio
import html
import logging

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

import database

logger = logging.getLogger(__name__)


async def tracker_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for /tracker and /app — выдаёт ПИН для входа в приложение."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not user:
        return

    # Код — это по сути пароль на десять минут, в общем чате его показывать нельзя.
    if chat and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
        bot_me = await context.bot.get_me()
        bot_username = bot_me.username or "logovobot"
        await message.reply_text(
            "🔐 Код для приложения выдаётся только в личных сообщениях.\n"
            f"Напишите мне <code>/tracker</code> в личку: @{html.escape(bot_username)}",
            parse_mode="HTML",
        )
        return

    team_name = await asyncio.to_thread(database.get_user_team, user.id)
    if not team_name:
        await message.reply_text(
            "❌ Вы ещё не зарегистрировали клуб.\n"
            "Пройдите регистрацию командой <code>/start</code>, потом возвращайтесь за кодом.",
            parse_mode="HTML",
        )
        return

    # Импорт локальный: api.auth тянет handlers.base, и импорт на уровне модуля
    # замкнул бы цикл handlers -> api -> handlers.
    from api.routes_tracker import issue_pin_code

    try:
        pin_code, ttl_seconds = await asyncio.to_thread(issue_pin_code, user.id)
    except Exception:
        logger.exception("TRACKER: не удалось выдать ПИН пользователю %s", user.id)
        await message.reply_text(
            "⚠️ Не получилось выдать код. Попробуйте ещё раз через минуту.",
            parse_mode="HTML",
        )
        return

    minutes = max(1, ttl_seconds // 60)
    spaced = " ".join(pin_code)

    await message.reply_text(
        "📱 <b>Logovo Tracker</b>\n"
        "<i>Живая трансляция вашего матча</i>\n\n"
        "Ваш код для входа:\n"
        f"<code>{spaced}</code>\n\n"
        f"🏟 Клуб: <b>{html.escape(team_name)}</b>\n"
        f"⏳ Код действует <b>{minutes} мин.</b> и только один раз.\n\n"
        "<b>Что делать:</b>\n"
        "1. Откройте приложение Logovo Tracker на телефоне.\n"
        f"2. Введите код <code>{pin_code}</code> на экране входа.\n"
        "3. Выберите матч из списка и нажмите «Начать трансляцию».\n"
        "4. Запустите FC Mobile — счёт и события уедут зрителям сами.\n\n"
        "🔒 Никому не передавайте код: он открывает доступ к вашему клубу.\n"
        "📋 Протокол матча по-прежнему сдаётся здесь, в боте — трансляция его не заменяет.",
        parse_mode="HTML",
    )
    logger.info("TRACKER: выдан ПИН пользователю %s (клуб «%s»)", user.id, team_name)
