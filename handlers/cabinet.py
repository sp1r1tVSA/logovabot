import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from telegram.ext import ContextTypes, ConversationHandler
import html
import database
from handlers.base import is_admin

import logging
logger = logging.getLogger(__name__)

import asyncio
import telegram.error
from telegram.error import Forbidden
import ai_recognizer
import config
from config import MAX_WARNS_LIMIT
import player_card_generator

def match_squad_player_names(raw_players: list[str], squad_list: list[str]) -> dict[str, int]:
    counts = {}
    for raw in raw_players:
        matched_name = raw
        raw_lower = raw.lower().strip()
        for squad_player in squad_list:
            sp_lower = squad_player.lower().strip()
            if raw_lower in sp_lower or sp_lower in raw_lower:
                matched_name = squad_player
                break
        counts[matched_name] = counts.get(matched_name, 0) + 1
    return counts

def match_and_enrich_squad(raw_side1_goals: list[str], raw_side2_goals: list[str], raw_side1_assists: list[str], raw_side2_assists: list[str], home_team: str, away_team: str, is_single_timeline: bool = False):
    """
    Handles both screenshot formats:
    - Format A (Standard 2-column stats): side1 is left team, side2 is right team.
    - Format B (Vertical timeline list): all goals are pooled together and assigned to home_team vs away_team by matching each goalscorer against home_squad vs away_squad in DB!
    """
    home_squad = database.get_squad(home_team) or []
    away_squad = database.get_squad(away_team) or []

    def find_squad_match(raw_name, squad_list):
        if not raw_name:
            return None
        raw_lower = raw_name.lower().strip()
        for squad_p in squad_list:
            sp_lower = squad_p.lower().strip()
            if raw_lower == sp_lower or raw_lower in sp_lower or sp_lower in raw_lower:
                return squad_p
            raw_parts = raw_lower.split()
            sp_parts = sp_lower.split()
            if any(p in sp_parts for p in raw_parts if len(p) > 2):
                return squad_p
        return None

    if is_single_timeline:
        all_raw_goals = raw_side1_goals + raw_side2_goals
        home_goals = {}
        away_goals = {}

        for raw in all_raw_goals:
            raw_clean = raw.strip()
            if not raw_clean:
                continue
            h_match = find_squad_match(raw_clean, home_squad)
            a_match = find_squad_match(raw_clean, away_squad)

            if h_match and not a_match:
                home_goals[h_match] = home_goals.get(h_match, 0) + 1
            elif a_match and not h_match:
                away_goals[a_match] = away_goals.get(a_match, 0) + 1
            elif h_match and a_match:
                home_goals[h_match] = home_goals.get(h_match, 0) + 1
            else:
                use_name = raw_clean
                database.add_squad(home_team, [use_name])
                home_squad.append(use_name)
                home_goals[use_name] = home_goals.get(use_name, 0) + 1

        return home_goals, away_goals, {}, {}, True

    side1_all = [p.lower().strip() for p in raw_side1_goals + raw_side1_assists]
    side2_all = [p.lower().strip() for p in raw_side2_goals + raw_side2_assists]

    home_squad_lower = [p.lower().strip() for p in home_squad]
    away_squad_lower = [p.lower().strip() for p in away_squad]

    side1_home_matches = sum(1 for p in side1_all if any(p in sp or sp in p for sp in home_squad_lower))
    side1_away_matches = sum(1 for p in side1_all if any(p in sp or sp in p for sp in away_squad_lower))

    if side1_away_matches > side1_home_matches:
        is_side1_home = False
        side1_team, side2_team = away_team, home_team
        side1_squad, side2_squad = away_squad, home_squad
    else:
        is_side1_home = True
        side1_team, side2_team = home_team, away_team
        side1_squad, side2_squad = home_squad, away_squad

    def process_side_events(raw_list, team_name, squad_list):
        counts = {}
        for raw in raw_list:
            raw_clean = raw.strip()
            if not raw_clean:
                continue
            matched_name = find_squad_match(raw_clean, squad_list)
            if matched_name:
                use_name = matched_name
            else:
                use_name = raw_clean
                database.add_squad(team_name, [use_name])
                squad_list.append(use_name)
            counts[use_name] = counts.get(use_name, 0) + 1
        return counts

    side1_goals = process_side_events(raw_side1_goals, side1_team, side1_squad)
    side2_goals = process_side_events(raw_side2_goals, side2_team, side2_squad)
    side1_assists = process_side_events(raw_side1_assists, side1_team, side1_squad)
    side2_assists = process_side_events(raw_side2_assists, side2_team, side2_squad)

    if is_side1_home:
        return side1_goals, side2_goals, side1_assists, side2_assists, True
    else:
        return side2_goals, side1_goals, side2_assists, side1_assists, False

def safe_escape(val: str | None, default: str = "") -> str:
    """Safe HTML escaping for strings that may be None."""
    if val is None:
        return html.escape(default)
    return html.escape(str(val))

async def safe_query_answer(query, text: str | None = None, show_alert: bool = False) -> None:
    if not query:
        return
    try:
        if text:
            await query.answer(text, show_alert=show_alert)
        else:
            await query.answer()
    except Exception as e:
        logger.debug(f"Ignored expired query answer: {e}")

async def safe_send_notification(bot, chat_id: int, text: str, reply_markup=None, parse_mode: str = "HTML") -> bool:
    """
    Safely send messages with Telegram rate limit (RetryAfter), Forbidden, and UserDeactivated handling.
    """
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )
        return True
    except telegram.error.Forbidden:
        logger.warning(f"User {chat_id} has blocked the bot (Forbidden).")
        return False
    except telegram.error.UserDeactivated:
        logger.warning(f"User {chat_id} account is deactivated.")
        return False
    except telegram.error.RetryAfter as e:
        logger.warning(f"Rate limited by Telegram API. Waiting {e.retry_after}s...")
        await asyncio.sleep(e.retry_after)
        return await safe_send_notification(bot, chat_id, text, reply_markup, parse_mode)
    except telegram.error.TelegramError as e:
        logger.exception("Telegram error sending to {chat_id}")
        return False
    except Exception as e:
        logger.exception("Unexpected error sending to {chat_id}")
        return False

# Conversation states
GAME_NICKNAME, TEAM_NAME, LEAGUE_NAME, EDITING_FIELD = range(4)
WAITING_FOR_SCORE = 100
SQUAD_PHOTO = 101
REPORT_SCORE_PHOTO = 102
MATCH_CUSTOM_TIME = 104

async def show_cabinet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display user profile info with stats, or access denied if not registered."""
    query = update.callback_query
    if query:
        await query.answer()

    user = update.effective_user
    if not user:
        return

    # Check if user has a club assigned
    team = await asyncio.to_thread(database.get_user_team, user.id)

    if not team:
        if is_admin(user.id):
            team = "Админ-Клуб (Тест)"
        else:
            # Not registered
            text = "⚠️ <b>Доступ ограничен</b>\n\nВы еще не зарегистрированы в системе лиги."
            keyboard = [[InlineKeyboardButton("« Назад в меню", callback_data="main_menu")]]
            markup = InlineKeyboardMarkup(keyboard)
            if query:
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
            elif update.message:
                await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)
            return

    # Get stats
    stats = await asyncio.to_thread(database.get_player_stats, user.id)
    username_display = f"@{html.escape(user.username)}" if user.username else html.escape(user.first_name)

    # Get warn count
    user_record = await asyncio.to_thread(database.get_user, user.id)
    warn_count = user_record['warn_count'] if user_record and user_record['warn_count'] else 0

    warn_line = f"\n⚠️ <b>Предупреждения:</b> {warn_count} / {MAX_WARNS_LIMIT}"

    text = (
        f"👤 <b>Личный кабинет участника</b>\n\n"
        f"• <b>Telegram:</b> {username_display}\n"
        f"• <b>Игровой клуб:</b> {html.escape(team)}\n"
        f"• <b>Лига:</b> КПЛ\n\n"
        f"📊 <b>Ваша статистика в лиге:</b>\n"
        f"• <b>Сыграно матчей:</b> {stats['played']}\n"
        f"• <b>Победы:</b> {stats['wins']} | <b>Ничьи:</b> {stats['draws']} | <b>Поражения:</b> {stats['losses']}\n"
        f"• <b>Забито/Пропущено:</b> {stats['goals_scored']} / {stats['goals_conceded']}\n"
        f"• <b>Очки:</b> {stats['points']}"
        f"{warn_line}"
    )

    keyboard = [
        [InlineKeyboardButton("📋 Мои матчи", callback_data="cabinet_my_matches")],
        [InlineKeyboardButton("📸 Мой состав", callback_data="cabinet_my_squad")],
        [InlineKeyboardButton("⚽ Бомбардиры и ассистенты", callback_data="cabinet_club_stats")],
        [InlineKeyboardButton("📜 История игр", callback_data="cabinet_game_history")],
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
            await context.bot.send_message(chat_id=user.id, text=text, parse_mode="HTML", reply_markup=markup)
    elif update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def show_club_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show top scorers and assistants for the user's club."""
    query = update.callback_query
    if query:
        await query.answer()

    user = update.effective_user
    if not user:
        return

    team = await asyncio.to_thread(database.get_user_team, user.id)
    keyboard = [[InlineKeyboardButton("« Назад в кабинет", callback_data="menu_cabinet")]]
    markup = InlineKeyboardMarkup(keyboard)

    if not team:
        text = "⚠️ Вы не привязаны к клубу."
        if query:
            await query.edit_message_text(text, reply_markup=markup)
        return

    scorers = await asyncio.to_thread(database.get_club_top_scorers, team)
    assisters = await asyncio.to_thread(database.get_club_top_assisters, team)

    text = f"⚽ <b>Статистика игроков клуба {html.escape(team)}:</b>\n"

    # Collect unique player names to add card buttons
    all_players: list[str] = []
    seen: set[str] = set()

    if scorers:
        text += "\n<b>🔥 Бомбардиры:</b>\n"
        for i, s in enumerate(scorers, 1):
            text += f"{i}. {html.escape(s['player_name'])} — {s['total']} ⚽\n"
            if s['player_name'] not in seen:
                all_players.append(s['player_name'])
                seen.add(s['player_name'])
    else:
        text += "\n<i>Пока нет данных о голах.</i>\n"

    if assisters:
        text += "\n<b>👟 Ассистенты:</b>\n"
        for i, a in enumerate(assisters, 1):
            text += f"{i}. {html.escape(a['player_name'])} — {a['total']} 🅰️\n"
            if a['player_name'] not in seen:
                all_players.append(a['player_name'])
                seen.add(a['player_name'])
    else:
        text += "\n<i>Пока нет данных о передачах.</i>\n"

    context.user_data["club_stats_team"] = team
    context.user_data["club_stats_players"] = all_players

    # Build inline keyboard: safe short callback_data (pcard_{idx}) to avoid Telegram 64-byte limit
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for idx, pname in enumerate(all_players):
        cb = f"pcard_{idx}"
        btn = InlineKeyboardButton(f"👤 {pname}", callback_data=cb)
        row.append(btn)
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    if all_players:
        text += "\n<i>Нажми на игрока, чтобы открыть его карточку:</i>"

    buttons.append([InlineKeyboardButton("« Назад в кабинет", callback_data="menu_cabinet")])
    markup = InlineKeyboardMarkup(buttons)

    if query and query.message and (query.message.photo or query.message.caption):
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=query.from_user.id, text=text, parse_mode="HTML", reply_markup=markup)
    elif query:
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            await context.bot.send_message(chat_id=query.from_user.id, text=text, parse_mode="HTML", reply_markup=markup)
    elif update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def show_player_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a player stats card image for the given player."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    player_name = None
    team_name = context.user_data.get("club_stats_team")

    if data.startswith("pcard_"):
        try:
            idx = int(data.replace("pcard_", ""))
            players_list = context.user_data.get("club_stats_players", [])
            if 0 <= idx < len(players_list):
                player_name = players_list[idx]
        except ValueError:
            pass
    elif data.startswith("player_card_"):
        payload = data[len("player_card_"):]
        if "|" in payload:
            try:
                team_name, player_name = payload.split("|", 1)
            except ValueError:
                pass
        else:
            player_name = payload

    if not player_name:
        await query.answer("Ошибка: не удалось распознать игрока.", show_alert=True)
        return

    if not team_name:
        user = await asyncio.to_thread(database.get_user, query.from_user.id)
        if user and user.get("team_name"):
            team_name = user["team_name"]
        else:
            team_name = "—"

    keyboard = [[InlineKeyboardButton("« Назад", callback_data="cabinet_club_stats")]]
    markup = InlineKeyboardMarkup(keyboard)

    # Fetch stats from DB in a thread
    stats = await asyncio.to_thread(database.get_player_card_stats, player_name, team_name)

    # Generate image in a thread
    buf = await asyncio.to_thread(player_card_generator.generate_player_card, stats)

    caption = (
        f"<b>{html.escape(player_name)}</b> · {html.escape(team_name)}\n"
        f"⚽ {stats['total_goals']} голов  |  🅰️ {stats['total_assists']} ассистов"
    )

    if query.message:
        try:
            await query.message.delete()
        except Exception:
            pass

    await context.bot.send_photo(
        chat_id=query.from_user.id,
        photo=buf,
        caption=caption,
        parse_mode="HTML",
        reply_markup=markup,
    )


