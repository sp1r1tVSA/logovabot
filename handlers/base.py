import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.ext import ContextTypes
import html
import asyncio
import database
import logging
from config import ADMIN_IDS, CLUBS
from table_generator import generate_league_table_image
import top_stats_generator
from constants import (
    CB_MAIN_MENU, CB_MENU_CABINET, CB_MENU_TOURNAMENTS,
    CB_MENU_LEAGUE, CB_MENU_SUPPORT, CB_LEAGUE_TABLE,
    CB_LEAGUE_SCORERS, CB_LEAGUE_ASSISTS, CB_REFRESH_LEAGUE_TABLE,
    CB_ADMIN_MAIN_MENU
)

logger = logging.getLogger(__name__)

def is_admin(telegram_id: int) -> bool:
    """Check if the user is in the configured Admin IDs or has admin role in database."""
    if not telegram_id:
        return False
    if telegram_id in ADMIN_IDS:
        return True
    try:
        user = database.get_user(telegram_id)
        if user and user.get("role") == "admin":
            return True
    except Exception:
        pass
    return False


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
        [InlineKeyboardButton("🖼 Графика (с фото)", callback_data="img_top_scorers")],
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
        [InlineKeyboardButton("🖼 Графика (с фото)", callback_data="img_top_assisters")],
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


async def send_top_scorers_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and send PNG graphics card for Top Scorers with player photos."""
    query = update.callback_query
    if query:
        await query.answer()

    buf = await asyncio.to_thread(top_stats_generator.generate_top_stats_image, "goals", 10)

    keyboard = [
        [InlineKeyboardButton("🎯 Ассистенты (Графика)", callback_data="img_top_assisters")],
        [InlineKeyboardButton("⚽ К списку бомбардиров", callback_data="league_scorers")],
        [InlineKeyboardButton("« Раздел «Лига»", callback_data="menu_league")]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if query and query.message:
        try:
            await query.message.delete()
        except Exception:
            pass

    await context.bot.send_photo(
        chat_id=update.effective_user.id,
        photo=buf,
        caption="<b>⚽ ТОП БОМБАРДИРОВ КПЛ 2026</b>",
        parse_mode="HTML",
        reply_markup=markup
    )


async def send_top_assisters_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and send PNG graphics card for Top Assisters with player photos."""
    query = update.callback_query
    if query:
        await query.answer()

    buf = await asyncio.to_thread(top_stats_generator.generate_top_stats_image, "assists", 10)

    keyboard = [
        [InlineKeyboardButton("⚽ Бомбардиры (Графика)", callback_data="img_top_scorers")],
        [InlineKeyboardButton("🎯 К списку ассистентов", callback_data="league_assists")],
        [InlineKeyboardButton("« Раздел «Лига»", callback_data="menu_league")]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if query and query.message:
        try:
            await query.message.delete()
        except Exception:
            pass

    await context.bot.send_photo(
        chat_id=update.effective_user.id,
        photo=buf,
        caption="<b>🎯 ТОП АССИСТЕНТОВ КПЛ 2026</b>",
        parse_mode="HTML",
        reply_markup=markup
    )

async def show_tournaments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()

    text = (
        "🏆 <b>Турниры КПЛ 2026</b>\n\n"
        "Выберите интересующий соревновательный раздел:"
    )

    keyboard = [
        [InlineKeyboardButton("⚽ Чемпионат КПЛ (Лига)", callback_data="tournaments_league_rounds")],
        [InlineKeyboardButton("🏆 Кубок КПЛ (Плей-офф Best-of-3)", callback_data="tournaments_cup_menu")],
        [InlineKeyboardButton("« Назад в меню", callback_data="main_menu")]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if query:
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=query.from_user.id, text=text, parse_mode="HTML", reply_markup=markup)
    elif update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)

