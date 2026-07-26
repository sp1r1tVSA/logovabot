import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.ext import ContextTypes
import html
import asyncio
import database
import logging
from config import ADMIN_IDS, CLUBS
from table_generator import generate_league_table_image
from constants import (
    CB_MAIN_MENU, CB_MENU_CABINET, CB_MENU_TOURNAMENTS,
    CB_MENU_LEAGUE, CB_MENU_SUPPORT, CB_LEAGUE_TABLE,
    CB_LEAGUE_SCORERS, CB_LEAGUE_ASSISTS, CB_REFRESH_LEAGUE_TABLE,
    CB_ADMIN_MAIN_MENU
)

logger = logging.getLogger(__name__)

def is_admin(telegram_id: int) -> bool:
    """Check if the user is in the configured Admin IDs."""
    return telegram_id in ADMIN_IDS

from functools import wraps
from telegram.ext import ConversationHandler

def admin_only(func):
    """Decorator to enforce admin permissions and answer CallbackQuery early."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        query = update.callback_query
        user_id = user.id if user else (query.from_user.id if query else None)

        if not user_id or not is_admin(user_id):
            if query:
                try:
                    await query.answer("⛔ Доступ запрещён", show_alert=True)
                except Exception:
                    pass
            elif update.message:
                try:
                    await update.message.reply_text("❌ У вас нет прав доступа к этой панели.")
                except Exception:
                    pass
            return ConversationHandler.END

        if query:
            try:
                await query.answer()
            except Exception:
                pass

        return await func(update, context, *args, **kwargs)
    return wrapper

def get_main_inline_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    """Generate main InlineKeyboardMarkup based on the user's role (matched to screenshot)."""
    keyboard = [
        [InlineKeyboardButton("👤 Мой Кабинет", callback_data=CB_MENU_CABINET)],
        [InlineKeyboardButton("🏆 Турниры", callback_data=CB_MENU_TOURNAMENTS)]
    ]
    if is_admin(telegram_id):
        keyboard.append([InlineKeyboardButton("👑 Админ-панель", callback_data=CB_ADMIN_MAIN_MENU)])
        
    keyboard.extend([
        [InlineKeyboardButton("🏆 Лига", callback_data=CB_MENU_LEAGUE)],
        [InlineKeyboardButton("🆘 Поддержка", callback_data=CB_MENU_SUPPORT)]
    ])
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start command: welcomes user and displays inline main menu. Private chats only."""
    if update.effective_chat.type != "private":
        return  # Ignore group chats

    user = update.effective_user
    if not user:
        return

    # Clear old text keyboards if they are stuck
    temp_msg = await update.message.reply_text("🔄 Загрузка...", reply_markup=ReplyKeyboardRemove())
    try:
        await temp_msg.delete()
    except Exception:
        pass

    # Determine role and upsert/match user
    role = "admin" if is_admin(user.id) else "user"
    await asyncio.to_thread(database.handle_user_startup, user.id, user.username, role)

    # Deliver pending notification if exists
    if await asyncio.to_thread(database.get_pending_notification, user.id):
        team = await asyncio.to_thread(database.get_user_team, user.id)
        if team:
            try:
                await update.message.reply_text(
                    f"🎉 Организатор закрепил за вашим аккаунтом игровой клуб <b>{html.escape(team)}</b>! "
                    f"Теперь вам доступен Личный кабинет и участие в лиге.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            await asyncio.to_thread(database.set_pending_notification, user.id, 0)

    welcome_text = (
        f"⚽️ <b>Добро пожаловать в систему Лиги, {user.first_name}!</b>\n\n"
        f"🏆 Здесь вы можете управлять своей карьерой, следить за турнирной таблицей и статистикой клубов.\n\n"
        f"👇 Выберите нужный раздел в меню ниже:"
    )
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_main_inline_keyboard(user.id),
        parse_mode="HTML"
    )

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback query handler to display the main inline menu."""
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception:
        pass

    # Clear lingering FSM temporary data when returning to main menu
    context.user_data.clear()

    user = query.from_user
    welcome_text = (
        f"⚽️ <b>Добро пожаловать в систему Лиги, {user.first_name}!</b>\n\n"
        f"🏆 Здесь вы можете управлять своей карьерой, следить за турнирной таблицей и статистикой клубов.\n\n"
        f"👇 Выберите нужный раздел в меню ниже:"
    )
    if query.message and query.message.photo:
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=user.id, text=welcome_text, reply_markup=get_main_inline_keyboard(user.id), parse_mode="HTML")
    else:
        try:
            await query.edit_message_text(welcome_text, reply_markup=get_main_inline_keyboard(user.id), parse_mode="HTML")
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=user.id, text=welcome_text, reply_markup=get_main_inline_keyboard(user.id), parse_mode="HTML")