async def show_my_matches_stub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stub for My Matches."""
    query = update.callback_query
    if query:
        await query.answer()
    keyboard = [[InlineKeyboardButton("« Назад в кабинет", callback_data="menu_cabinet")]]
    markup = InlineKeyboardMarkup(keyboard)
    text = "🚧 <b>В разработке</b>\n\nРаздел матчей находится в разработке."
    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)


async def show_game_history_stub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stub for Game History."""
    query = update.callback_query
    if query:
        await query.answer()
    keyboard = [[InlineKeyboardButton("« Назад в кабинет", callback_data="menu_cabinet")]]
    markup = InlineKeyboardMarkup(keyboard)
    text = "🚧 <b>В разработке</b>\n\nИстория игр находится в разработке."
    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)

def is_valid_name(text: str) -> bool:
    """Validate entered text."""
    system_buttons = ["👤 Мой кабинет", "🏆 Турниры", "📊 Рейтинги", "💬 Поддержка", "⚙️ Админ-панель", "Отмена", "/cancel", "/start"]
    if text in system_buttons:
        return False
    if len(text) < 2 or len(text) > 50:
        return False
    if text.startswith("/"):
        return False
    return True

async def show_edit_profile_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Placeholder."""
    pass

# --- Full Registration Flow (Only asks for Club) ---

async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inform players that self-registration is disabled and only admin can register them."""
    query = update.callback_query
    text = (
        "ℹ️ **Регистрация участников**\n\n"
        "В нашей лиге регистрация участников производится только администратором.\n"
        "Обратитесь к организатору турнира для привязки вашего клуба."
    )
    keyboard = [[InlineKeyboardButton("« Назад в меню", callback_data="main_menu")]]
    markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.answer()
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
    elif update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
    return ConversationHandler.END

async def reg_team_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Capture team name, save profile to DB and finish."""
    text = update.message.text.strip()
    if not is_valid_name(text):
        await update.message.reply_text("❌ Пожалуйста, введите корректное название команды (без команд и системных кнопок):")
        return TEAM_NAME

    user = update.effective_user
    if not user:
        return ConversationHandler.END

    # Update DB profile
    await asyncio.to_thread(database.update_profile, user.id, text, "Основная")

    await update.message.reply_text(
        "🎉 **Регистрация успешно завершена!**",
        parse_mode="Markdown"
    )
    
    # Send updated profile view
    await show_cabinet(update, context)
    return ConversationHandler.END

# --- Selective Field Editing Flow ---

async def start_selective_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Initiate selective edit for the club name."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    await query.answer()
        
    context.user_data["edit_field"] = "team_name"

    await query.edit_message_text(
        "✏️ Введите новое название вашего **клуба / команды**:\n\n*(Отправить /cancel для отмены)*",
        parse_mode="Markdown"
    )
    return EDITING_FIELD

async def save_selective_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the updated club name to the database."""
    if not update.message or (update.effective_chat and update.effective_chat.type != "private"):
        return ConversationHandler.END
    text = update.message.text.strip()
    user = update.effective_user

    if not is_valid_name(text):
        await update.message.reply_text("❌ Недопустимое значение. Пожалуйста, введите корректный текст:")
        return EDITING_FIELD

    # Update DB
    await asyncio.to_thread(database.update_single_field, user.id, "team_name", text)
    context.user_data.pop("edit_field", None)

    await update.message.reply_text("✅ Клуб успешно изменен!")
    
    # Show updated cabinet view
    await show_cabinet(update, context)
    return ConversationHandler.END

async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Abort registration or selective edit conversation."""
    context.user_data.pop("edit_field", None)
    
    msg_text = "Регистрация / редактирование отменено."
    if update.message:
        await update.message.reply_text(msg_text)
        await show_cabinet(update, context)
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg_text)
        await show_cabinet(update, context)
        
    return ConversationHandler.END

async def safe_edit_or_reply(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None, parse_mode: str = "HTML") -> None:
    """Safely edit a message whether it's a text message or photo message, preventing BadRequest errors."""
    if not query:
        return
    user_id = query.from_user.id

    # 1. Проверяем, содержит ли исходное сообщение медиафайл (фотография)
    has_photo = bool(query.message and (query.message.photo or query.message.caption or query.message.document))

    if has_photo:
        # Для фото-сообщений ВСЕГДА удаляем старое сообщение с картинкой и слаем новое текстовое
        try:
            await query.message.delete()
        except Exception as e:
            logger.debug(f"Could not delete photo message: {e}")
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)
    else:
        # Для обычных текстовых сообщений пробуем отредактировать текст
        try:
            await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        except telegram.error.BadRequest as e:
            err_msg = str(e).lower()
            if "there is no text in the message to edit" in err_msg or "message is not modified" in err_msg:
                # Если сработал краевой случай Telegram — удаляем и пересоздаем
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await context.bot.send_message(chat_id=user_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)
            else:
                logger.warning(f"safe_edit_or_reply BadRequest: {e}")
        except Exception as e:
            logger.warning(f"edit_message_text failed ({e}). Falling back to delete & send_message...")
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=user_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)

# --- Placeholders ---

async def show_my_matches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    user_id = query.from_user.id
    matches = await asyncio.to_thread(database.get_pending_matches, user_id)
    
    text = "📋 **Ваши открытые матчи:**\n\n"
    keyboard = []
    
    if not matches:
        text += "У вас нет несыгранных матчей на данный момент."
    else:
        text += "Выберите матч для просмотра и ввода результата:"
        for m in matches:
            opp = m['opponent_team'] or m['opponent_username'] or "Соперник"
            if m.get('tournament_type') == 'cup':
                stage = m.get('cup_stage', 'Кубок')
                g_num = m.get('game_num_in_series', 1)
                btn_text = f"🏆 {stage} (Игра {g_num}): 🆚 {opp}"
            else:
                btn_text = f"⚽ Тур {m['round_number']}: 🆚 {opp}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"cabinet_view_match_{m['id']}")])
            
    keyboard.append([InlineKeyboardButton("« Назад в кабинет", callback_data="menu_cabinet")])
    markup = InlineKeyboardMarkup(keyboard)
    await safe_edit_or_reply(query, context, text, parse_mode="Markdown", reply_markup=markup)

