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
from media_utils import send_high_quality_animation

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
    from telegram import WebAppInfo
    import config

    bet_btn = (
        InlineKeyboardButton("🎰 Logovo.bet (Mini App)", web_app=WebAppInfo(url=config.WEBAPP_URL))
        if config.WEBAPP_URL.startswith("https://")
        else InlineKeyboardButton("🎰 Logovo.bet (Тест Букмекерки)", callback_data="bet_menu_main")
    )

    keyboard = [
        [InlineKeyboardButton("🃏 Тестировать Карточки EA FC", callback_data="lab_card_menu")],
        [bet_btn],
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
    """Card test menu featuring 3 official KPL League formats & specialized themes."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    text = (
        "🏆 <b>Официальные Форматы Карточек Лиги КПЛ 2026</b>\n\n"
        "Градация дизайна под турниры КПЛ по рейтингу OVR:\n\n"
        "🥉 1. <b>КПЛ Standard (OVR ≤ 85)</b> — Графитовый титан и рубиновый кант КПЛ\n"
        "🥈 2. <b>КПЛ Star Edition (OVR 86–92)</b> — Сапфирово-рубиновый неон\n"
        "🥇 3. <b>КПЛ Prime MVP (OVR 93+)</b> — 24K Золото и базальтовое пламя КПЛ\n\n"
        "<i>Дополнительные концепт-стили:</i>"
    )
    keyboard = [
        [
            InlineKeyboardButton("🥉 1. КПЛ Standard (≤85)", callback_data="lab_demo_card_kpl_standard"),
            InlineKeyboardButton("🥈 2. КПЛ Star (86-92)", callback_data="lab_demo_card_kpl_star"),
        ],
        [
            InlineKeyboardButton("🥇 3. КПЛ Prime (93+)", callback_data="lab_demo_card_kpl_prime"),
            InlineKeyboardButton("⭐ 4. UCL Night", callback_data="lab_demo_card_ucl_night"),
        ],
        [
            InlineKeyboardButton("🔥 5. Inferno Magma", callback_data="lab_demo_card_inferno_magma"),
            InlineKeyboardButton("⚡ 6. Cyberpunk", callback_data="lab_demo_card_cyber_hud"),
        ],
        [
            InlineKeyboardButton("💎 7. Hyper-Glass", callback_data="lab_demo_card_hyper_glass"),
            InlineKeyboardButton("🌌 8. Void Eclipse", callback_data="lab_demo_card_void_eclipse"),
        ],
        [InlineKeyboardButton("🎬 ➔ ТЕСТ АНИМИРОВАННЫХ (GIF/MP4)", callback_data="lab_anim_card_menu")],
        [InlineKeyboardButton("🔍 Выбрать реального игрока из клуба", callback_data="lab_card_pick_club")],
        [InlineKeyboardButton("« Назад в лабораторию", callback_data="admin_lab_menu")],
    ]
    await safe_edit_or_reply(query, context, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def cb_lab_anim_card_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Animated card testing menu featuring KPL formats and specialized loop shaders."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    text = (
        "🎬 <b>Анимированные Карточки Лиги КПЛ 2026 (MP4/GIF)</b>\n\n"
        "Выберите формат динамической анимации:\n\n"
        "🥉 1. <b>КПЛ Standard</b> — Стальной титановый блеск и пульс рубинового канта\n"
        "🥈 2. <b>КПЛ Star</b> — Сапфировый лазерный луч и мерцание звезд\n"
        "🥇 3. <b>КПЛ Prime MVP</b> — Золотой световой поток и восходящие искры КПЛ\n"
        "⭐ 4. <b>UCL Night</b> — Лазерные созвездия звезд ЛЧ\n"
        "🔥 5. <b>Inferno Magma</b> — Раскаленная магма и летящие угли"
    )
    keyboard = [
        [
            InlineKeyboardButton("🥉 1. КПЛ Standard", callback_data="lab_demo_anim_kpl_standard"),
            InlineKeyboardButton("🥈 2. КПЛ Star", callback_data="lab_demo_anim_kpl_star"),
        ],
        [
            InlineKeyboardButton("🥇 3. КПЛ Prime", callback_data="lab_demo_anim_kpl_prime"),
            InlineKeyboardButton("⭐ 4. UCL Night", callback_data="lab_demo_anim_ucl_night"),
        ],
        [
            InlineKeyboardButton("🔥 5. Inferno Magma", callback_data="lab_demo_anim_inferno_magma"),
            InlineKeyboardButton("⚡ 6. Cyberpunk", callback_data="lab_demo_anim_cyber_hud"),
        ],
        [InlineKeyboardButton("🖼️ ➔ Тест статичных карточек", callback_data="lab_card_menu")],
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
        filename=f"{style_id}.png",
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

    frames, fps, anim_w, anim_h = await asyncio.to_thread(fc_card_generator.render_animated_card_frames, test_player, anim_style)
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

    await send_high_quality_animation(
        bot=context.bot,
        chat_id=user_id,
        animation_input=frames,
        caption=caption,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
        filename=f"{anim_style}.mp4"
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

    db_stats = await asyncio.to_thread(database.get_player_card_stats, player_name, club)
    goals = db_stats.get("total_goals", 0)
    assists = db_stats.get("total_assists", 0)
    real_pos = db_stats.get("position") or await asyncio.to_thread(database.get_player_position, player_name, club)

    card_data = {
        "player_name": player_name,
        "team_name": club,
        "position": real_pos,
        "total_goals": goals,
        "total_assists": assists,
        "matches_played": max(1, goals + assists),
    }

    fut_stats = fc_card_generator.calculate_fut_attributes(card_data)
    theme_name = fc_card_generator.get_kpl_tier_by_ovr(fut_stats["ovr"])
    cfg = fc_card_generator.CARD_STYLES.get(theme_name, fc_card_generator.CARD_STYLES["kpl_prime"])

    buf = await asyncio.to_thread(fc_card_generator.generate_ea_fc_card, card_data, theme_name)

    caption = (
        f"🏆 <b>Карточка Лиги КПЛ 2026: {cfg['title']}</b>\n\n"
        f"👤 <b>{html.escape(player_name)}</b> · {html.escape(club)}\n"
        f"⭐ <b>OVR: {fut_stats['ovr']}</b> ({cfg['desc']})\n"
        f"⚽ Голов: <b>{goals}</b> | 🅰️ Ассистов: <b>{assists}</b> | 🔥 Очков: <b>{goals + assists}</b>\n\n"
        f"⚡ <b>PAC:</b> {fut_stats['pac']} | 🎯 <b>SHO:</b> {fut_stats['sho']} | 🅰️ <b>PAS:</b> {fut_stats['pas']}\n"
        f"🪄 <b>DRI:</b> {fut_stats['dri']} | 🛡️ <b>DEF:</b> {fut_stats['def']} | 💪 <b>PHY:</b> {fut_stats['phy']}\n\n"
        f"<i>🧪 Тестовая лаборатория Logovobot</i>"
    )

    keyboard = [
        [
            InlineKeyboardButton(f"🎬 Анимировать ({cfg['title']})", callback_data=f"lab_p_anim_{club}|{player_name}|{theme_name}"),
            InlineKeyboardButton("✨ Выбрать стиль анимации", callback_data=f"lab_p_styles_{club}|{player_name}"),
        ],
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
async def cb_lab_player_anim_styles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show list of 10 animation styles for a specific chosen squad player."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    payload = query.data.replace("lab_p_styles_", "")
    club, player_name = payload.split("|", 1) if "|" in payload else ("—", payload)

    text = f"🎬 <b>Выберите стиль анимации</b> для игрока <b>{html.escape(player_name)}</b> ({html.escape(club)}):"

    keyboard = [
        [
            InlineKeyboardButton("🌟 1. TOTY Gold", callback_data=f"lab_p_anim_{club}|{player_name}|toty_gold"),
            InlineKeyboardButton("🌌 2. Void Eclipse", callback_data=f"lab_p_anim_{club}|{player_name}|void_eclipse"),
        ],
        [
            InlineKeyboardButton("⚡ 3. Cyberpunk", callback_data=f"lab_p_anim_{club}|{player_name}|cyber_hud"),
            InlineKeyboardButton("💎 4. Hyper-Glass", callback_data=f"lab_p_anim_{club}|{player_name}|hyper_glass"),
        ],
        [
            InlineKeyboardButton("🔥 5. Inferno Magma", callback_data=f"lab_p_anim_{club}|{player_name}|inferno_magma"),
            InlineKeyboardButton("❄️ 6. Glacial Frost", callback_data=f"lab_p_anim_{club}|{player_name}|glacial_frost"),
        ],
        [
            InlineKeyboardButton("⚽ 7. Anime Sakuga", callback_data=f"lab_p_anim_{club}|{player_name}|anime_sakuga"),
            InlineKeyboardButton("👑 8. Royal 24K", callback_data=f"lab_p_anim_{club}|{player_name}|royal_24k"),
        ],
        [
            InlineKeyboardButton("🏎️ 9. Aero Carbon", callback_data=f"lab_p_anim_{club}|{player_name}|aero_carbon"),
            InlineKeyboardButton("🌌 10. UCL Night", callback_data=f"lab_p_anim_{club}|{player_name}|ucl_night"),
        ],
        [InlineKeyboardButton(f"« Назад к {player_name}", callback_data=f"lab_gen_card_{club}|{player_name}")]
    ]

    await safe_edit_or_reply(query, context, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


@admin_only
async def cb_lab_player_anim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and dispatch high-quality animation for a specific squad player."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer("Рендерю высококачественную анимацию игрока...")

    payload = query.data.replace("lab_p_anim_", "")
    parts = payload.split("|")
    club = parts[0] if len(parts) > 0 else "—"
    player_name = parts[1] if len(parts) > 1 else "Игрок"
    style_id = parts[2] if len(parts) > 2 else "toty_gold"

    user_id = query.from_user.id

    db_stats = await asyncio.to_thread(database.get_player_card_stats, player_name, club)
    goals = db_stats.get("total_goals", 0)
    assists = db_stats.get("total_assists", 0)
    real_pos = db_stats.get("position") or await asyncio.to_thread(database.get_player_position, player_name, club)

    card_data = {
        "player_name": player_name,
        "team_name": club,
        "position": real_pos,
        "total_goals": goals,
        "total_assists": assists,
        "matches_played": max(1, goals + assists),
    }

    frames, fps, anim_w, anim_h = await asyncio.to_thread(
        fc_card_generator.render_animated_card_frames, card_data, style_id
    )
    fut_stats = fc_card_generator.calculate_fut_attributes(card_data)
    cfg = fc_card_generator.CARD_STYLES.get(fc_card_generator._normalize_style_key(style_id), fc_card_generator.CARD_STYLES["toty_gold"])

    caption = (
        f"🎬 <b>Анимированная карточка: {cfg['title']}</b>\n\n"
        f"👤 <b>{html.escape(player_name)}</b> ({html.escape(club)})\n"
        f"⭐ <b>OVR: {fut_stats['ovr']}</b> | Позиция: <b>{fut_stats['position']}</b>\n"
        f"⚡ <b>PAC:</b> {fut_stats['pac']} | 🎯 <b>SHO:</b> {fut_stats['sho']} | 🅰️ <b>PAS:</b> {fut_stats['pas']}\n"
        f"🪄 <b>DRI:</b> {fut_stats['dri']} | 🛡️ <b>DEF:</b> {fut_stats['def']} | 💪 <b>PHY:</b> {fut_stats['phy']}\n\n"
        f"<i>Анимация: {cfg['desc']}</i>"
    )

    keyboard = [
        [
            InlineKeyboardButton("🖼️ Статичная", callback_data=f"lab_gen_card_{club}|{player_name}"),
            InlineKeyboardButton("🔄 Другой стиль", callback_data=f"lab_p_styles_{club}|{player_name}"),
        ],
        [InlineKeyboardButton("👥 Другой игрок", callback_data=f"lab_pick_player_{club}")],
        [InlineKeyboardButton("« В лабораторию", callback_data="admin_lab_menu")]
    ]

    await send_high_quality_animation(
        bot=context.bot,
        chat_id=user_id,
        animation_input=frames,
        caption=caption,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
        filename=f"{player_name}_{style_id}.mp4"
    )


@admin_only
async def cmd_test_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick command: /test_card [Player Name] [Team Name (optional)] [Style (optional)]"""
    user = update.effective_user
    if not user or not is_admin(user.id):
        if update.message:
            await update.message.reply_text("⛔ Доступ запрещён.")
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "ℹ️ <b>Использование команды:</b>\n"
            "<code>/test_card [Имя Фамилия] [Клуб (опционально)] [Стиль (опционально)]</code>\n\n"
            "Примеры:\n"
            "• <code>/test_card Винисиус Спортинг toty</code>\n"
            "• <code>/test_card Gyokeres Спортинг inferno</code>\n"
            "• <code>/test_card Pedri Барселона ucl</code>",
            parse_mode="HTML"
        )
        return

    player_name = args[0]
    team_name = args[1] if len(args) > 1 else "Спортинг"
    style_id = args[2] if len(args) > 2 else "toty_gold"

    status_msg = await update.message.reply_text("⏳ Генерирую карточку игрока...")

    card_data = {
        "player_name": player_name,
        "team_name": team_name,
        "position": "ST",
        "total_goals": 15,
        "total_assists": 8,
        "matches_played": 12,
    }

    try:
        buf = await asyncio.to_thread(fc_card_generator.generate_ea_fc_card, card_data, style_id)
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


