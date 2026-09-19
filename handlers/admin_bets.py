"""
handlers/admin_bets.py

Мониторинг и управление ставками для супер-администраторов (Logovo.bet).
СТРОГО ТОЛЬКО В ЛИЧНЫХ СООБЩЕНИЯХ (Private chat only).
СТРОГО ТОЛЬКО ДЛЯ СУПЕР-АДМИНИСТРАТОРОВ (is_global_admin).
"""

import asyncio
import html
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

import database
from handlers.base import is_global_admin
from handlers.cabinet import safe_send_notification

logger = logging.getLogger(__name__)

# Маппинг статусов купона
BET_STATUS_TITLES = {
    "pending": "⏳ В игре",
    "won": "✅ Выигрыш",
    "lost": "❌ Проигрыш",
    "refunded": "🔄 Возврат",
    "cancelled": "🔄 Отмена",
    "cashed_out": "💵 Кэшаут",
}

ITEM_STATUS_EMOJI = {
    "pending": "⏳",
    "won": "✅",
    "lost": "❌",
    "refunded": "🔄",
}

OUTCOME_TITLES = {
    "p1": "П1",
    "draw": "Ничья",
    "p2": "П2",
    "1x": "1X",
    "12": "12",
    "x2": "X2",
    "tb_1_5": "ТБ 1.5",
    "tm_1_5": "ТМ 1.5",
    "tb_2_5": "ТБ 2.5",
    "tm_2_5": "ТМ 2.5",
    "tb_3_5": "ТБ 3.5",
    "tm_3_5": "ТМ 3.5",
    "both_yes": "ОЗ (Да)",
    "both_no": "ОЗ (Нет)",
}

PAGE_SIZE = 5


def _ensure_private_chat_and_super_admin(update: Update) -> tuple[bool, int | None]:
    """Проверка ограничений: строго ЛС и строго глобальный супер-админ."""
    chat = update.effective_chat
    user = update.effective_user

    if not chat or not user:
        return False, None

    if chat.type != "private":
        return False, user.id

    if not is_global_admin(user.id):
        return False, None

    return True, user.id


def _build_overview_header(stats: dict, filter_status: str | None = None, filter_user_id: int | None = None) -> str:
    """Генерация сводки метрик банка и конторы."""
    profit = stats.get("bookmaker_profit", 0)
    profit_sign = "+" if profit > 0 else ""
    profit_emoji = "🟢" if profit >= 0 else "🔴"

    filter_note = ""
    if filter_user_id:
        u = database.get_user(filter_user_id)
        u_name = f"@{u['username']}" if u and u.get("username") else f"ID {filter_user_id}"
        filter_note = f"\n🎯 <i>Фильтр по игроку: <b>{html.escape(u_name)}</b></i>"
    elif filter_status and filter_status != "all":
        status_label = BET_STATUS_TITLES.get(filter_status, filter_status)
        filter_note = f"\n🎯 <i>Фильтр по статусу: <b>{status_label}</b></i>"

    return (
        f"🎰 <b>МОНИТОРИНГ СТАВОК (Super-Admin)</b>{filter_note}\n"
        f"──────────────────────────────\n"
        f"📊 <b>Сводка по ставкам:</b>\n"
        f"• Всего ставок: <b>{stats['total_bets']:,}</b>\n"
        f"• ⏳ В игре: <b>{stats['count_pending']:,}</b> | ✅ Выигрышей: <b>{stats['count_won']:,}</b>\n"
        f"• ❌ Проигрышей: <b>{stats['count_lost']:,}</b> | 🔄 Возвратов: <b>{stats['count_refunded']:,}</b>\n"
        f"• 💵 Кэшаутов: <b>{stats['count_cashed_out']:,}</b>\n\n"
        f"💰 <b>Банк и Риск Конторы:</b>\n"
        f"• Оборот (Wagered): <code>{stats['total_wagered']:,} 🪙</code>\n"
        f"• В игре (Exposure): <code>{stats['pending_exposure']:,} 🪙</code>\n"
        f"• Макс. выплата по активным: <code>{stats['pending_potential_liability']:,} 🪙</code>\n"
        f"• Выплачено игрокам: <code>{stats['total_paid_out']:,} 🪙</code>\n"
        f"• Профит конторы: {profit_emoji} <b>{profit_sign}{profit:,} 🪙</b>\n"
        f"──────────────────────────────\n"
    )