async def cabinet_view_match(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    
    match_id = int(query.data.replace("cabinet_view_match_", ""))
    user_id = query.from_user.id
    
    m = await asyncio.to_thread(database.get_match, match_id)
    if not m:
        await safe_edit_or_reply(query, context, "Матч не найден.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Назад", callback_data="cabinet_my_matches")]]))
        return
        
    round_info = await asyncio.to_thread(database.get_round_info, m['round_number'])
    deadline_text = round_info.get("deadline") if round_info else None
    
    is_overdue = False
    if deadline_text:
        try:
            dt = datetime.datetime.strptime(deadline_text, "%d.%m.%Y %H:%M")
            if datetime.datetime.now() > dt:
                is_overdue = True
        except ValueError:
            pass
            
    if m['player1_id'] == user_id:
        opp_id = m['player2_id']
        my_team = m['player1_team'] or m['player1_nickname']
        opp_team = m['player2_team'] or m['player2_nickname']
        score_str = f"{m['player1_score']}:{m['player2_score']}" if m['player1_score'] is not None else "-:-"
    else:
        opp_id = m['player1_id']
        my_team = m['player2_team'] or m['player2_nickname']
        opp_team = m['player1_team'] or m['player1_nickname']
        score_str = f"{m['player2_score']}:{m['player1_score']}" if m['player1_score'] is not None else "-:-"
        
    status_text = "⏳ Ожидает ввода результата"
    if m['status'] == 'disputed':
        status_text = "⚠️ Спорный"
    elif m['status'] == 'confirmed':
        status_text = "✅ Завершен"
        
    proposed_time = m.get('proposed_time')
    proposed_by = m.get('proposed_by')
    time_status = m.get('time_status') or 'none'

    time_info_text = ""
    if time_status == 'accepted' and proposed_time:
        time_info_text = f"⏰ **Согласованное время:** {html.escape(proposed_time)} ✅\n"
    elif time_status == 'proposed' and proposed_time:
        if proposed_by == user_id:
            time_info_text = f"⏰ **Предложено вами:** {html.escape(proposed_time)} *(ожидание ответа)*\n"
        else:
            time_info_text = f"⏰ **Соперник предлагает время:** {html.escape(proposed_time)}\n"
    else:
        time_info_text = "⏰ **Время матча:** не согласовано\n"

    text = f"🏟 <b>МАТЧ #{m['id']} | Тур {m['round_number']}</b>\n"
    text += f"Статус: {status_text}\n"
    if deadline_text:
        text += f"⏳ Дедлайн: {html.escape(deadline_text)}\n"
    text += time_info_text
        
    text += f"\n🏠 <b>Вы</b> {score_str} <b>{html.escape(opp_team)}</b> ✈️\n"
    text += "────────────────────────\n\n"
    
    if m['status'] == 'pending':
        if is_overdue and not m.get('is_extended'):
            text += "⏳ <b>Дедлайн истек.</b> Результат принимает администратор.\n\nВы можете отправить запрос администратору — он внесёт результат за вас."
        else:
            if m['player1_id'] == user_id:
                text += "🏠 <b>Вы играете Дома.</b>\n\n"
            else:
                text += "✈️ <b>Вы играете в Гостях.</b>\n\n"
            text += (
                "📌 <b>Инструкция по внесению результата:</b>\n"
                "1. Нажмите кнопку <b>📝 Ввести результат</b>.\n"
                "2. Выберите <b>⚡ Автоматический ввод (по фото)</b>.\n"
                "3. Отправьте боту от 1 до 3 скриншотов статистики из игры.\n"
                "4. ИИ автоматически распознает счёт, авторов голов и ассистов.\n"
                "5. Проверьте данные и нажмите <b>✅ Всё верно</b> — результат сразу автоматически подтверждается и заносится в турнирную таблицу лиги!"
            )
    elif m['status'] == 'disputed':
        text += "⚠️ <b>Матч оспорен.</b> Ожидайте решения администратора."
    elif m['status'] == 'confirmed':
        text += "✅ <b>Матч сыгран и результат подтвержден.</b>"
        
    keyboard = []
    
    keyboard.append([
        InlineKeyboardButton("👀 Состав", callback_data=f"cabinet_view_squad_{opp_id}"), 
        InlineKeyboardButton("🕵️‍♂️ Скаут", callback_data="stub")
    ])

    if m['status'] == 'pending':
        if time_status == 'proposed' and proposed_by != user_id:
            keyboard.append([
                InlineKeyboardButton("✅ Согласовать время", callback_data=f"cb_accept_time_{match_id}"),
                InlineKeyboardButton("⏰ Предложить другое", callback_data=f"cb_propose_time_prompt_{match_id}")
            ])
        elif time_status == 'proposed' and proposed_by == user_id:
            keyboard.append([
                InlineKeyboardButton("✏️ Изменить предложенное время", callback_data=f"cb_propose_time_prompt_{match_id}")
            ])
        elif time_status == 'accepted':
            keyboard.append([
                InlineKeyboardButton("⏰ Изменить время", callback_data=f"cb_propose_time_prompt_{match_id}")
            ])
        else:
            keyboard.append([
                InlineKeyboardButton("⏰ Предложить время матча", callback_data=f"cb_propose_time_prompt_{match_id}")
            ])
    
    if m['status'] == 'pending' and user_id in (m['player1_id'], m['player2_id']) and (not is_overdue or m.get('is_extended')):
        keyboard.append([InlineKeyboardButton("📝 Ввести результат", callback_data=f"cabinet_report_score_{match_id}")])

    # Overdue: show request to admin button
    if m['status'] == 'pending' and is_overdue and not m.get('is_extended') and user_id in (m['player1_id'], m['player2_id']):
        keyboard.append([InlineKeyboardButton("📨 Запросить ввод через админа", callback_data=f"cb_request_admin_result_{match_id}")])
        
    keyboard.append([InlineKeyboardButton("📜 Правила турнира", url="https://t.me/fifulatyrniru/3405")])
    keyboard.append([InlineKeyboardButton("🔙 К списку матчей", callback_data="cabinet_my_matches")])
    
    await safe_edit_or_reply(query, context, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def cancel_score_report_and_navigate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """FSM fallback: clears all score-reporting state and routes user to the appropriate screen.

    Triggered when a user presses a navigation button (e.g. «К списку матчей», «Главное меню»)
    while a score-reporting ConversationHandler is active. Without this fallback the reporting
    keys survive in context.user_data and corrupt the next reporting session.
    """
    # ── Wipe every key that the reporting flow may have written ──────────────────
    for key in (
        "reporting_match_id",
        "report_photo_id",
        "awaiting_report_photo",
        "home_goals",
        "away_goals",
        "home_assists",
        "away_assists",
        "home_goal_players",
        "away_goal_players",
        "home_assist_players",
        "away_assist_players",
        "is_admin_reporting",
        "ai_photos_list",
        "processed_media_groups",
    ):
        context.user_data.pop(key, None)

    query = update.callback_query
    if query:
        await query.answer()
        dest = query.data  # "main_menu" | "cabinet_my_matches" | anything else
    else:
        dest = ""

    from telegram.ext import ConversationHandler
    if dest == "main_menu":
        from handlers.base import show_main_menu
        await show_main_menu(update, context)
    else:
        await show_my_matches(update, context)

    return ConversationHandler.END


async def cb_request_admin_result(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Player requests permission from admin to enter result for an overdue match."""
    query = update.callback_query
    if not query:
        return

    match_id = int(query.data.replace("cb_request_admin_result_", ""))
    user_id = query.from_user.id
    
    # Anti-spam check: See if we already changed the button on this message
    if query.message and query.message.reply_markup:
        is_already_sent = False
        for row in query.message.reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data == "ignore" and "Запрос отправлен" in btn.text:
                    is_already_sent = True
        if is_already_sent:
            await query.answer("⏳ Запрос уже отправлен!", show_alert=False)
            return
            
    await query.answer()  # Single answer() to dismiss loading spinner

    m = await asyncio.to_thread(database.get_match, match_id)
    if not m:
        await context.bot.send_message(chat_id=user_id, text="❌ Матч не найден.")
        return

    if user_id not in (m['player1_id'], m['player2_id']):
        await context.bot.send_message(chat_id=user_id, text="⛔ Вы не являетесь участником этого матча.")
        return

    if m.get('is_extended'):
        await context.bot.send_message(chat_id=user_id, text="✅ Разрешение уже выдано! Вы можете вносить результат.")
        return

    team1 = m.get('player1_team') or m.get('player1_nickname') or '?'
    team2 = m.get('player2_team') or m.get('player2_nickname') or '?'
    nick1 = m.get('player1_nickname') or '?'
    nick2 = m.get('player2_nickname') or '?'
    rnd = m.get('round_number', '?')
    deadline = m.get('deadline', '—')

    requester_name = query.from_user.full_name or query.from_user.username or str(user_id)
    requester_username = f"@{query.from_user.username}" if query.from_user.username else f"id{user_id}"

    admin_text = (
        f"📨 <b>Запрос на разрешение внесения результата</b>\n\n"
        f"🏟 Матч #{match_id} | Тур {rnd}\n"
        f"🏠 {html.escape(team1)} (@{html.escape(nick1)}) vs {html.escape(team2)} (@{html.escape(nick2)}) ✈️\n"
        f"⏳ Дедлайн истёк: {html.escape(str(deadline))}\n\n"
        f"👤 Запрос отправил: {html.escape(requester_name)} ({requester_username})\n\n"
        f"Нажмите <b>✅ Разрешить</b>, чтобы игрок мог сам внести результат."
    )
    # callback carries both match_id and requesting player_id
    admin_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Разрешить внесение", callback_data=f"cb_admin_approve_{match_id}_{user_id}")]
    ])

    sent_count = 0
    from config import ADMIN_IDS
    # Filter unique ADMIN_IDS in case of duplicates
    unique_admins = list(set(ADMIN_IDS))
    
    for admin_id in unique_admins:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_text,
                parse_mode="HTML",
                reply_markup=admin_keyboard
            )
            sent_count += 1
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")

    if sent_count > 0:
        await query.answer("✅ Запрос отправлен администратору!\nКак только он одобрит — бот пришлёт вам уведомление.", show_alert=True)
        # Update button to prevent multiple clicks
        if query.message and query.message.reply_markup:
            keyboard = query.message.reply_markup.inline_keyboard
            new_keyboard = []
            for row in keyboard:
                new_row = []
                for btn in row:
                    if btn.callback_data == query.data:
                        new_row.append(InlineKeyboardButton("⏳ Запрос отправлен", callback_data="ignore"))
                    else:
                        new_row.append(btn)
                new_keyboard.append(new_row)
            try:
                await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_keyboard))
            except Exception as e:
                pass
    else:
        await query.answer("⚠️ Не удалось уведомить администраторов. Напишите напрямую @antonv2801.", show_alert=True)


async def cb_admin_approve_result(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin approves the player's request — unlocks the match and sends player a match card to enter result."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    admin_id = query.from_user.id
    from config import ADMIN_IDS
    if admin_id not in ADMIN_IDS:
        await query.answer("⛔ Только администратор может одобрять запросы.", show_alert=True)
        return

    # parse match_id and player_id from callback data: cb_admin_approve_{match_id}_{player_id}
    parts = query.data.replace("cb_admin_approve_", "").split("_")
    if len(parts) < 2:
        await query.answer("Ошибка данных.", show_alert=True)
        return
    match_id = int(parts[0])
    player_id = int(parts[1])

    m = await asyncio.to_thread(database.get_match, match_id)
    if not m:
        await query.answer("Матч не найден.", show_alert=True)
        return

    if m['status'] != 'pending':
        await query.answer(f"Матч уже имеет статус: {m['status']}.", show_alert=True)
        return

    # Unlock the match — set is_extended = 1 so overdue check is bypassed for the player
    await asyncio.to_thread(database.extend_match_deadline, match_id)

    team1 = m.get('player1_team') or m.get('player1_nickname') or '?'
    team2 = m.get('player2_team') or m.get('player2_nickname') or '?'
    rnd = m.get('round_number', '?')
    admin_username = query.from_user.username or str(admin_id)

    # Notify player with match card and direct "Enter result" button
    player_text = (
        f"✅ <b>Разрешение получено!</b>\n\n"
        f"Администратор @{html.escape(admin_username)} разрешил вам внести результат матча:\n\n"
        f"🏟 Матч #{match_id} | Тур {rnd}\n"
        f"🏠 {html.escape(team1)} vs {html.escape(team2)} ✈️\n\n"
        f"Нажмите кнопку ниже, чтобы сразу внести результат:"
    )
    player_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Ввести результат", callback_data=f"cabinet_report_score_{match_id}")]
    ])

    try:
        await context.bot.send_message(
            chat_id=player_id,
            text=player_text,
            parse_mode="HTML",
            reply_markup=player_keyboard
        )
    except Exception as e:
        logger.exception("Failed to notify player {player_id} about admin approval")

    # Update admin's message to show it's been approved
    try:
        await query.edit_message_text(
            text=(
                f"✅ <b>Разрешение выдано!</b>\n\n"
                f"Игрок получил уведомление и может сам внести результат матча #{match_id}."
            ),
            parse_mode="HTML"
        )
    except Exception:
        pass


async def cb_propose_time_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user with quick time choices or custom text entry."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    match_id = int(query.data.replace("cb_propose_time_prompt_", ""))
    match = await asyncio.to_thread(database.get_match, match_id)
    if not match:
        return

    text = (
        f"⏰ **Предложение времени матча #{match_id}**\n\n"
        f"Выберите удобный вариант из списка ниже или введите своё время текстом:"
    )

    keyboard = [
        [
            InlineKeyboardButton("Сегодня в 19:00", callback_data=f"cb_quick_time_{match_id}_Сегодня в 19:00"),
            InlineKeyboardButton("Сегодня в 20:00", callback_data=f"cb_quick_time_{match_id}_Сегодня в 20:00")
        ],
        [
            InlineKeyboardButton("Сегодня в 21:00", callback_data=f"cb_quick_time_{match_id}_Сегодня в 21:00"),
            InlineKeyboardButton("Сегодня в 22:00", callback_data=f"cb_quick_time_{match_id}_Сегодня в 22:00")
        ],
        [
            InlineKeyboardButton("Завтра в 19:00", callback_data=f"cb_quick_time_{match_id}_Завтра в 19:00"),
            InlineKeyboardButton("Завтра в 20:00", callback_data=f"cb_quick_time_{match_id}_Завтра в 20:00")
        ],
        [
            InlineKeyboardButton("Завтра в 21:00", callback_data=f"cb_quick_time_{match_id}_Завтра в 21:00"),
            InlineKeyboardButton("Завтра в 22:00", callback_data=f"cb_quick_time_{match_id}_Завтра в 22:00")
        ],
        [
            InlineKeyboardButton("✍️ Ввести своё время", callback_data=f"cb_custom_time_prompt_{match_id}")
        ],
        [
            InlineKeyboardButton("« Назад к матчу", callback_data=f"cabinet_view_match_{match_id}")
        ]
    ]

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def cb_quick_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save quick selected time and notify opponent."""
    query = update.callback_query
    if not query:
        return
    await query.answer("✅ Предложение времени отправлено!")

    # Pattern: cb_quick_time_{match_id}_{time_str}
    raw = query.data.replace("cb_quick_time_", "")
    parts = raw.split("_", 1)
    match_id = int(parts[0])
    time_str = parts[1]
    user_id = query.from_user.id

    await asyncio.to_thread(database.propose_match_time, match_id, user_id, time_str)
    match = await asyncio.to_thread(database.get_match, match_id)

    # Notify opponent
    if match:
        opp_id = match['player2_id'] if match['player1_id'] == user_id else match['player1_id']
        sender_team = match['player1_team'] if match['player1_id'] == user_id else match['player2_team']
        sender_team = sender_team or "Соперник"

        if opp_id:
            pm_text = (
                f"⏰ <b>ПРЕДЛОЖЕНИЕ ВРЕМЕНИ МАТЧА!</b>\n\n"
                f"Соперник <b>{html.escape(sender_team)}</b> предлагает сыграть <b>Матч #{match_id} (Тур {match['round_number']})</b>:\n"
                f"🕒 <b>{html.escape(time_str)}</b>\n\n"
                f"<i>Перейдите в карточку матча для подтверждения или ответа!</i>"
            )
            kb = [[InlineKeyboardButton("🏟 Открыть карточку матча", callback_data=f"cabinet_view_match_{match_id}")]]
            await safe_send_notification(context.bot, opp_id, pm_text, InlineKeyboardMarkup(kb))

    # Return user to match card view
    await cabinet_view_match(update, context)

async def cb_accept_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Accept the proposed match time."""
    query = update.callback_query
    if not query:
        return
    await query.answer("🎉 Время матча успешно согласовано!", show_alert=True)

    match_id = int(query.data.replace("cb_accept_time_", ""))
    user_id = query.from_user.id

    await asyncio.to_thread(database.accept_match_time, match_id)
    match = await asyncio.to_thread(database.get_match, match_id)

    if match:
        proposer_id = match.get("proposed_by")
        accepted_time = match.get("proposed_time") or "неизвестно"
        acceptor_team = match['player1_team'] if match['player1_id'] == user_id else match['player2_team']
        acceptor_team = acceptor_team or "Соперник"

        if proposer_id and proposer_id != user_id:
            pm_text = (
                f"✅ <b>ВРЕМЯ МАТЧА СОГЛАСОВАНО!</b>\n\n"
                f"Соперник <b>{html.escape(acceptor_team)}</b> подтвердил время проведения <b>Матча #{match_id} (Тур {match['round_number']})</b>:\n"
                f"🕒 <b>{html.escape(accepted_time)}</b>\n\n"
                f"Удачной игры!"
            )
            kb = [[InlineKeyboardButton("🏟 Открыть карточку матча", callback_data=f"cabinet_view_match_{match_id}")]]
            await safe_send_notification(context.bot, proposer_id, pm_text, InlineKeyboardMarkup(kb))

    await cabinet_view_match(update, context)

async def start_custom_time_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt user to type custom time."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    await query.answer()

    match_id = int(query.data.replace("cb_custom_time_prompt_", ""))
    context.user_data["custom_time_match_id"] = match_id

    text = (
        f"✍️ **Ввод своего времени для матча #{match_id}**\n\n"
        f"Напишите удобные дату и время сообщением (например: `Сегодня в 22:30` или `23.07 в 18:00`):\n\n"
        f"*(Отправьте /cancel для отмены)*"
    )
    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data=f"cabinet_view_match_{match_id}")]]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    return MATCH_CUSTOM_TIME

async def save_custom_match_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save custom match time entered by text."""
    if not update.message or not update.message.text or (update.effective_chat and update.effective_chat.type != "private"):
        return MATCH_CUSTOM_TIME

    time_str = update.message.text.strip()
    if len(time_str) > 100:
        await update.message.reply_text("⚠️ Введенное время слишком длинное (максимум 100 символов). Попробуйте еще раз:")
        return MATCH_CUSTOM_TIME

    match_id = context.user_data.get("custom_time_match_id")
    user_id = update.effective_user.id

    if not match_id:
        await update.message.reply_text("❌ Ошибка: матч не найден.")
        return ConversationHandler.END

    await asyncio.to_thread(database.propose_match_time, match_id, user_id, time_str)
    match = await asyncio.to_thread(database.get_match, match_id)

    if match:
        opp_id = match['player2_id'] if match['player1_id'] == user_id else match['player1_id']
        sender_team = match['player1_team'] if match['player1_id'] == user_id else match['player2_team']
        sender_team = sender_team or "Соперник"

        if opp_id:
            pm_text = (
                f"⏰ <b>ПРЕДЛОЖЕНИЕ ВРЕМЕНИ МАТЧА!</b>\n\n"
                f"Соперник <b>{html.escape(sender_team)}</b> предлагает сыграть <b>Матч #{match_id} (Тур {match['round_number']})</b>:\n"
                f"🕒 <b>{html.escape(time_str)}</b>\n\n"
                f"<i>Перейдите в карточку матча для подтверждения или ответа!</i>"
            )
            kb = [[InlineKeyboardButton("🏟 Открыть карточку матча", callback_data=f"cabinet_view_match_{match_id}")]]
            await safe_send_notification(context.bot, opp_id, pm_text, InlineKeyboardMarkup(kb))

    await update.message.reply_text(f"✅ Время матча #{match_id} успешно предложено: **{time_str}**", parse_mode="Markdown")
    
    # Render updated match card
    m_info = await asyncio.to_thread(database.get_match, match_id)
    if m_info:
        round_info = await asyncio.to_thread(database.get_round_info, m_info['round_number'])
        deadline_text = round_info.get("deadline") if round_info else None
        
        if m_info['player1_id'] == user_id:
            opp_team = m_info['player2_team'] or m_info['player2_nickname']
            score_str = f"{m_info['player1_score']}:{m_info['player2_score']}" if m_info['player1_score'] is not None else "-:-"
        else:
            opp_team = m_info['player1_team'] or m_info['player1_nickname']
            score_str = f"{m_info['player2_score']}:{m_info['player1_score']}" if m_info['player1_score'] is not None else "-:-"

        opp_id = m_info['player2_id'] if m_info['player1_id'] == user_id else m_info['player1_id']
        kb_match = [
            [InlineKeyboardButton("👀 Состав", callback_data=f"cabinet_view_squad_{opp_id}"), InlineKeyboardButton("🕵️‍♂️ Скаут", callback_data="stub")],
            [InlineKeyboardButton("✏️ Изменить предложенное время", callback_data=f"cb_propose_time_prompt_{match_id}")],
            [InlineKeyboardButton("🔙 К списку матчей", callback_data="cabinet_my_matches")]
        ]
        if m_info['status'] == 'pending' and m_info['player1_id'] == user_id:
            kb_match.insert(1, [InlineKeyboardButton("📝 Ввести результат", callback_data=f"cabinet_report_score_{match_id}")])

        card_text = (
            f"🏟 **МАТЧ #{match_id} | Тур {m_info['round_number']}**\n"
            f"Статус: ⏳ Ожидает ввода результата\n"
        )
        if deadline_text:
            card_text += f"⏳ Дедлайн: {deadline_text}\n"
        card_text += f"⏰ **Предложено вами:** {html.escape(time_str)} *(ожидание ответа)*\n"
        card_text += f"\n🏠 **Вы** {score_str} **{opp_team}** ✈️\n────────────────────────\n\n"
        card_text += "⏳ Предложение времени отправлено сопернику."

        await update.message.reply_text(card_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb_match))

    return ConversationHandler.END

async def show_game_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user_id = query.from_user.id
    matches = await asyncio.to_thread(database.get_match_history, user_id)

    text = "📜 **Ваша история игр:**\n\n"
    if not matches:
        text += "Вы еще не сыграли ни одного матча."
    else:
        for m in matches:
            opp = m['opponent_team'] or m['opponent_username']
            user_score = m['player1_score'] if m['player1_id'] == user_id else m['player2_score']
            opp_score = m['player2_score'] if m['player1_id'] == user_id else m['player1_score']
            text += f"Тур {m['round_number']}: *Вы* {user_score} : {opp_score} *{opp}*\n"

    keyboard = [[InlineKeyboardButton("« Назад в кабинет", callback_data="menu_cabinet")]]
    markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)


