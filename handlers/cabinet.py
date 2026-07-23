from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from telegram.ext import ContextTypes, ConversationHandler
import html
import database
from handlers.base import is_admin

import json
import logging
logger = logging.getLogger(__name__)

import asyncio
import telegram.error
import ai_recognizer
from config import GEMINI_API_KEY

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

def match_and_enrich_squad(raw_side1_goals: list[str], raw_side2_goals: list[str], raw_side1_assists: list[str], raw_side2_assists: list[str], home_team: str, away_team: str):
    """
    Determines Home vs Away side, strictly preserves side1 (left) vs side2 (right) goal assignments from screenshot,
    matches player names against DB squad, and auto-adds new squad players to DB if missing.
    Returns: (home_goals_dict, away_goals_dict, home_assists_dict, away_assists_dict, is_side1_home)
    """
    home_squad = database.get_squad(home_team) or []
    away_squad = database.get_squad(away_team) or []

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
            raw_lower = raw_clean.lower()
            matched_name = None

            for squad_p in squad_list:
                sp_lower = squad_p.lower().strip()
                if raw_lower == sp_lower or raw_lower in sp_lower or sp_lower in raw_lower:
                    matched_name = squad_p
                    break
                raw_parts = raw_lower.split()
                sp_parts = sp_lower.split()
                if any(p in sp_parts for p in raw_parts if len(p) > 2):
                    matched_name = squad_p
                    break

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
        logger.error(f"Telegram error sending to {chat_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending to {chat_id}: {e}")
        return False

# Conversation states
GAME_NICKNAME, TEAM_NAME, LEAGUE_NAME, EDITING_FIELD = range(4)
WAITING_FOR_SCORE = 100
SQUAD_PHOTO = 101
REPORT_SCORE_PHOTO = 102
GUEST_DISPUTE_PHOTOS = 103
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
    team = database.get_user_team(user.id)

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
    stats = database.get_player_stats(user.id)
    username_display = f"@{html.escape(user.username)}" if user.username else html.escape(user.first_name)

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

    team = database.get_user_team(user.id)
    keyboard = [[InlineKeyboardButton("« Назад в кабинет", callback_data="menu_cabinet")]]
    markup = InlineKeyboardMarkup(keyboard)

    if not team:
        text = "⚠️ Вы не привязаны к клубу."
        if query:
            await query.edit_message_text(text, reply_markup=markup)
        return

    scorers = database.get_club_top_scorers(team)
    assisters = database.get_club_top_assisters(team)

    text = f"⚽ <b>Статистика игроков клуба {html.escape(team)}:</b>\n"

    if scorers:
        text += "\n<b>🔥 Бомбардиры:</b>\n"
        for i, s in enumerate(scorers, 1):
            text += f"{i}. {html.escape(s['player_name'])} — {s['total']} ⚽\n"
    else:
        text += "\n<i>Пока нет данных о голах.</i>\n"

    if assisters:
        text += "\n<b>👟 Ассистенты:</b>\n"
        for i, a in enumerate(assisters, 1):
            text += f"{i}. {html.escape(a['player_name'])} — {a['total']} 🅰️\n"
    else:
        text += "\n<i>Пока нет данных о передачах.</i>\n"

    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    elif update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)


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
    database.update_profile(user.id, text, "Основная")

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
    database.update_single_field(user.id, "team_name", text)
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

