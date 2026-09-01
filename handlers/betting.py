"""
handlers/betting.py

Logovo.bet — Telegram Interactive UI & Betting Engine Handlers.
Manages user wallets, betting line navigation, single & express slips,
daily bonus collection, and leaderboard display.
"""

import html
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from handlers.base import is_admin
import database
from services.betting_engine import generate_round_markets

logger = logging.getLogger(__name__)

# Human-readable outcome names
OUTCOME_TITLES = {
    "p1": "Победа 1",
    "x": "Ничья",
    "p2": "Победа 2",
    "tb25": "Тотал Б 2.5",
    "tm25": "Тотал М 2.5",
    "btts_yes": "Обе забьют: ДА",
    "btts_no": "Обе забьют: НЕТ"
}


def _check_betting_access(user_id: int) -> bool:
    """Check if Logovo.bet is accessible to the user (admin_only while in Lab)."""
    if is_admin(user_id):
        return True
    try:
        flag = database.get_feature_flag("betting_market")
        return flag == "public"
    except Exception:
        return False


def _get_slip(context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
    """Retrieve or initialize current user's bet coupon in session."""
    if "bet_slip" not in context.user_data:
        context.user_data["bet_slip"] = []
    return context.user_data["bet_slip"]


def _format_wallet_header(user_id: int, wallet: dict) -> str:
    """Render beautiful status block with coins and statistics."""
    bal = wallet.get("balance", 0)
    wagered = wallet.get("total_wagered", 0)
    won = wallet.get("total_won", 0)
    b_count = wallet.get("bets_count", 0)
    b_won = wallet.get("bets_won", 0)
    
    winrate = int((b_won / b_count * 100)) if b_count > 0 else 0
    profit = won - wagered
    profit_str = f"+{profit:,} 🪙" if profit >= 0 else f"{profit:,} 🪙"

    return (
        f"🎰 <b>Букмекерская Контора «Logovo.bet»</b>\n"
        f"<i>Управляющий: ИИ «Темшик» [Лаборатория]</i>\n\n"
        f"🪙 <b>Ваш баланс:</b> <code>{bal:,} 🪙</code>\n"
        f"📊 <b>Ставок:</b> {b_count} | <b>Побед:</b> {b_won} (<b>{winrate}%</b>)\n"
        f"📈 <b>Чистый профит:</b> <code>{profit_str}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )


async def cmd_bet_hub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entrypoint for /bet and /logovobet."""
    if not update.effective_user:
        return

    user_id = update.effective_user.id

    # Check Lab access restriction
    if not _check_betting_access(user_id):
        text_restricted = (
            "🧪 <b>Logovo.bet находится в Лаборатории!</b>\n\n"
            "<i>Букмекерская контора ИИ «Темшик» в данный момент проходит закрытое тестирование администрацией турнира в <code>/lab</code>.\n\n"
            "Скоро мы откроем ставки для всех участников чемпионата! Следите за анонсами в канале лиги. 🎰</i>"
        )
        if update.callback_query:
            await update.callback_query.answer("🎰 Logovo.bet временно на закрытом тесте в Лаборатории.", show_alert=True)
        elif update.message:
            await update.message.reply_text(text_restricted, parse_mode="HTML")
        return

    wallet = await asyncio.to_thread(database.get_or_create_wallet, user_id)
    slip = _get_slip(context)

    text = _format_wallet_header(user_id, wallet)
    text += "\nВыберите действие:"

    slip_count = len(slip)
    slip_btn_text = f"🎫 Купон ({slip_count})" if slip_count > 0 else "🎫 Мой Купон"

    kb = [
        [InlineKeyboardButton("📋 Линия на Тур", callback_data="bet_view_tours")],
        [
            InlineKeyboardButton(slip_btn_text, callback_data="bet_view_slip"),
            InlineKeyboardButton("📜 Мои Ставки", callback_data="bet_my_history")
        ],
        [
            InlineKeyboardButton("🎁 Бонус (+250 🪙)", callback_data="bet_claim_bonus"),
            InlineKeyboardButton("🏆 Топ Капперов", callback_data="bet_leaderboard")
        ]
    ]

    if is_admin(user_id):
        kb.append([InlineKeyboardButton("🧪 Назад в Лабораторию (/lab)", callback_data="admin_lab_menu")])

    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML"
            )
        except Exception:
            await update.effective_chat.send_message(
                text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML"
            )
    elif update.message:
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML"
        )


async def cb_bet_view_tours(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show available tours with open betting markets."""
    query = update.callback_query
    await query.answer()

    # Get active round
    active_round = await asyncio.to_thread(database.get_active_round_number) or 1
    
    # Auto-generate markets for active round if empty
    await asyncio.to_thread(generate_round_markets, active_round)
    markets = await asyncio.to_thread(database.get_active_bet_markets, active_round)

    if not markets:
        text = (
            f"📋 <b>Линия на Тур #{active_round}</b>\n\n"
            f"<i>В данный момент все матчи тура завершены или линия формируется Темшиком.</i>"
        )
        kb = [[InlineKeyboardButton("🔙 Главное Меню", callback_data="bet_menu_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        return

    text = f"📋 <b>Линия Logovo.bet • Тур #{active_round}</b>\n\nВыберите матч для ставки:\n"
    kb = []
    
    for m in markets:
        m_id = m.get("match_id")
        t1 = m.get("team1_name", "Команда 1")
        t2 = m.get("team2_name", "Команда 2")
        btn_title = f"⚽ {t1} vs {t2}"
        kb.append([InlineKeyboardButton(btn_title, callback_data=f"bet_match_{m_id}")])

    kb.append([
        InlineKeyboardButton("🎫 Перейти в купон", callback_data="bet_view_slip"),
        InlineKeyboardButton("🔙 Меню", callback_data="bet_menu_main")
    ])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")


async def cb_bet_match_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display detailed odds for a specific match."""
    query = update.callback_query
    await query.answer()

    match_id = int(query.data.replace("bet_match_", ""))
    market = await asyncio.to_thread(database.get_bet_market_by_match_id, match_id)

    if not market:
        await query.edit_message_text(
            "❌ Данный матч не найден или линия закрыта.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="bet_view_tours")]])
        )
        return

    t1 = html.escape(market["team1_name"])
    t2 = html.escape(market["team2_name"])
    tour = market["tour"]

    text = (
        f"⚽ <b>{t1} vs {t2}</b> (Тур #{tour})\n"
        f"<i>Коэффициенты от ИИ «Темшик»</i>\n\n"
        f"<b>🏆 Основные исходы:</b>\n"
        f"• <b>П1 ({t1}):</b> <code>{market['odd_p1']:.2f}</code>\n"
        f"• <b>Ничья (Х):</b> <code>{market['odd_x']:.2f}</code>\n"
        f"• <b>П2 ({t2}):</b> <code>{market['odd_p2']:.2f}</code>\n\n"
        f"<b>🎯 Тоталы и Обе Забьют:</b>\n"
        f"• <b>ТБ 2.5:</b> <code>{market['odd_tb25']:.2f}</code> | <b>ТМ 2.5:</b> <code>{market['odd_tm25']:.2f}</code>\n"
        f"• <b>Обе забьют (Да):</b> <code>{market['odd_btts_yes']:.2f}</code> | <b>(Нет):</b> <code>{market['odd_btts_no']:.2f}</code>\n\n"
        f"<i>Нажмите на исход, чтобы добавить его в купон:</i>"
    )

    kb = [
        [
            InlineKeyboardButton(f"П1 ({market['odd_p1']:.2f})", callback_data=f"bet_add_{match_id}_p1"),
            InlineKeyboardButton(f"Х ({market['odd_x']:.2f})", callback_data=f"bet_add_{match_id}_x"),
            InlineKeyboardButton(f"П2 ({market['odd_p2']:.2f})", callback_data=f"bet_add_{match_id}_p2")
        ],
        [
            InlineKeyboardButton(f"ТБ 2.5 ({market['odd_tb25']:.2f})", callback_data=f"bet_add_{match_id}_tb25"),
            InlineKeyboardButton(f"ТМ 2.5 ({market['odd_tm25']:.2f})", callback_data=f"bet_add_{match_id}_tm25")
        ],
        [
            InlineKeyboardButton(f"ОЗ: ДА ({market['odd_btts_yes']:.2f})", callback_data=f"bet_add_{match_id}_btts_yes"),
            InlineKeyboardButton(f"ОЗ: НЕТ ({market['odd_btts_no']:.2f})", callback_data=f"bet_add_{match_id}_btts_no")
        ],
        [
            InlineKeyboardButton("🎫 В Купон", callback_data="bet_view_slip"),
            InlineKeyboardButton("🔙 К списку матчей", callback_data="bet_view_tours")
        ]
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")


async def cb_bet_add_outcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add or replace an outcome in the user's bet coupon."""
    query = update.callback_query
    parts = query.data.split("_")
    match_id = int(parts[2])
    outcome = parts[3]
    if len(parts) > 4:
        outcome = f"{parts[3]}_{parts[4]}"

    market = await asyncio.to_thread(database.get_bet_market_by_match_id, match_id)
    if not market:
        await query.answer("❌ Линия на этот матч уже закрыта.", show_alert=True)
        return

    slip = _get_slip(context)

    # Remove existing pick for this match if any
    context.user_data["bet_slip"] = [s for s in slip if s["match_id"] != match_id]
    
    odd_val = market.get(f"odd_{outcome}", 1.85)
    context.user_data["bet_slip"].append({
        "match_id": match_id,
        "team1": market["team1_name"],
        "team2": market["team2_name"],
        "outcome": outcome,
        "odd": odd_val
    })

    out_name = OUTCOME_TITLES.get(outcome, outcome)
    await query.answer(f"✅ Добавлено: {out_name} (Кэф {odd_val:.2f})!", show_alert=False)
    
    # Refresh to coupon
    await cb_bet_view_slip(update, context)


async def cb_bet_view_slip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View coupon with current selections and bet placement buttons."""
    query = update.callback_query
    if query:
        await query.answer()

    user_id = update.effective_user.id
    wallet = await asyncio.to_thread(database.get_or_create_wallet, user_id)
    bal = wallet.get("balance", 0)
    slip = _get_slip(context)

    if not slip:
        text = (
            f"🎫 <b>Ваш Купон Ставок</b>\n\n"
            f"<i>Купон пуст. Перейдите в линию и выберите исходы матчей!</i>\n\n"
            f"🪙 <b>Ваш баланс:</b> <code>{bal:,} 🪙</code>"
        )
        kb = [
            [InlineKeyboardButton("📋 Открыть Линию", callback_data="bet_view_tours")],
            [InlineKeyboardButton("🔙 Главное Меню", callback_data="bet_menu_main")]
        ]
        if query:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        return

    # Calculate total odd
    total_odd = 1.0
    lines = ["🎫 <b>Ваш Купон Ставок:</b>\n"]
    for i, s in enumerate(slip, 1):
        t1 = html.escape(s['team1'])
        t2 = html.escape(s['team2'])
        out_title = OUTCOME_TITLES.get(s['outcome'], s['outcome'])
        odd_v = s['odd']
        total_odd *= odd_v
        lines.append(f"{i}. <b>{t1} vs {t2}</b>\n   👉 <code>{out_title}</code> • Кэф: <b>{odd_v:.2f}</b>")

    total_odd = round(total_odd, 2)
    bet_type = "Ординар" if len(slip) == 1 else f"Экспресс ({len(slip)} события)"
    
    lines.append(f"\n🏷️ <b>Тип:</b> {bet_type}")
    lines.append(f"🔥 <b>Итоговый Коэффициент:</b> <code>{total_odd:.2f}</code>")
    lines.append(f"🪙 <b>Ваш баланс:</b> <code>{bal:,} 🪙</code>")
    lines.append("\n<b>Выберите сумму ставки:</b>")

    text = "\n".join(lines)

    kb = [
        [
            InlineKeyboardButton("50 🪙", callback_data="bet_place_50"),
            InlineKeyboardButton("100 🪙", callback_data="bet_place_100"),
            InlineKeyboardButton("250 🪙", callback_data="bet_place_250")
        ],
        [
            InlineKeyboardButton("500 🪙", callback_data="bet_place_500"),
            InlineKeyboardButton("1 000 🪙", callback_data="bet_place_1000"),
            InlineKeyboardButton("🔥 ВСЁ (All-In)", callback_data=f"bet_place_{bal}")
        ],
        [
            InlineKeyboardButton("🗑 Очистить купон", callback_data="bet_clear_slip"),
            InlineKeyboardButton("➕ Добавить событие", callback_data="bet_view_tours")
        ],
        [InlineKeyboardButton("🔙 Главное Меню", callback_data="bet_menu_main")]
    ]

    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")


async def cb_bet_place_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute bet placement with selected amount."""
    query = update.callback_query
    amount = int(query.data.replace("bet_place_", ""))
    user_id = update.effective_user.id

    if amount <= 0:
        await query.answer("❌ Баланс пуст!", show_alert=True)
        return

    slip = _get_slip(context)
    if not slip:
        await query.answer("❌ Купон пуст!", show_alert=True)
        return

    success, res = await asyncio.to_thread(database.place_user_bet, user_id, amount, slip)
    if not success:
        await query.answer(f"❌ Ошибка: {res}", show_alert=True)
        return

    # Clear coupon on success
    context.user_data["bet_slip"] = []
    wallet = await asyncio.to_thread(database.get_or_create_wallet, user_id)

    text = (
        f"✅ <b>Ставка #{res} успешно принята!</b>\n\n"
        f"💵 <b>Сумма ставки:</b> <code>{amount:,} 🪙</code>\n"
        f"🪙 <b>Остаток на балансе:</b> <code>{wallet['balance']:,} 🪙</code>\n\n"
        f"<i>Темшик следит за матчами. Как только игра завершится, выигрыш будет зачислен автоматически!</i>"
    )

    kb = [
        [InlineKeyboardButton("📜 Мои Ставки", callback_data="bet_my_history")],
        [InlineKeyboardButton("📋 В Линию", callback_data="bet_view_tours")],
        [InlineKeyboardButton("🔙 Главное Меню", callback_data="bet_menu_main")]
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")


async def cb_bet_clear_slip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear coupon."""
    context.user_data["bet_slip"] = []
    if update.callback_query:
        await update.callback_query.answer("Купон очищен")
    await cb_bet_view_slip(update, context)


async def cb_bet_my_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show list of recent bets for user."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    bets = await asyncio.to_thread(database.get_user_bets, user_id, limit=8)

    if not bets:
        text = "📜 <b>История Ставок</b>\n\n<i>У вас пока нет активных или рассчитанных ставок.</i>"
        kb = [[InlineKeyboardButton("🔙 Меню", callback_data="bet_menu_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        return

    lines = ["📜 <b>Ваши Последние Ставки:</b>\n"]
    for b in bets:
        status_emoji = "⏳ В игре" if b["status"] == "pending" else ("✅ Выигрыш" if b["status"] == "won" else "❌ Проигрыш")
        b_type = "Ординар" if b["bet_type"] == "single" else "Экспресс"
        lines.append(
            f"• <b>Ставка #{b['id']}</b> ({b_type}) — {status_emoji}\n"
            f"  Сумма: <code>{b['amount']:,} 🪙</code> | Кэф: <b>{b['total_odd']:.2f}</b> | Выигрыш: <b>{b['potential_win']:,} 🪙</b>"
        )
        for item in b.get("items", []):
            t1 = html.escape(item.get("team1_name") or "Команда 1")
            t2 = html.escape(item.get("team2_name") or "Команда 2")
            out_name = OUTCOME_TITLES.get(item["outcome_type"], item["outcome_type"])
            item_emoji = "⏳" if item["status"] == "pending" else ("✅" if item["status"] == "won" else "❌")
            lines.append(f"    {item_emoji} {t1} vs {t2} (<code>{out_name}</code>)")
        lines.append("")

    kb = [
        [InlineKeyboardButton("📋 Линия на Тур", callback_data="bet_view_tours")],
        [InlineKeyboardButton("🔙 Главное Меню", callback_data="bet_menu_main")]
    ]

    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")


async def cb_bet_claim_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Claim daily bonus via callback button."""
    query = update.callback_query
    user_id = update.effective_user.id

    success, val, msg = await asyncio.to_thread(database.claim_daily_bonus, user_id, 250)
    await query.answer(msg, show_alert=True)
    await cmd_bet_hub(update, context)


async def cmd_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Direct command /bonus."""
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    if not _check_betting_access(user_id):
        await update.message.reply_text(
            "🧪 <b>Logovo.bet находится в Лаборатории!</b>\n\n"
            "<i>Функция ежедневного бонуса станет доступна после открытия букмекерки для всех участников чемпионата. 🎰</i>",
            parse_mode="HTML"
        )
        return

    success, val, msg = await asyncio.to_thread(database.claim_daily_bonus, user_id, 250)
    await update.message.reply_text(msg, parse_mode="HTML")


async def cb_bet_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display top bettors leaderboard."""
    query = update.callback_query
    await query.answer()

    top_bettors = await asyncio.to_thread(database.get_top_bettors, 10)

    lines = ["🏆 <b>Топ Капперов • Рейтинг Logovo.bet</b>\n"]
    if not top_bettors:
        lines.append("<i>Рейтинг пока пуст. Сделайте первую ставку!</i>")
    else:
        for i, b in enumerate(top_bettors, 1):
            name = b.get("username") or b.get("team_name") or f"Игрок {b['user_id']}"
            name = html.escape(str(name))
            bal = b.get("balance", 0)
            won_cnt = b.get("bets_won", 0)
            total_cnt = b.get("bets_count", 0)
            medal = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else f"{i}."))
            lines.append(f"{medal} <b>{name}</b> — <code>{bal:,} 🪙</code> (Побед: {won_cnt}/{total_cnt})")

    kb = [[InlineKeyboardButton("🔙 Главное Меню", callback_data="bet_menu_main")]]
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")


def register_betting_handlers(app) -> None:
    """Register all Logovo.bet commands and callback queries."""
    app.add_handler(CommandHandler(["bet", "logovobet", "ставки", "букмекер"], cmd_bet_hub))
    app.add_handler(CommandHandler(["bonus", "бонус"], cmd_bonus))
    app.add_handler(CommandHandler(["bet_top", "top_bettors"], cb_bet_leaderboard))

    app.add_handler(CallbackQueryHandler(cmd_bet_hub, pattern="^bet_menu_main$"))
    app.add_handler(CallbackQueryHandler(cb_bet_view_tours, pattern="^bet_view_tours$"))
    app.add_handler(CallbackQueryHandler(cb_bet_match_detail, pattern="^bet_match_\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_bet_add_outcome, pattern="^bet_add_\\d+_.+$"))
    app.add_handler(CallbackQueryHandler(cb_bet_view_slip, pattern="^bet_view_slip$"))
    app.add_handler(CallbackQueryHandler(cb_bet_place_amount, pattern="^bet_place_\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_bet_clear_slip, pattern="^bet_clear_slip$"))
    app.add_handler(CallbackQueryHandler(cb_bet_my_history, pattern="^bet_my_history$"))
    app.add_handler(CallbackQueryHandler(cb_bet_claim_bonus, pattern="^bet_claim_bonus$"))
    app.add_handler(CallbackQueryHandler(cb_bet_leaderboard, pattern="^bet_leaderboard$"))
