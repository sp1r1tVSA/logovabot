"""
handlers/lab.py

Admin-Only Test Laboratory (Sandbox & Feature Flags Management).
Provides isolated testing for new features, including EA FC Ultimate Team cards,
TOTW infographics, hype match posters, and dynamic feature flag toggling.
All test outputs and renders are strictly delivered in private messages (DM) to admins.
"""

import io
import html
import asyncio
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import database
import config
from config import CLUBS, ADMIN_IDS
from handlers.base import admin_only, is_admin
from handlers.cabinet import safe_edit_or_reply
import fc_card_generator

logger = logging.getLogger(__name__)

# Feature key human readable metadata
FEATURE_META = {
    "fc_player_cards": ("🃏 Карточки игроков EA FC", "Генерация карточек Ultimate Team с рейтингом OVR и статами"),
    "totw_infographics": ("📰 Сборная тура (TOTW)", "Автоматический плакат с 11 лучшими игроками тура"),
    "hype_match_posters": ("🥊 Хайп-постеры к матчам", "Афиша дерби и центрального матча тура с H2H"),
    "match_roast_ai": ("🎙️ AI-разбор матча Темшиком", "Авто-комментарий и флеш-интервью после драфта"),
    "betting_market": ("🎰 Мини-тотализатор коинов", "Ставки на матчи тура виртуальной валютой"),
    "fantasy_league": ("⚽ Фэнтези-Лига", "Сборка виртуальной команды из игроков лиги"),
    "achievements_hall_of_fame": ("🏆 Зал славы и ачивки", "Бейджи и карьерные трофеи игроков"),
}

STATUS_BADGES = {
    "disabled": "🔴 ВЫКЛЮЧЕНА",
    "admin_only": "🟡 ТОЛЬКО АДМИНЫ",
    "public": "🟢 ДЛЯ ВСЕХ",
}