async def safe_edit_or_reply(query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None, parse_mode: str = "Markdown") -> None:
    """Safely edit a message whether it's a text message or photo message, preventing BadRequest errors."""
    if not query:
        return
    user_id = query.from_user.id
    if query.message and query.message.photo:
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)
    else:
        try:
            await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
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
    matches = database.get_pending_matches(user_id)
    
    text = "📋 **Ваши матчи в открытых турах:**\n\n"
    keyboard = []
    
    if not matches:
        text += "У вас нет несыгранных матчей в открытых турах."
    else:
        text += "Выберите матч для просмотра и ввода результата:"
        for m in matches:
            opp = m['opponent_team'] or m['opponent_username']
            btn_text = f"Тур {m['round_number']}: 🆚 {opp}"
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
    
    m = database.get_match(match_id)
    if not m:
        await safe_edit_or_reply(query, context, "Матч не найден.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Назад", callback_data="cabinet_my_matches")]]))
        return
        
    round_info = database.get_round_info(m['round_number'])
    deadline_text = round_info.get("deadline") if round_info else None
    
    is_overdue = False
    if deadline_text:
        import datetime
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
    if m['status'] == 'reported':
        status_text = "⚖️ Проверка"
    elif m['status'] == 'disputed':
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

    text = f"🏟 **МАТЧ #{m['id']} | Тур {m['round_number']}**\n"
    text += f"Статус: {status_text}\n"
    if deadline_text:
        text += f"⏳ Дедлайн: {deadline_text}\n"
    text += time_info_text
    text += "📜 [Правила турнира](https://t.me/fifulatyrniru/3405)\n"
        
    text += f"\n🏠 **Вы** {score_str} **{opp_team}** ✈️\n"
    text += "────────────────────────\n\n"
    
    if m['status'] == 'pending':
        if is_overdue and not m.get('is_extended'):
            text += "⏳ **Дедлайн истек.** Результат принимает администратор."
        else:
            if m['player1_id'] == user_id:
                text += "🏠 **Вы играете Дома.** Пожалуйста, введите результат матча."
            else:
                text += "✈️ **Вы играете в Гостях.** Ожидайте, пока хозяева поля введут результат матча."
    elif m['status'] == 'reported':
        if m.get('reported_by') == user_id:
            text += "⏳ Результат отправлен сопернику. Ожидайте подтверждения."
        else:
            text += "🔔 **Хозяева ввели результат.** Проверьте сообщения от бота для подтверждения."
    elif m['status'] == 'disputed':
        text += "⚠️ **Матч оспорен.** Ожидайте решения администратора."
    elif m['status'] == 'confirmed':
        text += "✅ **Матч сыгран и результат подтвержден.**"
        
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
    
    if m['status'] == 'pending' and m['player1_id'] == user_id and (not is_overdue or m.get('is_extended')):
        keyboard.append([InlineKeyboardButton("📝 Ввести результат", callback_data=f"cabinet_report_score_{match_id}")])
        
    if m['status'] == 'reported' and m.get('reported_by') == user_id:
        keyboard.append([InlineKeyboardButton("✏️ Отменить отправку", callback_data=f"cabinet_cancel_report_{match_id}")])
        
    keyboard.append([InlineKeyboardButton("📜 Правила турнира", url="https://t.me/fifulatyrniru/3405")])
    keyboard.append([InlineKeyboardButton("🔙 К списку матчей", callback_data="cabinet_my_matches")])
    
    await safe_edit_or_reply(query, context, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def cb_propose_time_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user with quick time choices or custom text entry."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    match_id = int(query.data.replace("cb_propose_time_prompt_", ""))
    match = database.get_match(match_id)
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

    database.propose_match_time(match_id, user_id, time_str)
    match = database.get_match(match_id)

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

    database.accept_match_time(match_id)
    match = database.get_match(match_id)

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

    database.propose_match_time(match_id, user_id, time_str)
    match = database.get_match(match_id)

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
    m_info = database.get_match(match_id)
    if m_info:
        round_info = database.get_round_info(m_info['round_number'])
        deadline_text = round_info.get("deadline") if round_info else None
        
        if m_info['player1_id'] == user_id:
            opp_team = m_info['player2_team'] or m_info['player2_nickname']
            score_str = f"{m_info['player1_score']}:{m_info['player2_score']}" if m_info['player1_score'] is not None else "-:-"
        else:
            opp_team = m_info['player1_team'] or m_info['player1_nickname']
            score_str = f"{m_info['player2_score']}:{m_info['player1_score']}" if m_info['player1_score'] is not None else "-:-"

        kb_match = [
            [InlineKeyboardButton("👀 Состав", callback_data=f"cabinet_view_squad_{match_id}"), InlineKeyboardButton("🕵️‍♂️ Скаут", callback_data="stub")],
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
    matches = database.get_match_history(user_id)
    
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

async def start_score_reporting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start score reporting for Home OR Away player."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    match_id = int(query.data.replace("cabinet_report_score_", ""))
    match = database.get_match(match_id)
    if not match:
        await query.edit_message_text("❌ Матч не найден.")
        return

    user_id = query.from_user.id
    if user_id not in (match['player1_id'], match['player2_id']):
        await query.answer("⛔ Вы не являетесь участником этого матча.", show_alert=True)
        return

    if match['status'] == 'completed':
        await query.answer("⏳ Результат этого матча уже внесен и занесен в лигу.", show_alert=True)
        return

    context.user_data["reporting_match_id"] = match_id
    context.user_data["report_home_team"] = match['player1_team'] or match['player1_nickname']
    context.user_data["report_away_team"] = match['player2_team'] or match['player2_nickname']
    context.user_data["reporter_id"] = user_id

    home_team = context.user_data["report_home_team"]
    away_team = context.user_data["report_away_team"]

    text = (
        f"⚽ <b>Ввод результата матча #{match_id}</b>\n"
        f"🏟 <b>Тур {match['round_number']}</b>\n"
        f"🏠 <b>{safe_escape(home_team)}</b> vs <b>{safe_escape(away_team)}</b> ✈️\n\n"
        f"Выберите удобный способ внесения результата:"
    )

    keyboard = [
        [InlineKeyboardButton("⚡ Автоматический ввод (по фото)", callback_data=f"cb_report_choice_auto_{match_id}")],
        [InlineKeyboardButton("✍️ Ручной ввод", callback_data=f"cb_report_choice_manual_{match_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"cabinet_view_match_{match_id}")]
    ]

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

async def cb_report_choice_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    match_id = int(query.data.replace("cb_report_choice_auto_", ""))
    context.user_data["reporting_match_id"] = match_id
    context.user_data["reporting_mode"] = "auto"
    context.user_data["awaiting_report_photo"] = True
    context.user_data["ai_photos_list"] = []

    text = (
        "📸 <b>Автоматический ввод по фото</b>\n\n"
        "Пожалуйста, отправьте <b>1 или 2 скриншота</b> матча строго с статистикой(голы и ассисты).\n\n"
        "💡 <i>Вы можете отправить 1 фото или сразу 2 фото альбомом.</i>"
    )
    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data=f"cabinet_view_match_{match_id}")]]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

async def cb_report_choice_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    match_id = int(query.data.replace("cb_report_choice_manual_", ""))
    context.user_data["reporting_match_id"] = match_id
    context.user_data["reporting_mode"] = "manual"

    home_team = context.user_data.get("report_home_team", "Хозяева")
    away_team = context.user_data.get("report_away_team", "Гости")

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
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data=f"cabinet_view_match_{match_id}")])

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

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
        f"⚽ **Ввод результата матча #{match_id}**\n"
        f"🏠 **{home_team}** ({hg}) vs **{away_team}** (?)\n\n"
        f"Теперь выберите, сколько забил соперник (**{away_team}**):"
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
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data=f"cabinet_view_match_{match_id}")])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def cb_report_away_goals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    ag = int(query.data.replace("cb_report_ag_", ""))
    context.user_data["report_away_goals"] = ag

    hg = context.user_data.get("report_home_goals", 0)
    home_team = context.user_data.get("report_home_team")

    # If home goals > 0, pick goalscorers for home team
    if hg > 0:
        context.user_data["goals_to_pick"] = hg
        context.user_data["home_goals_count"] = {}
        await render_squad_goals_picker(update, context, home_team)
    else:
        # Move directly to assists
        await start_assists_picker(update, context, home_team)