def _format_bet_snippet(bet: dict) -> str:
    """Форматирование одной ставки для ленты."""
    b_id = bet["id"]
    status = bet["status"]
    status_title = BET_STATUS_TITLES.get(status, status)
    b_type = "Ординар" if bet.get("bet_type") == "single" else "Экспресс"
    amount = bet.get("amount", 0)
    odd = float(bet.get("total_odd") or 1.0)
    potential_win = bet.get("potential_win", 0)
    actual_payout = bet.get("actual_payout", 0)

    # Информация об игроке
    user_name = f"@{bet['username']}" if bet.get("username") else f"ID {bet['user_id']}"
    team_name = bet.get("user_team")
    team_info = f" ({html.escape(team_name)})" if team_name else ""

    created_at = str(bet.get("created_at") or "")[:16]

    win_info = f"Выплата: <b>{actual_payout:,} 🪙</b>" if status in ("won", "cashed_out") else f"Потенц. выигрыш: <b>{potential_win:,} 🪙</b>"

    lines = [
        f"• <b>Ставка #{b_id}</b> ({b_type}) — <b>{status_title}</b>",
        f"  👤 <b>{html.escape(user_name)}</b>{team_info}",
        f"  💵 <code>{amount:,} 🪙</code> | Кэф: <b>{odd:.2f}</b> | {win_info}",
        f"  🕒 <i>{created_at}</i>"
    ]

    items = bet.get("items", [])
    for item in items[:3]:
        t1 = html.escape(item.get("team1_name") or "Команда 1")
        t2 = html.escape(item.get("team2_name") or "Команда 2")
        out_code = item.get("outcome_type") or ""
        out_name = OUTCOME_TITLES.get(out_code, out_code)
        item_odd = float(item.get("odd") or 1.0)
        item_emoji = ITEM_STATUS_EMOJI.get(item.get("status"), "•")
        lines.append(f"    {item_emoji} {t1} vs {t2} (<code>{out_name}</code> @{item_odd:.2f})")

    if len(items) > 3:
        lines.append(f"    <i>...и ещё {len(items) - 3} событ.</i>")

    return "\n".join(lines)


def _build_monitor_keyboard(
    status: str,
    page: int,
    total_count: int,
    bets: list[dict],
    admin_id: int,
    user_id_filter: int | None = None
) -> InlineKeyboardMarkup:
    """Генерация клавиатуры фильтров, пагинации, быстрых кнопок и переключателя оповещений."""
    keyboard = []

    # Строка 1: Фильтры статусов
    def flt_btn(code: str, label: str) -> InlineKeyboardButton:
        is_active = (status == code)
        text = f"• {label} •" if is_active else label
        uid_str = str(user_id_filter) if user_id_filter else "0"
        return InlineKeyboardButton(text, callback_data=f"admin_bets_flt:{code}:0:{uid_str}")

    keyboard.append([
        flt_btn("all", "Все"),
        flt_btn("pending", "⏳ В игре"),
        flt_btn("won", "✅ Выигрыш"),
        flt_btn("lost", "❌ Проигрыш"),
    ])

    # Строка 2: Пагинация
    total_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)
    uid_str = str(user_id_filter) if user_id_filter else "0"
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ Пред", callback_data=f"admin_bets_page:{status}:{page - 1}:{uid_str}"))
    nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data=f"admin_bets_refresh:{status}:{page}:{uid_str}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("След ▶️", callback_data=f"admin_bets_page:{status}:{page + 1}:{uid_str}"))
    keyboard.append(nav_row)

    # Строка 3: Быстрые кнопки просмотра деталей по каждой ставке на странице
    if bets:
        detail_buttons = [
            InlineKeyboardButton(f"🔍 #{b['id']}", callback_data=f"admin_bet_view:{b['id']}")
            for b in bets
        ]
        # Разбиваем по 3 в ряд
        for i in range(0, len(detail_buttons), 3):
            keyboard.append(detail_buttons[i:i + 3])

    # Строка 4: Переключатель Live-оповещений + Сброс фильтра игрока (если включен)
    alerts_on = database.is_live_bet_alerts_enabled(admin_id)
    alert_label = "🔔 Оповещения: ВКЛ" if alerts_on else "🔕 Оповещения: ВЫКЛ"
    ctrl_row = [
        InlineKeyboardButton(alert_label, callback_data=f"admin_bets_alerts_toggle:{status}:{page}:{uid_str}"),
        InlineKeyboardButton("🔄 Обновить", callback_data=f"admin_bets_refresh:{status}:{page}:{uid_str}"),
    ]
    keyboard.append(ctrl_row)

    bottom_row = []
    if user_id_filter:
        bottom_row.append(InlineKeyboardButton("👥 Сбросить фильтр игрока", callback_data=f"admin_bets_flt:{status}:0:0"))
    bottom_row.append(InlineKeyboardButton("« Админ-панель", callback_data="admin_main_menu"))
    keyboard.append(bottom_row)

    return InlineKeyboardMarkup(keyboard)