async def show_league_rounds(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()

    rounds = await asyncio.to_thread(database.get_all_rounds)

    keyboard = []
    if not rounds:
        text = "⚽ <b>Чемпионат КПЛ</b>\n\nРасписание туров еще не сформировано."
    else:
        text = "⚽ <b>Чемпионат КПЛ</b>\n\nВыберите тур для просмотра расписания:"
        row = []
        for r in rounds:
            row.append(InlineKeyboardButton(f"{r} Тур", callback_data=f"show_round_matches_{r}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    keyboard.append([InlineKeyboardButton("« Назад к турнирам", callback_data="menu_tournaments")])
    markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)

async def show_cup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()

    stage = "1/8"
    if query and query.data.startswith("show_cup_stage_"):
        stage = query.data.replace("show_cup_stage_", "")

    series_list = await asyncio.to_thread(database.get_cup_series_list, stage)

    stage_title_map = {
        '1/8': '1/8 Финала',
        '1/4': '1/4 Финала',
        '1/2': '1/2 Финала (Полуфинал)',
        'final': '🏆 ФИНАЛ КУБКА КПЛ'
    }
    title = stage_title_map.get(stage, stage)

    text = f"🏆 <b>КУБОК КПЛ | {title}</b>\n"
    text += f"<i>Формат: Серии до 2-х побед (Best-of-3)</i>\n\n"

    if not series_list:
        text += "Матчи данной стадии пока не сформированы."
    else:
        for s in series_list:
            t1 = html.escape(s['team1_name'])
            t2 = html.escape(s['team2_name'])
            w1 = s['team1_wins']
            w2 = s['team2_wins']
            s_num = s['series_num']

            if s['status'] == 'completed':
                text += f"⚔️ <b>Серия {s_num}:</b> <b>{t1}</b> ({w1}) 🆚 ({w2}) <b>{t2}</b>\n"
                text += f"   🏆 <b>Победитель серии:</b> <b>{html.escape(s['winner_name'])}</b>\n\n"
            else:
                text += f"⚔️ <b>Серия {s_num}:</b> <b>{t1}</b> ({w1}) 🆚 ({w2}) <b>{t2}</b>\n"
                matches = s.get("matches", [])
                for m in matches:
                    g_num = m['game_num_in_series']
                    p1 = html.escape(m['player1_team'] or m['player1_nickname'] or t1)
                    p2 = html.escape(m['player2_team'] or m['player2_nickname'] or t2)
                    if m['status'] == 'confirmed':
                        text += f"   • Игра {g_num}: {p1} {m['player1_score']} : {m['player2_score']} {p2} ✅\n"
                    else:
                        text += f"   • Игра {g_num}: {p1} 🆚 {p2} ⏳ (Ожидается)\n"
                text += "\n"

    keyboard = [
        [
            InlineKeyboardButton("1/8 Финала", callback_data="show_cup_stage_1/8"),
            InlineKeyboardButton("1/4 Финала", callback_data="show_cup_stage_1/4"),
        ],
        [
            InlineKeyboardButton("1/2 Финала", callback_data="show_cup_stage_1/2"),
            InlineKeyboardButton("🏆 Финал", callback_data="show_cup_stage_final"),
        ],
        [InlineKeyboardButton("🖼 Графическая сетка", callback_data=f"show_cup_graphic_{stage}")],
        [InlineKeyboardButton("📊 Бомбардиры и Ассистенты Кубка", callback_data="show_cup_stats")],
        [InlineKeyboardButton("« Назад к турнирам", callback_data="menu_tournaments")]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)

async def cb_show_cup_graphic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    stage = "1/8"
    if query.data.startswith("show_cup_graphic_"):
        stage = query.data.replace("show_cup_graphic_", "")

    from table_generator import generate_cup_bracket_image
    img_buf = await asyncio.to_thread(generate_cup_bracket_image, stage)

    from telegram import InputFile
    stage_title_map = {'1/8': '1/8 Финала', '1/4': '1/4 Финала', '1/2': '1/2 Финала', 'final': '🏆 Финал'}
    title = stage_title_map.get(stage, stage)

    await query.message.reply_photo(
        photo=InputFile(img_buf, filename=f"cup_bracket_{stage}.png"),
        caption=f"🏆 <b>КУБОК КПЛ 2026 | {title}</b>\n<i>Графическая сетка турнира</i>",
        parse_mode="HTML"
    )

async def show_cup_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()

    top_goals, top_assists = await asyncio.gather(
        asyncio.to_thread(database.get_cup_top_scorers, 10),
        asyncio.to_thread(database.get_cup_top_assists, 10)
    )

    text = "🏆 <b>СТАТИСТИКА КУБКА КПЛ 2026</b>\n\n"

    text += "⚽ <b>ТОП-10 БОМБАРДИРОВ КУБКА:</b>\n"
    if not top_goals:
        text += "<i>Пока нет забитых голов в кубке.</i>\n\n"
    else:
        for idx, item in enumerate(top_goals, 1):
            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
            prefix = medals.get(idx, f"<b>{idx}.</b>")
            text += f"{prefix} <b>{html.escape(item['player_name'])}</b> ({html.escape(item['team_name'])}) — <b>{item['total_goals']}</b> ⚽\n"
        text += "\n"

    text += "🎯 <b>ТОП-10 АССИСТЕНТОВ КУБКА:</b>\n"
    if not top_assists:
        text += "<i>Пока нет голевых передач в кубке.</i>\n\n"
    else:
        for idx, item in enumerate(top_assists, 1):
            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
            prefix = medals.get(idx, f"<b>{idx}.</b>")
            text += f"{prefix} <b>{html.escape(item['player_name'])}</b> ({html.escape(item['team_name'])}) — <b>{item['total_assists']}</b> 🎯\n"
        text += "\n"

    keyboard = [
        [
            InlineKeyboardButton("⚽ Бомбардиры (Графика)", callback_data="img_cup_scorers"),
            InlineKeyboardButton("🎯 Ассистенты (Графика)", callback_data="img_cup_assisters")
        ],
        [InlineKeyboardButton("« Назад к Кубку", callback_data="tournaments_cup_menu")]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)

async def send_cup_scorers_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and send PNG graphics card for Cup Top Scorers."""
    query = update.callback_query
    if query:
        await query.answer()

    import top_stats_generator
    buf = await asyncio.to_thread(top_stats_generator.generate_top_stats_image, "goals", 10, "cup")

    keyboard = [
        [InlineKeyboardButton("🎯 Ассистенты Кубка (Графика)", callback_data="img_cup_assisters")],
        [InlineKeyboardButton("🏆 Назад к Кубку", callback_data="tournaments_cup_menu")]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if query and query.message:
        try:
            await query.message.delete()
        except Exception:
            pass

    from telegram import InputFile
    await context.bot.send_photo(
        chat_id=update.effective_user.id,
        photo=InputFile(buf, filename="cup_top_scorers.png"),
        caption="<b>⚽ ТОП БОМБАРДИРОВ КУБКА КПЛ 2026</b>",
        parse_mode="HTML",
        reply_markup=markup
    )

async def send_cup_assisters_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and send PNG graphics card for Cup Top Assisters."""
    query = update.callback_query
    if query:
        await query.answer()

    import top_stats_generator
    buf = await asyncio.to_thread(top_stats_generator.generate_top_stats_image, "assists", 10, "cup")

    keyboard = [
        [InlineKeyboardButton("⚽ Бомбардиры Кубка (Графика)", callback_data="img_cup_scorers")],
        [InlineKeyboardButton("🏆 Назад к Кубку", callback_data="tournaments_cup_menu")]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if query and query.message:
        try:
            await query.message.delete()
        except Exception:
            pass

    from telegram import InputFile
    await context.bot.send_photo(
        chat_id=update.effective_user.id,
        photo=InputFile(buf, filename="cup_top_assisters.png"),
        caption="<b>🎯 ТОП АССИСТЕНТОВ КУБКА КПЛ 2026</b>",
        parse_mode="HTML",
        reply_markup=markup
    )

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
        if "Message is not modified" in str(e):
            await query.answer("✅ Данные таблицы уже актуальны!", show_alert=True)
        else:
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