async def render_squad_goals_picker(update: Update, context: ContextTypes.DEFAULT_TYPE, team_name: str) -> None:
    query = update.callback_query
    left = context.user_data.get("goals_to_pick", 0)
    picked_dict = context.user_data.get("home_goals_count", {})

    squad = database.get_squad(team_name)

    summary_str = ""
    if picked_dict:
        summary_str = "\n\n⚽ **Уже выбрано:**\n" + "\n".join([f"• {p}: {c}" for p, c in picked_dict.items()])

    text = (
        f"⚽ **Авторы голов вашей команды ({html.escape(team_name)})**\n"
        f"Осталось распределить голов: **{left}**{summary_str}\n\n"
        f"Нажимайте на кнопки с игроками вашего состава:"
    )

    keyboard = []
    if not squad:
        text += "\n\n⚠️ *Состав вашей команды пока не добавлен в систему.*"
        keyboard.append([InlineKeyboardButton("⏩ Пропустить ввод авторов голов", callback_data="cb_skip_goals")])
    else:
        row = []
        for player in squad:
            row.append(InlineKeyboardButton(f"🏃‍♂️ {player}", callback_data=f"cb_pick_goal_{player}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("⏩ Пропустить", callback_data="cb_skip_goals")])

    match_id = context.user_data.get("reporting_match_id")
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data=f"cabinet_view_match_{match_id}")])

    markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)

async def cb_pick_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    player = query.data.replace("cb_pick_goal_", "")
    left = context.user_data.get("goals_to_pick", 0)
    dict_goals = context.user_data.get("home_goals_count", {})

    dict_goals[player] = dict_goals.get(player, 0) + 1
    left -= 1
    context.user_data["goals_to_pick"] = left
    context.user_data["home_goals_count"] = dict_goals

    home_team = context.user_data.get("report_home_team")
    if left > 0:
        await render_squad_goals_picker(update, context, home_team)
    else:
        # Done with goals -> Move to assists
        await start_assists_picker(update, context, home_team)

async def cb_skip_goals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    home_team = context.user_data.get("report_home_team")
    await start_assists_picker(update, context, home_team)

async def start_assists_picker(update: Update, context: ContextTypes.DEFAULT_TYPE, team_name: str) -> None:
    hg = context.user_data.get("report_home_goals", 0)
    context.user_data["assists_to_pick"] = hg
    context.user_data["home_assists_count"] = {}
    await render_squad_assists_picker(update, context, team_name)

async def render_squad_assists_picker(update: Update, context: ContextTypes.DEFAULT_TYPE, team_name: str) -> None:
    query = update.callback_query
    left = context.user_data.get("assists_to_pick", 0)
    picked_dict = context.user_data.get("home_assists_count", {})

    squad = database.get_squad(team_name)

    summary_str = ""
    if picked_dict:
        summary_str = "\n\n🎯 **Уже выбрано:**\n" + "\n".join([f"• {p}: {c}" for p, c in picked_dict.items()])

    text = (
        f"🎯 **Авторы ассистов вашей команды ({html.escape(team_name)})**\n"
        f"Осталось ассистов (макс {context.user_data.get('report_home_goals', 0)}): **{left}**{summary_str}\n\n"
        f"Нажимайте на кнопки с игроками или пропустите:"
    )

    keyboard = []
    if squad and left > 0:
        row = []
        for player in squad:
            row.append(InlineKeyboardButton(f"🎯 {player}", callback_data=f"cb_pick_assist_{player}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    keyboard.append([InlineKeyboardButton("⏩ Пропустить ассисты", callback_data="cb_skip_assists")])
    match_id = context.user_data.get("reporting_match_id")
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data=f"cabinet_view_match_{match_id}")])

    markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)

async def cb_pick_assist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    player = query.data.replace("cb_pick_assist_", "")
    left = context.user_data.get("assists_to_pick", 0)
    dict_assists = context.user_data.get("home_assists_count", {})

    dict_assists[player] = dict_assists.get(player, 0) + 1
    left -= 1
    context.user_data["assists_to_pick"] = left
    context.user_data["home_assists_count"] = dict_assists

    home_team = context.user_data.get("report_home_team")
    if left > 0:
        await render_squad_assists_picker(update, context, home_team)
    else:
        await prompt_photo_upload(update, context)