# ==========================================
# ВВОД И ПОДТВЕРЖДЕНИЕ РЕЗУЛЬТАТОВ МАТЧА
# ==========================================

def get_match_cancel_cb(context: ContextTypes.DEFAULT_TYPE, user_id: int, match_id: int) -> str:
    """Return appropriate cancel callback data depending on whether user is admin or player."""
    is_admin_user = is_admin(user_id) or context.user_data.get("is_admin_reporting", False)
    if is_admin_user:
        return f"admin_view_match_{match_id}"
    return f"cabinet_view_match_{match_id}"

async def start_score_reporting(update: Update, context: ContextTypes.DEFAULT_TYPE, match_id: int | None = None) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        if match_id is None:
            match_id = int(query.data.replace("cabinet_report_score_", ""))

    context.user_data["reporting_match_id"] = match_id

    match = await asyncio.to_thread(database.get_match, match_id)
    if not match:
        if query:
            await context.bot.send_message(chat_id=query.from_user.id, text="❌ Матч не найден.")
        return

    if match['status'] == 'confirmed':
        if query:
            await context.bot.send_message(chat_id=query.from_user.id, text="⛔ Результат этого матча уже занесён в таблицу!")
        return

    user_id = query.from_user.id if query else update.effective_user.id

    home_team = match['player1_team'] or match['player1_nickname']
    away_team = match['player2_team'] or match['player2_nickname']

    context.user_data["report_home_team"] = home_team
    context.user_data["report_away_team"] = away_team

    user_id = query.from_user.id if query else update.effective_user.id
    cancel_cb = get_match_cancel_cb(context, user_id, match_id)

    text = (
        f"📝 <b>Ввод результата матча #{match_id}</b>\n\n"
        f"⚔️ <b>{safe_escape(home_team)}</b> 🆚 <b>{safe_escape(away_team)}</b>\n\n"
        f"Выберите способ ввода результата:"
    )

    keyboard = [
        [InlineKeyboardButton("⚡ Автоматический ввод (по фото)", callback_data=f"cb_report_choice_auto_{match_id}")],
        [InlineKeyboardButton("✍️ Ручной ввод", callback_data=f"cb_report_choice_manual_{match_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=cancel_cb)]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if query:
        await safe_edit_or_reply(query, context, text, parse_mode="HTML", reply_markup=markup)

async def cb_report_choice_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    match_id = int(query.data.replace("cb_report_choice_auto_", ""))

    match = await asyncio.to_thread(database.get_match, match_id)
    if not match:
        await context.bot.send_message(chat_id=query.from_user.id, text="❌ Матч не найден.")
        return

    if match['status'] == 'confirmed':
        await context.bot.send_message(chat_id=query.from_user.id, text="⛔ Результат этого матча уже занесён в таблицу!")
        return

    user_id = query.from_user.id
    context.user_data["reporting_match_id"] = match_id
    context.user_data["reporting_mode"] = "auto"
    context.user_data["awaiting_report_photo"] = True
    context.user_data["ai_photos_list"] = []

    text = (
        "📸 <b>Автоматический ввод по фото</b>\n\n"
        "Пожалуйста, отправьте <b>от 1 до 3 скриншотов</b> матча строго с статистикой(голы и ассисты).\n\n"
        "💡 <i>Вы можете отправить от 1 до 3 фото сразу альбомом.</i>"
    )
    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data=f"cabinet_view_match_{match_id}")]]
    await safe_edit_or_reply(query, context, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

async def cb_report_choice_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    match_id = int(query.data.replace("cb_report_choice_manual_", ""))

    match = await asyncio.to_thread(database.get_match, match_id)
    if not match:
        await context.bot.send_message(chat_id=query.from_user.id, text="❌ Матч не найден.")
        return

    if match['status'] == 'confirmed':
        await context.bot.send_message(chat_id=query.from_user.id, text="⛔ Результат этого матча уже занесён в таблицу!")
        return

    user_id = query.from_user.id
    context.user_data["reporting_match_id"] = match_id
    context.user_data["reporting_mode"] = "manual"

    home_team = match['player1_team'] or match['player1_nickname']
    away_team = match['player2_team'] or match['player2_nickname']
    context.user_data["report_home_team"] = home_team
    context.user_data["report_away_team"] = away_team

    text = (
        f"⚽ <b>Ввод результата матча #{match_id}</b>\n"
        f"🏠 <b>{safe_escape(home_team)}</b> vs <b>{safe_escape(away_team)}</b> ✈️\n\n"
        f"Выберите, сколько забила домашняя команда (<b>{safe_escape(home_team)}</b>):"
    )

    keyboard = []
    row = []
    for i in range(16):
        row.append(InlineKeyboardButton(str(i), callback_data=f"cb_report_hg_{i}"))
        if len(row) == 4:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    user_id = query.from_user.id
    cancel_cb = get_match_cancel_cb(context, user_id, match_id)
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data=cancel_cb)])

    # Используем safe_edit_or_reply, чтобы корректно удалить карточку с картинкой!
    await safe_edit_or_reply(query, context, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

async def cb_report_home_goals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    hg = int(query.data.replace("cb_report_hg_", ""))
    context.user_data["report_home_goals"] = hg

    match_id = context.user_data.get("reporting_match_id")
    home_team = context.user_data.get("report_home_team")
    away_team = context.user_data.get("report_away_team")

    text = (
        f"⚽ <b>Ввод результата матча #{match_id}</b>\n"
        f"🏠 <b>{safe_escape(home_team)}</b> ({hg}) vs <b>{safe_escape(away_team)}</b> (?)\n\n"
        f"Теперь выберите, сколько забил соперник (<b>{safe_escape(away_team)}</b>):"
    )

    keyboard = []
    row = []
    for i in range(16):
        row.append(InlineKeyboardButton(str(i), callback_data=f"cb_report_ag_{i}"))
        if len(row) == 4:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    user_id = query.from_user.id
    cancel_cb = get_match_cancel_cb(context, user_id, match_id)
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data=cancel_cb)])

    await safe_edit_or_reply(query, context, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

async def cb_report_away_goals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    ag = int(query.data.replace("cb_report_ag_", ""))
    context.user_data["report_away_goals"] = ag

    hg = context.user_data.get("report_home_goals", 0)
    home_team = context.user_data.get("report_home_team")

    # Clear previous selections
    context.user_data["home_goals_count"] = {}
    context.user_data["home_assists_count"] = {}
    context.user_data["away_goals_count"] = {}
    context.user_data["away_assists_count"] = {}

    if hg > 0:
        context.user_data["current_picking_phase"] = "home_goals"
        context.user_data["goals_to_pick"] = hg
        await render_squad_goals_picker(update, context, home_team)
    else:
        await start_home_assists_picker(update, context)

async def start_home_assists_picker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hg = context.user_data.get("report_home_goals", 0)
    home_team = context.user_data.get("report_home_team")
    if hg > 0:
        context.user_data["current_picking_phase"] = "home_assists"
        context.user_data["assists_to_pick"] = hg
        await render_squad_assists_picker(update, context, home_team)
    else:
        await start_away_goals_picker(update, context)

async def start_away_goals_picker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ag = context.user_data.get("report_away_goals", 0)
    away_team = context.user_data.get("report_away_team")
    if ag > 0:
        context.user_data["current_picking_phase"] = "away_goals"
        context.user_data["goals_to_pick"] = ag
        await render_squad_goals_picker(update, context, away_team)
    else:
        await start_away_assists_picker(update, context)

async def start_away_assists_picker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ag = context.user_data.get("report_away_goals", 0)
    away_team = context.user_data.get("report_away_team")
    if ag > 0:
        context.user_data["current_picking_phase"] = "away_assists"
        context.user_data["assists_to_pick"] = ag
        await render_squad_assists_picker(update, context, away_team)
    else:
        await prompt_photo_upload(update, context)

async def render_squad_goals_picker(update: Update, context: ContextTypes.DEFAULT_TYPE, team_name: str) -> None:
    query = update.callback_query
    phase = context.user_data.get("current_picking_phase", "home_goals")
    left = context.user_data.get("goals_to_pick", 0)

    dict_key = "home_goals_count" if "home" in phase else "away_goals_count"
    picked_dict = context.user_data.get(dict_key, {})

    squad = await asyncio.to_thread(database.get_squad, team_name)

    summary_str = ""
    if picked_dict:
        summary_str = "\n\n⚽ **Уже выбрано:**\n" + "\n".join([f"• {p}: {c}" for p, c in picked_dict.items()])

    text = (
        f"⚽ <b>Авторы голов команды ({html.escape(team_name)})</b>\n"
        f"Осталось распределить голов: <b>{left}</b>{summary_str}\n\n"
        f"Нажимайте на кнопки с игроками состава:"
    )

    keyboard = []
    context.user_data["temp_active_squad_goals"] = squad
    if not squad:
        text += "\n\n⚠️ <i>Состав команды пока не добавлен в систему.</i>"
        keyboard.append([InlineKeyboardButton("⏩ Пропустить ввод авторов", callback_data="cb_skip_goals")])
    else:
        row = []
        for idx, player in enumerate(squad):
            row.append(InlineKeyboardButton(f"🏃‍♂️ {player}", callback_data=f"cb_pick_goal_idx_{idx}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("⏩ Пропустить остаток (пенальти/автоголы)", callback_data="cb_skip_goals")])

    match_id = context.user_data.get("reporting_match_id")
    user_id = query.from_user.id if query else update.effective_user.id
    cancel_cb = get_match_cancel_cb(context, user_id, match_id)
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data=cancel_cb)])

    markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)