async def cmd_admin_bets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Команда /admin_bets (алиасы: /all_bets, /track_bets, /ставки_админ).
    Работает строго в ЛС и только для супер-админа.
    """
    chat = update.effective_chat
    user = update.effective_user

    if not chat or not user:
        return

    if chat.type != "private":
        bot_user = (context.bot.username or "").lower() if context and context.bot else ""
        pm_url = f"https://t.me/{bot_user}?start=admin_bets" if bot_user else "https://t.me"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("💬 Открыть в ЛС", url=pm_url)]])
        await update.effective_message.reply_text(
            "🔒 <b>Команда доступна только в личных сообщениях</b>\n\n"
            "Мониторинг ставок содержит конфиденциальную финансовую статистику "
            "и информацию об игроках. Пожалуйста, откройте бота в ЛС.",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return

    if not is_global_admin(user.id):
        await update.effective_message.reply_text(
            "⛔ <b>Доступ запрещён</b>\n\n"
            "Данная команда доступна исключительно супер-администраторам лиги.",
            parse_mode="HTML"
        )
        return

    # Разбор аргументов
    status_filter = "all"
    user_id_filter = None

    if context and context.args:
        arg = context.args[0].strip().lower()
        if arg in ("pending", "active", "в_игре", "игра"):
            status_filter = "pending"
        elif arg in ("won", "win", "выигрыш"):
            status_filter = "won"
        elif arg in ("lost", "проигрыш"):
            status_filter = "lost"
        elif arg in ("refunded", "cancelled", "возврат"):
            status_filter = "refunded"
        elif arg.startswith("@"):
            raw_user = arg[1:]
            with database.transaction() as conn:
                cur = conn.cursor()
                cur.execute("SELECT telegram_id FROM users WHERE LOWER(username) = ?", (raw_user,))
                row = cur.fetchone()
                if row:
                    user_id_filter = row["telegram_id"]
        elif arg.isdigit():
            user_id_filter = int(arg)

    await _render_bets_monitor(update, context, status=status_filter, page=0, user_id_filter=user_id_filter)


async def _render_bets_monitor(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    status: str = "all",
    page: int = 0,
    user_id_filter: int | None = None,
    edit: bool = False
) -> None:
    """Отрисовка главного экрана мониторинга ставок."""
    user = update.effective_user
    if not user:
        return

    offset = page * PAGE_SIZE
    bets, total_count = await asyncio.to_thread(
        database.get_all_bets,
        status=status,
        user_id=user_id_filter,
        limit=PAGE_SIZE,
        offset=offset
    )
    stats = await asyncio.to_thread(database.get_bets_summary_stats)

    header = _build_overview_header(stats, filter_status=status, filter_user_id=user_id_filter)

    if not bets:
        body = "\n<i>Ставок с выбранными критериями не найдено.</i>\n"
    else:
        items_text = "\n\n".join(_format_bet_snippet(b) for b in bets)
        body = f"\n📋 <b>Лента ставок ({page * PAGE_SIZE + 1}-{min((page + 1) * PAGE_SIZE, total_count)} из {total_count}):</b>\n\n{items_text}\n"

    full_text = header + body
    reply_markup = _build_monitor_keyboard(status, page, total_count, bets, user.id, user_id_filter)

    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(full_text, reply_markup=reply_markup, parse_mode="HTML")
            return
        except Exception as e:
            logger.debug(f"Failed edit_message_text in _render_bets_monitor: {e}")

    if update.effective_message:
        await update.effective_message.reply_text(full_text, reply_markup=reply_markup, parse_mode="HTML")


async def cb_admin_bets_navigate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик навигации, смены фильтра и обновления списка."""
    query = update.callback_query
    if not query:
        return

    ok, admin_id = _ensure_private_chat_and_super_admin(update)
    if not ok:
        await query.answer("⛔ Доступ запрещён или чат не является приватным.", show_alert=True)
        return

    await query.answer()
    data = query.data or ""
    parts = data.split(":")
    action = parts[0]
    status = parts[1] if len(parts) > 1 else "all"
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    raw_uid = parts[3] if len(parts) > 3 else "0"
    user_id_filter = int(raw_uid) if raw_uid.isdigit() and int(raw_uid) > 0 else None

    await _render_bets_monitor(update, context, status=status, page=page, user_id_filter=user_id_filter, edit=True)


