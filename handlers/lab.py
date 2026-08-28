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
    """Card test menu with all 10 distinct design concepts."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    text = (
        "🃏 <b>10 Топовых Концептов Дизайна Карточек (FC 25 / Esports)</b>\n\n"
        "Выберите стиль для генерации статичной карточки (PNG):\n\n"
        "1. 🌟 <b>TOTY Celestial Gold</b> — Божественное жидкое золото\n"
        "2. 🌌 <b>Void Eclipse</b> — Черная дыра и гравитация\n"
        "3. ⚡ <b>Cyberpunk 2077</b> — Лазерный кибер-HUD\n"
        "4. 💎 <b>Liquid Hyper-Glass</b> — Сапфировое стекло\n"
        "5. 🔥 <b>Inferno Magma</b> — Раскаленная лава и базальт\n"
        "6. ❄️ <b>Glacial Frost</b> — Арктический лед и алмазы\n"
        "7. 👁️ <b>Anime Sakuga</b> — Аура эгоиста Blue Lock\n"
        "8. 👑 <b>Royal 24K Ingot</b> — Золотой банковский слиток\n"
        "9. 🏎️ <b>Red Bull Aero Carbon</b> — Кованый карбон F1\n"
        "10. ⭐ <b>UEFA Champions Night</b> — Звездный хром ЛЧ"
    )
    keyboard = [
        [
            InlineKeyboardButton("🌟 1. TOTY Gold", callback_data="lab_demo_card_toty_gold"),
            InlineKeyboardButton("🌌 2. Void Eclipse", callback_data="lab_demo_card_void_eclipse"),
        ],
        [
            InlineKeyboardButton("⚡ 3. Cyberpunk", callback_data="lab_demo_card_cyber_hud"),
            InlineKeyboardButton("💎 4. Hyper-Glass", callback_data="lab_demo_card_hyper_glass"),
        ],
        [
            InlineKeyboardButton("🔥 5. Inferno Magma", callback_data="lab_demo_card_inferno_magma"),
            InlineKeyboardButton("❄️ 6. Glacial Frost", callback_data="lab_demo_card_glacial_frost"),
        ],
        [
            InlineKeyboardButton("👁️ 7. Anime Sakuga", callback_data="lab_demo_card_anime_sakuga"),
            InlineKeyboardButton("👑 8. Royal 24K", callback_data="lab_demo_card_royal_24k"),
        ],
        [
            InlineKeyboardButton("🏎️ 9. Aero Carbon", callback_data="lab_demo_card_aero_carbon"),
            InlineKeyboardButton("⭐ 10. UCL Night", callback_data="lab_demo_card_ucl_night"),
        ],
        [InlineKeyboardButton("🎬 ➔ ТЕСТ АНИМИРОВАННЫХ (GIF)", callback_data="lab_anim_card_menu")],
        [InlineKeyboardButton("🔍 Выбрать реального игрока из клуба", callback_data="lab_card_pick_club")],
        [InlineKeyboardButton("« Назад в лабораторию", callback_data="admin_lab_menu")],
    ]
    await safe_edit_or_reply(query, context, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def cb_lab_anim_card_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Animated card testing menu for all 10 styles."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    text = (
        "🎬 <b>10 Топовых АНИМИРОВАННЫХ Карточек Игроков (GIF)</b>\n\n"
        "Выберите стиль динамической анимации:\n\n"
        "1. 🌟 <b>TOTY Gold</b> — Скользящий жидкий луч света и аура\n"
        "2. 🌌 <b>Void Eclipse</b> — Вращающийся диск и притяжение частиц\n"
        "3. ⚡ <b>Cyberpunk</b> — Сканирующий лазерный луч и неоновый пульс\n"
        "4. 💎 <b>Hyper-Glass</b> — Каустика и радужные призмы\n"
        "5. 🔥 <b>Inferno Magma</b> — Пульс лавы и 35 горящих искр\n"
        "6. ❄️ <b>Glacial Frost</b> — Кристаллизация и мерцающие алмазы\n"
        "7. 👁️ <b>Anime Sakuga</b> — Потрескивающие молнии и огонь ауры\n"
        "8. 👑 <b>Royal 24K</b> — Тяжелый золотой металлический блик\n"
        "9. 🏎️ <b>Aero Carbon</b> — Ветровые струи аэродинамики F1\n"
        "10. ⭐ <b>UEFA Night</b> — Лазерные созвездия звезд ЛЧ"
    )
    keyboard = [
        [
            InlineKeyboardButton("🌟 1. TOTY Gold", callback_data="lab_demo_anim_toty_gold"),
            InlineKeyboardButton("🌌 2. Void Eclipse", callback_data="lab_demo_anim_void_eclipse"),
        ],
        [
            InlineKeyboardButton("⚡ 3. Cyberpunk", callback_data="lab_demo_anim_cyber_hud"),
            InlineKeyboardButton("💎 4. Hyper-Glass", callback_data="lab_demo_anim_hyper_glass"),
        ],
        [
            InlineKeyboardButton("🔥 5. Inferno Magma", callback_data="lab_demo_anim_inferno_magma"),
            InlineKeyboardButton("❄️ 6. Glacial Frost", callback_data="lab_demo_anim_glacial_frost"),
        ],
        [
            InlineKeyboardButton("👁️ 7. Anime Sakuga", callback_data="lab_demo_anim_anime_sakuga"),
            InlineKeyboardButton("👑 8. Royal 24K", callback_data="lab_demo_anim_royal_24k"),
        ],
        [
            InlineKeyboardButton("🏎️ 9. Aero Carbon", callback_data="lab_demo_anim_aero_carbon"),
            InlineKeyboardButton("⭐ 10. UCL Night", callback_data="lab_demo_anim_ucl_night"),
        ],
        [InlineKeyboardButton("🖼️ ➔ К статичным карточкам (PNG)", callback_data="lab_card_menu")],
        [InlineKeyboardButton("« В лабораторию", callback_data="admin_lab_menu")],
    ]
    await safe_edit_or_reply(query, context, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def cb_lab_demo_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate selected static card design and send directly to admin in DM."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer("Генерирую карточку...")

    style_id = query.data.replace("lab_demo_card_", "")
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

    buf = await asyncio.to_thread(fc_card_generator.generate_ea_fc_card, test_player, style_id)
    stats = fc_card_generator.calculate_fut_attributes(test_player)

    cfg = fc_card_generator.CARD_STYLES.get(fc_card_generator._normalize_style_key(style_id), fc_card_generator.CARD_STYLES["toty_gold"])

    caption = (
        f"🎨 <b>Статичная карточка: {cfg['title']}</b>\n\n"
        f"👤 <b>{html.escape(test_player['player_name'])}</b> ({html.escape(test_player['team_name'])})\n"
        f"⭐ <b>OVR: {stats['ovr']}</b> | Позиция: <b>{stats['position']}</b>\n"
        f"⚡ <b>PAC:</b> {stats['pac']} | 🎯 <b>SHO:</b> {stats['sho']} | 🅰️ <b>PAS:</b> {stats['pas']}\n"
        f"🪄 <b>DRI:</b> {stats['dri']} | 🛡️ <b>DEF:</b> {stats['def']} | 💪 <b>PHY:</b> {stats['phy']}\n\n"
        f"<i>Стиль: {cfg['desc']}</i>"
    )

    keyboard = [
        [
            InlineKeyboardButton("🎬 Анимировать (GIF)", callback_data=f"lab_demo_anim_{style_id}"),
            InlineKeyboardButton("🔄 Выбрать другой стиль", callback_data="lab_card_menu"),
        ],
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

    cfg = fc_card_generator.CARD_STYLES.get(fc_card_generator._normalize_style_key(anim_style), fc_card_generator.CARD_STYLES["toty_gold"])

    caption = (
        f"🎬 <b>Анимированная карточка: {cfg['title']}</b>\n\n"
        f"👤 <b>{html.escape(test_player['player_name'])}</b> ({html.escape(test_player['team_name'])})\n"
        f"⭐ <b>OVR: {stats['ovr']}</b> | Позиция: <b>{stats['position']}</b>\n"
        f"⚡ <b>PAC:</b> {stats['pac']} | 🎯 <b>SHO:</b> {stats['sho']} | 🅰️ <b>PAS:</b> {stats['pas']}\n"
        f"🪄 <b>DRI:</b> {stats['dri']} | 🛡️ <b>DEF:</b> {stats['def']} | 💪 <b>PHY:</b> {stats['phy']}\n\n"
        f"<i>Анимация: {cfg['desc']}</i>"
    )

    keyboard = [
        [
            InlineKeyboardButton("🖼️ Статичная (PNG)", callback_data=f"lab_demo_card_{anim_style}"),
            InlineKeyboardButton("🔄 Другая анимация", callback_data="lab_anim_card_menu"),
        ],
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