async def cb_pick_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    idx = int(query.data.replace("cb_pick_goal_idx_", ""))
    squad = context.user_data.get("temp_active_squad_goals", [])
    player = squad[idx] if idx < len(squad) else "Unknown"

    phase = context.user_data.get("current_picking_phase", "home_goals")
    dict_key = "home_goals_count" if "home" in phase else "away_goals_count"
    dict_goals = context.user_data.get(dict_key, {})

    dict_goals[player] = dict_goals.get(player, 0) + 1
    left = context.user_data.get("goals_to_pick", 0) - 1
    context.user_data["goals_to_pick"] = left
    context.user_data[dict_key] = dict_goals

    team_name = context.user_data.get("report_home_team") if "home" in phase else context.user_data.get("report_away_team")

    if left > 0:
        await render_squad_goals_picker(update, context, team_name)
    else:
        if phase == "home_goals":
            await start_home_assists_picker(update, context)
        else:
            await start_away_assists_picker(update, context)

async def cb_skip_goals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    phase = context.user_data.get("current_picking_phase", "home_goals")
    if phase == "home_goals":
        await start_home_assists_picker(update, context)
    else:
        await start_away_assists_picker(update, context)

async def render_squad_assists_picker(update: Update, context: ContextTypes.DEFAULT_TYPE, team_name: str) -> None:
    query = update.callback_query
    phase = context.user_data.get("current_picking_phase", "home_assists")
    left = context.user_data.get("assists_to_pick", 0)

    dict_key = "home_assists_count" if "home" in phase else "away_assists_count"
    goals_key = "report_home_goals" if "home" in phase else "report_away_goals"
    max_assists = context.user_data.get(goals_key, 0)

    picked_dict = context.user_data.get(dict_key, {})

    squad = await asyncio.to_thread(database.get_squad, team_name)

    summary_str = ""
    if picked_dict:
        summary_str = "\n\n🎯 **Уже выбрано:**\n" + "\n".join([f"• {p}: {c}" for p, c in picked_dict.items()])

    text = (
        f"🎯 <b>Авторы ассистов команды ({html.escape(team_name)})</b>\n"
        f"Осталось ассистов (макс {max_assists}): <b>{left}</b>{summary_str}\n\n"
        f"Нажимайте на кнопки с игроками состава или пропустите:"
    )

    keyboard = []
    context.user_data["temp_active_squad_assists"] = squad
    if squad and left > 0:
        row = []
        for idx, player in enumerate(squad):
            row.append(InlineKeyboardButton(f"🎯 {player}", callback_data=f"cb_pick_assist_idx_{idx}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    keyboard.append([InlineKeyboardButton("⏩ Пропустить остаток ассистов", callback_data="cb_skip_assists")])
    match_id = context.user_data.get("reporting_match_id")
    user_id = query.from_user.id if query else update.effective_user.id
    cancel_cb = get_match_cancel_cb(context, user_id, match_id)
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data=cancel_cb)])

    markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)

async def cb_pick_assist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    idx = int(query.data.replace("cb_pick_assist_idx_", ""))
    squad = context.user_data.get("temp_active_squad_assists", [])
    player = squad[idx] if idx < len(squad) else "Unknown"

    phase = context.user_data.get("current_picking_phase", "home_assists")
    dict_key = "home_assists_count" if "home" in phase else "away_assists_count"
    dict_assists = context.user_data.get(dict_key, {})

    dict_assists[player] = dict_assists.get(player, 0) + 1
    left = context.user_data.get("assists_to_pick", 0) - 1
    context.user_data["assists_to_pick"] = left
    context.user_data[dict_key] = dict_assists

    team_name = context.user_data.get("report_home_team") if "home" in phase else context.user_data.get("report_away_team")

    if left > 0:
        await render_squad_assists_picker(update, context, team_name)
    else:
        if phase == "home_assists":
            await start_away_goals_picker(update, context)
        else:
            await prompt_photo_upload(update, context)

async def cb_skip_assists(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    phase = context.user_data.get("current_picking_phase", "home_assists")
    if phase == "home_assists":
        await start_away_goals_picker(update, context)
    else:
        await prompt_photo_upload(update, context)

async def prompt_photo_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    context.user_data["awaiting_report_photo"] = True
    match_id = context.user_data.get('reporting_match_id')
    user_id = query.from_user.id if query else update.effective_user.id
    cancel_cb = get_match_cancel_cb(context, user_id, match_id)

    text = (
        "📸 **Прикрепление скриншота результата**\n\n"
        "Пожалуйста, отправьте **от 1 до 3 скриншотов** результата сыгранной игры."
    )
    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data=cancel_cb)]]
    markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)

    return REPORT_SCORE_PHOTO