async def cb_admin_bets_toggle_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переключение подписки супер-админа на Live-оповещения о ставках."""
    query = update.callback_query
    if not query:
        return

    ok, admin_id = _ensure_private_chat_and_super_admin(update)
    if not ok:
        await query.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    current = await asyncio.to_thread(database.is_live_bet_alerts_enabled, admin_id)
    new_state = not current
    await asyncio.to_thread(database.set_live_bet_alerts_enabled, admin_id, new_state)

    msg = "🔔 Live-оповещения о ставках включены!" if new_state else "🔕 Live-оповещения о ставках выключены."
    await query.answer(msg, show_alert=True)

    data = query.data or ""
    parts = data.split(":")
    status = parts[1] if len(parts) > 1 else "all"
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    raw_uid = parts[3] if len(parts) > 3 else "0"
    user_id_filter = int(raw_uid) if raw_uid.isdigit() and int(raw_uid) > 0 else None

    await _render_bets_monitor(update, context, status=status, page=page, user_id_filter=user_id_filter, edit=True)


async def cb_admin_bet_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отображение подробной карточки конкретной ставки."""
    query = update.callback_query
    if not query:
        return

    ok, admin_id = _ensure_private_chat_and_super_admin(update)
    if not ok:
        await query.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    await query.answer()
    data = query.data or ""
    try:
        bet_id = int(data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer("Неверный ID ставки.", show_alert=True)
        return

    bet = await asyncio.to_thread(database.get_bet_by_id, bet_id)
    if not bet:
        await query.answer("Ставка не найдена.", show_alert=True)
        return

    status = bet["status"]
    status_title = BET_STATUS_TITLES.get(status, status)
    b_type = "Ординар" if bet.get("bet_type") == "single" else "Экспресс"
    amount = bet.get("amount", 0)
    odd = float(bet.get("total_odd") or 1.0)
    potential_win = bet.get("potential_win", 0)
    actual_payout = bet.get("actual_payout", 0)
    created_at = bet.get("created_at")
    settled_at = bet.get("settled_at")

    u_name = f"@{bet['username']}" if bet.get("username") else f"ID {bet['user_id']}"
    team_name = bet.get("user_team") or "—"
    league_name = bet.get("user_league") or "—"
    wallet_bal = bet.get("user_wallet_balance", 0)

    lines = [
        f"🔍 <b>КАРТОЧКА СТАВКИ #{bet_id}</b>",
        f"──────────────────────────────",
        f"👤 <b>Игрок:</b> {html.escape(u_name)} (ID: <code>{bet['user_id']}</code>)",
        f"🛡 <b>Клуб:</b> {html.escape(team_name)} | Лиforeground/Лига: {html.escape(league_name)}",
        f"🪙 <b>Текущий баланс игрока:</b> <code>{wallet_bal:,} 🪙</code>",
        f"──────────────────────────────",
        f"📋 <b>Тип:</b> {b_type}",
        f"💵 <b>Сумма ставки:</b> <code>{amount:,} 🪙</code>",
        f"📊 <b>Общий коэффициент:</b> <b>{odd:.2f}</b>",
        f"🎯 <b>Потенциальный выигрыш:</b> <code>{potential_win:,} 🪙</code>",
        f"💰 <b>Фактическая выплата:</b> <code>{actual_payout:,} 🪙</code>",
        f"📌 <b>Статус:</b> <b>{status_title}</b>",
        f"🕒 <b>Создана:</b> {created_at}",
    ]
    if settled_at:
        lines.append(f"🏁 <b>Рассчитана:</b> {settled_at}")

    lines.append("\n⚽ <b>События в купоне:</b>")
    items = bet.get("items", [])
    for idx, it in enumerate(items, 1):
        t1 = html.escape(it.get("team1_name") or "Хозяева")
        t2 = html.escape(it.get("team2_name") or "Гости")
        m_tour = it.get("tour", 1)
        div_name = it.get("division_name") or f"Дивизион #{it.get('division_id', 1)}"
        out_code = it.get("outcome_type") or ""
        out_name = OUTCOME_TITLES.get(out_code, out_code)
        m_name = it.get("market_name") or "1X2"
        it_odd = float(it.get("odd") or 1.0)
        it_status = it.get("status", "pending")
        it_emoji = ITEM_STATUS_EMOJI.get(it_status, "•")

        score_info = ""
        s1, s2 = it.get("player1_score"), it.get("player2_score")
        if s1 is not None and s2 is not None:
            score_info = f" [Счёт: {s1}:{s2}]"

        lines.append(
            f"{idx}. {it_emoji} <b>{t1} vs {t2}</b>{score_info}\n"
            f"   🏆 {html.escape(div_name)} | Тур {m_tour}\n"
            f"   Рынок: <i>{html.escape(m_name)}</i> -> Выбор: <b>{out_name}</b> (@<b>{it_odd:.2f}</b>)"
        )

    text = "\n".join(lines)

    kb = []
    # Если ставка в игре, суперадмин может ее аннулировать (Void)
    if status == "pending":
        kb.append([InlineKeyboardButton("⚠️ Аннулировать ставку (Void / Возврат)", callback_data=f"admin_bet_void_ask:{bet_id}")])

    kb.append([
        InlineKeyboardButton("👤 Все ставки игрока", callback_data=f"admin_bets_flt:all:0:{bet['user_id']}"),
        InlineKeyboardButton("« К списку ставок", callback_data="admin_bets_page:all:0:0")
    ])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")


async def cb_admin_bet_void_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Подтверждение аннулирования ставки."""
    query = update.callback_query
    if not query:
        return

    ok, admin_id = _ensure_private_chat_and_super_admin(update)
    if not ok:
        await query.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    await query.answer()
    data = query.data or ""
    bet_id = int(data.split(":")[1])

    bet = await asyncio.to_thread(database.get_bet_by_id, bet_id)
    if not bet:
        await query.answer("Ставка не найдена.", show_alert=True)
        return

    u_name = f"@{bet['username']}" if bet.get("username") else f"ID {bet['user_id']}"
    text = (
        f"⚠️ <b>Подтверждение аннулирования ставки #{bet_id}</b>\n\n"
        f"• Игрок: <b>{html.escape(u_name)}</b>\n"
        f"• Сумма возврата: <code>{bet['amount']:,} 🪙</code>\n"
        f"• Текущий статус: <b>{BET_STATUS_TITLES.get(bet['status'], bet['status'])}</b>\n\n"
        f"<i>При подтверждении ставка получит статус «refunded», а {bet['amount']:,} монет будут мгновенно возвращены на кошелёк пользователя.</i>"
    )

    kb = [
        [
            InlineKeyboardButton("✅ Да, аннулировать", callback_data=f"admin_bet_void_do:{bet_id}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"admin_bet_view:{bet_id}")
        ]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")


async def cb_admin_bet_void_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выполнение аннулирования ставки."""
    query = update.callback_query
    if not query:
        return

    ok, admin_id = _ensure_private_chat_and_super_admin(update)
    if not ok:
        await query.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    data = query.data or ""
    bet_id = int(data.split(":")[1])

    try:
        res = await asyncio.to_thread(database.void_user_bet, bet_id=bet_id, actor_id=admin_id)
        await query.answer("Ставка успешно аннулирована!", show_alert=True)

        text = (
            f"✅ <b>Ставка #{bet_id} успешно аннулирована!</b>\n\n"
            f"💵 <b>Сумма возврата:</b> <code>{res['refunded_amount']:,} 🪙</code>\n"
            f"👤 <b>Пользователь ID:</b> <code>{res['user_id']}</code>\n\n"
            f"<i>Средства зачислены обратно на баланс игрока. Запись внесена в аудит-лог.</i>"
        )
        kb = [[InlineKeyboardButton("« К списку ставок", callback_data="admin_bets_page:all:0:0")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    except ValueError as e:
        await query.answer(f"Ошибка: {e}", show_alert=True)
    except Exception as e:
        logger.exception("Error in cb_admin_bet_void_execute")
        await query.answer(f"Непредвиденная ошибка: {e}", show_alert=True)


async def notify_super_admins_new_bet(bot=None, bet_id: int = 0) -> None:
    """
    Отправка уведомления подписанным супер-администраторам в ЛС о новой ставке.
    Вызывается после размещения ставки.
    """
    try:
        subscribers = await asyncio.to_thread(database.get_live_bet_alert_subscribers)
        if not subscribers:
            return

        if bot is None:
            import config
            from telegram import Bot
            if not getattr(config, "TOKEN", None):
                return
            bot = Bot(config.TOKEN)

        bet = await asyncio.to_thread(database.get_bet_by_id, bet_id)
        if not bet:
            return

        u_name = f"@{bet['username']}" if bet.get("username") else f"ID {bet['user_id']}"
        team_name = bet.get("user_team")
        team_str = f" ({html.escape(team_name)})" if team_name else ""
        b_type = "Ординар" if bet.get("bet_type") == "single" else "Экспресс"
        amount = bet.get("amount", 0)
        odd = float(bet.get("total_odd") or 1.0)
        potential_win = bet.get("potential_win", 0)

        lines = [
            f"🎰 <b>Новая ставка #{bet_id}!</b> ({b_type})",
            f"👤 <b>Игрок:</b> {html.escape(u_name)}{team_str}",
            f"💵 <b>Сумма:</b> <code>{amount:,} 🪙</code> | Кэф: <b>{odd:.2f}</b>",
            f"🎯 <b>Потенц. выигрыш:</b> <code>{potential_win:,} 🪙</code>",
            "",
            "⚽ <b>События:</b>"
        ]
        for it in bet.get("items", [])[:3]:
            t1 = html.escape(it.get("team1_name") or "Хозяева")
            t2 = html.escape(it.get("team2_name") or "Гости")
            out_code = it.get("outcome_type") or ""
            out_name = OUTCOME_TITLES.get(out_code, out_code)
            it_odd = float(it.get("odd") or 1.0)
            lines.append(f"• {t1} vs {t2} (<code>{out_name}</code> @{it_odd:.2f})")

        text = "\n".join(lines)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Открыть карточку", callback_data=f"admin_bet_view:{bet_id}")]])

        for admin_id in subscribers:
            asyncio.create_task(safe_send_notification(bot, admin_id, text, reply_markup=kb))

    except Exception as e:
        logger.debug(f"Failed to notify super admins about new bet #{bet_id}: {e}")