async def show_league_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sub-menu for League section: Table, Top Scorers, Top Assists."""
    query = update.callback_query
    if query:
        try:
            await query.answer()
        except Exception:
            pass

    text = (
        "🏆 <b>Раздел «Лига»</b>\n\n"
        "Выберите интересующий вас раздел:"
    )

    keyboard = [
        [InlineKeyboardButton("📊 Таблица лиги", callback_data="league_table")],
        [InlineKeyboardButton("⚽ Бомбардиры", callback_data="league_scorers")],
        [InlineKeyboardButton("🎯 Ассисты", callback_data="league_assists")],
        [InlineKeyboardButton("« Назад в меню", callback_data="main_menu")]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if query:
        if query.message and query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=markup, parse_mode="HTML")
        else:
            try:
                await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            except Exception:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=markup, parse_mode="HTML")
    elif update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")

async def show_top_scorers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show Top 20 goalscorers leaderboard."""
    query = update.callback_query
    if query:
        try:
            await query.answer()
        except Exception:
            pass

    scorers = await asyncio.to_thread(database.get_top_scorers, limit=20)
    
    text = "⚽ <b>ТОП БОМБАРДИРОВ ЛИГИ</b>\n\n"
    if not scorers:
        text += "<i>Пока нет забитых голов в турнире.</i>"
    else:
        medals = ["🥇", "🥈", "🥉"]
        for idx, row in enumerate(scorers, 1):
            rank = medals[idx - 1] if idx <= 3 else f"<b>{idx}.</b>"
            p_name = html.escape(str(row['player_name']))
            t_name = html.escape(str(row['team_name']))
            goals = row['total_goals']
            text += f"{rank} <b>{p_name}</b> ({t_name}) — <b>{goals}</b> ⚽\n"

    keyboard = [
        [InlineKeyboardButton("🎯 Перейти к Ассистам", callback_data="league_assists")],
        [InlineKeyboardButton("« Назад в раздел «Лига»", callback_data="menu_league")]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if query:
        if query.message and query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=markup, parse_mode="HTML")
        else:
            try:
                await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            except Exception:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=markup, parse_mode="HTML")
    elif update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")