@admin_only
async def cmd_test_anim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick command: /test_anim [Player Name] [Team Name (optional)] [Style (optional)]"""
    user = update.effective_user
    if not user or not is_admin(user.id):
        if update.message:
            await update.message.reply_text("⛔ Доступ запрещён.")
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "ℹ️ <b>Использование команды:</b>\n"
            "<code>/test_anim [Имя Фамилия] [Клуб (опционально)] [Стиль (опционально)]</code>\n\n"
            "Доступные стили: <code>toty, void, cyber, glass, inferno, frost, anime, royal, aero, ucl</code>\n\n"
            "Примеры:\n"
            "• <code>/test_anim Винисиус Спортинг toty</code>\n"
            "• <code>/test_anim Gyokeres Спортинг inferno</code>\n"
            "• <code>/test_anim Rodrygo Бенфика ucl</code>",
            parse_mode="HTML"
        )
        return

    player_name = args[0]
    team_name = args[1] if len(args) > 1 else "Спортинг"
    style_id = args[2] if len(args) > 2 else "kpl_prime"
    real_pos = await asyncio.to_thread(database.get_player_position, player_name, team_name)

    status_msg = await update.message.reply_text("⏳ Рендерю анимацию через FFmpeg...")

    card_data = {
        "player_name": player_name,
        "team_name": team_name,
        "position": real_pos,
        "total_goals": 18,
        "total_assists": 9,
        "matches_played": 14,
    }

    try:
        frames, fps, anim_w, anim_h = await asyncio.to_thread(
            fc_card_generator.render_animated_card_frames, card_data, style_id
        )
        fut_stats = fc_card_generator.calculate_fut_attributes(card_data)
        cfg = fc_card_generator.CARD_STYLES.get(fc_card_generator._normalize_style_key(style_id), fc_card_generator.CARD_STYLES["toty_gold"])

        caption = (
            f"🎬 <b>Анимированная карточка: {cfg['title']}</b>\n\n"
            f"👤 <b>{html.escape(player_name.upper())}</b> ({html.escape(team_name)})\n"
            f"⭐ <b>OVR: {fut_stats['ovr']}</b> | Позиция: <b>{fut_stats['position']}</b>\n"
            f"⚡ <b>PAC:</b> {fut_stats['pac']} | 🎯 <b>SHO:</b> {fut_stats['sho']} | 🅰️ <b>PAS:</b> {fut_stats['pas']}\n"
            f"🪄 <b>DRI:</b> {fut_stats['dri']} | 🛡️ <b>DEF:</b> {fut_stats['def']} | 💪 <b>PHY:</b> {fut_stats['phy']}\n\n"
            f"<i>Анимация: {cfg['desc']}</i>"
        )

        await send_high_quality_animation(
            bot=context.bot,
            chat_id=user.id,
            animation_input=frames,
            caption=caption,
            parse_mode="HTML",
            filename=f"{player_name}_{style_id}.mp4"
        )
        await status_msg.delete()
    except Exception as e:
        logger.exception(f"Error in /test_anim: {e}")
        await status_msg.edit_text(f"❌ Ошибка генерации анимации: {e}")