async def cb_skip_assists(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    await prompt_photo_upload(update, context)

async def prompt_photo_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    context.user_data["awaiting_report_photo"] = True
    text = (
        "📸 **Прикрепление скриншота результата**\n\n"
        "Пожалуйста, отправьте **1 скриншот/фотография** результата сыгранной игры."
    )
    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data=f"cabinet_view_match_{context.user_data.get('reporting_match_id')}")]]
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

    match_id = context.user_data.get("reporting_match_id")
    match = database.get_match(match_id) if match_id else None
    home_team = context.user_data.get("report_home_team") or (match.get("player1_team") if match else "Хозяева")
    away_team = context.user_data.get("report_away_team") or (match.get("player2_team") if match else "Гости")

    reporting_mode = context.user_data.get("reporting_mode", "auto")

    if reporting_mode == "auto" and GEMINI_API_KEY:
        photos_list = context.user_data.get("ai_photos_list", [])
        photos_list.append(photo_id)
        context.user_data["ai_photos_list"] = photos_list
        context.user_data["report_photo_id"] = photos_list[0]

        status_msg = None
        try:
            status_msg = await update.message.reply_text("🤖 <i>ИИ распознаёт результат со скриншота(ов)...</i>", parse_mode="HTML")

            downloaded_bytes = []
            for p_id in photos_list[:2]:
                f_obj = await context.bot.get_file(p_id)
                img_b = await f_obj.download_as_bytearray()
                downloaded_bytes.append(bytes(img_b))

            ai_res = ai_recognizer.recognize_match_screenshots_bytes(downloaded_bytes)

            if ai_res and ("home_score" in ai_res) and ("away_score" in ai_res):
                s1_goals = ai_res.get("side1_goals") or ai_res.get("home_goals") or []
                s2_goals = ai_res.get("side2_goals") or ai_res.get("away_goals") or []
                s1_assists = ai_res.get("side1_assists") or ai_res.get("home_assists") or []
                s2_assists = ai_res.get("side2_assists") or ai_res.get("away_assists") or []

                h_goals, a_goals, h_assists, a_assists, is_side1_home = match_and_enrich_squad(
                    s1_goals, s2_goals, s1_assists, s2_assists, home_team, away_team
                )

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

                h_goals_summary = ", ".join([f"{p} ({c})" for p, c in h_goals.items()]) if h_goals else "Не указано"
                a_goals_summary = ", ".join([f"{p} ({c})" for p, c in a_goals.items()]) if a_goals else "Не указано"
                h_assists_summary = ", ".join([f"{p} ({c})" for p, c in h_assists.items()]) if h_assists else "Нет"
                a_assists_summary = ", ".join([f"{p} ({c})" for p, c in a_assists.items()]) if a_assists else "Нет"

                text = (
                    f"🤖 <b>ИИ автоматически распознал результат со скриншота:</b>\n\n"
                    f"🏟 <b>Матч #{match_id}</b>\n"
                    f"🏠 <b>{safe_escape(home_team)}</b> {h_score} : {a_score} <b>{safe_escape(away_team)}</b> ✈️\n\n"
                    f"⚽ <b>Голы ({safe_escape(home_team)}):</b> {safe_escape(h_goals_summary)}\n"
                    f"🎯 <b>Ассисты ({safe_escape(home_team)}):</b> {safe_escape(h_assists_summary)}\n\n"
                    f"⚽ <b>Голы ({safe_escape(away_team)}):</b> {safe_escape(a_goals_summary)}\n"
                    f"🎯 <b>Ассисты ({safe_escape(away_team)}):</b> {safe_escape(a_assists_summary)}\n\n"
                    f"📸 <i>Скриншот(ы) прикреплены.</i>"
                )

                keyboard = [
                    [InlineKeyboardButton("✅ Всё верно (Сохранить и занести результат)", callback_data=f"cb_confirm_ai_final_{match_id}")],
                    [InlineKeyboardButton("✏️ Изменить вручную", callback_data=f"cb_report_choice_manual_{match_id}")],
                    [InlineKeyboardButton("❌ Отмена", callback_data=f"cabinet_view_match_{match_id}")]
                ]
                markup = InlineKeyboardMarkup(keyboard)

                await context.bot.send_photo(chat_id=update.effective_user.id, photo=photo_id, caption=text, parse_mode="HTML", reply_markup=markup)
                return ConversationHandler.END
        except Exception as e:
            logger.error(f"AI Vision processing error: {e}")
        finally:
            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass

    context.user_data["report_photo_id"] = photo_id
    context.user_data.pop("awaiting_report_photo", None)

    hg = context.user_data.get("report_home_goals", 0)
    ag = context.user_data.get("report_away_goals", 0)

    goals_dict = context.user_data.get("home_goals_count", {})
    assists_dict = context.user_data.get("home_assists_count", {})

    goals_summary = ", ".join([f"{p} ({c})" for p, c in goals_dict.items()]) if goals_dict else "Не указано"
    assists_summary = ", ".join([f"{p} ({c})" for p, c in assists_dict.items()]) if assists_dict else "Нет"

    text = (
        f"📊 <b>Проверьте данные перед отправкой:</b>\n\n"
        f"🏟 <b>Матч #{match_id}</b>\n"
        f"🏠 <b>{safe_escape(home_team)}</b> {hg} : {ag} <b>{safe_escape(away_team)}</b> ✈️\n\n"
        f"⚽ <b>Голы ({safe_escape(home_team)}):</b> {safe_escape(goals_summary)}\n"
        f"🎯 <b>Ассисты ({safe_escape(home_team)}):</b> {safe_escape(assists_summary)}\n\n"
        f"📸 <i>Скриншот прикреплен.</i>"
    )

    keyboard = [
        [InlineKeyboardButton("✅ Отправить гостям", callback_data=f"cb_submit_report_to_guest_{match_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"cabinet_view_match_{match_id}")]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_photo(chat_id=update.effective_user.id, photo=photo_id, caption=text, parse_mode="HTML", reply_markup=markup)
    return ConversationHandler.END

async def cb_confirm_ai_final(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Instantly save and finalize match score in database from AI Vision result."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    match_id = int(query.data.replace("cb_confirm_ai_final_", ""))
    match = database.get_match(match_id)
    if not match:
        await query.answer("❌ Матч не найден.", show_alert=True)
        return

    user_id = query.from_user.id
    h_score = context.user_data.get("report_home_goals", 0)
    a_score = context.user_data.get("report_away_goals", 0)
    h_goals = context.user_data.get("home_goals_count", {})
    a_goals = context.user_data.get("away_goals_count", {})
    h_assists = context.user_data.get("home_assists_count", {})
    a_assists = context.user_data.get("away_assists_count", {})
    photo_id = context.user_data.get("report_photo_id")

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

    database.confirm_and_finalize_match(match_id, h_score, a_score, events, reporter_id=user_id, photo_id=photo_id)

    # 1. PM to reporter
    reporter_text = (
        f"🎉 <b>Результат успешно занесен в лигу!</b>\n\n"
        f"🏟 <b>Матч #{match_id} (Тур {match['round_number']})</b>\n"
        f"🏠 <b>{safe_escape(home_team)}</b> {h_score} : {a_score} <b>{safe_escape(away_team)}</b> ✈️\n\n"
        f"📊 Турнирная таблица и статистика игроков обновлены."
    )
    try:
        await query.edit_message_caption(caption=reporter_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« К своим матчам", callback_data="cabinet_my_matches")]]))
    except Exception:
        await context.bot.send_message(chat_id=user_id, text=reporter_text, parse_mode="HTML")

    # 2. PM to players
    players_to_notify = []
    if user_id == match['player1_id']:
        if match['player2_id']: players_to_notify.append(match['player2_id'])
    elif user_id == match['player2_id']:
        if match['player1_id']: players_to_notify.append(match['player1_id'])
    else:
        if match['player1_id']: players_to_notify.append(match['player1_id'])
        if match['player2_id']: players_to_notify.append(match['player2_id'])

    for p_id in set(players_to_notify):
        opp_text = (
            f"🔔 <b>Результат вашего матча занесен в лигу!</b>\n\n"
            f"🏟 <b>Матч #{match_id} (Тур {match['round_number']})</b>\n"
            f"🏠 <b>{safe_escape(home_team)}</b> {h_score} : {a_score} <b>{safe_escape(away_team)}</b> ✈️\n\n"
            f"📸 <i>Результат внесен и верифицирован по скриншоту статистики.</i>"
        )
        await safe_send_notification(context.bot, p_id, opp_text)

    # 3. Post to Group
    main_group_id = database.get_group_id()
    results_topic_id = database.get_config("results_topic_id") or database.get_config("reports_topic_id")
    if main_group_id:
        p1_user = f"@{match['player1_username']}" if match['player1_username'] else home_team
        p2_user = f"@{match['player2_username']}" if match['player2_username'] else away_team
        h_goals_summary = ", ".join([f"{p} ({c})" for p, c in h_goals.items()]) if h_goals else "Нет"
        a_goals_summary = ", ".join([f"{p} ({c})" for p, c in a_goals.items()]) if a_goals else "Нет"

        group_text = (
            f"🏆 <b>РЕЗУЛЬТАТ МАТЧА | Тур {match['round_number']}</b>\n\n"
            f"🏠 <b>{safe_escape(home_team)}</b> ({safe_escape(p1_user)}) <b>{h_score} : {a_score}</b> <b>{safe_escape(away_team)}</b> ({safe_escape(p2_user)}) ✈️\n\n"
            f"⚽ <b>Голы ({safe_escape(home_team)}):</b> {safe_escape(h_goals_summary)}\n"
            f"⚽ <b>Голы ({safe_escape(away_team)}):</b> {safe_escape(a_goals_summary)}\n\n"
            f"📸 <i>Результат официально занесен в турнирную таблицу.</i>"
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
            logger.error(f"Failed to post result to group: {e}")

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

    match = database.get_match(match_id) if match_id else None
    if not match:
        await query.answer("❌ Ошибка: матч не найден.", show_alert=True)
        return

    home_user_id = query.from_user.id
    hg = context.user_data.get("report_home_goals", 0)
    ag = context.user_data.get("report_away_goals", 0)
    photo_id = context.user_data.get("report_photo_id")

    # Build events list for database
    events = []
    home_team = match['player1_team'] or match['player1_nickname']
    goals_dict = context.user_data.get("home_goals_count", {})
    assists_dict = context.user_data.get("home_assists_count", {})

    for p, c in goals_dict.items():
        events.append((home_team, p, "goal", c))
    for p, c in assists_dict.items():
        events.append((home_team, p, "assist", c))

    # Save to database
    database.report_match_score(match_id, hg, ag, home_user_id, photo_id)
    if events:
        database.save_match_events(match_id, events, team_name=home_team)

    # Notify Guest
    guest_id = match['player2_id']
    away_team = match['player2_team'] or match['player2_nickname']

    goals_summary = ", ".join([f"{p} ({c})" for p, c in goals_dict.items()]) if goals_dict else "Не указано"
    assists_summary = ", ".join([f"{p} ({c})" for p, c in assists_dict.items()]) if assists_dict else "Нет"

    guest_text = (
        f"🔔 <b>Хозяева поля ({html.escape(home_team)}) ввели результат матча #{match_id}!</b>\n\n"
        f"🏠 <b>{html.escape(home_team)}</b> {hg} : {ag} <b>{html.escape(away_team)}</b> ✈️\n\n"
        f"⚽ <b>Голы ({html.escape(home_team)}):</b> {html.escape(goals_summary)}\n"
        f"🎯 <b>Ассисты ({html.escape(home_team)}):</b> {html.escape(assists_summary)}\n\n"
        f"Вы подтверждаете этот результат?"
    )

    guest_keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_score_{match_id}"), InlineKeyboardButton("❌ Оспорить", callback_data=f"dispute_score_{match_id}")]
    ]
    guest_markup = InlineKeyboardMarkup(guest_keyboard)

    sent_success = False
    err_reason = ""
    if photo_id:
        try:
            await context.bot.send_photo(chat_id=guest_id, photo=photo_id, caption=guest_text, parse_mode="HTML", reply_markup=guest_markup)
            sent_success = True
        except Exception as e1:
            logger.warning(f"Photo send with HTML failed for guest {guest_id}: {e1}. Trying text fallback...")
            err_reason = str(e1)

    if not sent_success:
        try:
            await context.bot.send_message(chat_id=guest_id, text=guest_text, parse_mode="HTML", reply_markup=guest_markup)
            sent_success = True
        except Exception as e2:
            logger.warning(f"Failed to send HTML message to guest {guest_id}: {e2}. Trying plain text fallback...")
            err_reason = str(e2)

    if not sent_success:
        try:
            plain_text = (
                f"🔔 Хозяева поля ({home_team}) ввели результат матча #{match_id}!\n\n"
                f"🏠 {home_team} {hg} : {ag} {away_team} ✈️\n\n"
                f"⚽ Голы ({home_team}): {goals_summary}\n"
                f"🎯 Ассисты ({home_team}): {assists_summary}\n\n"
                f"Вы подтверждаете этот результат?"
            )
            await context.bot.send_message(chat_id=guest_id, text=plain_text, reply_markup=guest_markup)
            sent_success = True
        except Exception as e3:
            logger.error(f"Plain text fallback failed for guest {guest_id}: {e3}")
            err_reason = str(e3)

    if sent_success:
        await safe_query_answer(query, "✅ Результат успешно отправлен гостям на подтверждение!", show_alert=True)
        try:
            await query.edit_message_caption(
                caption=f"✅ <b>Результат матча #{match_id} успешно отправлен на подтверждение гостям!</b>",
                parse_mode="HTML"
            )
        except Exception:
            try:
                await query.edit_message_text(
                    text=f"✅ <b>Результат матча #{match_id} успешно отправлен на подтверждение гостям!</b>",
                    parse_mode="HTML"
                )
            except Exception:
                pass

        try:
            await context.bot.send_message(
                chat_id=query.from_user.id,
                text=f"✅ <b>Результат матча #{match_id} успешно отправлен!</b>\n\nОжидаем подтверждения от соперника (<b>{html.escape(away_team)}</b>).",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to send confirmation message to home player: {e}")
    else:
        await safe_query_answer(
            query,
            f"⚠️ Не удалось доставить результат гостю (ID: {guest_id}). Возможно, у гостя заблокирован бот или закрыто ЛС. Ошибка: {err_reason[:50]}",
            show_alert=True
        )

async def handle_confirm_score(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    match_id = int(query.data.replace("confirm_score_", ""))
    match = database.get_match(match_id)
    if not match:
        await query.edit_message_text("❌ Матч не найден.")
        return

    user_id = query.from_user.id
    if match['player2_id'] != user_id:
        await query.answer("⛔ Только гостевой игрок может подтвердить данный результат.", show_alert=True)
        return

    away_team = match['player2_team'] or match['player2_nickname']
    away_goals = match['player2_score'] or 0

    # If away team scored > 0, prompt guest to pick their goalscorers
    if away_goals > 0:
        context.user_data["guest_confirm_match_id"] = match_id
        context.user_data["guest_away_team"] = away_team
        context.user_data["guest_goals_to_pick"] = away_goals
        context.user_data["guest_goals_count"] = {}
        await render_guest_goals_picker(update, context, away_team)
    else:
        # 0 away goals -> confirm immediately
        database.confirm_match(match_id)
        await notify_match_confirmed(context, match_id)

async def render_guest_goals_picker(update: Update, context: ContextTypes.DEFAULT_TYPE, team_name: str) -> None:
    query = update.callback_query
    left = context.user_data.get("guest_goals_to_pick", 0)
    picked_dict = context.user_data.get("guest_goals_count", {})

    squad = database.get_squad(team_name)
    summary_str = ""
    if picked_dict:
        summary_str = "\n\n⚽ **Уже выбрано:**\n" + "\n".join([f"• {p}: {c}" for p, c in picked_dict.items()])

    text = (
        f"⚽ **Авторы голов вашей команды ({html.escape(team_name)})**\n"
        f"Осталось распределить голов: **{left}**{summary_str}\n\n"
        f"Нажимайте на кнопки с игроками вашего состава:"
    )

    keyboard = []
    if not squad:
        text += "\n\n⚠️ *Состав вашей команды пока не добавлен в систему.*"
        keyboard.append([InlineKeyboardButton("⏩ Пропустить авторов голов", callback_data="guest_skip_goals")])
    else:
        row = []
        for player in squad:
            row.append(InlineKeyboardButton(f"🏃‍♂️ {player}", callback_data=f"guest_pick_goal_{player}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("⏩ Пропустить", callback_data="guest_skip_goals")])

    markup = InlineKeyboardMarkup(keyboard)
    if query.message.caption:
        await query.edit_message_caption(caption=text, parse_mode="HTML", reply_markup=markup)
    else:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)

async def guest_pick_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    player = query.data.replace("guest_pick_goal_", "")
    left = context.user_data.get("guest_goals_to_pick", 0)
    dict_goals = context.user_data.get("guest_goals_count", {})

    dict_goals[player] = dict_goals.get(player, 0) + 1
    left -= 1
    context.user_data["guest_goals_to_pick"] = left
    context.user_data["guest_goals_count"] = dict_goals

    away_team = context.user_data.get("guest_away_team")
    if left > 0:
        await render_guest_goals_picker(update, context, away_team)
    else:
        await start_guest_assists_picker(update, context, away_team)

async def guest_skip_goals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    away_team = context.user_data.get("guest_away_team")
    await start_guest_assists_picker(update, context, away_team)

async def start_guest_assists_picker(update: Update, context: ContextTypes.DEFAULT_TYPE, team_name: str) -> None:
    match_id = context.user_data.get("guest_confirm_match_id")
    match = database.get_match(match_id)
    ag = match['player2_score'] if match else 1
    context.user_data["guest_assists_to_pick"] = ag
    context.user_data["guest_assists_count"] = {}
    await render_guest_assists_picker(update, context, team_name)

async def render_guest_assists_picker(update: Update, context: ContextTypes.DEFAULT_TYPE, team_name: str) -> None:
    query = update.callback_query
    left = context.user_data.get("guest_assists_to_pick", 0)
    picked_dict = context.user_data.get("guest_assists_count", {})

    squad = database.get_squad(team_name)
    summary_str = ""
    if picked_dict:
        summary_str = "\n\n🎯 **Уже выбрано:**\n" + "\n".join([f"• {p}: {c}" for p, c in picked_dict.items()])

    text = (
        f"🎯 **Авторы ассистов вашей команды ({html.escape(team_name)})**\n"
        f"Осталось ассистов: **{left}**{summary_str}\n\n"
        f"Нажимайте на кнопки или пропустите:"
    )

    keyboard = []
    if squad and left > 0:
        row = []
        for player in squad:
            row.append(InlineKeyboardButton(f"🎯 {player}", callback_data=f"guest_pick_assist_{player}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    keyboard.append([InlineKeyboardButton("⏩ Пропустить ассисты", callback_data="guest_skip_assists")])
    markup = InlineKeyboardMarkup(keyboard)

    if query.message.caption:
        await query.edit_message_caption(caption=text, parse_mode="HTML", reply_markup=markup)
    else:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)

async def guest_pick_assist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    player = query.data.replace("guest_pick_assist_", "")
    left = context.user_data.get("guest_assists_to_pick", 0)
    dict_assists = context.user_data.get("guest_assists_count", {})

    dict_assists[player] = dict_assists.get(player, 0) + 1
    left -= 1
    context.user_data["guest_assists_to_pick"] = left
    context.user_data["guest_assists_count"] = dict_assists

    away_team = context.user_data.get("guest_away_team")
    if left > 0:
        await render_guest_assists_picker(update, context, away_team)
    else:
        await finalize_guest_confirmation(update, context)

async def guest_skip_assists(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    await finalize_guest_confirmation(update, context)

async def finalize_guest_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    match_id = context.user_data.get("guest_confirm_match_id")
    away_team = context.user_data.get("guest_away_team")

    goals_dict = context.user_data.get("guest_goals_count", {})
    assists_dict = context.user_data.get("guest_assists_count", {})

    events = []
    for p, c in goals_dict.items():
        events.append((away_team, p, "goal", c))
    for p, c in assists_dict.items():
        events.append((away_team, p, "assist", c))

    if events:
        database.save_match_events(match_id, events, team_name=away_team)

    database.confirm_match(match_id)
    await notify_match_confirmed(context, match_id)

async def notify_match_confirmed(context: ContextTypes.DEFAULT_TYPE, match_id: int) -> None:
    match = database.get_match(match_id)
    if not match:
        return

    home_team = match['player1_team'] or match['player1_nickname']
    away_team = match['player2_team'] or match['player2_nickname']
    p1_score = match['player1_score']
    p2_score = match['player2_score']

    text = (
        f"✅ **Матч #{match_id} успешно подтвержден и сыгран!**\n\n"
        f"🏠 **{html.escape(home_team)}** {p1_score} : {p2_score} **{html.escape(away_team)}** ✈️\n\n"
        f"Результат внесен в турнирную таблицу и статистику лиги."
    )

    for p_id in (match['player1_id'], match['player2_id']):
        try:
            await context.bot.send_message(chat_id=p_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to send confirmation to player {p_id}: {e}")

    results_topic_id = database.get_config("results_topic_id")
    group_id = database.get_group_id()
    if group_id:
        events = database.get_match_events(match_id)
        
        home_goals = [f"{e['player_name']} ({e['count']})" if e['count'] > 1 else e['player_name'] for e in events if e['event_type'] == 'goal' and e['team_name'].lower() == home_team.lower()]
        away_goals = [f"{e['player_name']} ({e['count']})" if e['count'] > 1 else e['player_name'] for e in events if e['event_type'] == 'goal' and e['team_name'].lower() == away_team.lower()]
        
        home_assists = [f"{e['player_name']} ({e['count']})" if e['count'] > 1 else e['player_name'] for e in events if e['event_type'] == 'assist' and e['team_name'].lower() == home_team.lower()]
        away_assists = [f"{e['player_name']} ({e['count']})" if e['count'] > 1 else e['player_name'] for e in events if e['event_type'] == 'assist' and e['team_name'].lower() == away_team.lower()]

        group_text = (
            f"⚽ <b>РЕЗУЛЬТАТ МАТЧА | Тур {match['round_number']}</b>\n\n"
            f"🏠 <b>{html.escape(home_team)}</b> {p1_score} : {p2_score} <b>{html.escape(away_team)}</b> ✈️\n\n"
        )
        if home_goals:
            group_text += f"⚽ <b>Голы ({html.escape(home_team)}):</b> {html.escape(', '.join(home_goals))}\n"
        if home_assists:
            group_text += f"🎯 <b>Ассисты ({html.escape(home_team)}):</b> {html.escape(', '.join(home_assists))}\n"
        if away_goals:
            group_text += f"⚽ <b>Голы ({html.escape(away_team)}):</b> {html.escape(', '.join(away_goals))}\n"
        if away_assists:
            group_text += f"🎯 <b>Ассисты ({html.escape(away_team)}):</b> {html.escape(', '.join(away_assists))}\n"

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
            logger.error(f"Failed to post result to topic/group: {e}")

# ==========================================
# ОСПОРАРИВАНИЕ МАТЧА ГОСТЕМ (ФОТО ДОКАЗАТЕЛЬСТВА)
# ==========================================

async def handle_dispute_score(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    await query.answer()

    match_id = int(query.data.replace("dispute_score_", ""))
    match = database.get_match(match_id)
    if not match:
        await query.edit_message_text("❌ Матч не найден.")
        return ConversationHandler.END

    user_id = query.from_user.id
    if match['player2_id'] != user_id:
        await query.answer("⛔ Только гостевой игрок может оспорить результат.", show_alert=True)
        return ConversationHandler.END

    context.user_data["dispute_match_id"] = match_id
    context.user_data["dispute_photos"] = []

    text = (
        f"⚠️ **Оспаривание результата матча #{match_id}**\n\n"
        f"Пожалуйста, отправьте **от 1 до 2 скриншотов** со статистикой вашего матча.\n"
        f"После отправки фото нажмите кнопку **«✅ Завершить отправку»**."
    )
    keyboard = [[InlineKeyboardButton("✅ Завершить отправку (0 фото)", callback_data="cb_finish_dispute_photos")]]
    markup = InlineKeyboardMarkup(keyboard)

    if query.message.caption:
        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)

    return GUEST_DISPUTE_PHOTOS

async def save_guest_dispute_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or (update.effective_chat and update.effective_chat.type != "private"):
        return GUEST_DISPUTE_PHOTOS
    if not update.message.photo and not update.message.document:
        await update.message.reply_text("❌ Пожалуйста, отправьте фото-скриншот.")
        return GUEST_DISPUTE_PHOTOS

    photos = context.user_data.get("dispute_photos", [])
    if len(photos) >= 2:
        await update.message.reply_text("⚠️ Вы уже прикрепили максимально допустимое количество фото (2 шт). Нажмите «✅ Завершить отправку».")
        return GUEST_DISPUTE_PHOTOS

    photo_id = update.message.photo[-1].file_id
    photos.append(photo_id)
    context.user_data["dispute_photos"] = photos

    keyboard = [[InlineKeyboardButton(f"✅ Завершить отправку ({len(photos)} фото)", callback_data="cb_finish_dispute_photos")]]
    markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"✅ Фото #{len(photos)} получено! Можете отправить еще одно фото или завершить отправку.",
        reply_markup=markup
    )
    return GUEST_DISPUTE_PHOTOS

async def cb_finish_dispute_photos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()

    match_id = context.user_data.get("dispute_match_id")
    photos = context.user_data.get("dispute_photos", [])

    photos_json = json.dumps(photos)
    database.save_dispute_evidence(match_id, photos_json)

    text = "❌ **Результат матча оспорен.** Спорное досье передано администраторам для проверки."
    if query:
        try:
            await query.edit_message_text(text, parse_mode="Markdown")
        except Exception:
            await query.message.reply_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

    match = database.get_match(match_id)
    if match:
        try:
            await context.bot.send_message(
                chat_id=match['player1_id'],
                text=f"⚠️ **Соперник оспорил результат матча #{match_id}.** Ожидайте решения администраторов.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    return ConversationHandler.END

SQUAD_PHOTO = 100

async def show_my_squad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        user = query.from_user
    else:
        user = update.effective_user

    db_user = database.get_user(user.id)
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
    
    database.update_single_field(user_id, "squad_photo_id", photo_id)
    
    await update.message.reply_text("✅ Состав успешно сохранен!")
    await show_my_squad(update, context)

    group_id = database.get_group_id()
    squad_topic_id = database.get_config("squad_topic_id")
    if group_id and squad_topic_id:
        try:
            db_user = database.get_user(user_id)
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
    opp_user = database.get_user(opp_id)
    
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
            logger.error(f"Failed to send squad photo: {e}")
    else:
        await safe_query_answer(query, f"❌ Команда {team_name} еще не загрузила свой состав.", show_alert=True)