@admin_only
async def cmd_lab(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry command /lab for opening the Admin Test Laboratory."""
    user = update.effective_user
    if not user or not is_admin(user.id):
        if update.message:
            await update.message.reply_text("⛔ Доступ запрещён. Меню доступно только администраторам.")
        return

    text, markup = _build_lab_main_menu()
    if update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)
    elif update.callback_query:
        await safe_edit_or_reply(update.callback_query, context, text, reply_markup=markup, parse_mode="HTML")


@admin_only
async def cb_lab_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback handler for returning to the main lab dashboard."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    text, markup = _build_lab_main_menu()
    await safe_edit_or_reply(query, context, text, reply_markup=markup, parse_mode="HTML")


def _build_lab_main_menu() -> tuple[str, InlineKeyboardMarkup]:
    """Render main lab dashboard text and buttons."""
    text = (
        "🧪 <b>Тестовая лаборатория админа (Sandbox)</b>\n\n"
        "Здесь вы можете безопасно тестировать новые механики и визуальные модули. "
        "Все тесты и сгенерированные изображения отправляются <b>только вам в ЛС</b> "
        "и не видны обычным пользователям.\n\n"
        "Выберите раздел для тестирования:"
    )
    keyboard = [
        [InlineKeyboardButton("🃏 Тестировать Карточки EA FC", callback_data="lab_card_menu")],
        [InlineKeyboardButton("🚩 Управление Feature Flags", callback_data="lab_flags_menu")],
        [InlineKeyboardButton("📊 Тест формулы OVR (Калькулятор)", callback_data="lab_ovr_calc_demo")],
        [InlineKeyboardButton("« Назад в Админ-панель", callback_data="admin_main_menu")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Feature Flags Management Menu
# ─────────────────────────────────────────────────────────────────────────────

@admin_only
async def cb_lab_flags_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the interactive feature flags management list."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    flags = await asyncio.to_thread(database.get_all_feature_flags)
    text = (
        "🚩 <b>Управление Feature Flags (Флаги фич)</b>\n\n"
        "Нажмите на кнопку фичи, чтобы переключить её режим:\n"
        "• 🔴 <b>ВЫКЛЮЧЕНА</b> — недоступна никому\n"
        "• 🟡 <b>ТОЛЬКО АДМИНЫ</b> — доступна только администраторам в ЛС и тестах\n"
        "• 🟢 <b>ДЛЯ ВСЕХ</b> — полностью открыта для всех участников турнира\n"
    )

    keyboard = []
    for key, (title, desc) in FEATURE_META.items():
        curr_status = flags.get(key, "admin_only")
        badge = STATUS_BADGES.get(curr_status, curr_status)
        btn_text = f"{title}: {badge}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"lab_toggle_flag_{key}")])

    keyboard.append([InlineKeyboardButton("« Назад в лабораторию", callback_data="admin_lab_menu")])
    await safe_edit_or_reply(query, context, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def cb_lab_toggle_flag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cycle feature flag: disabled -> admin_only -> public -> disabled."""
    query = update.callback_query
    if not query or not query.data:
        return

    key = query.data.replace("lab_toggle_flag_", "")
    curr_status = await asyncio.to_thread(database.get_feature_flag, key)

    # Next status cycle
    next_map = {
        "disabled": "admin_only",
        "admin_only": "public",
        "public": "disabled",
    }
    new_status = next_map.get(curr_status, "admin_only")
    await asyncio.to_thread(database.set_feature_flag, key, new_status)

    await query.answer(f"Статус {key} изменен на: {new_status}")
    # Refresh menu
    await cb_lab_flags_menu(update, context)


# ─────────────────────────────────────────────────────────────────────────────
# 2. EA FC Player Cards Testing Menu
# ─────────────────────────────────────────────────────────────────────────────

@admin_only
async def cb_lab_card_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Card test menu with 3 fundamentally distinct design concepts."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    text = (
        "🃏 <b>Сравнение 3 концептов дизайна карточек</b>\n\n"
        "Выберите дизайн для генерации превью:\n\n"
        "1. ⚡ <b>Концепт 1: Cyber Hybrid (Modern Broadcast)</b>\n"
        "   • Неоново-бирюзовый/золотой кибер-стиль, вертикальный HUD-рейтинг, прогресс-бары стат.\n\n"
        "2. 👑 <b>Концепт 2: EA FC 25 (Authentic FUT Shield)</b>\n"
        "   • Классическая форма золотого щита FIFA 25, 3D фаски, центрированный игрок, классическая сетка 2x3.\n\n"
        "3. 💎 <b>Концепт 3: Obsidian Luxury (VIP Editorial Poster)</b>\n"
        "   • Премиальный черный оникс с двойной золотой окантовкой, эмблема [95 • CAM], 6 капсул 3x2.\n\n"
        "Или выберите реального игрока из клуба лиги."
    )
    keyboard = [
        [InlineKeyboardButton("⚡ 1. Cyber Broadcast (Неон HUD)", callback_data="lab_demo_card_design_1")],
        [InlineKeyboardButton("👑 2. EA FC 25 (Золотой Щит)", callback_data="lab_demo_card_design_2")],
        [InlineKeyboardButton("💎 3. Obsidian Luxury (VIP Люкс)", callback_data="lab_demo_card_design_3")],
        [InlineKeyboardButton("🎬 Анимированные карточки (GIF)", callback_data="lab_anim_card_menu")],
        [InlineKeyboardButton("🔍 Выбрать реального игрока из клуба", callback_data="lab_card_pick_club")],
        [InlineKeyboardButton("« Назад в лабораторию", callback_data="admin_lab_menu")],
    ]
    await safe_edit_or_reply(query, context, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def cb_lab_anim_card_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Animated card testing menu."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    text = (
        "🎬 <b>Тестирование Анимированных Карточек Игроков (GIF)</b>\n\n"
        "Выберите стиль динамической анимации:\n\n"
        "1. 🌟 <b>Голографический блик (Holo Shimmer)</b>\n"
        "   • Диагональный золотой луч света скользит по карточке EA FC Gold Shield с пульсирующим ореолом.\n\n"
        "2. ✨ <b>Парящие золотые искры (Golden Sparks)</b>\n"
        "   • Живой рой светящихся золотых частиц и искр, поднимающихся за игроком на Luxury Onyx.\n\n"
        "3. ⚡ <b>Неоновый кибер-пульс (Cyber Pulse)</b>\n"
        "   • Пульсирующие неоновые границы HUD и энергетические бегущие волны на Cyber Broadcast."
    )
    keyboard = [
        [InlineKeyboardButton("🌟 1. Голографический блик (Holo)", callback_data="lab_demo_anim_holo_shimmer")],
        [InlineKeyboardButton("✨ 2. Парящие искры (Sparks)", callback_data="lab_demo_anim_golden_sparks")],
        [InlineKeyboardButton("⚡ 3. Неоновый кибер-пульс (Pulse)", callback_data="lab_demo_anim_cyber_pulse")],
        [InlineKeyboardButton("« Назад к статичным карточкам", callback_data="lab_card_menu")],
        [InlineKeyboardButton("« В лабораторию", callback_data="admin_lab_menu")],
    ]
    await safe_edit_or_reply(query, context, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def cb_lab_demo_anim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate animated card and send directly via send_animation."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer("Рендерю анимированную карточку (GIF)...")

    anim_style = query.data.replace("lab_demo_anim_", "")
    user_id = query.from_user.id

    test_player = {
        "player_name": "ROONY BARDGHJI",
        "team_name": "АЕК",
        "position": "CAM",
        "total_goals": 18,
        "total_assists": 9,
        "matches_played": 12,
        "ovr": 95,
        "custom_stats": {
            "PAC": 96,
            "SHO": 98,
            "PAS": 99,
            "DRI": 86,
            "DEF": 80,
            "PHY": 98
        }
    }

    buf = await asyncio.to_thread(fc_card_generator.generate_animated_ea_fc_card, test_player, anim_style)
    stats = fc_card_generator.calculate_fut_attributes(test_player)

    anim_titles = {
        "holo_shimmer": "🌟 ГОЛОГРАФИЧЕСКИЙ БЛИК (HOLO SHIMMER)",
        "golden_sparks": "✨ ПАРЯЩИЕ ЗОЛОТЫЕ ИСКРЫ (GOLDEN SPARKS)",
        "cyber_pulse": "⚡ НЕОНОВЫЙ КИБЕР-ПУЛЬС (CYBER PULSE)",
    }
    cur_title = anim_titles.get(anim_style, anim_style.upper())

    caption = (
        f"🎬 <b>Анимированная карточка: {cur_title}</b>\n\n"
        f"👤 <b>{html.escape(test_player['player_name'])}</b> ({html.escape(test_player['team_name'])})\n"
        f"⭐ <b>OVR: {stats['ovr']}</b> | Позиция: <b>{stats['position']}</b>\n"
        f"⚡ <b>PAC:</b> {stats['pac']} | 🎯 <b>SHO:</b> {stats['sho']} | 🅰️ <b>PAS:</b> {stats['pas']}\n"
        f"🪄 <b>DRI:</b> {stats['dri']} | 🛡️ <b>DEF:</b> {stats['def']} | 💪 <b>PHY:</b> {stats['phy']}\n\n"
        f"<i>Зацикленная плавная анимация в формате GIF / Telegram Animation.</i>"
    )

    keyboard = [
        [
            InlineKeyboardButton("🌟 Блик", callback_data="lab_demo_anim_holo_shimmer"),
            InlineKeyboardButton("✨ Искры", callback_data="lab_demo_anim_golden_sparks"),
            InlineKeyboardButton("⚡ Неон", callback_data="lab_demo_anim_cyber_pulse"),
        ],
        [InlineKeyboardButton("« К выбору анимации", callback_data="lab_anim_card_menu")],
        [InlineKeyboardButton("« В лабораторию", callback_data="admin_lab_menu")]
    ]

    await context.bot.send_animation(
        chat_id=user_id,
        animation=buf,
        caption=caption,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


@admin_only
async def cb_lab_demo_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate selected design concept and send directly to admin in DM."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer("Генерирую тестовую карточку...")

    theme_name = query.data.replace("lab_demo_card_", "")
    user_id = query.from_user.id

    test_player = {
        "player_name": "ROONY BARDGHJI",
        "team_name": "АЕК",
        "position": "CAM",
        "total_goals": 18,
        "total_assists": 9,
        "matches_played": 12,
        "ovr": 95,
        "custom_stats": {
            "PAC": 96,
            "SHO": 98,
            "PAS": 99,
            "DRI": 86,
            "DEF": 80,
            "PHY": 98
        }
    }

    buf = await asyncio.to_thread(fc_card_generator.generate_ea_fc_card, test_player, theme_name)
    stats = fc_card_generator.calculate_fut_attributes(test_player)

    design_names = {
        "design_1": "1. CYBER HYBRID (BROADCAST)",
        "design_2": "2. AUTHENTIC EA FC 25 SHIELD",
        "design_3": "3. OBSIDIAN LUXURY POSTER",
    }
    cur_title = design_names.get(theme_name, theme_name.upper())

    caption = (
        f"🧪 <b>Превью концепта: {cur_title}</b>\n\n"
        f"👤 <b>{html.escape(test_player['player_name'])}</b> ({html.escape(test_player['team_name'])})\n"
        f"⭐ <b>OVR: {stats['ovr']}</b> | Позиция: <b>{stats['position']}</b>\n"
        f"⚡ <b>PAC:</b> {stats['pac']} | 🎯 <b>SHO:</b> {stats['sho']} | 🅰️ <b>PAS:</b> {stats['pas']}\n"
        f"🪄 <b>DRI:</b> {stats['dri']} | 🛡️ <b>DEF:</b> {stats['def']} | 💪 <b>PHY:</b> {stats['phy']}\n\n"
        f"<i>Нажмите кнопки ниже для сравнения с другими концептами:</i>"
    )

    keyboard = [
        [
            InlineKeyboardButton("⚡ Дизайн 1", callback_data="lab_demo_card_design_1"),
            InlineKeyboardButton("👑 Дизайн 2", callback_data="lab_demo_card_design_2"),
            InlineKeyboardButton("💎 Дизайн 3", callback_data="lab_demo_card_design_3"),
        ],
        [InlineKeyboardButton("« К выбору дизайна", callback_data="lab_card_menu")],
        [InlineKeyboardButton("« В лабораторию", callback_data="admin_lab_menu")]
    ]

    await context.bot.send_photo(
        chat_id=user_id,
        photo=buf,
        caption=caption,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


@admin_only
async def cb_lab_card_pick_club(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show list of clubs to choose a player from."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    text = "🔍 <b>Выберите клуб</b> для просмотра состава и генерации карточки игрока:"
    keyboard = []
    row = []
    for c in CLUBS:
        row.append(InlineKeyboardButton(c, callback_data=f"lab_pick_player_{c}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("« Назад к карточкам", callback_data="lab_card_menu")])
    await safe_edit_or_reply(query, context, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def cb_lab_card_pick_player(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show squad players for chosen club."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    club = query.data.replace("lab_pick_player_", "")
    squad = await asyncio.to_thread(database.get_squad, club)

    if not squad:
        text = f"❌ В клубе <b>{html.escape(club)}</b> пока нет зарегистрированных игроков."
        keyboard = [[InlineKeyboardButton("« Выбрать другой клуб", callback_data="lab_card_pick_club")]]
        await safe_edit_or_reply(query, context, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    text = f"👥 <b>Состав {html.escape(club)}:</b>\nВыберите игрока для генерации EA FC карточки:"
    keyboard = []
    for p in squad:
        keyboard.append([InlineKeyboardButton(p, callback_data=f"lab_gen_card_{club}|{p}")])

    keyboard.append([InlineKeyboardButton("« Назад к клубам", callback_data="lab_card_pick_club")])
    await safe_edit_or_reply(query, context, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def cb_lab_card_generate_player(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and send EA FC card for a specific squad player from the database."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer("Считываю статистику и рисую карточку...")

    payload = query.data.replace("lab_gen_card_", "")
    if "|" in payload:
        club, player_name = payload.split("|", 1)
    else:
        club, player_name = "—", payload

    user_id = query.from_user.id

    # 1. Fetch player stats from database
    db_stats = await asyncio.to_thread(database.get_player_card_stats, player_name, club)
    goals = db_stats.get("total_goals", 0)
    assists = db_stats.get("total_assists", 0)

    # Determine card theme based on performance
    if (goals + assists) >= 15:
        theme_name = "totw"
    elif (goals + assists) >= 8:
        theme_name = "gold_rare"
    else:
        theme_name = "gold_rare"

    card_data = {
        "player_name": player_name,
        "team_name": club,
        "position": "ST" if goals >= assists else "CAM",
        "total_goals": goals,
        "total_assists": assists,
        "matches_played": max(1, goals + assists),
    }

    buf = await asyncio.to_thread(fc_card_generator.generate_ea_fc_card, card_data, theme_name)
    fut_stats = fc_card_generator.calculate_fut_attributes(card_data)

    caption = (
        f"🃏 <b>EA FC Карточка игрока</b>\n\n"
        f"👤 <b>{html.escape(player_name)}</b> · {html.escape(club)}\n"
        f"⭐ <b>OVR: {fut_stats['ovr']}</b> ({theme_name.upper()})\n"
        f"⚽ Голов: <b>{goals}</b> | 🅰️ Ассистов: <b>{assists}</b> | 🔥 Очков: <b>{goals + assists}</b>\n\n"
        f"⚡ <b>PAC:</b> {fut_stats['pac']} | 🎯 <b>SHO:</b> {fut_stats['sho']} | 🅰️ <b>PAS:</b> {fut_stats['pas']}\n"
        f"🪄 <b>DRI:</b> {fut_stats['dri']} | 🛡️ <b>DEF:</b> {fut_stats['def']} | 💪 <b>PHY:</b> {fut_stats['phy']}\n\n"
        f"<i>🧪 Тестовая лаборатория Logovobot</i>"
    )

    keyboard = [
        [InlineKeyboardButton("👥 Другой игрок", callback_data=f"lab_pick_player_{club}")],
        [InlineKeyboardButton("🏛 Выбрать клуб", callback_data="lab_card_pick_club")],
        [InlineKeyboardButton("« В лабораторию", callback_data="admin_lab_menu")]
    ]

    await context.bot.send_photo(
        chat_id=user_id,
        photo=buf,
        caption=caption,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


@admin_only
async def cmd_test_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick command: /test_card [Player Name] [Team Name (optional)]"""
    user = update.effective_user
    if not user or not is_admin(user.id):
        if update.message:
            await update.message.reply_text("⛔ Доступ запрещён.")
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "ℹ️ <b>Использование команды:</b>\n<code>/test_card [Имя Фамилия] [Клуб (опционально)]</code>\n\n"
            "Пример: <code>/test_card Винисиус Спортинг</code>",
            parse_mode="HTML"
        )
        return

    player_name = args[0]
    team_name = args[1] if len(args) > 1 else "Спортинг"

    status_msg = await update.message.reply_text("⏳ Генерирую карточку игрока...")

    card_data = {
        "player_name": player_name,
        "team_name": team_name,
        "position": "ST",
        "total_goals": 10,
        "total_assists": 5,
        "matches_played": 8,
    }

    try:
        buf = await asyncio.to_thread(fc_card_generator.generate_ea_fc_card, card_data, "gold_rare")
        fut_stats = fc_card_generator.calculate_fut_attributes(card_data)

        caption = (
            f"🃏 <b>Тестовая карточка: {html.escape(player_name.upper())}</b>\n"
            f"🏛 Клуб: {html.escape(team_name)} | ⭐ OVR: <b>{fut_stats['ovr']}</b>\n\n"
            f"⚡ PAC: {fut_stats['pac']} | 🎯 SHO: {fut_stats['sho']} | 🅰️ PAS: {fut_stats['pas']}\n"
            f"🪄 DRI: {fut_stats['dri']} | 🛡️ DEF: {fut_stats['def']} | 💪 PHY: {fut_stats['phy']}"
        )

        await update.message.reply_photo(photo=buf, caption=caption, parse_mode="HTML")
        await status_msg.delete()
    except Exception as e:
        logger.exception(f"Error in /test_card: {e}")
        await status_msg.edit_text(f"❌ Ошибка генерации карточки: {e}")