async def show_top_assists(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show Top 20 assist providers leaderboard."""
    query = update.callback_query
    if query:
        try:
            await query.answer()
        except Exception:
            pass

    assists = await asyncio.to_thread(database.get_top_assists, limit=20)
    
    text = "🎯 <b>ТОП АССИСТЕНТОВ ЛИГИ</b>\n\n"
    if not assists:
        text += "<i>Пока нет голевых передач в турнире.</i>"
    else:
        medals = ["🥇", "🥈", "🥉"]
        for idx, row in enumerate(assists, 1):
            rank = medals[idx - 1] if idx <= 3 else f"<b>{idx}.</b>"
            p_name = html.escape(str(row['player_name']))
            t_name = html.escape(str(row['team_name']))
            ast = row['total_assists']
            text += f"{rank} <b>{p_name}</b> ({t_name}) — <b>{ast}</b> 🎯\n"

    keyboard = [
        [InlineKeyboardButton("⚽ Перейти к Бомбардирам", callback_data="league_scorers")],
        [InlineKeyboardButton("« Назад в раздел «Лига»", callback_data="menu_league")]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if query:
        if query.message and query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=markup, parse_mode="HTML")
        else:
            try:
                await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
            except Exception:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=markup, parse_mode="HTML")
    elif update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")

async def show_tournaments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()

    rounds = await asyncio.to_thread(database.get_all_rounds)
    
    keyboard = []
    if not rounds:
        text = "🏆 **Турниры**\n\nРасписание еще не сформировано."
    else:
        text = "🏆 **Турниры**\n\nВыберите тур для просмотра расписания:"
        row = []
        for r in rounds:
            row.append(InlineKeyboardButton(f"{r} Тур", callback_data=f"show_round_matches_{r}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
            
    keyboard.append([InlineKeyboardButton("« Назад в меню", callback_data="main_menu")])
    markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=query.from_user.id, text=text, parse_mode="Markdown", reply_markup=markup)
    elif update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)

async def show_round_matches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    
    round_number = int(query.data.replace("show_round_matches_", ""))
    matches, info = await asyncio.gather(
        asyncio.to_thread(database.get_matches_by_round, round_number),
        asyncio.to_thread(database.get_round_info, round_number)
    )
    
    text = f"📅 **Расписание: {round_number}-й Тур**\n"
    if info:
        is_open = info["is_open"]
        deadline_text = info["deadline"]
        
        if is_open and deadline_text:
            try:
                dt = datetime.datetime.strptime(deadline_text, "%d.%m.%Y %H:%M")
                if datetime.datetime.now() > dt:
                    text += "Статус: 🔴 Дедлайн истек (результаты принимаются только администратором)\n"
                else:
                    text += f"Статус: 🟢 Открыт\nДедлайн: {deadline_text}\n"
            except ValueError:
                text += f"Статус: 🟢 Открыт\nДедлайн: {deadline_text}\n"
        else:
            text += f"Статус: {'🟢 Открыт' if is_open else '🔴 Закрыт'}\n"
    text += "\n"
    
    for m in matches:
        p1 = m["player1_team"] or m["player1_nickname"]
        p2 = m["player2_team"] or m["player2_nickname"]
        if m["status"] == "confirmed":
            text += f"*{p1}* {m['player1_score']} : {m['player2_score']} *{p2}*\n_Статус: ✅ Завершен_\n\n"
        else:
            text += f"*{p1}* 🆚 *{p2}*\n_Статус: ⏳ Ожидается игра_\n\n"
            
    if not matches:
        text += "Матчи не найдены."
        
    keyboard = [[InlineKeyboardButton("« Назад к турам", callback_data="menu_tournaments")]]
    markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)

async def group_table_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /table command in any chat — send graphic league standings."""
    img_buf = await asyncio.to_thread(generate_league_table_image)
    caption = "🏆 <b>Турнирная таблица лиги КПЛ 2026</b>"
    keyboard = [[InlineKeyboardButton("🔄 Обновить", callback_data="refresh_league_table_topic")]]
    await update.message.reply_photo(photo=img_buf, caption=caption, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_league_table(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show graphic league standings table via inline button / PM."""
    query = update.callback_query
    if query:
        await query.answer()

    img_buf = await asyncio.to_thread(generate_league_table_image)
    caption = "🏆 <b>Турнирная таблица лиги КПЛ 2026</b>"
    keyboard = [[InlineKeyboardButton("« Назад в меню", callback_data="main_menu")]]
    markup = InlineKeyboardMarkup(keyboard)

    if query:
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_photo(chat_id=query.from_user.id, photo=img_buf, caption=caption, parse_mode="HTML", reply_markup=markup)
    elif update.message:
        await update.message.reply_photo(photo=img_buf, caption=caption, parse_mode="HTML", reply_markup=markup)

async def format_league_table_text() -> str:
    standings = await asyncio.to_thread(database.get_standings)
    if not standings:
        return "📊 <b>Таблица лиги пуста — ещё нет данных.</b>"

    lines = ["🏆 <b>ТЕКУЩАЯ ТУРНИРНАЯ ТАБЛИЦА ЛИГИ:</b>\n"]
    for i, s in enumerate(standings, 1):
        team = html.escape(s.get('team_name', '—'))
        p = s.get('points', 0)
        w = s.get('wins', 0)
        d = s.get('draws', 0)
        l = s.get('losses', 0)
        gf = s.get('goals_scored', 0)
        ga = s.get('goals_conceded', 0)
        
        lines.append(f"{i}. <b>{team}</b> — {p} очк. (И: {w+d+l}, В: {w}, Н: {d}, П: {l}, ЗГ: {gf}, ПГ: {ga})")

    return "\n".join(lines)

async def post_league_table_to_reports(context: ContextTypes.DEFAULT_TYPE) -> None:
    reports_topic_id, group_id = await asyncio.gather(
        asyncio.to_thread(database.get_config, "reports_topic_id"),
        asyncio.to_thread(database.get_group_id)
    )
    if not group_id:
        return

    img_buf = await asyncio.to_thread(generate_league_table_image)
    caption = "🏆 <b>ТЕКУЩАЯ ТУРНИРНАЯ ТАБЛИЦА ЛИГИ</b>"
    keyboard = [[InlineKeyboardButton("🔄 Обновить таблицу", callback_data="refresh_league_table_topic")]]
    markup = InlineKeyboardMarkup(keyboard)

    try:
        kwargs = {"chat_id": group_id, "photo": img_buf, "caption": caption, "parse_mode": "HTML", "reply_markup": markup}
        if reports_topic_id:
            kwargs["message_thread_id"] = int(reports_topic_id)
        await context.bot.send_photo(**kwargs)
    except Exception as e:
        logger.exception("Failed to post graphic league table to reports topic")

async def cb_refresh_league_table_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer("🔄 Таблица обновлена!")

    img_buf = await asyncio.to_thread(generate_league_table_image)
    caption = "🏆 <b>ТЕКУЩАЯ ТУРНИРНАЯ ТАБЛИЦА ЛИГИ</b>"
    keyboard = [[InlineKeyboardButton("🔄 Обновить таблицу", callback_data="refresh_league_table_topic")]]
    markup = InlineKeyboardMarkup(keyboard)

    try:
        from telegram import InputMediaPhoto
        await query.edit_message_media(
            media=InputMediaPhoto(media=img_buf, caption=caption, parse_mode="HTML"),
            reply_markup=markup
        )
    except Exception as e:
        logger.exception("Failed to refresh graphic table")

async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    keyboard = [[InlineKeyboardButton("« Назад в меню", callback_data="main_menu")]]
    markup = InlineKeyboardMarkup(keyboard)
    text = "🚧 **В разработке**\n\nРаздел поддержки находится в разработке."
    if query:
        await query.answer()
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
    elif update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