async def save_report_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or (update.effective_chat and update.effective_chat.type != "private"):
        return REPORT_SCORE_PHOTO

    photo_id = None
    if update.message.photo:
        photo_id = update.message.photo[-1].file_id
    elif update.message.document:
        photo_id = update.message.document.file_id

    if not photo_id:
        await update.message.reply_text("❌ Пожалуйста, отправьте фото скриншота результата.")
        return REPORT_SCORE_PHOTO

    media_group_id = update.message.media_group_id
    if media_group_id:
        processed_groups = context.user_data.setdefault("processed_media_groups", set())
        photos_list = context.user_data.get("ai_photos_list", [])
        if photo_id not in photos_list:
            photos_list.append(photo_id)
        context.user_data["ai_photos_list"] = photos_list

        if media_group_id in processed_groups:
            return REPORT_SCORE_PHOTO
        processed_groups.add(media_group_id)
        await asyncio.sleep(0.6)

    match_id = context.user_data.get("reporting_match_id")
    match = await asyncio.to_thread(database.get_match, match_id) if match_id else None
    home_team = match.get("player1_team") if match else "Хозяева"
    away_team = match.get("player2_team") if match else "Гости"
    context.user_data["report_home_team"] = home_team
    context.user_data["report_away_team"] = away_team

    reporting_mode = context.user_data.get("reporting_mode", "auto")

    if reporting_mode == "auto" and config.GEMINI_API_KEY:
        photos_list = context.user_data.get("ai_photos_list", [])
        if photo_id not in photos_list:
            photos_list.append(photo_id)
        context.user_data["ai_photos_list"] = photos_list
        context.user_data["report_photo_id"] = photos_list[0]

        match_id = context.user_data.get("reporting_match_id")
        n_photos = len(photos_list)
        collected_text = (
            f"📸 <b>Скриншот {n_photos}/3 принят.</b>\n\n"
            f"Отправьте остальные скриншоты (вертикальная колонка голов и/или таблица статистики), "
            f"затем нажмите кнопку распознавания."
        )
        keyboard = [
            [InlineKeyboardButton(f"🔍 Распознать результат ({n_photos})", callback_data=f"ai_recognize_now_{match_id}")],
            [InlineKeyboardButton("✏️ Ввести вручную", callback_data=f"cb_report_choice_manual_{match_id}")],
        ]
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=collected_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return REPORT_SCORE_PHOTO

    context.user_data["report_photo_id"] = photo_id
    context.user_data.pop("awaiting_report_photo", None)

    h_goals = context.user_data.get("home_goals_count", {})
    a_goals = context.user_data.get("away_goals_count", {})
    h_assists = context.user_data.get("home_assists_count", {})
    a_assists = context.user_data.get("away_assists_count", {})

    h_goals_summary = ", ".join([f"{p} ({c})" for p, c in h_goals.items()]) if h_goals else "Нет"
    a_goals_summary = ", ".join([f"{p} ({c})" for p, c in a_goals.items()]) if a_goals else "Нет"
    h_assists_summary = ", ".join([f"{p} ({c})" for p, c in h_assists.items()]) if h_assists else "Нет"
    a_assists_summary = ", ".join([f"{p} ({c})" for p, c in a_assists.items()]) if a_assists else "Нет"

    h_score = context.user_data.get("report_home_goals", 0)
    a_score = context.user_data.get("report_away_goals", 0)

    text = (
        f"📊 <b>Проверьте данные перед сохранением:</b>\n\n"
        f"🏟 <b>Матч #{match_id}</b>\n"
        f"🏠 <b>{safe_escape(home_team)}</b> {h_score} : {a_score} <b>{safe_escape(away_team)}</b> ✈️\n\n"
        f"⚽ <b>Голы ({safe_escape(home_team)}):</b> {safe_escape(h_goals_summary)}\n"
        f"🎯 <b>Ассисты ({safe_escape(home_team)}):</b> {safe_escape(h_assists_summary)}\n\n"
        f"⚽ <b>Голы ({safe_escape(away_team)}):</b> {safe_escape(a_goals_summary)}\n"
        f"🎯 <b>Ассисты ({safe_escape(away_team)}):</b> {safe_escape(a_assists_summary)}\n\n"
        f"📸 <i>Скриншот(ы) прикреплены.</i>"
    )

    is_admin_user = is_admin(update.effective_user.id) or context.user_data.get("is_admin_reporting", False)
    cancel_cb = f"admin_view_match_{match_id}" if is_admin_user else f"cabinet_view_match_{match_id}"
    confirm_text = "✅ Сохранить и занести результат" if is_admin_user else "✅ Подтвердить и занести результат"

    keyboard = [
        [InlineKeyboardButton(confirm_text, callback_data=f"cb_submit_report_to_guest_{match_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=cancel_cb)]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_photo(chat_id=update.effective_user.id, photo=photo_id, caption=text, parse_mode="HTML", reply_markup=markup)
    return ConversationHandler.END

async def ai_recognize_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run AI recognition on all collected screenshots and show the result for confirmation."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    user_id = query.from_user.id

    match_id = None
    try:
        match_id = int(query.data.replace("ai_recognize_now_", ""))
    except (ValueError, TypeError):
        match_id = context.user_data.get("reporting_match_id")

    photos_list = context.user_data.get("ai_photos_list", [])
    if not photos_list:
        await query.message.reply_text("⚠️ Не найдено скриншотов. Отправьте фото заново.")
        return

    status_msg = None
    try:
        status_msg = await query.message.reply_text("🤖 <i>ИИ распознаёт результат со скриншотов...</i>", parse_mode="HTML")

        downloaded_bytes = []
        for p_id in photos_list[:3]:
            f_obj = await context.bot.get_file(p_id)
            img_b = await f_obj.download_as_bytearray()
            downloaded_bytes.append(bytes(img_b))

        ai_res = await asyncio.to_thread(ai_recognizer.recognize_match_screenshots_bytes, downloaded_bytes)

        match = await asyncio.to_thread(database.get_match, match_id) if match_id else None
        home_team = match.get("player1_team") if match else "Хозяева"
        away_team = match.get("player2_team") if match else "Гости"
        context.user_data["report_home_team"] = home_team
        context.user_data["report_away_team"] = away_team

        if ai_res and ("home_score" in ai_res) and ("away_score" in ai_res):
            s1_goals = ai_res.get("side1_goals") or ai_res.get("home_goals") or []
            s2_goals = ai_res.get("side2_goals") or ai_res.get("away_goals") or []
            s1_assists = ai_res.get("side1_assists") or ai_res.get("home_assists") or []
            s2_assists = ai_res.get("side2_assists") or ai_res.get("away_assists") or []

            is_single_timeline = bool(ai_res.get("is_single_timeline", False))

            h_goals, a_goals, h_assists, a_assists, is_side1_home = await asyncio.to_thread(
                match_and_enrich_squad,
                s1_goals, s2_goals, s1_assists, s2_assists,
                home_team, away_team,
                is_single_timeline=is_single_timeline,
            )

            if is_single_timeline:
                h_score = sum(h_goals.values())
                a_score = sum(a_goals.values())
            else:
                if is_side1_home:
                    h_score = int(ai_res.get("home_score", sum(h_goals.values())))
                    a_score = int(ai_res.get("away_score", sum(a_goals.values())))
                else:
                    h_score = int(ai_res.get("away_score", sum(h_goals.values())))
                    a_score = int(ai_res.get("home_score", sum(a_goals.values())))

            context.user_data["report_home_goals"] = h_score
            context.user_data["report_away_goals"] = a_score
            context.user_data["home_goals_count"] = h_goals
            context.user_data["away_goals_count"] = a_goals
            context.user_data["home_assists_count"] = h_assists
            context.user_data["away_assists_count"] = a_assists
            context.user_data["is_single_timeline"] = bool(is_single_timeline)
            context.user_data["report_photo_id"] = photos_list[0] if photos_list else context.user_data.get("report_photo_id")

            h_goals_summary = ", ".join([f"{p} ({c})" for p, c in h_goals.items()]) if h_goals else "Нет"
            a_goals_summary = ", ".join([f"{p} ({c})" for p, c in a_goals.items()]) if a_goals else "Нет"
            h_assists_summary = ", ".join([f"{p} ({c})" for p, c in h_assists.items()]) if h_assists else "Нет"
            a_assists_summary = ", ".join([f"{p} ({c})" for p, c in a_assists.items()]) if a_assists else "Нет"

            h_assists_str = safe_escape(h_assists_summary) if not is_single_timeline else "<i>не отображаются в данном формате скриншота</i>"
            a_assists_str = safe_escape(a_assists_summary) if not is_single_timeline else "<i>не отображаются в данном формате скриншота</i>"

            text = (
                f"🤖 <b>ИИ автоматически распознал результат со скриншота:</b>\n\n"
                f"🏟 <b>Матч #{match_id}</b>\n"
                f"🏠 <b>{safe_escape(home_team)}</b> {h_score} : {a_score} <b>{safe_escape(away_team)}</b> ✈️\n\n"
                f"⚽ <b>Голы ({safe_escape(home_team)}):</b> {safe_escape(h_goals_summary)}\n"
                f"🎯 <b>Ассисты ({safe_escape(home_team)}):</b> {h_assists_str}\n\n"
                f"⚽ <b>Голы ({safe_escape(away_team)}):</b> {safe_escape(a_goals_summary)}\n"
                f"🎯 <b>Ассисты ({safe_escape(away_team)}):</b> {a_assists_str}\n\n"
                f"📸 <i>Скриншот(ы) прикреплены.</i>"
            )

            is_admin_user = is_admin(user_id) or context.user_data.get("is_admin_reporting", False)
            cancel_cb = f"admin_view_match_{match_id}" if is_admin_user else f"cabinet_view_match_{match_id}"
            manual_cb = f"cb_report_choice_manual_{match_id}"

            keyboard = [
                [InlineKeyboardButton("✅ Всё верно (Сохранить и занести результат)", callback_data=f"cb_confirm_ai_final_{match_id}")],
                [InlineKeyboardButton("✏️ Изменить вручную", callback_data=manual_cb)],
                [InlineKeyboardButton("❌ Отмена", callback_data=cancel_cb)]
            ]
            markup = InlineKeyboardMarkup(keyboard)

            photo_to_show = photos_list[0] if photos_list else context.user_data.get("report_photo_id")
            await context.bot.send_photo(chat_id=user_id, photo=photo_to_show, caption=text, parse_mode="HTML", reply_markup=markup)
        else:
            context.user_data["report_photo_id"] = photos_list[0] if photos_list else context.user_data.get("report_photo_id")
            cancel_cb = get_match_cancel_cb(context, user_id, match_id)
            fail_text = (
                "⚠️ <b>Не удалось автоматически распознать результат со скриншотов.</b>\n\n"
                "Пожалуйста, выберите способ внесения результата вручную:"
            )
            keyboard = [
                [InlineKeyboardButton("✍️ Ввести результат вручную", callback_data=f"cb_report_choice_manual_{match_id}")],
                [InlineKeyboardButton("❌ Отмена", callback_data=cancel_cb)]
            ]
            await query.message.reply_text(fail_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.exception("AI Vision processing error")
        context.user_data["report_photo_id"] = photos_list[0] if photos_list else context.user_data.get("report_photo_id")
        await query.message.reply_text("⚠️ Ошибка при распознавании скриншотов. Попробуйте ещё раз.")
    finally:
        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass

def build_formatted_match_post(
    round_number: int | str,
    home_team: str,
    away_team: str,
    h_score: int,
    a_score: int,
    p1_username: str | None = None,
    p2_username: str | None = None,
    h_goals: dict | list | None = None,
    a_goals: dict | list | None = None,
    h_assists: dict | list | None = None,
    a_assists: dict | list | None = None,
    is_single_timeline: bool = False,
    is_pm: bool = False,
    pm_title: str = "🎉 <b>Результат успешно занесен в лигу!</b>",
    match_id: int | None = None,
    is_draft: bool = False
) -> str:
    """
    Constructs a unified match result text block with goals and assists for PM notifications and group posts.
    """
    home_team_esc = safe_escape(home_team)
    away_team_esc = safe_escape(away_team)

    def _format_events(data) -> str:
        if not data:
            return ""
        if isinstance(data, dict):
            items = [f"{p} ({c})" if c > 1 else f"{p} (1)" for p, c in data.items() if c > 0]
            return ", ".join(items)
        if isinstance(data, list):
            return ", ".join(data)
        return str(data)

    h_goals_str = _format_events(h_goals)
    a_goals_str = _format_events(a_goals)
    h_assists_str = _format_events(h_assists)
    a_assists_str = _format_events(a_assists)

    lines = []
    # Home Team Stats
    if h_score > 0:
        lines.append(f"⚽ <b>Голы ({home_team_esc}):</b> {safe_escape(h_goals_str) if h_goals_str else 'не указаны'}")
        if is_single_timeline:
            lines.append(f"🎯 <b>Ассисты ({home_team_esc}):</b> <i>не отображаются в данном формате скриншота</i>")
        else:
            lines.append(f"🎯 <b>Ассисты ({home_team_esc}):</b> {safe_escape(h_assists_str) if h_assists_str else 'Нет'}")

    # Away Team Stats
    if a_score > 0:
        lines.append(f"⚽ <b>Голы ({away_team_esc}):</b> {safe_escape(a_goals_str) if a_goals_str else 'не указаны'}")
        if is_single_timeline:
            lines.append(f"🎯 <b>Ассисты ({away_team_esc}):</b> <i>не отображаются в данном формате скриншота</i>")
        else:
            lines.append(f"🎯 <b>Ассисты ({away_team_esc}):</b> {safe_escape(a_assists_str) if a_assists_str else 'Нет'}")

    events_block = ("\n\n" + "\n".join(lines)) if lines else ""

    match_info = database.get_match(match_id) if match_id else None
    is_cup = match_info and match_info.get("tournament_type") == "cup"

    cup_stage = match_info.get("cup_stage", "1/8") if match_info else "1/8"
    g_num = match_info.get("game_num_in_series", 1) if match_info else 1
    series_info_text = ""

    if is_cup and match_info.get("cup_series_id"):
        s_id = match_info["cup_series_id"]
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("SELECT team1_name, team2_name, team1_wins, team2_wins, winner_name, status FROM cup_series WHERE id = ?", (s_id,))
            s_row = c.fetchone()
            if s_row:
                t1 = safe_escape(s_row["team1_name"])
                t2 = safe_escape(s_row["team2_name"])
                w1 = s_row["team1_wins"]
                w2 = s_row["team2_wins"]
                series_info_text = f"\n📊 <b>Счёт серии (Best-of-3):</b> {t1} {w1} : {w2} {t2}"
                if s_row["winner_name"]:
                    series_info_text += f"\n🏆 <b>Победитель серии: {safe_escape(s_row['winner_name'])}! Проходит в следующий раунд!</b>"

    p1_clean = safe_escape(p1_username.lstrip('@')) if p1_username else ""
    p2_clean = safe_escape(p2_username.lstrip('@')) if p2_username else ""
    p1_str = f" (@{p1_clean})" if p1_clean else ""
    p2_str = f" (@{p2_clean})" if p2_clean else ""

    if is_cup:
        title_stage = f"{cup_stage} Финала" if cup_stage != "final" else "ФИНАЛ"
        if is_draft:
            header = (
                f"📝 <b>ЧЕРНОВИК РЕЗУЛЬТАТА | КУБОК КПЛ - {title_stage} (Игра {g_num})</b>\n\n"
                f"🏠 <b>{home_team_esc}</b>{p1_str} <b>{h_score} : {a_score}</b> <b>{away_team_esc}</b>{p2_str} ✈️"
                f"{series_info_text}"
            )
            footer = "\n\n⏳ <i>Ожидает подтверждения администратором...</i>"
        elif is_pm:
            match_id_str = f" #{match_id}" if match_id else ""
            header = (
                f"🏆 <b>Результат кубкового матча занесен!</b>\n\n"
                f"🏟 <b>Кубок КПЛ | {title_stage} (Игра {g_num})</b>{match_id_str}\n"
                f"🏠 <b>{home_team_esc}</b> <b>{h_score} : {a_score}</b> <b>{away_team_esc}</b> ✈️"
                f"{series_info_text}"
            )
            footer = "\n\n📊 <i>Сетка Кубка и статистика игроков обновлены.</i>"
        else:
            header = (
                f"🏆 <b>КУБОК КПЛ | {title_stage} (Игра {g_num})</b>\n\n"
                f"🏠 <b>{home_team_esc}</b>{p1_str} <b>{h_score} : {a_score}</b> <b>{away_team_esc}</b>{p2_str} ✈️"
                f"{series_info_text}"
            )
            footer = "\n\n📸 <i>Результат официально занесен в сетку Кубка КПЛ.</i>"
    else:
        if is_draft:
            header = (
                f"📝 <b>ЧЕРНОВИК РЕЗУЛЬТАТА | Тур {round_number}</b>\n\n"
                f"🏠 <b>{home_team_esc}</b>{p1_str} <b>{h_score} : {a_score}</b> <b>{away_team_esc}</b>{p2_str} ✈️"
            )
            footer = "\n\n⏳ <i>Ожидает подтверждения администратором...</i>"
        elif is_pm:
            match_id_str = f" #{match_id}" if match_id else ""
            header = (
                f"{pm_title}\n\n"
                f"🏟 <b>Матч{match_id_str} (Тур {round_number})</b>\n"
                f"🏠 <b>{home_team_esc}</b> <b>{h_score} : {a_score}</b> <b>{away_team_esc}</b> ✈️"
            )
            footer = "\n\n📊 <i>Турнирная таблица и статистика игроков обновлены.</i>"
        else:
            header = (
                f"🏆 <b>РЕЗУЛЬТАТ МАТЧА | Тур {round_number}</b>\n\n"
                f"🏠 <b>{home_team_esc}</b>{p1_str} <b>{h_score} : {a_score}</b> <b>{away_team_esc}</b>{p2_str} ✈️"
            )
            footer = "\n\n📸 <i>Результат официально занесен в турнирную таблицу.</i>"

    return f"{header}{events_block}{footer}"

async def cb_confirm_ai_final(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Instantly save and finalize match score in database from AI Vision result."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    match_id = int(query.data.replace("cb_confirm_ai_final_", ""))
    match = await asyncio.to_thread(database.get_match, match_id)
    if not match:
        await context.bot.send_message(chat_id=query.from_user.id, text="❌ Матч не найден.")
        return

    if match['status'] == 'confirmed':
        await context.bot.send_message(chat_id=query.from_user.id, text="✅ Результат уже зафиксирован!")
        return

    user_id = query.from_user.id
    h_score = context.user_data.get("report_home_goals", 0)
    a_score = context.user_data.get("report_away_goals", 0)
    h_goals = context.user_data.get("home_goals_count", {})
    a_goals = context.user_data.get("away_goals_count", {})
    h_assists = context.user_data.get("home_assists_count", {})
    a_assists = context.user_data.get("away_assists_count", {})
    photo_id = context.user_data.get("report_photo_id")
    is_single_tl = bool(context.user_data.get("is_single_timeline", False))

    home_team = match['player1_team'] or match['player1_nickname']
    away_team = match['player2_team'] or match['player2_nickname']

    events = []
    for p, c in h_goals.items():
        events.append((home_team, p, "goal", c))
    for p, c in a_goals.items():
        events.append((away_team, p, "goal", c))
    for p, c in h_assists.items():
        events.append((home_team, p, "assist", c))
    for p, c in a_assists.items():
        events.append((away_team, p, "assist", c))

    next_stage = await asyncio.to_thread(database.confirm_and_finalize_match, match_id, h_score, a_score, events, reporter_id=user_id, photo_id=photo_id)
    if next_stage:
        from handlers.admin import notify_cup_stage_opened
        await notify_cup_stage_opened(context.bot, next_stage)
    await refresh_debts_summary(context)
    await refresh_league_table(context)

    # 1. PM to reporter
    reporter_text = build_formatted_match_post(
        round_number=match['round_number'],
        home_team=home_team,
        away_team=away_team,
        h_score=h_score,
        a_score=a_score,
        h_goals=h_goals,
        a_goals=a_goals,
        h_assists=h_assists,
        a_assists=a_assists,
        is_single_timeline=is_single_tl,
        is_pm=True,
        pm_title="🎉 <b>Результат успешно занесен в лигу!</b>",
        match_id=match_id
    )

    is_admin_user = is_admin(user_id) or context.user_data.get("is_admin_reporting", False)
    if is_admin_user:
        back_buttons = [
            [InlineKeyboardButton("« Назад к матчу", callback_data=f"admin_view_match_{match_id}")],
            [InlineKeyboardButton("« Назад к туру", callback_data=f"admin_round_matches_{match['round_number']}")]
        ]
    else:
        back_buttons = [[InlineKeyboardButton("« К своим матчам", callback_data="cabinet_my_matches")]]

    try:
        await query.edit_message_caption(caption=reporter_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(back_buttons))
    except Exception:
        await context.bot.send_message(chat_id=user_id, text=reporter_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(back_buttons))

    # 2. PM to players
    players_to_notify = []
    if user_id == match['player1_id']:
        if match['player2_id']: players_to_notify.append(match['player2_id'])
    elif user_id == match['player2_id']:
        if match['player1_id']: players_to_notify.append(match['player1_id'])
    else:
        if match['player1_id']: players_to_notify.append(match['player1_id'])
        if match['player2_id']: players_to_notify.append(match['player2_id'])

    opp_text = build_formatted_match_post(
        round_number=match['round_number'],
        home_team=home_team,
        away_team=away_team,
        h_score=h_score,
        a_score=a_score,
        h_goals=h_goals,
        a_goals=a_goals,
        h_assists=h_assists,
        a_assists=a_assists,
        is_single_timeline=is_single_tl,
        is_pm=True,
        pm_title="🔔 <b>Результат вашего матча занесен в лигу!</b>",
        match_id=match_id
    )

    for p_id in set(players_to_notify):
        await safe_send_notification(context.bot, p_id, opp_text)

    # 3. Post to Group
    main_group_id = await asyncio.to_thread(database.get_group_id)
    results_topic_id = (await asyncio.to_thread(database.get_config, "results_topic_id")) or (await asyncio.to_thread(database.get_config, "reports_topic_id"))
    if main_group_id:
        group_text = build_formatted_match_post(
            round_number=match['round_number'],
            home_team=home_team,
            away_team=away_team,
            h_score=h_score,
            a_score=a_score,
            p1_username=match['player1_username'],
            p2_username=match['player2_username'],
            h_goals=h_goals,
            a_goals=a_goals,
            h_assists=h_assists,
            a_assists=a_assists,
            is_single_timeline=is_single_tl,
            is_pm=False,
            match_id=match_id
        )
        try:
            kwargs = {"chat_id": main_group_id, "caption": group_text, "parse_mode": "HTML"}
            if results_topic_id:
                kwargs["message_thread_id"] = int(results_topic_id)
            if photo_id:
                await context.bot.send_photo(photo=photo_id, **kwargs)
            else:
                kwargs["text"] = group_text
                kwargs.pop("caption", None)
                await context.bot.send_message(**kwargs)
        except Exception as e:
            logger.exception("Failed to post result to group")

async def submit_report_to_guest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    match_id = None
    if query.data and query.data.startswith("cb_submit_report_to_guest_"):
        try:
            match_id = int(query.data.replace("cb_submit_report_to_guest_", ""))
        except ValueError:
            pass
    if not match_id:
        match_id = context.user_data.get("reporting_match_id")

    match = await asyncio.to_thread(database.get_match, match_id) if match_id else None
    if not match:
        await context.bot.send_message(chat_id=query.from_user.id, text="❌ Ошибка: матч не найден.")
        return

    if match['status'] == 'confirmed':
        await context.bot.send_message(chat_id=query.from_user.id, text="⛔ Результат этого матча уже занесён в таблицу!")
        return

    submitter_id = query.from_user.id
    is_submitter_home = (submitter_id == match['player1_id'])

    submitter_team = match['player1_team'] or match['player1_nickname'] if is_submitter_home else match['player2_team'] or match['player2_nickname']
    recipient_team = match['player2_team'] or match['player2_nickname'] if is_submitter_home else match['player1_team'] or match['player1_nickname']
    recipient_id = match['player2_id'] if is_submitter_home else match['player1_id']

    hg = context.user_data.get("report_home_goals", 0)
    ag = context.user_data.get("report_away_goals", 0)
    photo_id = context.user_data.get("report_photo_id")

    home_team = match['player1_team'] or match['player1_nickname']
    away_team = match['player2_team'] or match['player2_nickname']

    # Build events list for database (collects goals/assists for both teams if present)
    events = []
    h_goals = context.user_data.get("home_goals_count", {})
    a_goals = context.user_data.get("away_goals_count", {})
    h_assists = context.user_data.get("home_assists_count", {})
    a_assists = context.user_data.get("away_assists_count", {})

    for p, c in h_goals.items():
        events.append((home_team, p, "goal", c))
    for p, c in a_goals.items():
        events.append((away_team, p, "goal", c))
    for p, c in h_assists.items():
        events.append((home_team, p, "assist", c))
    for p, c in a_assists.items():
        events.append((away_team, p, "assist", c))

    # Instant Match Finalization in DB
    next_stage = await asyncio.to_thread(database.confirm_and_finalize_match, match_id, hg, ag, events, reporter_id=submitter_id, photo_id=photo_id)
    if next_stage:
        from handlers.admin import notify_cup_stage_opened
        await notify_cup_stage_opened(context.bot, next_stage)
    await refresh_debts_summary(context)
    await refresh_league_table(context)

    # 1. Respond to Submitter
    submitter_msg = f"🎉 <b>Результат матча #{match_id} ({hg}:{ag}) успешно занесён в турнирную таблицу!</b>"
    try:
        await query.edit_message_caption(caption=submitter_msg, parse_mode="HTML")
    except Exception:
        try:
            await query.edit_message_text(text=submitter_msg, parse_mode="HTML")
        except Exception:
            await context.bot.send_message(chat_id=submitter_id, text=submitter_msg, parse_mode="HTML")

    # 2. Informative notification to players
    h_summary = ", ".join([f"{p} ({c})" for p, c in h_goals.items()]) if h_goals else "Нет"
    a_summary = ", ".join([f"{p} ({c})" for p, c in a_goals.items()]) if a_goals else "Нет"
    h_ast_summary = ", ".join([f"{p} ({c})" for p, c in h_assists.items()]) if h_assists else "Нет"
    a_ast_summary = ", ".join([f"{p} ({c})" for p, c in a_assists.items()]) if a_assists else "Нет"

    notify_text = (
        f"🔔 <b>Результат матча #{match_id} зафиксирован!</b>\n"
        f"🏠 <b>{html.escape(home_team)}</b> {hg} : {ag} <b>{html.escape(away_team)}</b> ✈️\n\n"
        f"⚽ <b>Голы ({html.escape(home_team)}):</b> {html.escape(h_summary)}\n"
        f"🎯 <b>Ассисты ({html.escape(home_team)}):</b> {html.escape(h_ast_summary)}\n\n"
        f"⚽ <b>Голы ({html.escape(away_team)}):</b> {html.escape(a_summary)}\n"
        f"🎯 <b>Ассисты ({html.escape(away_team)}):</b> {html.escape(a_ast_summary)}\n\n"
        f"📊 Данные внесены в турнирную таблицу."
    )

    players_to_notify = [p_id for p_id in (match['player1_id'], match['player2_id']) if p_id and p_id != submitter_id]
    for p_id in players_to_notify:
        try:
            if photo_id:
                await context.bot.send_photo(chat_id=p_id, photo=photo_id, caption=notify_text, parse_mode="HTML")
            else:
                await context.bot.send_message(chat_id=p_id, text=notify_text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Failed to send match notification to player {p_id}: {e}")

    # 3. Post to Main Group & Update Standings Graphic
    await notify_match_confirmed(context, match_id)

async def refresh_debts_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh the debts summary in the ПРЕДЫ thread after a match result is recorded."""
    try:
        from handlers.admin import _post_or_update_debts_in_warns
        await _post_or_update_debts_in_warns(context)
    except Exception as e:
        logger.warning(f"Failed to refresh debts summary: {e}")


async def refresh_league_table(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh the graphic league table in the reports topic after a match result is recorded."""
    try:
        from handlers.base import post_league_table_to_reports
        await post_league_table_to_reports(context)
    except Exception as e:
        logger.warning(f"Failed to refresh league table: {e}")

async def notify_match_confirmed(context: ContextTypes.DEFAULT_TYPE, match_id: int) -> None:
    match = await asyncio.to_thread(database.get_match, match_id)
    if not match:
        return

    home_team = match['player1_team'] or match['player1_nickname']
    away_team = match['player2_team'] or match['player2_nickname']
    p1_score = match['player1_score']
    p2_score = match['player2_score']

    events = await asyncio.to_thread(database.get_match_events, match_id)

    home_goals = [f"{e['player_name']} ({e['count']})" if e['count'] > 1 else f"{e['player_name']} (1)" for e in events if e['event_type'] == 'goal' and e['team_name'].lower() == home_team.lower()]
    away_goals = [f"{e['player_name']} ({e['count']})" if e['count'] > 1 else f"{e['player_name']} (1)" for e in events if e['event_type'] == 'goal' and e['team_name'].lower() == away_team.lower()]

    home_assists = [f"{e['player_name']} ({e['count']})" if e['count'] > 1 else f"{e['player_name']} (1)" for e in events if e['event_type'] == 'assist' and e['team_name'].lower() == home_team.lower()]
    away_assists = [f"{e['player_name']} ({e['count']})" if e['count'] > 1 else f"{e['player_name']} (1)" for e in events if e['event_type'] == 'assist' and e['team_name'].lower() == away_team.lower()]

    pm_text = build_formatted_match_post(
        round_number=match['round_number'],
        home_team=home_team,
        away_team=away_team,
        h_score=p1_score,
        a_score=p2_score,
        h_goals=home_goals,
        a_goals=away_goals,
        h_assists=home_assists,
        a_assists=away_assists,
        is_pm=True,
        pm_title="✅ <b>Матч успешно подтвержден и сыгран!</b>",
        match_id=match_id
    )

    for p_id in (match['player1_id'], match['player2_id']):
        if p_id:
            try:
                await context.bot.send_message(chat_id=p_id, text=pm_text, parse_mode="HTML")
            except Exception as e:
                logger.exception("Failed to send confirmation to player {p_id}")

    results_topic_id = await asyncio.to_thread(database.get_config, "results_topic_id")
    group_id = await asyncio.to_thread(database.get_group_id)
    if group_id:
        group_text = build_formatted_match_post(
            round_number=match['round_number'],
            home_team=home_team,
            away_team=away_team,
            h_score=p1_score,
            a_score=p2_score,
            p1_username=match['player1_username'],
            p2_username=match['player2_username'],
            h_goals=home_goals,
            a_goals=away_goals,
            h_assists=home_assists,
            a_assists=away_assists,
            is_pm=False,
            match_id=match_id
        )

        photo_id = match.get("photo_id")
        try:
            if photo_id:
                kwargs = {"chat_id": group_id, "photo": photo_id, "caption": group_text, "parse_mode": "HTML"}
                if results_topic_id:
                    kwargs["message_thread_id"] = int(results_topic_id)
                await context.bot.send_photo(**kwargs)
            else:
                kwargs = {"chat_id": group_id, "text": group_text, "parse_mode": "HTML"}
                if results_topic_id:
                    kwargs["message_thread_id"] = int(results_topic_id)
                await context.bot.send_message(**kwargs)
        except Exception as e:
            logger.exception("Failed to post result to topic/group")

SQUAD_PHOTO = 101

async def show_my_squad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        user = query.from_user
    else:
        user = update.effective_user

    db_user = await asyncio.to_thread(database.get_user, user.id)
    if not db_user:
        return ConversationHandler.END

    photo_id = db_user['squad_photo_id']
    keyboard = [
        [InlineKeyboardButton("🔄 Обновить состав", callback_data="cabinet_upload_squad")],
        [InlineKeyboardButton("« Назад в кабинет", callback_data="menu_cabinet")]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if photo_id:
        text = "📸 **Ваш текущий состав:**"
        if query:
            try:
                await query.message.delete()
            except Exception:
                pass
        await context.bot.send_photo(chat_id=user.id, photo=photo_id, caption=text, parse_mode="Markdown", reply_markup=markup)
    else:
        text = "📸 **Ваш состав:**\n\nУ вас еще не загружен состав."
        if query:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        else:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
            
    return ConversationHandler.END

async def start_upload_squad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        
    text = "📤 **Загрузка состава**\n\nПожалуйста, отправьте скриншот вашего состава *одним фото*."
    keyboard = [[InlineKeyboardButton("Отмена", callback_data="cabinet_my_squad")]]
    
    if query:
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            await query.message.delete()
            await context.bot.send_message(chat_id=query.from_user.id, text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        
    return SQUAD_PHOTO

async def save_squad_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or (update.effective_chat and update.effective_chat.type != "private"):
        return ConversationHandler.END
        
    # Get the best quality photo
    photo_id = update.message.photo[-1].file_id
    user_id = update.effective_user.id
    
    await asyncio.to_thread(database.update_single_field, user_id, "squad_photo_id", photo_id)
    
    await update.message.reply_text("✅ Состав успешно сохранен!")
    await show_my_squad(update, context)

    group_id = await asyncio.to_thread(database.get_group_id)
    squad_topic_id = await asyncio.to_thread(database.get_config, "squad_topic_id")
    if group_id and squad_topic_id:
        try:
            db_user = await asyncio.to_thread(database.get_user, user_id)
            team_name = db_user['team_name'] if db_user and db_user['team_name'] else "Неизвестный клуб"
            username = update.effective_user.username
            username_str = f"@{username}" if username else update.effective_user.first_name
            caption = f"📸 <b>Обновление состава!</b>\n\n<b>Игрок:</b> {html.escape(username_str)}\n<b>Клуб:</b> {html.escape(team_name)}"
            
            await context.bot.send_photo(
                chat_id=group_id,
                message_thread_id=int(squad_topic_id),
                photo=photo_id,
                caption=caption,
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Error sending squad photo to topic: {e}")

    return ConversationHandler.END

async def cancel_upload_squad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await show_my_squad(update, context)
    return ConversationHandler.END

async def cabinet_view_squad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
        
    await safe_query_answer(query)
    opp_id = int(query.data.replace("cabinet_view_squad_", ""))
    opp_user = await asyncio.to_thread(database.get_user, opp_id)
    
    if not opp_user:
        await safe_query_answer(query, "Игрок не найден.", show_alert=True)
        return
        
    photo_id = opp_user['squad_photo_id']
    team_name = opp_user['team_name'] or opp_user['username']
    
    if photo_id:
        try:
            await context.bot.send_photo(
                chat_id=query.from_user.id, 
                photo=photo_id, 
                caption=f"⚽ Состав команды <b>{html.escape(team_name)}</b>",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.exception("Failed to send squad photo")
    else:
        await safe_query_answer(query, f"❌ Команда {team_name} еще не загрузила свой состав.", show_alert=True)

