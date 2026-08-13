import asyncio
import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest, TelegramError, Forbidden
from telegram.ext import ContextTypes, ConversationHandler
import html
import database
from handlers.base import is_admin, admin_only, post_league_table_to_reports
from handlers.cabinet import notify_match_confirmed, safe_send_notification, cb_report_choice_manual, safe_edit_or_reply
import config
from config import CLUBS, MAX_WARNS_LIMIT, GROUP_ID

from schedule_parser import parse_schedule_text, create_matches_from_parsed_schedule
import player_photos
import logging

logger = logging.getLogger(__name__)

WARN_REASONS = [
    "🔴 Долг (1 несыгранный матч / тур)",
    "Несвоевременный отчет",
    "Оскорбления / Неспортивное поведение",
    "Игнорирование соперника",
    "Нарушение регламента составов"
]
_warn_action_locks: set[int] = set()

def generate_round_robin_fixtures(player_ids: list[int]) -> list[tuple[int, int, int]]:
    """
    Generate double round-robin fixtures (each plays each other twice: Home & Away).
    Returns a list of tuples: (round_number, player1_id, player2_id)
    """
    n = len(player_ids)
    if n < 2:
        return []
    
    players = list(player_ids)
    if n % 2 != 0:
        players.append(None)
        n += 1
        
    single_fixtures = []
    temp_players = list(players)
    
    # First round-robin half (n - 1 rounds)
    for round_num in range(1, n):
        for i in range(n // 2):
            p1 = temp_players[i]
            p2 = temp_players[n - 1 - i]
            if p1 is not None and p2 is not None:
                if round_num % 2 == 0:
                    single_fixtures.append((round_num, p2, p1))
                else:
                    single_fixtures.append((round_num, p1, p2))
        # Rotate players (keep the first player fixed)
        temp_players = [temp_players[0]] + [temp_players[-1]] + temp_players[1:-1]
        
    # Second round-robin half (swap roles)
    double_fixtures = list(single_fixtures)
    rounds_in_half = n - 1
    for round_num, p1, p2 in single_fixtures:
        double_fixtures.append((round_num + rounds_in_half, p2, p1))
        
    double_fixtures.sort(key=lambda x: x[0])
    return double_fixtures

@admin_only
async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not is_admin(user.id):
        if update.callback_query:
            await update.callback_query.answer("⛔ Доступ запрещён", show_alert=True)
        elif update.message:
            await update.message.reply_text("❌ У вас нет прав доступа к этой панели.")
        return

    query = update.callback_query
    chat_mode = database.get_config("chat_mode") or "temshik"
    mode_label = "Темшик 🍺" if chat_mode == "temshik" else "Персона 2 🎭"
    keyboard = [
        [InlineKeyboardButton("👥 Управление игроками", callback_data="admin_manage_players")],
        [InlineKeyboardButton("📋 Составы команд", callback_data="admin_manage_squads")],
        [InlineKeyboardButton("⚔️ Управление матчами", callback_data="admin_manage_matches_info")],
        [InlineKeyboardButton("🏆 Управление Кубком КПЛ", callback_data="admin_manage_cup")],
        [InlineKeyboardButton("📢 Рассылка задолженностей", callback_data="admin_broadcast_menu")],
        [InlineKeyboardButton("🔄 Обновить таблицы и стату", callback_data="admin_force_update")],
        [InlineKeyboardButton(f"🎭 Режим общения: {mode_label}", callback_data="admin_toggle_chat_mode")],
        [InlineKeyboardButton("« Назад в меню", callback_data="main_menu")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    text = "👑 <b>Админ-панель</b>\n\nВыберите раздел:"
    
    if update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)
    elif query:
        await query.answer()
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=query.from_user.id, text=text, parse_mode="HTML", reply_markup=markup)

@admin_only
async def admin_toggle_chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()

    current = database.get_config("chat_mode") or "temshik"
    new_mode = "persona2" if current == "temshik" else "temshik"
    database.set_config("chat_mode", new_mode)
    await query.message.reply_text(
        f"✅ Режим общения ИИ изменён: <b>{'Персона 2 🎭' if new_mode == 'persona2' else 'Темшик 🍺'}</b>",
        parse_mode="HTML"
    )

@admin_only
async def admin_force_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Force rechecks the DB and updates the league table in the reports topic."""
    user = update.effective_user
    if not is_admin(user.id):
        if update.callback_query:
            await update.callback_query.answer("⛔ Доступ запрещён", show_alert=True)
        return
        
    if update.callback_query:
        await update.callback_query.answer("🔄 Запущено обновление баз данных и таблиц...")
    else:
        await update.message.reply_text("🔄 Запущено обновление баз данных и таблиц...")

    try:
        # Trigger league table update which recalculates standings
        await post_league_table_to_reports(context)
        
        msg = "✅ Все базы перепроверены. Турнирная таблица и статистика бомбардиров актуализированы!"
        if update.callback_query:
            await update.callback_query.message.reply_text(msg)
        elif update.message:
            await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"Error in force_update: {e}")
        err_msg = "❌ Ошибка при обновлении таблиц. Проверьте логи."
        if update.callback_query:
            await update.callback_query.message.reply_text(err_msg)
        elif update.message:
            await update.message.reply_text(err_msg)

@admin_only
async def admin_list_players(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.edit_message_text("❌ У вас нет прав.")
        return

    players = await asyncio.to_thread(database.list_users)
    keyboard = [[InlineKeyboardButton("« Назад", callback_data="admin_main_menu")]]
    markup = InlineKeyboardMarkup(keyboard)

    if not players:
        await query.edit_message_text("👥 Нет зарегистрированных игроков.", reply_markup=markup)
        return

    lines = ["👥 **Зарегистрированные игроки:**\n"]
    for i, p in enumerate(players, start=1):
        username_str = f"@{p['username']}" if p['username'] else "(без юзернейма)"
        team_str = f" [{p['team_name']}]" if p['team_name'] else ""
        lines.append(f"{i}. {username_str}{team_str} `ID: {p['telegram_id']}`")

    await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=markup)

# --- KPL Cup Admin Management ---

@admin_only
async def admin_manage_cup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()

    stage = "1/8"
    if query.data.startswith("admin_cup_stage_"):
        stage = query.data.replace("admin_cup_stage_", "")

    series_list = await asyncio.to_thread(database.get_cup_series_list, stage)

    text = f"🏆 <b>Админ-панель: Управление Кубком КПЛ ({stage})</b>\n\n"

    if not series_list:
        text += "⚠️ Сетка Кубка еще не инициализирована.\n\nНажмите кнопку ниже, чтобы сформировать сразу все стадии (1/8, 1/4, 1/2 и Финал)."
        keyboard = [
            [InlineKeyboardButton("🚀 Сформировать сетку Кубка (Все стадии)", callback_data="admin_init_cup_execute")],
            [InlineKeyboardButton("« Назад в админку", callback_data="admin_main_menu")]
        ]
    else:
        for s in series_list:
            t1 = html.escape(s['team1_name'])
            t2 = html.escape(s['team2_name'])
            w1 = s['team1_wins']
            w2 = s['team2_wins']
            s_num = s['series_num']

            if s['status'] == 'completed':
                text += f"⚔️ <b>Серия {s_num}:</b> {t1} ({w1}) 🆚 ({w2}) {t2} ➔ 🏆 <b>{html.escape(s['winner_name'] or 'Победитель')}</b>\n"
            else:
                text += f"⚔️ <b>Серия {s_num}:</b> <b>{t1}</b> ({w1}) 🆚 ({w2}) <b>{t2}</b>\n"
        
        text += "\n"

        keyboard = [
            [
                InlineKeyboardButton("1/8", callback_data="admin_cup_stage_1/8"),
                InlineKeyboardButton("1/4", callback_data="admin_cup_stage_1/4"),
                InlineKeyboardButton("1/2", callback_data="admin_cup_stage_1/2"),
                InlineKeyboardButton("Финал", callback_data="admin_cup_stage_final"),
            ]
        ]

        # Add match management buttons for all matches in current stage
        for s in series_list:
            for m in s.get("matches", []):
                g_num = m['game_num_in_series']
                t1 = m['player1_team'] or s['team1_name']
                t2 = m['player2_team'] or s['team2_name']
                st_icon = "✅" if m['status'] == 'confirmed' else "⏳"
                score_part = f"({m['player1_score']}:{m['player2_score']})" if m['status'] == 'confirmed' else "vs"
                btn_label = f"⚙️ Игра {g_num}: {t1} {score_part} {t2} {st_icon}"
                keyboard.append([InlineKeyboardButton(btn_label, callback_data=f"admin_view_match_{m['id']}")])

        keyboard.append([InlineKeyboardButton("📢 Напомнить участникам Кубка в тему отчётов", callback_data=f"admin_remind_cup_{stage}")])
        keyboard.append([InlineKeyboardButton("🔄 Обновить", callback_data=f"admin_cup_stage_{stage}")])
        keyboard.append([InlineKeyboardButton("« Назад в админку", callback_data="admin_main_menu")])

    markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)

@admin_only
async def admin_remind_cup_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()

    stage = "1/8"
    if query.data.startswith("admin_remind_cup_"):
        stage = query.data.replace("admin_remind_cup_", "")

    series_list = await asyncio.to_thread(database.get_cup_series_list, stage)

    unplayed_matches = []
    for s in series_list:
        if s["status"] != "completed":
            for m in s.get("matches", []):
                if m["status"] == "pending":
                    unplayed_matches.append((s, m))

    if not unplayed_matches:
        await query.answer(f"✅ В стадии {stage} нет несыгранных матчей!", show_alert=True)
        return

    # 1. PM reminders
    pm_sent = 0
    for s, m in unplayed_matches:
        t1, t2 = s["team1_name"], s["team2_name"]
        w1, w2 = s["team1_wins"], s["team2_wins"]
        g_num = m["game_num_in_series"]

        p1_id = None
        p2_id = None
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("SELECT telegram_id FROM users WHERE LOWER(team_name) = LOWER(?)", (t1.strip(),))
            r1 = c.fetchone()
            if r1: p1_id = r1[0]
            c.execute("SELECT telegram_id FROM users WHERE LOWER(team_name) = LOWER(?)", (t2.strip(),))
            r2 = c.fetchone()
            if r2: p2_id = r2[0]

        pm_text = (
            f"🏆 <b>НАПОМИНАНИЕ О КУБКОВОМ МАТЧЕ!</b>\n\n"
            f"⚔️ <b>Стадия:</b> {stage} Финала (Игра {g_num})\n"
            f"🏠 <b>{html.escape(t1)}</b> 🆚 <b>{html.escape(t2)}</b> ✈️\n"
            f"📊 <b>Счёт серии (Best-of-3):</b> {w1} : {w2}\n\n"
            f"Пожалуйста, сыграйте свой кубковый матч! Каждая игра до победы (с доп. временем и пенальти)."
        )
        kb = [[InlineKeyboardButton("📋 Внести результат", callback_data=f"cabinet_report_score_{m['id']}")]]

        if p1_id and p1_id > 0:
            if await safe_send_notification(context.bot, p1_id, pm_text, InlineKeyboardMarkup(kb)):
                pm_sent += 1
        if p2_id and p2_id > 0:
            if await safe_send_notification(context.bot, p2_id, pm_text, InlineKeyboardMarkup(kb)):
                pm_sent += 1

    # 2. Group post to Reports Topic
    main_group_id = await asyncio.to_thread(database.get_group_id)
    reports_topic_id = await asyncio.to_thread(database.get_config, "reports_topic_id")

    if main_group_id:
        lines = [
            f"🏆 <b>НАПОМИНАНИЕ О КУБКЕ КПЛ | {stage} Финала</b>\n",
            f"Несыгранные кубковые матчи ({len(unplayed_matches)}):"
        ]
        for s, m in unplayed_matches:
            t1_esc, t2_esc = html.escape(s["team1_name"]), html.escape(s["team2_name"])
            w1, w2 = s["team1_wins"], s["team2_wins"]
            g_num = m["game_num_in_series"]
            lines.append(f"• ⚔️ <b>Игра {g_num}:</b> <b>{t1_esc}</b> 🆚 <b>{t2_esc}</b> (Счёт серии: {w1} : {w2})")

        lines.append("\n⚠️ Напоминаем: в каждом кубковом матче обязательно доп. время и пенальти (ничьих нет).")
        lines.append("Пожалуйста, внесите результаты в бота!")

        try:
            kwargs = {"chat_id": main_group_id, "text": "\n".join(lines), "parse_mode": "HTML"}
            if reports_topic_id:
                kwargs["message_thread_id"] = int(reports_topic_id)
            await context.bot.send_message(**kwargs)
        except Exception as e:
            logger.exception("Failed to post cup reminder summary to group")

    await query.answer(f"🚀 Напоминания отправлены! (ЛС: {pm_sent}, Тема отчетов: ✅)", show_alert=True)

async def notify_cup_stage_opened(bot, stage: str) -> None:
    """Post an announcement for the newly opened Cup stage to Reports Topic."""
    main_group_id = await asyncio.to_thread(database.get_group_id)
    reports_topic_id = await asyncio.to_thread(database.get_config, "reports_topic_id")
    if not main_group_id:
        return

    series_list = await asyncio.to_thread(database.get_cup_series_list, stage)
    if not series_list:
        return

    # Проверяем, известны ли все участники стадии
    for s in series_list:
        if s["team1_name"].startswith("Победитель") or s["team2_name"].startswith("Победитель"):
            return  # Ждем, пока все пары определятся


    stage_title_map = {
        '1/8': '1/8 ФИНАЛА',
        '1/4': '1/4 ФИНАЛА',
        '1/2': '1/2 ФИНАЛА (ПОЛУФИНАЛ)',
        'final': '🏆 ФИНАЛ КУБКА КПЛ 2026'
    }
    title = stage_title_map.get(stage, f"СТАДИЯ {stage}")

    lines = [
        f"🚀 <b>ОТКРЫТИЕ СТАДИИ | КУБОК КПЛ — {title}</b>\n",
        f"<i>Формат: Серии до 2-х побед (Best-of-3)</i>",
        f"<i>Каждая игра проводится с возможным доп. временем и пенальти (ничьих нет).</i>\n",
        f"⚔️ <b>Пары участников:</b>"
    ]

    for s in series_list:
        t1 = html.escape(s["team1_name"])
        t2 = html.escape(s["team2_name"])
        s_num = s["series_num"]
        lines.append(f"• <b>Серия {s_num}:</b> <b>{t1}</b> 🆚 <b>{t2}</b>")

    lines.append("\n📋 Матчи доступны для игры в вашем кабинете (раздел «Мои открытые матчи»). Удачи участникам!")

    try:
        from table_generator import generate_cup_bracket_image
        from telegram import InputFile
        img_buf = await asyncio.to_thread(generate_cup_bracket_image, stage)

        kwargs = {
            "chat_id": main_group_id,
            "photo": InputFile(img_buf, filename=f"cup_bracket_{stage}.png"),
            "caption": "\n".join(lines),
            "parse_mode": "HTML"
        }
        if reports_topic_id:
            kwargs["message_thread_id"] = int(reports_topic_id)
        await bot.send_photo(**kwargs)
    except Exception as e:
        logger.exception("Failed to post cup stage opening photo, fallback to text")
        kwargs = {"chat_id": main_group_id, "text": "\n".join(lines), "parse_mode": "HTML"}
        if reports_topic_id:
            kwargs["message_thread_id"] = int(reports_topic_id)
        await bot.send_message(**kwargs)

@admin_only
async def admin_init_cup_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()

    created_count = await asyncio.to_thread(database.init_kpl_cup_all_stages)
    if created_count > 0:
        await query.answer(f"✅ Сформировано {created_count} серий 1/8 финала!", show_alert=True)
        await notify_cup_stage_opened(context.bot, '1/8')
    else:
        await query.answer("⚠️ Сетка 1/8 финала уже сформирована!", show_alert=True)

    await admin_manage_cup(update, context)

@admin_only
async def admin_sync_cup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually synchronize winners of completed series to next stages."""
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if not msg:
        return
        
    try:
        sync_count = await asyncio.to_thread(database.sync_cup_bracket)
        await msg.reply_text(f"✅ Синхронизация Кубка завершена!\nПереведено победителей на следующие стадии: {sync_count}")
    except Exception as e:
        await msg.reply_text(f"❌ Ошибка синхронизации: {e}")

@admin_only
async def admin_test_ai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run interactive diagnostic check for WARP proxy and Gemini AI models."""
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if not msg:
        return

    status_msg = await msg.reply_text("🔄 **Запуск диагностики связи с WARP и Gemini AI...**", parse_mode="Markdown")

    import socket
    import urllib.request
    from ai_recognizer import GEMINI_MODELS, _check_proxy_alive
    import config

    warp_alive = _check_proxy_alive("http://127.0.0.1:4001")
    warp_status_str = "✅ **Доступен (127.0.0.1:4001)**" if warp_alive else "❌ **Не прослушивается (прямой режим)**"

    lines = [
        "🤖 **РЕЗУЛЬТАТЫ ДИАГНОСТИКИ AI & WARP**\n",
        f"📡 **WARP Proxy Status:** {warp_status_str}\n",
        "🧪 **Статус моделей Gemini:**"
    ]

    target_api_key = (getattr(config, "GEMINI_API_KEY", "") or "").strip()
    if not target_api_key:
        lines.append("❌ `GEMINI_API_KEY не установлен в config.py!`")
        await status_msg.edit_text("\n".join(lines), parse_mode="Markdown")
        return

    proxy_url = "http://127.0.0.1:4001" if warp_alive else None

    for m_name in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_name}:generateContent?key={target_api_key}"
        payload = {"contents": [{"parts": [{"text": "Reply OK"}]}]}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        if proxy_url:
            handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            opener = urllib.request.build_opener(handler)
        else:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        try:
            with opener.open(req, timeout=8) as res:
                res_data = json.loads(res.read().decode("utf-8"))
                if res_data.get("candidates"):
                    lines.append(f"• `{m_name}`: ✅ 200 OK")
                else:
                    lines.append(f"• `{m_name}`: ⚠️ Нет ответа")
        except urllib.error.HTTPError as e:
            err_text = e.read().decode("utf-8", errors="ignore")[:60].replace("\n", " ")
            lines.append(f"• `{m_name}`: ❌ HTTP {e.code} ({err_text})")
        except Exception as e:
            lines.append(f"• `{m_name}`: ❌ {e}")

    await status_msg.edit_text("\n".join(lines), parse_mode="Markdown")

# --- Broadcast Handlers (Debt Notifications) ---

async def _build_debts_summary() -> tuple[str | None, int]:
    """
    Build a full HTML summary of outstanding debts (League + Cup) grouped by participant (club).
    Returns (text, total_debts_count). text is None when there are no debts.
    """
    league_unplayed, cup_unplayed, users = await asyncio.gather(
        asyncio.to_thread(database.get_all_unplayed_league_matches),
        asyncio.to_thread(database.get_all_unplayed_cup_matches),
        asyncio.to_thread(database.list_users),
    )

    if not league_unplayed and not cup_unplayed:
        return None, 0

    # Map club name (lowercased) -> user info to group debts by participant
    user_by_team: dict[str, dict] = {}
    for u in users:
        team = (u["team_name"] or "").strip()
        if team:
            user_by_team.setdefault(team.lower(), {"telegram_id": u["telegram_id"], "username": u["username"], "team_name": team})

    participants: dict[str, dict] = {}

    def ensure_participant(team: str | None) -> dict | None:
        if not team:
            return None
        info = user_by_team.get(team.strip().lower())
        p = participants.setdefault(
            team.strip().lower(),
            {
                "team_name": team,
                "username": info["username"] if info else None,
                "league": [],
                "cup": [],
            },
        )
        return p

    for m in league_unplayed:
        p1 = ensure_participant(m.get("player1_team") or m.get("p1_team"))
        p2 = ensure_participant(m.get("player2_team") or m.get("p2_team"))
        t1 = html.escape(m['player1_team'] or m['p1_team'] or 'неизвестно')
        t2 = html.escape(m['player2_team'] or m['p2_team'] or 'неизвестно')
        u1 = f" (@{html.escape(m['p1_username'])})" if m['p1_username'] else ""
        u2 = f" (@{html.escape(m['p2_username'])})" if m['p2_username'] else ""
        line = f"Тур {m['round_number']}: 🏠 <b>{t1}</b>{u1} -:- <b>{t2}</b>{u2} ✈️"
        if p1:
            p1["league"].append(line)
        if p2:
            p2["league"].append(line)

    for m in cup_unplayed:
        stage = m.get('cup_stage', '1/8')
        g_num = m.get('game_num_in_series', 1)
        w1 = m.get('team1_wins', 0)
        w2 = m.get('team2_wins', 0)
        t1 = html.escape(m['player1_team'] or m['team1_name'] or 'неизвестно')
        t2 = html.escape(m['player2_team'] or m['team2_name'] or 'неизвестно')
        u1 = f" (@{html.escape(m['p1_username'])})" if m['p1_username'] else ""
        u2 = f" (@{html.escape(m['p2_username'])})" if m['p2_username'] else ""
        match_line = f"{stage} Финала (игра {g_num}): 🏠 <b>{t1}</b>{u1} 🆚 <b>{t2}</b>{u2} ✈️ <i>(Счёт серии: {w1}:{w2})</i>"

        p1 = ensure_participant(m.get("player1_team") or m.get("p1_team"))
        p2 = ensure_participant(m.get("player2_team") or m.get("p2_team"))
        if p1:
            p1["cup"].append(match_line)
        if p2:
            p2["cup"].append(match_line)

    total_debts = len(league_unplayed) + len(cup_unplayed)

    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [
        "🗂 <b>ДОЛГИ УЧАСТНИКОВ | ЛИГА И КУБОК КПЛ</b>\n",
        f"<i>Обновлено: {now_str}</i>\n",
    ]

    bar = "━━━━━━━━━━━━━━━━━━━━━━"

    for idx, p in enumerate(sorted(participants.values(), key=lambda x: len(x["league"]) + len(x["cup"]), reverse=True), 1):
        uname_str = f"@{p['username']}" if p['username'] else p['team_name']
        total_n = len(p["league"]) + len(p["cup"])
        card: list[str] = [bar]
        card.append(f"{idx}. 👤 <b>{html.escape(uname_str)}</b> [{html.escape(p['team_name'])}] — {total_n} матч.")
        if p["league"]:
            card.append("⚙️ <b>ЛИГА:</b>")
            for line in p["league"]:
                card.append(f"   • {line}")
        if p["cup"]:
            card.append("🏆 <b>КУБОК:</b>")
            for line in p["cup"]:
                card.append(f"   • {line}")
        card.append(bar)
        lines.extend(card)
        lines.append("")

    lines.append("⏰ Пожалуйста, согласуйте время и сыграйте матчи! Несыгранные игры ведут к предупреждениям.")

    return "\n".join(lines), total_debts

@admin_only
async def admin_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()

    text = (
        "📢 <b>Управление Рассылкой Задолженностей</b>\n\n"
        "Данный инструмент формирует и рассылает <b>полный список долгов (Лига + Кубок КПЛ)</b>:\n\n"
        "1. 📩 <b>Персональные ЛС всем должникам:</b> Список несыгранных матчей каждого участника с кнопками прямого перехода к вводу результата.\n"
        "2. 💬 <b>Сводка долгов в Тему ПРЕДЫ</b> (кнопкой ниже).\n\n"
        "Нажмите кнопку ниже для старта рассылки:"
    )

    keyboard = [
        [InlineKeyboardButton("🚀 Запустить рассылку всех долгов (Лига + Кубок)", callback_data="admin_broadcast_all_debts_execute")],
        [InlineKeyboardButton("📋 Отправить сводку долгов в тему «ПРЕДЫ»", callback_data="admin_send_debts_to_warns")],
        [InlineKeyboardButton("« Назад в админку", callback_data="admin_main_menu")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)

@admin_only
async def admin_broadcast_all_debts_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()

    users = await asyncio.to_thread(database.list_users)
    league_unplayed, cup_unplayed = await asyncio.gather(
        asyncio.to_thread(database.get_all_unplayed_league_matches),
        asyncio.to_thread(database.get_all_unplayed_cup_matches)
    )

    if not league_unplayed and not cup_unplayed:
        await query.answer("✅ Отличная новость! В Лиге и Кубке нет несыгранных матчей-долгов!", show_alert=True)
        return

    # 1. Individual PM Notifications to every debtor
    pm_sent = 0
    notified_users_count = 0

    for u in users:
        uid = u["telegram_id"]
        if not uid or uid <= 0:
            continue

        u_matches = await asyncio.to_thread(database.get_pending_matches, uid, True)
        if not u_matches:
            continue

        notified_users_count += 1
        total = len(u_matches)
        league_matches = [m for m in u_matches if m.get("tournament_type") != "cup"]
        cup_matches = [m for m in u_matches if m.get("tournament_type") == "cup"]

        bar = "━━━━━━━━━━━━━━━━━━━━━━"
        lines = [
            "🚨 <b>НАПОМИНАНИЕ О ЗАДОЛЖЕННОСТЯХ</b>\n",
            f"У вас <b>{total}</b> несыгранн{'ый' if total == 1 else 'ых'} матч{'а' if total in (2, 3, 4) else 'ей'} 🕒\n",
        ]

        if league_matches:
            lines.append(bar)
            lines.append("⚽ <b>ЧЕМПИОНАТ КПЛ</b>")
            for i, m in enumerate(league_matches, 1):
                opp = m['opponent_team'] or m['opponent_username'] or "Соперник"
                lines.append(f"   {i}. Тур {m['round_number']}: 🆚 <b>{html.escape(opp)}</b>")
            lines.append(bar)
            lines.append("")

        if cup_matches:
            lines.append(bar)
            lines.append("🏆 <b>КУБОК КПЛ · Best-of-3</b>")
            for i, m in enumerate(cup_matches, 1):
                opp = m['opponent_team'] or m['opponent_username'] or "Соперник"
                stage = m.get('cup_stage', '1/8')
                g_num = m.get('game_num_in_series', 1)
                lines.append(f"   {i}. {stage} Финала (Игра {g_num}): 🆚 <b>{html.escape(opp)}</b>")
            lines.append(bar)
            lines.append("")

        lines.append("📅 Согласуйте время с соперниками и внесите результаты через кабинет — иначе последуют ⚠️ предупреждения!")

        keyboard = [[InlineKeyboardButton("📋 Мои матчи в кабинете", callback_data="cabinet_my_matches")]]
        markup = InlineKeyboardMarkup(keyboard)

        if await safe_send_notification(context.bot, uid, "\n".join(lines), markup):
            pm_sent += 1

    await query.answer(f"🚀 Рассылка успешно выполнена! (ЛС: {pm_sent} из {notified_users_count})", show_alert=True)
    await admin_broadcast_menu(update, context)


@admin_only
async def admin_send_debts_to_warns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send/update the debts summary in the ПРЕДЫ thread."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()

    await _post_or_update_debts_in_warns(context)

    await admin_broadcast_menu(update, context)


MAX_DEBTS_MSG_LEN = 4000


def _chunk_debts_text(text: str) -> list[str]:
    """
    Split a (possibly too long) HTML debts summary into Telegram-safe chunks.
    Cuts only on participant-block boundaries (blank-line separated blocks), so every
    message shows complete, correctly ordered participant blocks. The summary header
    is repeated in each chunk and the closing reminder goes into the last one.
    """
    blocks = text.split("\n\n")
    if not blocks:
        return [""]

    header = blocks[0]
    footer = blocks[-1]
    body = blocks[1:-1]

    chunks: list[str] = []
    current = header + "\n\n"
    for block in body:
        piece = block + "\n\n"
        if len(piece) > MAX_DEBTS_MSG_LEN:
            for line in block.split("\n"):
                lp = line + "\n"
                if len(current) + len(lp) > MAX_DEBTS_MSG_LEN and len(current) > len(header):
                    chunks.append(current.rstrip())
                    current = header + "\n\n" + lp
                else:
                    current += lp
            current += "\n"
            continue
        if len(current) + len(piece) > MAX_DEBTS_MSG_LEN and len(current) > len(header):
            chunks.append(current.rstrip())
            current = header + "\n\n" + piece
        else:
            current += piece
    current += footer
    chunks.append(current.rstrip())
    return chunks or [""]


async def _delete_any_message(context, group_id: int, ids: list[int]) -> None:
    """Best-effort deletion of the given message ids in the target chat."""
    for mid in ids:
        try:
            await context.bot.delete_message(chat_id=group_id, message_id=mid)
        except (BadRequest, TelegramError):
            pass


async def _post_or_update_debts_in_warns(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Send debts summary to ПРЕДЫ thread, or edit the previously sent messages if they exist.
    Splits the summary into multiple messages (<= MAX_DEBTS_MSG_LEN chars each) when needed.
    Keeps the stored message ids always in sync with what is actually posted: whenever an
    in-place update fails (deleted / too old message), all old messages are deleted and a
    fresh batch is re-posted so no message is ever left un-updated or forgotten.
    Returns True if posted/updated, False if no debts or thread not configured.
    """
    text, total_debts = await _build_debts_summary()
    group_id = GROUP_ID or await asyncio.to_thread(database.get_group_id)
    warns_topic_id = await asyncio.to_thread(database.get_config, "warns_topic_id")
    if not group_id or not warns_topic_id:
        return False

    existing_raw = await asyncio.to_thread(database.get_config, "warns_debts_msg_id")
    existing_ids = [int(x) for x in str(existing_raw or "").split(",") if str(x).strip().isdigit()]

    # No outstanding debts — delete the old summary messages so the thread stays clean
    if text is None:
        await _delete_any_message(context, group_id, existing_ids)
        if existing_ids:
            await asyncio.to_thread(database.set_config, "warns_debts_msg_id", "")
        return True

    chunks = _chunk_debts_text(text)

    # Fast path: number of messages matches stored ids and every edit succeeds.
    if len(chunks) == len(existing_ids):
        try:
            new_ids: list[int] = []
            for i, chunk in enumerate(chunks):
                try:
                    await context.bot.edit_message_text(
                        chat_id=group_id, message_id=existing_ids[i], text=chunk, parse_mode="HTML"
                    )
                    new_ids.append(existing_ids[i])
                    continue
                except BadRequest as e:
                    if "message is not modified" in str(e).lower():
                        new_ids.append(existing_ids[i])
                        continue
                raise TelegramError("debts message cannot be edited in place")
            await asyncio.to_thread(database.set_config, "warns_debts_msg_id", ",".join(map(str, new_ids)))
            return True
        except (BadRequest, TelegramError) as e:
            logger.warning(f"Debts summary needs rebuild ({e}); will re-post all messages")

    # Rebuild path: delete every previously stored message and post a fresh batch.
    await _delete_any_message(context, group_id, existing_ids)
    new_ids: list[int] = []
    try:
        for chunk in chunks:
            msg = await context.bot.send_message(
                chat_id=group_id, text=chunk, parse_mode="HTML", message_thread_id=int(warns_topic_id)
            )
            new_ids.append(msg.message_id)
    except (BadRequest, TelegramError) as e:
        # Save whatever was posted to avoid leaking orphan messages.
        if new_ids:
            await asyncio.to_thread(database.set_config, "warns_debts_msg_id", ",".join(map(str, new_ids)))
        logger.warning(f"Failed to post debts to ПРЕДЫ thread: {e}")
        return False

    await asyncio.to_thread(database.set_config, "warns_debts_msg_id", ",".join(map(str, new_ids)))
    return True

# --- Match Generation Handlers ---

@admin_only
async def admin_generate_matches_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()
    keyboard = [[InlineKeyboardButton("« Назад в админку", callback_data="admin_main_menu")]]
    await query.edit_message_text(
        "🚧 **В разработке**\n\nФункция генерации матчей находится в разработке.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

@admin_only
async def admin_generate_matches_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()

    # Get players with team
    players = [p['telegram_id'] for p in (await asyncio.to_thread(database.list_users)) if p['team_name']]
    
    if len(players) < 2:
        keyboard = [[InlineKeyboardButton("« Назад в админку", callback_data="admin_main_menu")]]
        await query.edit_message_text(
            "❌ **Ошибка генерации:**\n\nНеобходимо как минимум 2 зарегистрированных игрока с заполненными профилями.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    # Generate round robin
    fixtures = generate_round_robin_fixtures(players)
    
    # Clear and insert
    await asyncio.to_thread(database.clear_all_matches)
    await asyncio.to_thread(database.batch_insert_matches, fixtures)

    total_rounds = max(f[0] for f in fixtures) if fixtures else 0
    
    keyboard = [[InlineKeyboardButton("« Назад в админку", callback_data="admin_main_menu")]]
    await query.edit_message_text(
        f"📅 **Расписание успешно сгенерировано!**\n\n"
        f"• Зарегистрировано участников: **{len(players)}**\n"
        f"• Всего туров: **{total_rounds}**\n"
        f"• Всего матчей: **{len(fixtures)}**\n\n"
        f"Все результаты и старые матчи сброшены в базе данных.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

    # Notify group
    group_id = await asyncio.to_thread(database.get_group_id)
    if group_id:
        group_text = (
            f"📅 **Старт новой лиги!**\n\n"
            f"Администратор сгенерировал расписание матчей.\n"
            f"• Участников: {len(players)}\n"
            f"• Всего туров: {total_rounds}\n\n"
            f"Свои матчи вы можете посмотреть в личном кабинете бота в разделе '📋 Мои матчи'."
        )
        try:
            await context.bot.send_message(chat_id=group_id, text=group_text, parse_mode="Markdown")
        except Exception as e:
            logger.exception("Не удалось отправить уведомление о генерации в группу")

# Conversation States for Admin Player management
ADMIN_EXPECT_PLAYER_USERNAME = 201
ADMIN_EXPECT_PLAYER_CLUB = 202
ADMIN_EXPECT_IMPORT_TEXT = 203
ADMIN_EXPECT_NEW_CLUB = 204
ADMIN_EXPECT_NEW_USERNAME = 206
ADMIN_EXPECT_NEW_NICKNAME = 207
ADMIN_EXPECT_RESET_CONFIRM = 208

# Conversation States for Admin Match management
ADMIN_EXPECT_MATCH_SCORE = 205
ADMIN_WAITING_FOR_DEADLINE = 209
ADMIN_WAITING_FOR_BATCH_ROUNDS = 210
ADMIN_WAITING_FOR_BATCH_DEADLINE = 211
ADMIN_EXPECT_MATCH_SCHEDULE_INPUT = 212

@admin_only
async def admin_manage_players_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show participant management hub menu."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()
    
    text = (
        "👥 **Управление участниками лиги**\n\n"
        "Выберите желаемое действие для управления списком игроков:"
    )
    keyboard = [
        [InlineKeyboardButton("📋 Список участников", callback_data="admin_list_players_page_0")],
        [InlineKeyboardButton("➕ Добавить игрока", callback_data="admin_add_player_start")],
        [InlineKeyboardButton("📊 Импорт списка участников", callback_data="admin_import_players_start")],
        [InlineKeyboardButton("⚠️ Сбросить лигу (Очистить всех)", callback_data="admin_clear_league_start")],
        [InlineKeyboardButton("« Назад в админку", callback_data="admin_main_menu")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

@admin_only
async def admin_list_players_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show a paginated list of all players (8 per page)."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()
    
    page = 0
    if query.data.startswith("admin_list_players_page_"):
        page = int(query.data.replace("admin_list_players_page_", ""))
        
    players = await asyncio.to_thread(database.list_users)
    if not players:
        keyboard = [[InlineKeyboardButton("« Назад", callback_data="admin_manage_players_info")]]
        await query.edit_message_text("👥 Нет зарегистрированных игроков.", reply_markup=InlineKeyboardMarkup(keyboard))
        return
        
    per_page = 8
    total_pages = (len(players) + per_page - 1) // per_page
    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1
        
    start_idx = page * per_page
    end_idx = start_idx + per_page
    page_players = players[start_idx:end_idx]
    
    keyboard = []
    for p in page_players:
        username_val = p['username'] or str(p['telegram_id'])
        team_val = f" ({p['team_name']})" if p['team_name'] else ""
        btn_text = f"👤 @{username_val}{team_val}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"admin_view_player_{p['telegram_id']}")])
        
    # Navigation row
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"admin_list_players_page_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"{page + 1} / {total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("➡️ Вперед", callback_data=f"admin_list_players_page_{page + 1}"))
    keyboard.append(nav_row)
    
    keyboard.append([InlineKeyboardButton("« Управление участниками", callback_data="admin_manage_players_info")])
    
    text = f"📋 **Список участников лиги** (Всего: {len(players)}):\n\nВыберите игрока для редактирования или удаления:"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

@admin_only
async def admin_view_player(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View details of a single player with actions."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()
    
    player_id = int(query.data.replace("admin_view_player_", ""))
    player = await asyncio.to_thread(database.get_user, player_id)
    
    if not player:
        keyboard = [[InlineKeyboardButton("« Назад к списку", callback_data="admin_list_players_page_0")]]
        await query.edit_message_text("❌ Игрок не найден.", reply_markup=InlineKeyboardMarkup(keyboard))
        return
        
    text = (
        f"👤 **Карточка участника лиги**\n\n"
        f"• **Telegram:** @{player['username'] or 'нет'}\n"
        f"• **Клуб:** {player['team_name'] or 'нет'}\n"
        f"• **Роль:** {player['role'].capitalize()}\n"
        f"• **ID в боте:** `{player['telegram_id']}`"
    )
    
    role_btn = (
        InlineKeyboardButton("🔑 Снять админку", callback_data=f"admin_toggle_role_{player_id}_player")
        if player['role'] == 'admin'
        else InlineKeyboardButton("🔑 Сделать админом", callback_data=f"admin_toggle_role_{player_id}_admin")
    )
    
    keyboard = [
        [
            InlineKeyboardButton("✏️ Клуб", callback_data=f"admin_edit_club_start_{player_id}"),
            InlineKeyboardButton("✏️ Юзернейм", callback_data=f"admin_edit_username_start_{player_id}")
        ],
        [
            role_btn
        ],
        [InlineKeyboardButton("❌ Удалить из лиги", callback_data=f"admin_delete_options_{player_id}")],
        [InlineKeyboardButton("« Назад к списку", callback_data="admin_list_players_page_0")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

@admin_only
async def admin_confirm_delete_player(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask admin for confirmation to delete the player."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()
    
    player_id = int(query.data.replace("admin_confirm_delete_player_", ""))
    player = await asyncio.to_thread(database.get_user, player_id)
    
    if not player:
        keyboard = [[InlineKeyboardButton("« Назад к списку", callback_data="admin_list_players_page_0")]]
        await query.edit_message_text("❌ Игрок не найден.", reply_markup=InlineKeyboardMarkup(keyboard))
        return
        
    text = (
        f"⚠️ **Подтвердите удаление**\n\n"
        f"Вы действительно хотите исключить игрока @{player['username']} "
        f"из лиги?\n\n"
        f"**Внимание:** все его несыгранные матчи будут автоматически закрыты техническим поражением (0:3)."
    )
    keyboard = [
        [InlineKeyboardButton("🗑️ Да, удалить игрока", callback_data=f"admin_delete_player_execute_{player_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"admin_view_player_{player_id}")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

@admin_only
async def admin_delete_player_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute player deletion and tech loss confirmation."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()
    
    player_id = int(query.data.replace("admin_delete_player_execute_", ""))
    player = await asyncio.to_thread(database.get_user, player_id)
    
    if not player:
        keyboard = [[InlineKeyboardButton("« Назад к списку", callback_data="admin_list_players_page_0")]]
        await query.edit_message_text("❌ Игрок не найден.", reply_markup=InlineKeyboardMarkup(keyboard))
        return
        
    success, msg = await asyncio.to_thread(database.remove_player, str(player_id))
    keyboard = [[InlineKeyboardButton("« Назад к списку", callback_data="admin_list_players_page_0")]]
    
    if success:
        await query.edit_message_text(f"✅ {msg}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
        group_id = await asyncio.to_thread(database.get_group_id)
        if group_id:
            try:
                await context.bot.send_message(chat_id=group_id, text=f"📢 **Изменение состава лиги!**\n\n{msg}", parse_mode="Markdown")
            except Exception as e:
                logger.exception("Не удалось отправить уведомление в группу")
    else:
        await query.edit_message_text(f"❌ {msg}", reply_markup=InlineKeyboardMarkup(keyboard))

@admin_only
async def admin_manage_matches_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display list of rounds for match management."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("📝 Создание матчей", callback_data="admin_create_matches_start")],
        [InlineKeyboardButton("📅 Открыть туры (массово)", callback_data="admin_open_batch_prompt")],
        [InlineKeyboardButton("⏰ Просроченные", callback_data="admin_list_overdue")]
    ]
    
    rounds = await asyncio.to_thread(database.get_all_rounds)
    row = []
    for r in rounds:
        info = await asyncio.to_thread(database.get_round_info, r)
        status_icon = "🟢" if info and info.get("is_open") else "🔴"
        row.append(InlineKeyboardButton(f"{status_icon} Тур {r}", callback_data=f"admin_manage_round_{r}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("« Назад в админку", callback_data="admin_main_menu")])
    
    await query.edit_message_text(
        "⚔️ **Управление матчами и турами**\n\nВыберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

@admin_only
async def admin_manage_round(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display round details and open/close options."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id): return
    await query.answer()
    
    round_number = int(query.data.replace("admin_manage_round_", ""))
    info = await asyncio.to_thread(database.get_round_info, round_number)
    
    if not info:
        keyboard = [[InlineKeyboardButton("« Назад", callback_data="admin_manage_matches_info")]]
        await query.edit_message_text("❌ Тур не найден в базе данных.", reply_markup=InlineKeyboardMarkup(keyboard))
        return
        
    is_open = info["is_open"]
    deadline = info["deadline"]
    
    text = f"📅 **Управление: {round_number}-й Тур**\n\n"
    text += f"Статус: {'🟢 Открыт' if is_open else '🔴 Закрыт'}\n"
    if is_open and deadline:
        text += f"Дедлайн: {deadline}\n"
        
    keyboard = []
    if is_open:
        keyboard.append([InlineKeyboardButton("🔴 Закрыть тур", callback_data=f"admin_close_round_{round_number}")])
        keyboard.append([InlineKeyboardButton("⏰ Напомнить должникам", callback_data=f"admin_remind_round_{round_number}")])
    else:
        keyboard.append([InlineKeyboardButton("🟢 Открыть тур (установить дедлайн)", callback_data=f"admin_open_round_{round_number}")])
        
    keyboard.append([InlineKeyboardButton("⚔️ Смотреть матчи тура", callback_data=f"admin_round_matches_{round_number}")])
    keyboard.append([InlineKeyboardButton("« Назад", callback_data="admin_manage_matches_info")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

@admin_only
async def admin_extend_match_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allow players to submit scores for an overdue match."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id): return
    await query.answer()
    
    match_id = int(query.data.replace("admin_extend_match_", ""))
    await asyncio.to_thread(database.extend_match_deadline, match_id)
    
    keyboard = [[InlineKeyboardButton("« Вернуться к матчу", callback_data=f"admin_view_match_{match_id}")]]
    await query.edit_message_text(
        "✅ Дедлайн для этого матча индивидуально продлен. Теперь игроки смогут ввести счет.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

@admin_only
async def admin_list_overdue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display all overdue matches."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id): return
    await query.answer()
    
    matches = await asyncio.to_thread(database.get_open_pending_matches)
    now = datetime.datetime.now()
    overdue_matches = []
    
    for m in matches:
        if m.get("deadline"):
            try:
                dt = datetime.datetime.strptime(m["deadline"], "%d.%m.%Y %H:%M")
                if now > dt:
                    overdue_matches.append(m)
            except ValueError:
                pass
                
    keyboard = []
    for m in overdue_matches:
        opp1 = m["player1_nickname"]
        opp2 = m["player2_nickname"]
        btn_text = f"Тур {m['round_number']}: {opp1} vs {opp2}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"admin_view_match_{m['id']}")])
        
    keyboard.append([InlineKeyboardButton("« Назад", callback_data="admin_manage_matches_info")])
    
    if overdue_matches:
        text = "⏰ **Просроченные матчи:**\n\nВыберите матч для выставления технического результата или индивидуального продления дедлайна."
    else:
        text = "⏰ Просроченных матчей нет."
        
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

@admin_only
async def admin_open_round_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not is_admin(query.from_user.id): return ConversationHandler.END
    await query.answer()
    
    round_number = int(query.data.replace("admin_open_round_", ""))
    context.user_data["admin_round_to_open"] = round_number
    
    keyboard = [[InlineKeyboardButton("Отмена", callback_data="admin_cancel_match_action")]]
    await query.edit_message_text(
        f"Укажите строгий дедлайн для {round_number}-го тура.\n"
        "Формат: `ДД.ММ.ГГГГ ЧЧ:ММ` (например: `29.07.2026 23:59`)\n\n"
        "Отправьте текст дедлайна:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ADMIN_WAITING_FOR_DEADLINE

import datetime

async def admin_open_round_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user or not is_admin(user.id):
        return ConversationHandler.END

    if not update.message or not update.message.text:
        return ADMIN_WAITING_FOR_DEADLINE
        
    deadline_text = update.message.text
    
    try:
        dt = datetime.datetime.strptime(deadline_text.strip(), "%d.%m.%Y %H:%M")
    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Пожалуйста, используйте формат: `ДД.ММ.ГГГГ ЧЧ:ММ`", parse_mode="Markdown")
        return ADMIN_WAITING_FOR_DEADLINE
        
    round_number = context.user_data.get("admin_round_to_open")
    if not round_number:
        return ConversationHandler.END
        
    await asyncio.to_thread(database.update_round_status, round_number, is_open=True, deadline=deadline_text)
    
    keyboard = [[InlineKeyboardButton("« К управлению турами", callback_data="admin_manage_matches_info")]]
    await update.message.reply_text(
        f"✅ {round_number}-й тур успешно открыт. Строгий дедлайн: {deadline_text}\n"
        "Уведомление отправлено в общую группу и игрокам в ЛС!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    main_group_id = await asyncio.to_thread(database.get_group_id)
    reports_topic_id = await asyncio.to_thread(database.get_config, "reports_topic_id")
    if main_group_id:
        try:
            kwargs = {"chat_id": main_group_id, "text": f"🟢 **Открыт {round_number}-й Тур!**\n\n🕒 Дедлайн: {deadline_text}\n\nПожалуйста, сыграйте свои матчи и внесите результаты до истечения срока.", "parse_mode": "Markdown"}
            if reports_topic_id:
                kwargs["message_thread_id"] = int(reports_topic_id)
            await context.bot.send_message(**kwargs)
            if round_number == 1:
                await post_league_table_to_reports(context)
        except Exception as e:
            logger.exception("Failed to notify main group")
            
    await notify_players_rounds_opened(context, [round_number], deadline_text)
    return ConversationHandler.END

@admin_only
async def admin_open_batch_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not is_admin(query.from_user.id): return ConversationHandler.END
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("Отмена", callback_data="admin_cancel_match_action")]]
    await query.edit_message_text(
        "Укажите диапазон туров для открытия.\n"
        "Формат: `Начальный-Конечный` (например: `1-3` или просто `2`)\n\n"
        "Отправьте текст:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ADMIN_WAITING_FOR_BATCH_ROUNDS

async def admin_open_batch_rounds(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user or not is_admin(user.id):
        return ConversationHandler.END

    if not update.message or not update.message.text:
        return ADMIN_WAITING_FOR_BATCH_ROUNDS
        
    text = update.message.text.strip()
    normalized = text.replace("–", "-").replace("—", "-").replace(" ", "")
    try:
        if "-" in normalized:
            parts = normalized.split("-")
            start_r = int(parts[0])
            end_r = int(parts[1])
        else:
            start_r = end_r = int(normalized)
        if start_r > end_r:
            start_r, end_r = end_r, start_r
    except Exception:
        await update.message.reply_text("❌ Неверный формат. Используйте `1-3` или `2`.", parse_mode="Markdown")
        return ADMIN_WAITING_FOR_BATCH_ROUNDS
        
    context.user_data["batch_start"] = start_r
    context.user_data["batch_end"] = end_r
    
    keyboard = [[InlineKeyboardButton("Отмена", callback_data="admin_cancel_match_action")]]
    await update.message.reply_text(
        f"Выбраны туры: с {start_r} по {end_r}.\n\n"
        "Теперь укажите строгий дедлайн для этих туров.\n"
        "Формат: `ДД.ММ.ГГГГ ЧЧ:ММ` (например: `29.07.2026 23:59`)",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ADMIN_WAITING_FOR_BATCH_DEADLINE

async def admin_open_batch_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user or not is_admin(user.id):
        return ConversationHandler.END

    if not update.message or not update.message.text:
        return ADMIN_WAITING_FOR_BATCH_DEADLINE
        
    deadline_text = update.message.text.strip()
    try:
        dt = datetime.datetime.strptime(deadline_text, "%d.%m.%Y %H:%M")
    except ValueError:
        await update.message.reply_text("❌ Неверный формат даты. Используйте: `ДД.ММ.ГГГГ ЧЧ:ММ`", parse_mode="Markdown")
        return ADMIN_WAITING_FOR_BATCH_DEADLINE
        
    start_r = context.user_data.get("batch_start")
    end_r = context.user_data.get("batch_end")
    
    await asyncio.to_thread(database.open_rounds_batch, start_r, end_r, deadline_text)
    
    keyboard = [[InlineKeyboardButton("« К управлению турами", callback_data="admin_manage_matches_info")]]
    await update.message.reply_text(
        f"✅ Туры с {start_r} по {end_r} успешно открыты.\nДедлайн: {deadline_text}\n"
        "Уведомления отправлены в группу и игрокам в ЛС!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    main_group_id = await asyncio.to_thread(database.get_group_id)
    reports_topic_id = await asyncio.to_thread(database.get_config, "reports_topic_id")
    if main_group_id:
        try:
            kwargs = {"chat_id": main_group_id, "text": f"🟢 **Открыты туры с {start_r} по {end_r}!**\n\n🕒 Дедлайн: {deadline_text}\n\nПожалуйста, сыграйте свои матчи и внесите результаты до истечения срока.", "parse_mode": "Markdown"}
            if reports_topic_id:
                kwargs["message_thread_id"] = int(reports_topic_id)
            await context.bot.send_message(**kwargs)
            if start_r == 1 or (start_r <= 1 <= end_r):
                await post_league_table_to_reports(context)
        except Exception as e:
            logger.exception("Failed to notify main group")
            
    opened_rounds = list(range(start_r, end_r + 1))
    await notify_players_rounds_opened(context, opened_rounds, deadline_text)
    return ConversationHandler.END

@admin_only
async def admin_close_round(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not is_admin(query.from_user.id): return
    await query.answer()
    
    round_number = int(query.data.replace("admin_close_round_", ""))
    await asyncio.to_thread(database.update_round_status, round_number, is_open=False, deadline=None)
    
    keyboard = [[InlineKeyboardButton("« Вернуться", callback_data=f"admin_manage_round_{round_number}")]]
    await query.edit_message_text(
        f"🔴 {round_number}-й тур закрыт. Прием результатов остановлен.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

@admin_only
async def admin_round_matches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display all matches in a round for admin action."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    try:
        await query.answer()
    except Exception:
        pass
    
    round_number = int(query.data.replace("admin_round_matches_", ""))
    matches = await asyncio.to_thread(database.get_matches_by_round, round_number)
    
    keyboard = []
    for m in matches:
        opp1 = m["player1_nickname"]
        opp2 = m["player2_nickname"]
        
        if m["status"] == "confirmed":
            status_lbl = f"{m['player1_score']}:{m['player2_score']}"
        elif m["status"] == "disputed":
            status_lbl = "⚠️ спор"
        else:
            status_lbl = "⚔️"
            
        btn_text = f"{opp1} vs {opp2} ({status_lbl})"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"admin_view_match_{m['id']}")])
        
    keyboard.append([InlineKeyboardButton("« Назад к турам", callback_data="admin_manage_matches_info")])
    
    text = f"📅 **Матчи {round_number}-го тура (Панель Администратора):**\n\nВыберите матч для ввода счета или сброса:"
    if query.message and query.message.photo:
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

@admin_only
async def admin_view_match(update: Update, context: ContextTypes.DEFAULT_TYPE, match_id: int | None = None) -> None:
    """View details of a single match with admin actions."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()
    
    if match_id is None:
        match_id = int(query.data.replace("admin_view_match_", ""))
    match = await asyncio.to_thread(database.get_match, match_id)
    
    if not match:
        keyboard = [[InlineKeyboardButton("« Назад", callback_data="admin_manage_matches_info")]]
        await query.edit_message_text("❌ Матч не найден.", reply_markup=InlineKeyboardMarkup(keyboard))
        return
        
    is_cup = match.get("tournament_type") == "cup"
    cup_stage = match.get("cup_stage", "1/8")
    g_num = match.get("game_num_in_series", 1)

    if is_cup:
        title_stage = f"{cup_stage} Финала" if cup_stage != "final" else "ФИНАЛ"
        header_title = f"🏆 <b>Карточка кубкового матча #{match['id']} | Кубок КПЛ — {title_stage} (Игра {g_num})</b>"
        back_button = InlineKeyboardButton("« Назад к Кубку", callback_data=f"admin_cup_stage_{cup_stage}")
    else:
        header_title = f"⚽️ <b>Карточка матча #{match['id']} (Тур {match['round_number']})</b>"
        back_button = InlineKeyboardButton("« Назад к туру", callback_data=f"admin_round_matches_{match['round_number']}")

    status_map = {
        "pending": "⚔️ Ожидает игры",
        "confirmed": "✅ Завершен",
        "disputed": "⚠️ Оспорен (Спор)"
    }
    
    club1 = f" [{html.escape(match['player1_team'])}]" if match['player1_team'] else ""
    club2 = f" [{html.escape(match['player2_team'])}]" if match['player2_team'] else ""
    p1_name = html.escape(str(match['player1_nickname'] or match['player1_team'] or ""))
    p2_name = html.escape(str(match['player2_nickname'] or match['player2_team'] or ""))
    score_str = f"<code>{match['player1_score']} : {match['player2_score']}</code>" if match['player1_score'] is not None else "Не сыгран"
    
    text = (
        f"{header_title}\n\n"
        f"⚔️ <b>{p1_name}</b>{club1}\n"
        f" 🆚 <b>{p2_name}</b>{club2}\n\n"
        f"• <b>Текущий счет:</b> {score_str}\n"
        f"• <b>Статус:</b> {html.escape(status_map.get(match['status'], match['status']))}\n"
        f"📜 <a href=\"https://t.me/fifulatyrniru/3405\">Правила турнира</a>"
    )
    
    keyboard = [
        [InlineKeyboardButton("📜 Правила турнира", url="https://t.me/fifulatyrniru/3405")],
        [InlineKeyboardButton("⚡ Внести результат по фото (ИИ)", callback_data=f"admin_report_score_auto_{match_id}")],
        [InlineKeyboardButton("✍️ Внести результат вручную", callback_data=f"cb_report_choice_manual_{match_id}")],
        [InlineKeyboardButton("🚫 ТП 3:0 (Хозяева)", callback_data=f"admin_tp_home_{match_id}"), InlineKeyboardButton("🚫 ТП 0:3 (Гости)", callback_data=f"admin_tp_away_{match_id}")],
    ]
    if not is_cup:
        keyboard.append([InlineKeyboardButton("🤝 ТН 0:0 (Ничья)", callback_data=f"admin_tp_draw_{match_id}"), InlineKeyboardButton("🔄 Сбросить результат", callback_data=f"admin_reset_match_execute_{match_id}")])
    else:
        keyboard.append([InlineKeyboardButton("🔄 Сбросить результат", callback_data=f"admin_reset_match_execute_{match_id}")])
    if match.get("photo_id"):
        keyboard.append([InlineKeyboardButton("📸 Просмотр скриншота матча", callback_data=f"admin_view_match_photo_{match_id}")])
    keyboard.append([back_button])

    if query.message and query.message.photo:
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

@admin_only
async def admin_view_match_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the uploaded screenshot of a match to the admin."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()

    match_id = int(query.data.replace("admin_view_match_photo_", ""))
    match = await asyncio.to_thread(database.get_match, match_id)

    if not match:
        await safe_edit_or_reply(query, context, "❌ Матч не найден.")
        return

    photo_id = match.get("photo_id")
    if not photo_id:
        await safe_edit_or_reply(query, context, "📸 Скриншот для этого матча не был загружен.")
        return

    p1 = html.escape(str(match['player1_nickname'] or match['player1_team'] or ""))
    p2 = html.escape(str(match['player2_nickname'] or match['player2_team'] or ""))
    score_str = f"{match['player1_score']} : {match['player2_score']}" if match['player1_score'] is not None else "Не сыгран"
    title = "🏆 Кубок" if match.get("tournament_type") == "cup" else f"Тур {match['round_number']}"

    caption = (
        f"📸 <b>Скриншот матча #{match_id} ({title})</b>\n"
        f"⚔️ <b>{p1}</b> {score_str} <b>{p2}</b>"
    )
    back_button = InlineKeyboardMarkup([[InlineKeyboardButton("« Назад к карточке матча", callback_data=f"admin_view_match_{match_id}")]])

    try:
        await context.bot.send_photo(chat_id=query.from_user.id, photo=photo_id, caption=caption, parse_mode="HTML", reply_markup=back_button)
    except BadRequest as e:
        logger.warning(f"Failed to resend screenshot for match #{match_id}: {e}")
        await safe_edit_or_reply(query, context, "📸 Не удалось отобразить скриншот (файл недоступен).")

@admin_only
async def admin_report_score_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start AI Vision photo recognition flow for Admin."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id): return
    await query.answer()

    match_id = int(query.data.replace("admin_report_score_auto_", ""))
    match = await asyncio.to_thread(database.get_match, match_id)
    if not match:
        await query.edit_message_text("❌ Матч не найден.")
        return

    context.user_data["reporting_match_id"] = match_id
    context.user_data["report_home_team"] = match['player1_team'] or match['player1_nickname']
    context.user_data["report_away_team"] = match['player2_team'] or match['player2_nickname']
    context.user_data["reporter_id"] = query.from_user.id
    context.user_data["reporting_mode"] = "auto"
    context.user_data["is_admin_reporting"] = True
    context.user_data["awaiting_report_photo"] = True
    context.user_data["ai_photos_list"] = []

    text = (
        f"🤖 <b>Автоматический ввод по фото (Администратор)</b>\n\n"
        f"Пожалуйста, отправьте <b>от 1 до 3 скриншотов</b> матча #{match_id} строго с статистикой (голы и ассисты).\n\n"
        f"💡 <i>ИИ мгновенно распознает счет, составы и предложит занести результат в лигу.</i>"
    )
    keyboard = [[InlineKeyboardButton("« Назад к карточке матча", callback_data=f"admin_view_match_{match_id}")]]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

async def _notify_group_about_tp(context: ContextTypes.DEFAULT_TYPE, match_id: int, tp_type: str):
    match = await asyncio.to_thread(database.get_match, match_id)
    group_id = await asyncio.to_thread(database.get_group_id)
    if not match or not group_id:
        return
        
    reports_topic_id = await asyncio.to_thread(database.get_config, "reports_topic_id")
    
    p1 = match.get("player1_nickname") or match.get("direct_p1_team") or "Хозяева"
    p2 = match.get("player2_nickname") or match.get("direct_p2_team") or "Гости"
    rnd = match.get("round_number", "?")
    tour_type = match.get("tournament_type", "league")
    
    tour_text = f"Тур {rnd}" if tour_type == "league" else "Кубковый матч"
    
    if tp_type == "home":
        res_text = f"{p1} <b>1:0</b> {p2} (ТП)"
    elif tp_type == "away":
        res_text = f"{p1} <b>0:1</b> {p2} (ТП)"
    else:
        res_text = f"{p1} <b>0:0</b> {p2} (ТН)"

    text = f"🚨 <b>Администратор назначил результат:</b>\n\n🏆 <b>{tour_text}</b>\n🎮 {res_text}"
    
    kwargs = {"chat_id": group_id, "text": text, "parse_mode": "HTML"}
    if reports_topic_id:
        kwargs["message_thread_id"] = int(reports_topic_id)
        
    try:
        await context.bot.send_message(**kwargs)
    except Exception as e:
        logger.error(f"Failed to send TP notification to group: {e}")

@admin_only
async def admin_set_tp_home_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not is_admin(query.from_user.id): return
    await query.answer()
    match_id = int(query.data.replace("admin_tp_home_", ""))
    next_stage = await asyncio.to_thread(database.set_technical_result, match_id, 1, 0)
    if next_stage:
        await notify_cup_stage_opened(context.bot, next_stage)
    await _notify_group_about_tp(context, match_id, "home")
    await query.answer("✅ Назначено ТП 1:0 (Победа Хозяев)", show_alert=True)
    await admin_view_match(update, context, match_id=match_id)

@admin_only
async def admin_set_tp_away_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not is_admin(query.from_user.id): return
    await query.answer()
    match_id = int(query.data.replace("admin_tp_away_", ""))
    next_stage = await asyncio.to_thread(database.set_technical_result, match_id, 0, 1)
    if next_stage:
        await notify_cup_stage_opened(context.bot, next_stage)
    await _notify_group_about_tp(context, match_id, "away")
    await query.answer("✅ Назначено ТП 0:1 (Победа Гостей)", show_alert=True)
    await admin_view_match(update, context, match_id=match_id)

@admin_only
async def admin_set_tp_draw_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not is_admin(query.from_user.id): return
    await query.answer()
    match_id = int(query.data.replace("admin_tp_draw_", ""))
    await asyncio.to_thread(database.set_technical_result, match_id, 0, 0)
    await _notify_group_about_tp(context, match_id, "draw")
    await query.answer("✅ Назначена Техническая ничья 0:0", show_alert=True)
    await admin_view_match(update, context, match_id=match_id)

@admin_only
async def admin_reset_match_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute match reset via inline callback button click."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()
    
    match_id = int(query.data.replace("admin_reset_match_execute_", ""))
    match = await asyncio.to_thread(database.get_match, match_id)
    
    if not match:
        keyboard = [[InlineKeyboardButton("« Назад", callback_data="admin_manage_matches_info")]]
        await query.edit_message_text("❌ Матч не найден.", reply_markup=InlineKeyboardMarkup(keyboard))
        return
        
    await asyncio.to_thread(database.reset_match, match_id)
    
    player_text = (
        f"🔄 **Результат вашего матча в Туре {match['round_number']} был сброшен администратором!**\n\n"
        f"⚔️ **{match['player1_nickname']}** vs **{match['player2_nickname']}**\n\n"
        f"Вы можете сыграть матч заново и ввести результаты через меню кабинета."
    )
    for p_id in (match["player1_id"], match["player2_id"]):
        if p_id:
            await safe_send_notification(context.bot, p_id, player_text, parse_mode="Markdown")
            
    # Refresh view
    await admin_view_match(update, context, match_id=match_id)

# --- Conversational Dialogs for Admin ---

@admin_only
async def admin_add_player_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start player creation flow."""
    query = update.callback_query
    user_id = query.from_user.id if query else update.effective_user.id
    if not is_admin(user_id):
        return ConversationHandler.END
    if query:
        await query.answer()

    # Check if username is passed as command argument (e.g. /add_player @sp1r1tVSA)
    args = context.args
    username = None
    if args:
        username = args[0].strip().lstrip("@")

    if username:
        if not username or " " in username:
            text = "❌ Неверный юзернейм. Введите корректный Telegram-юзернейм (без пробелов):"
            if query:
                await query.edit_message_text(text)
            else:
                await update.message.reply_text(text)
            return ADMIN_EXPECT_PLAYER_USERNAME
        
        context.user_data["admin_add_player_username"] = username
        return await admin_show_free_clubs(update, context)

    # Ask for username
    text = (
        "➕ **Добавление игрока**\n\n"
        "Введите Telegram-юзернейм игрока (например, `@username`):\n\n"
        "*(Отправьте /cancel для отмены)*"
    )
    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="admin_cancel_player_action")]]
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADMIN_EXPECT_PLAYER_USERNAME

@admin_only
async def admin_add_player_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Capture username and show club selection."""
    username = update.message.text.strip().lstrip("@")
    if not username or " " in username:
        await update.message.reply_text(
            "❌ Неверный юзернейм. Введите корректный Telegram-юзернейм (без пробелов):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="admin_cancel_player_action")]])
        )
        return ADMIN_EXPECT_PLAYER_USERNAME
        
    context.user_data["admin_add_player_username"] = username
    return await admin_show_free_clubs(update, context)

@admin_only
async def admin_show_free_clubs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Helper to display clubs list for selection."""
    username = context.user_data.get("admin_add_player_username")
    if not username:
        text = "Произошла ошибка (не найден юзернейм). Сброс."
        if update.message:
            await update.message.reply_text(text)
        return ConversationHandler.END

    # Get active mapping of club to player username
    club_to_player = {u["team_name"].lower(): u["username"] for u in (await asyncio.to_thread(database.list_users)) if u["team_name"]}
    
    keyboard = []
    row = []
    
    for club in CLUBS:
        # Check if busy
        occupied_by = club_to_player.get(club.lower())
        if occupied_by:
            btn_text = f"🔴 {club} (@{occupied_by})"
        else:
            btn_text = f"🟢 {club} (свободен)"
            
        row.append(InlineKeyboardButton(btn_text, callback_data=f"assign_club_{club}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="admin_cancel_player_action")])
    markup = InlineKeyboardMarkup(keyboard)
    
    text = f"⚽ <b>Выберите клуб для игрока @{username}</b>:\n\n<i>(Красным отмечены уже занятые клубы — выбор такого клуба переназначит его новому игроку)</i>"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")
            
    return ADMIN_EXPECT_PLAYER_CLUB

@admin_only
async def admin_add_player_club_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle callback from club selection button."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    await query.answer()
    
    club = query.data.replace("assign_club_", "")
    username = context.user_data.pop("admin_add_player_username", None)
    
    if not username:
        await query.answer("❌ Ошибка: не найден юзернейм. Возможно, вы уже добавили этого игрока.", show_alert=True)
        return ConversationHandler.END
        
    # Assign new player to the club (will automatically handle unlinking the old one)
    temp_id, old_username = await asyncio.to_thread(database.assign_player_to_club, username, club)
    
    text = (
        f"✅ <b>Игрок успешно добавлен!</b>\n\n"
        f"👤 <b>Telegram:</b> @{username}\n"
        f"🛡️ <b>Клуб:</b> {club}\n"
        f"🆔 <b>Временный ID:</b> <code>{temp_id}</code>\n\n"
        f"Когда @{username} запустит бота (отправит `/start`), его аккаунт свяжется автоматически."
    )
    if old_username:
        text += f"\n\n<i>⚠️ Примечание: старый участник @{old_username} был автоматически отвязан от клуба {club} и удален.</i>"
        
    keyboard = [[InlineKeyboardButton("« Назад в меню", callback_data="admin_cancel_player_action")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return ConversationHandler.END

@admin_only
async def admin_import_players_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start players multiline import flow."""
    query = update.callback_query
    user_id = query.from_user.id if query else update.effective_user.id
    if not is_admin(user_id):
        return ConversationHandler.END
    if query:
        await query.answer()
    
    text = (
        "📊 **Импорт списка участников**\n\n"
        "Отправьте список игроков, где каждый участник с новой строки в формате:\n"
        "`@юзернейм - Название Клуба` (через дефис)\n\n"
        "Пример:\n"
        "`@user1 - Real Madrid`\n"
        "`@user2 - Barcelona`"
    )
    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="admin_cancel_player_action")]]
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADMIN_EXPECT_IMPORT_TEXT

@admin_only
async def admin_import_players_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Parse list input, pre-register users, and display results."""
    payload = update.message.text
    lines = payload.split("\n")
    
    added = []
    errors = []
    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        parts = line_clean.split("-", 1)
        if len(parts) != 2:
            parts = line_clean.split(":", 1)
        if len(parts) != 2:
            errors.append(f"Не удалось распарсить строку: `{line_clean}`")
            continue
            
        part1 = parts[0].strip()
        part2 = parts[1].strip()
        
        if part2.startswith("@") or (not part1.startswith("@") and "@" in part2):
            username = part2.lstrip("@").strip()
            team_name = part1
        else:
            username = part1.lstrip("@").strip()
            team_name = part2
            
        if not username or not team_name:
            errors.append(f"Пустой юзернейм или клуб в строке: `{line_clean}`")
            continue
            
        try:
            temp_id, old_username = await asyncio.to_thread(database.assign_player_to_club, username, team_name)
            added.append(f"• @{username} — {team_name} (ID: `{temp_id}`)")
        except Exception as e:
            errors.append(f"Ошибка при добавлении @{username}: {e}")
            
    res = []
    if added:
        res.append("✅ **Участники успешно импортированы:**")
        res.extend(added)
    if errors:
        res.append("\n⚠️ **Ошибки при импорте:**")
        res.extend(errors)
        
    await update.message.reply_text(
        "\n".join(res),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« К списку участников", callback_data="admin_list_players_page_0")]]),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

@admin_only
async def admin_edit_club_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start club modification flow."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return ConversationHandler.END
    await query.answer()
    
    player_id = int(query.data.replace("admin_edit_club_start_", ""))
    player = await asyncio.to_thread(database.get_user, player_id)
    
    if not player:
        await query.edit_message_text("❌ Игрок не найден.")
        return ConversationHandler.END
        
    context.user_data["admin_edit_player_id"] = player_id
    
    text = (
        f"✏️ **Изменение клуба**\n\n"
        f"Игрок: @{player['username']}\n"
        f"Текущий клуб: {player['team_name'] or 'нет'}\n\n"
        f"Введите новое название клуба для этого игрока:"
    )
    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data=f"admin_view_player_{player_id}")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADMIN_EXPECT_NEW_CLUB

@admin_only
async def admin_edit_club_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save new club name in database."""
    new_club = update.message.text.strip()
    player_id = context.user_data.pop("admin_edit_player_id", None)
    
    if not player_id:
        await update.message.reply_text("Произошла ошибка (не найден ID игрока). Сброс.")
        return ConversationHandler.END
        
    success, msg = await asyncio.to_thread(database.set_player_club, str(player_id), new_club)
    await update.message.reply_text(
            "✅ {msg}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« К карточке игрока", callback_data=f"admin_view_player_{player_id}")]])
    )
    return ConversationHandler.END

@admin_only
async def admin_create_matches_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt admin to provide schedule text or .txt file."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return ConversationHandler.END
    await query.answer()

    text = (
        "📝 **Создание матчей и туров (Ввод расписания)**\n\n"
        "Отправьте список туров и парных матчей текстом в сообщении или прикрепите `.txt` файл с расписанием.\n\n"
        "**Пример формата:**\n"
        "```\n"
        "1 Тур\n"
        "Спортинг - Ривер Плейт\n"
        "Бока Хуниорс - Бенфика\n\n"
        "2 Тур\n"
        "Бенфика - Спортинг\n"
        "```"
    )
    keyboard = [[InlineKeyboardButton("Отмена", callback_data="admin_manage_matches_info")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADMIN_EXPECT_MATCH_SCHEDULE_INPUT

@admin_only
async def admin_receive_schedule_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process incoming text or file with schedule and create matches in DB."""
    user = update.effective_user
    if not user or not is_admin(user.id):
        return ConversationHandler.END

    raw_text = ""
    if update.message.document:
        doc = update.message.document
        if not doc.file_name.lower().endswith(('.txt', '.text', '.log', '.csv')):
            await update.message.reply_text("❌ Пожалуйста, отправьте текстовый файл формата `.txt` или введите текст прямо в чат.")
            return ADMIN_EXPECT_MATCH_SCHEDULE_INPUT
        file = await context.bot.get_file(doc.file_id)
        byte_data = await file.download_as_bytearray()
        raw_text = byte_data.decode('utf-8', errors='ignore')
    elif update.message.text:
        raw_text = update.message.text.strip()
    else:
        await update.message.reply_text("❌ Отправьте текстовое сообщение или файл `.txt` с расписанием.")
        return ADMIN_EXPECT_MATCH_SCHEDULE_INPUT

    rounds_data, parse_errors = parse_schedule_text(raw_text)
    if not rounds_data:
        err_msg = "❌ Не удалось найти ни одного тура или матча в отправленном тексте.\n\n"
        if parse_errors:
            err_msg += "Ошибки:\n" + "\n".join(parse_errors[:5])
        keyboard = [[InlineKeyboardButton("« Назад к управлению", callback_data="admin_manage_matches_info")]]
        await update.message.reply_text(err_msg, reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END

    r_cnt, m_cnt, unmatched = create_matches_from_parsed_schedule(rounds_data)
    
    msg = f"🎉 **Создание матчей завершено!**\n\n"
    msg += f"• **Создано туров:** {r_cnt}\n"
    msg += f"• **Занесено матчей в базу:** {m_cnt}\n"
    
    if unmatched:
        msg += f"\n⚠️ **Следующие клубы из списка не найдены в базе зарегистрированных игроков:**\n"
        for u_team in unmatched:
            msg += f"• `{u_team}`\n"
        msg += "\n_Зарегистрируйте этих игроков в админке или перепроверьте написание названия клубов._"

    keyboard = [[InlineKeyboardButton("« Вернуться к матчам", callback_data="admin_manage_matches_info")]]
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END

@admin_only
async def admin_set_score_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Redirect admin to full interactive manual match result entry (score + goal scorers + assists)."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return ConversationHandler.END
    await query.answer()
    
    match_id = int(query.data.replace("admin_set_score_start_", ""))
    context.user_data["is_admin_reporting"] = True
    query.data = f"cb_report_choice_manual_{match_id}"
    await cb_report_choice_manual(update, context)
    return ConversationHandler.END

@admin_only
async def admin_set_score_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Parse score input, save to DB, and notify players."""
    score_text = update.message.text.strip()
    match_id = context.user_data.get("admin_set_match_id")
    
    if not match_id:
        await update.message.reply_text("Произошла ошибка (не найден ID матча). Сброс.")
        return ConversationHandler.END
        
    parts = score_text.split(":")
    if len(parts) != 2:
        parts = score_text.split("-")
    if len(parts) != 2:
        parts = score_text.split(" ")
        
    try:
        s1 = int(parts[0].strip())
        s2 = int(parts[1].strip())
    except (ValueError, IndexError):
        await update.message.reply_text(
            "❌ Неверный формат счета. Введите результат в формате `хозяева:гости` (например, `3:1`):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data=f"admin_view_match_{match_id}")]])
        )
        return ADMIN_EXPECT_MATCH_SCORE

    if s1 < 0 or s2 < 0 or s1 > config.MAX_MATCH_GOALS or s2 > config.MAX_MATCH_GOALS:
        await update.message.reply_text(
            f"❌ Некорректный счёт. Максимальное количество голов: {config.MAX_MATCH_GOALS}.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data=f"admin_view_match_{match_id}")]])
        )
        return ADMIN_EXPECT_MATCH_SCORE

    match = await asyncio.to_thread(database.get_match, match_id)
    if not match:
        await update.message.reply_text("❌ Матч не найден. Сброс.")
        return ConversationHandler.END
        
    await asyncio.to_thread(database.admin_set_match_score, match_id, s1, s2)
    
    await update.message.reply_text(
        f"✅ Счет матча #{match_id} изменен: **{s1}:{s2}**!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« К карточке матча", callback_data=f"admin_view_match_{match_id}")]])
    )
    
    # Notify players
    player_text = (
        f"⚙️ **Администратор вручную установил результат вашего матча (Тур {match['round_number']})!**\n\n"
        f"⚔️ **{match['player1_nickname']}**  `{s1} : {s2}`  **{match['player2_nickname']}**\n\n"
        f"Результат подтвержден и обновлен в таблице."
    )
    for p_id in (match["player1_id"], match["player2_id"]):
        try:
            await context.bot.send_message(chat_id=p_id, text=player_text, parse_mode="Markdown")
        except Exception as e:
            logger.exception("Не удалось отправить уведомление игроку {p_id}")

    # Notify Telegram Group
    group_id = await asyncio.to_thread(database.get_group_id)
    if group_id:
        group_text = (
            f"⚙️ **Результат матча изменен администратором!**\n"
            f"🏆 **Тур {match['round_number']}**\n"
            f"⚔️ **{match['player1_nickname']}** ({match['player1_team'] or 'нет'}) "
            f"**{s1} : {s2}** "
            f"**{match['player2_nickname']}** ({match['player2_team'] or 'нет'})"
        )
        try:
            await context.bot.send_message(chat_id=group_id, text=group_text, parse_mode="Markdown")
        except Exception as e:
            logger.exception("Не удалось отправить сообщение в группу")

    try:
        await post_league_table_to_reports(context)
    except Exception:
        logger.exception("Failed to refresh league table after manual score")

    return ConversationHandler.END

@admin_only
async def admin_cancel_player_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Abort player edits and return to players hub or admin panel."""
    query = update.callback_query
    context.user_data.pop("admin_add_player_username", None)
    context.user_data.pop("admin_edit_player_id", None)
    
    if query:
        await query.answer()
        await admin_manage_players_info(update, context)
    else:
        await show_admin_panel(update, context)
    return ConversationHandler.END

@admin_only
async def admin_cancel_match_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Abort match edits and return to match card."""
    query = update.callback_query
    match_id = context.user_data.pop("admin_set_match_id", None)
    
    if query:
        await query.answer()
        if match_id:
            await admin_view_match(update, context, match_id=match_id)
        else:
            await admin_manage_matches_info(update, context)
    else:
        await show_admin_panel(update, context)
    return ConversationHandler.END

@admin_only
async def admin_toggle_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle user system role between player and admin."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()
    
    # Format: admin_toggle_role_{player_id}_{new_role}
    parts = query.data.split("_")
    player_id = int(parts[3])
    new_role = parts[4]
    
    # Safety: do not allow admins to revoke their own admin rights
    if player_id == query.from_user.id and new_role == "player":
        await query.message.reply_text("❌ Вы не можете снять роль администратора с себя.")
        return
        
    success, msg = await asyncio.to_thread(database.update_player_role, player_id, new_role)
    if success:
        # Refresh player card
        await admin_view_player(update, context, player_id=player_id)
    else:
        await query.message.reply_text(f"❌ Ошибка: {msg}")

@admin_only
async def admin_delete_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show options screen for player deletion (soft exclusion vs complete wipe)."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()
    
    player_id = int(query.data.replace("admin_delete_options_", ""))
    player = await asyncio.to_thread(database.get_user, player_id)
    
    if not player:
        keyboard = [[InlineKeyboardButton("« Назад к списку", callback_data="admin_list_players_page_0")]]
        await query.edit_message_text("❌ Игрок не найден.", reply_markup=InlineKeyboardMarkup(keyboard))
        return
        
    text = (
        f"❌ **Удаление участника @{player['username']}**\n\n"
        f"Выберите тип удаления:\n\n"
        f"1. **Исключить (Тех. поражения)**:\n"
        f"Сохраняет сыгранные матчи игрока, а все его будущие/несыгранные матчи закрывает техническим поражением (0:3).\n\n"
        f"2. **Стереть полностью (Без следов)**:\n"
        f"Полностью удаляет игрока и **все матчи с его участием** (включая уже сыгранные)."
    )
    
    keyboard = [
        [InlineKeyboardButton("🗑️ 1. Исключить (Тех. поражения)", callback_data=f"admin_confirm_delete_player_{player_id}")],
        [InlineKeyboardButton("🔥 2. Стереть полностью (Без следов)", callback_data=f"admin_confirm_wipe_player_{player_id}")],
        [InlineKeyboardButton("« Назад к карточке", callback_data=f"admin_view_player_{player_id}")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

@admin_only
async def admin_confirm_wipe_player(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show final warning for complete player wipe."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()
    
    player_id = int(query.data.replace("admin_confirm_wipe_player_", ""))
    player = await asyncio.to_thread(database.get_user, player_id)
    
    if not player:
        keyboard = [[InlineKeyboardButton("« Назад к списку", callback_data="admin_list_players_page_0")]]
        await query.edit_message_text("❌ Игрок не найден.", reply_markup=InlineKeyboardMarkup(keyboard))
        return
        
    text = (
        f"⚠️ **ВНИМАНИЕ: ПОЛНОЕ УДАЛЕНИЕ**\n\n"
        f"Вы действительно хотите безвозвратно стереть игрока @{player['username']} "
        f"и ВСЕ матчи с его участием?\n\n"
        f"**Это действие удалит сыгранные им матчи и изменит турнирные расклады остальных участников!**"
    )
    
    keyboard = [
        [InlineKeyboardButton("🔥 Да, стереть полностью", callback_data=f"admin_wipe_player_execute_{player_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"admin_view_player_{player_id}")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

@admin_only
async def admin_wipe_player_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute complete player wipe from database."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()
    
    player_id = int(query.data.replace("admin_wipe_player_execute_", ""))
    success, msg = await asyncio.to_thread(database.delete_player_completely, player_id)
    
    keyboard = [[InlineKeyboardButton("« Назад к списку", callback_data="admin_list_players_page_0")]]
    if success:
        await query.edit_message_text(f"✅ {msg}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
        group_id = await asyncio.to_thread(database.get_group_id)
        if group_id:
            try:
                await context.bot.send_message(chat_id=group_id, text=f"📢 **Полное удаление участника!**\n\n{msg}", parse_mode="Markdown")
            except Exception as e:
                logger.exception("Не удалось отправить уведомление в группу")
    else:
        await query.edit_message_text(f"❌ {msg}", reply_markup=InlineKeyboardMarkup(keyboard))

# --- Conversation handlers for username / nickname / reset ---



@admin_only
async def admin_edit_username_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start Telegram username edit flow."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return ConversationHandler.END
    await query.answer()
    
    player_id = int(query.data.replace("admin_edit_username_start_", ""))
    player = await asyncio.to_thread(database.get_user, player_id)
    
    if not player:
        await query.edit_message_text("❌ Игрок не найден.")
        return ConversationHandler.END
        
    context.user_data["admin_edit_player_id"] = player_id
    
    text = (
        f"✏️ **Изменение юзернейма**\n\n"
        f"Игрок: @{player['username']}\n"
        f"Текущий юзернейм: @{player['username'] or 'нет'}\n\n"
        f"Введите новый Telegram-юзернейм (например, `@username`):"
    )
    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data=f"admin_view_player_{player_id}")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADMIN_EXPECT_NEW_USERNAME

@admin_only
async def admin_edit_username_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save new Telegram username in database."""
    new_username = update.message.text.strip().lstrip("@")
    player_id = context.user_data.pop("admin_edit_player_id", None)
    
    if not player_id:
        await update.message.reply_text("Произошла ошибка (не найден ID игрока). Сброс.")
        return ConversationHandler.END
        
    success, msg = await asyncio.to_thread(database.update_player_username, player_id, new_username)
    await update.message.reply_text(
        f"✅ {msg}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« К карточке игрока", callback_data=f"admin_view_player_{player_id}")]])
    )
    return ConversationHandler.END

# --- Reset League Flow ---

@admin_only
async def admin_clear_league_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start full league reset flow."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return ConversationHandler.END
    await query.answer()
    
    text = (
        "⚠️ **СБРОС ВСЕЙ ЛИГИ**\n\n"
        "Внимание! Это действие удалит всех зарегистрированных участников и все сгенерированные матчи.\n"
        "Все пользователи с ролью Admin будут сохранены.\n\n"
        "Для подтверждения сброса, пожалуйста, отправьте кодовое слово **СБРОС** большими буквами:\n"
        "*(Или нажмите Отмена ниже)*"
    )
    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="admin_cancel_player_action")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADMIN_EXPECT_RESET_CONFIRM

@admin_only
async def admin_clear_league_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Confirm text word and wipe the database tables."""
    text_input = update.message.text.strip()
    
    if text_input != "СБРОС":
        await update.message.reply_text(
            "❌ Кодовое слово введено неверно. Сброс лиги отменен.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Управление участниками", callback_data="admin_manage_players_info")]])
        )
        return ConversationHandler.END
        
    await asyncio.to_thread(database.clear_entire_league)
    
    await update.message.reply_text(
        "✅ **Все матчи и игроки успешно удалены!**\n\nБаза данных очищена (за исключением администраторов). Вы можете добавлять новый список участников и генерировать расписание заново.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Управление участниками", callback_data="admin_manage_players_info")]]),
        parse_mode="Markdown"
    )
    
    # Notify group if configured
    group_id = await asyncio.to_thread(database.get_group_id)
    if group_id:
        try:
            await context.bot.send_message(
                chat_id=group_id,
                text="📢 **Лига сброшена администратором!**\n\nВсе игроки и расписание матчей были очищены.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.exception("Не удалось отправить уведомление в группу")
            
    return ConversationHandler.END


@admin_only
async def admin_remove_player_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to remove a player by @username."""
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
        
    args = context.args
    if not args:
        await update.message.reply_text("❌ Использование: `/remove_player @username`", parse_mode="Markdown")
        return
        
    target = args[0].strip()
    success, msg = await asyncio.to_thread(database.remove_player, target)
    if success:
        await update.message.reply_text(f"✅ {msg}", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Ошибка: {msg}")


@admin_only
async def admin_list_players_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command to list all 16 clubs and who is assigned to them."""
    user = update.effective_user
    if not user:
        return
        
    # Get active mapping of club to player row
    club_to_player = {u["team_name"].lower(): u for u in (await asyncio.to_thread(database.list_users)) if u["team_name"]}
    
    lines = ["📋 <b>Текущий состав участников и клубов:</b>\n"]
    for club in CLUBS:
        user_row = club_to_player.get(club.lower())
        if user_row:
            status = "✅" if user_row["telegram_id"] > 0 else "⏳ ждёт старта"
            lines.append(f"🔴 <b>{club}</b> — @{user_row['username']} ({status})")
        else:
            lines.append(f"🟢 <b>{club}</b> — <i>свободен</i>")
            
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# --- Conversation state for squad upload ---
ADMIN_EXPECT_SQUAD_TEXT = 201
ADMIN_EXPECT_SINGLE_PLAYER = 202


@admin_only
async def admin_manage_players_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the interactive player management menu."""
    query = update.callback_query
    if query:
        await query.answer()
    
    user_id = query.from_user.id if query else update.effective_user.id
    if not is_admin(user_id):
        if query: await query.answer("⛔ Доступ запрещён", show_alert=True)
        return

    users = await asyncio.to_thread(database.list_users)
    total_count = len(users)

    text = (
        f"👥 <b>Управление игроками лиги</b>\n\n"
        f"Зарегистрировано участников: <b>{total_count}</b>\n\n"
        f"Выберите действие в меню ниже:"
    )

    keyboard = [
        [InlineKeyboardButton("📋 Список участников", callback_data="admin_list_players_page_0")],
        [InlineKeyboardButton("➕ Добавить игрока", callback_data="admin_add_player_start")],
        [InlineKeyboardButton("📥 Массовый импорт (списком)", callback_data="admin_import_players_start")],
        [InlineKeyboardButton("🔄 Сбросить варны (новый сезон)", callback_data="admin_reset_season_warns")],
        [InlineKeyboardButton("🗑 Очистить всю лигу", callback_data="admin_clear_league_start")],
        [InlineKeyboardButton("« Назад в админ-панель", callback_data="admin_main_menu")]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    elif update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)

@admin_only
async def admin_list_players_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int | None = None) -> None:
    """Paginated list of players with inline buttons for each player."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()

    if page is None:
        page = 0
        if query.data and query.data.startswith("admin_list_players_page_"):
            try:
                page = int(query.data.replace("admin_list_players_page_", ""))
            except ValueError:
                page = 0

    users = await asyncio.to_thread(database.list_users)
    if not users:
        keyboard = [
            [InlineKeyboardButton("➕ Добавить игрока", callback_data="admin_add_player_start")],
            [InlineKeyboardButton("« Назад", callback_data="admin_manage_players_info")]
        ]
        await query.edit_message_text("👥 <b>Участники не найдены.</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    PER_PAGE = 6
    total_pages = (len(users) + PER_PAGE - 1) // PER_PAGE
    if page < 0: page = 0
    if page >= total_pages: page = total_pages - 1

    start_idx = page * PER_PAGE
    page_users = users[start_idx : start_idx + PER_PAGE]

    keyboard = []
    for u in page_users:
        u_name = f"@{u['username']}" if u['username'] else f"ID: {u['telegram_id']}"
        team_name = u['team_name'] or 'Без клуба'
        btn_text = f"👤 {u_name} — {team_name}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"admin_view_player_{u['telegram_id']}")])

    # Pagination row
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"admin_list_players_page_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"{page + 1} / {total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Вперед ➡️", callback_data=f"admin_list_players_page_{page + 1}"))
    keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("➕ Добавить игрока", callback_data="admin_add_player_start")])
    keyboard.append([InlineKeyboardButton("« Назад в меню", callback_data="admin_manage_players_info")])

    text = f"📋 <b>Список участников лиги (Стр. {page + 1}/{total_pages}):</b>\n\nВыберите игрока для управления:"
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

@admin_only
async def admin_view_player(update: Update, context: ContextTypes.DEFAULT_TYPE, player_id: int | None = None) -> None:
    """View detailed player card with inline action buttons."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()

    if player_id is None:
        p_id = int(query.data.replace("admin_view_player_", ""))
    else:
        p_id = player_id
    player = await asyncio.to_thread(database.get_user, p_id)

    if not player:
        keyboard = [[InlineKeyboardButton("« К списку участников", callback_data="admin_list_players_page_0")]]
        await query.edit_message_text("❌ Игрок не найден.", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    username_str = f"@{player['username']}" if player['username'] else "(без юзернейма)"
    team_str = player['team_name'] or 'Без клуба'
    role_str = "Администратор" if player['role'] == 'admin' else "Игрок"
    warn_count = player.get('warn_count', 0) if isinstance(player, dict) else player['warn_count']

    text = (
        f"👤 <b>Карточка участника:</b>\n\n"
        f"• <b>Telegram:</b> {html.escape(username_str)}\n"
        f"• <b>Клуб:</b> {html.escape(team_str)}\n"
        f"• <b>Telegram ID:</b> <code>{player['telegram_id']}</code>\n"
        f"• <b>Роль:</b> {role_str}\n"
        f"• <b>Варны:</b> {warn_count} / {MAX_WARNS_LIMIT}\n"
    )

    keyboard = [
        [InlineKeyboardButton("✏️ Изменить клуб", callback_data=f"admin_edit_club_select_{p_id}")],
        [InlineKeyboardButton("✏️ Изменить юзернейм", callback_data=f"admin_edit_username_start_{p_id}")],
        [
            InlineKeyboardButton("➕ Выдать варн", callback_data=f"warn_add_{p_id}"),
            InlineKeyboardButton("➖ Снять варн", callback_data=f"warn_remove_{p_id}")
        ],
        [
            InlineKeyboardButton("📜 История варнов", callback_data=f"warn_hist_{p_id}"),
            InlineKeyboardButton("🕊 Амнистия", callback_data=f"warn_amnesty_{p_id}")
        ],
        [InlineKeyboardButton("🗑 Исключить из лиги", callback_data=f"admin_delete_player_confirm_{p_id}")],
        [InlineKeyboardButton("« К списку участников", callback_data="admin_list_players_page_0")]
    ]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

@admin_only
async def admin_edit_club_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show grid of inline buttons for all CLUBS to edit player's club."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()

    p_id = int(query.data.replace("admin_edit_club_select_", ""))
    player = await asyncio.to_thread(database.get_user, p_id)

    if not player:
        keyboard = [[InlineKeyboardButton("« К списку", callback_data="admin_list_players_page_0")]]
        await query.edit_message_text("❌ Игрок не найден.", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    club_to_player = {u["team_name"].lower(): u["username"] for u in (await asyncio.to_thread(database.list_users)) if u["team_name"]}

    keyboard = []
    row = []

    for club_idx, club in enumerate(CLUBS):
        occupied_by = club_to_player.get(club.lower())
        if player['team_name'] and player['team_name'].lower() == club.lower():
            btn_text = f"⭐ {club} (текущий)"
        elif occupied_by:
            btn_text = f"🔴 {club} (@{occupied_by})"
        else:
            btn_text = f"🟢 {club} (свободен)"

        # Use club index instead of full club name to stay under Telegram's 64-byte callback_data limit
        row.append(InlineKeyboardButton(btn_text, callback_data=f"admin_eclub_{p_id}_{club_idx}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data=f"admin_view_player_{p_id}")])

    text = (
        f"⚽ <b>Выберите новый клуб для игрока {html.escape('@' + player['username'] if player['username'] else str(p_id))}:</b>\n\n"
        f"<i>(Клик по кнопке моментально сменит клуб)</i>"
    )
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

@admin_only
async def admin_edit_club_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute club change via inline button click."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    # Parse: admin_eclub_{p_id}_{club_idx}
    data_parts = query.data.replace("admin_eclub_", "").split("_", 1)
    if len(data_parts) != 2:
        await query.answer()
        return
    p_id = int(data_parts[0])
    club_idx = int(data_parts[1])
    if club_idx < 0 or club_idx >= len(CLUBS):
        await query.answer("❌ Неверный индекс клуба.", show_alert=True)
        return
    new_club = CLUBS[club_idx]

    success, msg = await asyncio.to_thread(database.set_player_club, str(p_id), new_club)
    # Use single query.answer() with the result message to avoid BadRequest: query already answered
    await query.answer(f"✅ {msg}" if success else f"❌ {msg}", show_alert=True)

    await admin_view_player(update, context, player_id=p_id)

@admin_only
async def admin_delete_player_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show confirmation screen before deleting a player."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()

    p_id = int(query.data.replace("admin_delete_player_confirm_", ""))
    player = await asyncio.to_thread(database.get_user, p_id)

    if not player:
        keyboard = [[InlineKeyboardButton("« К списку", callback_data="admin_list_players_page_0")]]
        await query.edit_message_text("❌ Игрок не найден.", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    username_str = f"@{player['username']}" if player['username'] else f"ID: {p_id}"
    team_str = player['team_name'] or 'Без клуба'

    text = (
        f"⚠️ <b>Исключение игрока из лиги</b>\n\n"
        f"Вы действительно хотите исключить <b>{html.escape(username_str)}</b> (Клуб: <b>{html.escape(team_str)}</b>)?\n\n"
        f"Клуб <b>{html.escape(team_str)}</b> освободится для нового участника. Матчи останутся несыгранными."
    )

    keyboard = [
        [InlineKeyboardButton("✅ Да, исключить", callback_data=f"admin_delete_player_execute_{p_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"admin_view_player_{p_id}")]
    ]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

@admin_only
async def admin_delete_player_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute player deletion and notify reports topic."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    await query.answer()

    p_id = int(query.data.replace("admin_delete_player_execute_", ""))
    player = await asyncio.to_thread(database.get_user, p_id)

    if not player:
        await admin_list_players_page(update, context, page=0)
        return

    username_str = f"@{player['username']}" if player['username'] else f"ID: {p_id}"
    team_str = player['team_name'] or 'без названия'

    success, msg = await asyncio.to_thread(database.remove_player, str(p_id))

    # Send notice to Reports Topic
    main_group_id = await asyncio.to_thread(database.get_group_id)
    reports_topic_id = await asyncio.to_thread(database.get_config, "reports_topic_id")
    if main_group_id:
        try:
            notice_text = (
                f"📢 <b>Изменение состава лиги!</b>\n\n"
                f"Игрок <b>{html.escape(username_str)}</b> покинул клуб <b>{html.escape(team_str)}</b>.\n"
                f"Клуб свободен и ждёт нового владельца!"
            )
            kwargs = {"chat_id": main_group_id, "text": notice_text, "parse_mode": "HTML"}
            if reports_topic_id:
                kwargs["message_thread_id"] = int(reports_topic_id)
            await context.bot.send_message(**kwargs)
        except Exception as e:
            logger.exception("Failed to post player deletion notice to reports topic")

    await query.answer(f"✅ {msg}", show_alert=True)
    await admin_list_players_page(update, context, page=0)


@admin_only
async def admin_manage_squads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show list of 16 clubs for squad management."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Доступ запрещён", show_alert=True)
        return

    keyboard = []
    row = []
    for club in CLUBS:
        row.append(InlineKeyboardButton(club, callback_data=f"admin_squad_view_{club}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🖼 Загрузить фото игроков", callback_data="admin_fetch_photos_cb")])
    keyboard.append([InlineKeyboardButton("➕ Добавить во все клубы игроков из матчей", callback_data="admin_squad_add_missing_all")])
    keyboard.append([InlineKeyboardButton("« Назад в админку", callback_data="admin_main_menu")])

    text = "📋 <b>Составы команд</b>\n\nВыберите клуб для просмотра и управления составом:"
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def admin_view_squad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View squad for a specific club."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Доступ запрещён", show_alert=True)
        return

    club = query.data.replace("admin_squad_view_", "")
    squad = await asyncio.to_thread(database.get_squad, club)

    if squad:
        lines = [f"👥 <b>Состав команды {html.escape(club)}:</b>\n"]
        for i, name in enumerate(squad, 1):
            lines.append(f"{i}. {html.escape(name)}")
        text = "\n".join(lines)
    else:
        text = f"👥 <b>Состав команды {html.escape(club)}:</b>\n\n<i>Состав пуст.</i>"

    keyboard = [
        [InlineKeyboardButton("📊 Загрузить состав", callback_data=f"admin_squad_upload_{club}")],
        [InlineKeyboardButton("➕ Добавить игрока", callback_data=f"admin_squad_add_player_{club}")],
        [InlineKeyboardButton("➕ Добавить игроков из матчей", callback_data=f"admin_squad_add_missing_{club}")],
        [InlineKeyboardButton("🗑️ Очистить состав", callback_data=f"admin_squad_clear_{club}")],
        [InlineKeyboardButton("« Назад к клубам", callback_data="admin_manage_squads")]
    ]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def admin_squad_upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start squad upload: ask admin to send player names."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Доступ запрещён", show_alert=True)
        return ConversationHandler.END

    club = query.data.replace("admin_squad_upload_", "")
    context.user_data["admin_squad_club"] = club

    text = (
        f"📊 <b>Загрузка состава для {html.escape(club)}</b>\n\n"
        "Отправьте список футболистов, каждый с новой строки.\n\n"
        "Пример:\n"
        "<code>Viktor Gyökeres\n"
        "Francisco Trincão\n"
        "Pedro Gonçalves</code>"
    )
    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="admin_manage_squads")]]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADMIN_EXPECT_SQUAD_TEXT


@admin_only
async def admin_squad_upload_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive player names and add them to the squad."""
    club = context.user_data.pop("admin_squad_club", None)
    if not club:
        await update.message.reply_text("❌ Ошибка: не найден клуб. Попробуйте снова.")
        return ConversationHandler.END

    lines = update.message.text.strip().split("\n")
    player_names = [html.escape(line.strip()) for line in lines if line.strip()]

    if not player_names:
        await update.message.reply_text("❌ Список пуст. Отправьте хотя бы одного игрока.")
        return ADMIN_EXPECT_SQUAD_TEXT

    added = await asyncio.to_thread(database.add_squad, club, [line.strip() for line in lines if line.strip()])

    text = f"✅ Добавлено <b>{added}</b> футболистов в состав команды <b>{html.escape(club)}</b>."
    keyboard = [[InlineKeyboardButton("👥 Просмотреть состав", callback_data=f"admin_squad_view_{club}")]]
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END


@admin_only
async def admin_squad_add_player_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start single-player add: ask admin for the player's name."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Доступ запрещён", show_alert=True)
        return ConversationHandler.END

    club = query.data.replace("admin_squad_add_player_", "")
    context.user_data["admin_squad_club"] = club

    text = (
        f"➕ <b>Добавление игрока в {html.escape(club)}</b>\n\n"
        "Отправьте имя футболиста одним сообщением."
    )
    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data=f"admin_squad_view_{club}")]]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADMIN_EXPECT_SINGLE_PLAYER


@admin_only
async def admin_squad_add_player_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive a single player name and add it to the squad."""
    club = context.user_data.pop("admin_squad_club", None)
    if not club:
        await update.message.reply_text("❌ Ошибка: не найден клуб. Попробуйте снова.")
        return ConversationHandler.END

    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("❌ Имя пустое. Отправьте имя игрока.")
        return ADMIN_EXPECT_SINGLE_PLAYER

    added = await asyncio.to_thread(database.add_squad, club, [name])

    if added:
        text = f"✅ Игрок <b>{html.escape(name)}</b> добавлен в состав команды <b>{html.escape(club)}</b>."
    else:
        text = f"ℹ️ Игрок <b>{html.escape(name)}</b> уже есть в составе команды <b>{html.escape(club)}</b> или имя некорректно."
    keyboard = [[InlineKeyboardButton("👥 Просмотреть состав", callback_data=f"admin_squad_view_{club}")]]
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END


@admin_only
async def admin_squad_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear all players from a club's squad."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Доступ запрещён", show_alert=True)
        return

    club = query.data.replace("admin_squad_clear_", "")
    deleted = await asyncio.to_thread(database.clear_squad, club)

    text = f"🗑️ Состав команды <b>{html.escape(club)}</b> очищен. Удалено игроков: <b>{deleted}</b>."
    keyboard = [[InlineKeyboardButton("« Назад к клубам", callback_data="admin_manage_squads")]]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def admin_squad_add_missing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add players that appear in match events but are missing from a club's squad."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Доступ запрещён", show_alert=True)
        return

    data = query.data
    if data == "admin_squad_add_missing_all":
        added = await asyncio.to_thread(database.add_missing_squad_players)
        text = f"✅ Во все клубы добавлено игроков из матчей: <b>{added}</b>."
        back_data = "admin_manage_squads"
    else:
        club = data.replace("admin_squad_add_missing_", "")
        missing = await asyncio.to_thread(database.get_missing_squad_players, club)
        if not missing:
            text = f"✅ В составе <b>{html.escape(club)}</b> нет игроков из матчей, отсутствующих в составе."
            keyboard = [[InlineKeyboardButton("« Назад к клубам", callback_data="admin_manage_squads")]]
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        added = await asyncio.to_thread(database.add_missing_squad_players, club)
        lines = [f"➕ Добавлено <b>{added}</b> игроков из матчей в состав <b>{html.escape(club)}</b>:\n"]
        for name in missing:
            lines.append(f"• {html.escape(name)}")
        text = "\n".join(lines)
        back_data = f"admin_squad_view_{club}"

    keyboard = [[InlineKeyboardButton("« Назад", callback_data=back_data)]]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def admin_stub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stub for admin features under development."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Доступ запрещён", show_alert=True)
        return

    keyboard = [[InlineKeyboardButton("« Назад в админку", callback_data="admin_main_menu")]]
    text = "🚧 <b>В разработке</b>\n\nЭтот раздел находится в разработке."
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

async def notify_players_rounds_opened(context: ContextTypes.DEFAULT_TYPE, round_numbers: list[int], deadline_text: str) -> None:
    """Send personal match card notifications to players when rounds are opened."""
    matches = await asyncio.to_thread(database.get_matches_in_rounds, round_numbers)
    if not matches:
        return

    is_single_round = (len(round_numbers) == 1)

    if is_single_round:
        r_num = round_numbers[0]
        for m in matches:
            p1_id = m['player1_id']
            p2_id = m['player2_id']
            p1_team = m['player1_team'] or 'неизвестно'
            p2_team = m['player2_team'] or 'неизвестно'
            p1_user = f"@{m['player1_username']}" if m['player1_username'] else p1_team
            p2_user = f"@{m['player2_username']}" if m['player2_username'] else p2_team

            instruction_text = (
                f"\n\n📌 <b>Как внести результат:</b>\n"
                f"1. Нажмите кнопку <b>📝 Ввести результат</b>.\n"
                f"2. Выберите <b>⚡ Автоматический ввод (по фото)</b>.\n"
                f"3. Отправьте боту от 1 до 3 скриншотов статистики из игры.\n"
                f"4. ИИ автоматически распознает счёт, авторов голов и ассистов.\n"
                f"5. Проверьте данные и нажмите <b>✅ Всё верно</b> — результат сразу автоматически подтверждается и заносится в турнирную таблицу лиги!"
            )

            # Home player card
            if p1_id:
                text_h = (
                    f"🏟 <b>ВАШ МАТЧ | Тур {r_num}</b>\n\n"
                    f"🏠 <b>Вы ({html.escape(p1_team)})</b> -:- <b>{html.escape(p2_team)} ({html.escape(p2_user)})</b> ✈️\n\n"
                    f"⏳ <b>Дедлайн:</b> {deadline_text}\n"
                    f"📌 <b>Статус:</b> Вы играете Дома."
                    f"{instruction_text}"
                )
                kb_h = [
                    [InlineKeyboardButton("📝 Ввести результат", callback_data=f"cabinet_report_score_{m['id']}")],
                    [InlineKeyboardButton("👀 Состав соперника", callback_data=f"cabinet_view_squad_{p2_id}")]
                ]
                await safe_send_notification(context.bot, p1_id, text_h, InlineKeyboardMarkup(kb_h))

            # Away player card
            if p2_id:
                text_a = (
                    f"🏟 <b>ВАШ МАТЧ | Тур {r_num}</b>\n\n"
                    f"🏠 <b>{html.escape(p1_team)} ({html.escape(p1_user)})</b> -:- <b>Вы ({html.escape(p2_team)})</b> ✈️\n\n"
                    f"⏳ <b>Дедлайн:</b> {deadline_text}\n"
                    f"📌 <b>Статус:</b> Вы играете в Гостях."
                    f"{instruction_text}"
                )
                kb_a = [
                    [InlineKeyboardButton("📝 Ввести результат", callback_data=f"cabinet_report_score_{m['id']}")],
                    [InlineKeyboardButton("👀 Состав соперника", callback_data=f"cabinet_view_squad_{p1_id}")]
                ]
                await safe_send_notification(context.bot, p2_id, text_a, InlineKeyboardMarkup(kb_a))

    else:
        # Multi-round combined notification per player
        player_matches = {}
        for m in matches:
            for pid in (m['player1_id'], m['player2_id']):
                if pid:
                    if pid not in player_matches:
                        player_matches[pid] = []
                    player_matches[pid].append(m)

        r_min, r_max = min(round_numbers), max(round_numbers)

        for pid, p_m_list in player_matches.items():
            lines = [
                f"🏟 <b>ВАШИ МАТЧИ В ОТКРЫТЫХ ТУРАХ (Туры {r_min}-{r_max})</b>\n",
                f"⏳ <b>Общий дедлайн:</b> {deadline_text}",
                "────────────────────────\n"
            ]

            for m in p_m_list:
                p1_team = m['player1_team'] or 'неизвестно'
                p2_team = m['player2_team'] or 'неизвестно'
                p1_user = f"@{m['player1_username']}" if m['player1_username'] else p1_team
                p2_user = f"@{m['player2_username']}" if m['player2_username'] else p2_team

                if m['player1_id'] == pid:
                    lines.append(f"📌 <b>Тур {m['round_number']} (Дома 🏠):</b>")
                    lines.append(f"🏠 <b>Вы ({html.escape(p1_team)})</b> -:- <b>{html.escape(p2_team)} ({html.escape(p2_user)})</b> ✈️\n")
                else:
                    lines.append(f"📌 <b>Тур {m['round_number']} (В гостях ✈️):</b>")
                    lines.append(f"🏠 <b>{html.escape(p1_team)} ({html.escape(p1_user)})</b> -:- <b>Вы ({html.escape(p2_team)})</b> ✈️\n")

            lines.append("────────────────────────")
            lines.append("👇 <i>Все матчи доступны в Личном кабинете в разделе «📋 Мои матчи»!</i>")

            kb = [
                [InlineKeyboardButton("📋 Перейти к матчам", callback_data="cabinet_my_matches")],
                [InlineKeyboardButton("👤 Личный кабинет", callback_data="menu_cabinet")]
            ]

            try:
                await context.bot.send_message(chat_id=pid, text="\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
            except Exception as e:
                logger.exception("Failed to send multi-match card to player {pid}")

async def send_round_reminders(
    context: ContextTypes.DEFAULT_TYPE, 
    round_number: int, 
    time_left_str: str | None = None,
    target_match_ids: set[int] | list[int] | None = None
) -> tuple[int, int]:
    """
    Send match reminders for unplayed matches in a round.
    Sends PM to unplayed match participants and a summary to Reports topic.
    Returns (sent_pm_count, unplayed_matches_count).
    """
    unplayed = await asyncio.to_thread(database.get_unplayed_matches_by_round, round_number)
    round_info = await asyncio.to_thread(database.get_round_info, round_number)
    deadline_text = round_info["deadline"] if round_info and round_info.get("deadline") else "не указан"

    if target_match_ids is not None:
        target_set = set(target_match_ids)
        unplayed = [m for m in unplayed if m['id'] in target_set]

    if not unplayed:
        return (0, 0)

    time_hdr = f" (Осталось: {time_left_str})" if time_left_str else ""
    pm_sent = 0

    # 1. PM to each player with unplayed match
    for m in unplayed:
        p1_id = m['player1_id']
        p2_id = m['player2_id']
        p1_team = m['player1_team'] or 'неизвестно'
        p2_team = m['player2_team'] or 'неизвестно'
        p1_user = f"@{m['player1_username']}" if m['player1_username'] else p1_team
        p2_user = f"@{m['player2_username']}" if m['player2_username'] else p2_team

        instruction_text = (
            f"\n\n📌 <b>Инструкция по внесению результата:</b>\n"
            f"1. Нажмите кнопку <b>📝 Ввести результат</b>.\n"
            f"2. Выберите <b>⚡ Автоматический ввод (по фото)</b>.\n"
            f"3. Отправьте боту от 1 до 3 скриншотов статистики из игры.\n"
            f"4. ИИ автоматически распознает счёт, авторов голов и ассистов.\n"
            f"5. Проверьте данные и нажмите <b>✅ Всё верно</b> — результат сразу автоматически подтверждается и заносится в турнирную таблицу лиги!"
        )

        if p1_id:
            text_h = (
                f"⏰ <b>НАПОМИНАНИЕ О МАТЧЕ | Тур {round_number}</b>{time_hdr}\n\n"
                f"🏠 <b>Вы ({html.escape(p1_team)})</b> -:- <b>{html.escape(p2_team)} ({html.escape(p2_user)})</b> ✈️\n\n"
                f"⏳ <b>Дедлайн:</b> {deadline_text}"
                f"{instruction_text}"
            )
            kb_h = [
                [InlineKeyboardButton("📝 Ввести результат", callback_data=f"cabinet_report_score_{m['id']}")],
                [InlineKeyboardButton("📋 Мои матчи", callback_data="cabinet_my_matches")]
            ]
            if await safe_send_notification(context.bot, p1_id, text_h, InlineKeyboardMarkup(kb_h)):
                pm_sent += 1

        if p2_id:
            text_a = (
                f"⏰ <b>НАПОМИНАНИЕ О МАТЧЕ | Тур {round_number}</b>{time_hdr}\n\n"
                f"🏠 <b>{html.escape(p1_team)} ({html.escape(p1_user)})</b> -:- <b>Вы ({html.escape(p2_team)})</b> ✈️\n\n"
                f"⏳ <b>Дедлайн:</b> {deadline_text}"
                f"{instruction_text}"
            )
            kb_a = [
                [InlineKeyboardButton("📝 Ввести результат", callback_data=f"cabinet_report_score_{m['id']}")],
                [InlineKeyboardButton("📋 Мои матчи", callback_data="cabinet_my_matches")]
            ]
            if await safe_send_notification(context.bot, p2_id, text_a, InlineKeyboardMarkup(kb_a)):
                pm_sent += 1

    # 2. Public summary to Reports Topic
    main_group_id = await asyncio.to_thread(database.get_group_id)
    reports_topic_id = await asyncio.to_thread(database.get_config, "reports_topic_id")
    if main_group_id:
        lines = [
            f"⏰ <b>НАПОМИНАНИЕ! Тур {round_number}</b>{time_hdr}\n",
            f"Несыгранные матчи ({len(unplayed)}):"
        ]
        for m in unplayed:
            p1_team = m['player1_team'] or 'неизвестно'
            p2_team = m['player2_team'] or 'неизвестно'
            p1_user = f"@{m['player1_username']}" if m['player1_username'] else p1_team
            p2_user = f"@{m['player2_username']}" if m['player2_username'] else p2_team
            lines.append(f"• 🏠 <b>{html.escape(p1_team)}</b> ({html.escape(p1_user)}) -:- <b>{html.escape(p2_team)}</b> ({html.escape(p2_user)}) ✈️")

        lines.append(f"\n🕒 <b>Дедлайн:</b> {deadline_text}")
        lines.append("Пожалуйста, поторопитесь сыграть свои матчи до истечения срока!")

        try:
            kwargs = {"chat_id": main_group_id, "text": "\n".join(lines), "parse_mode": "HTML"}
            if reports_topic_id:
                kwargs["message_thread_id"] = int(reports_topic_id)
            await context.bot.send_message(**kwargs)
        except Exception as e:
            logger.exception("Failed to post reminder summary to group")

    return (pm_sent, len(unplayed))

@admin_only
async def admin_remind_round(update: Update, context: ContextTypes.DEFAULT_TYPE, round_number: int | None = None) -> None:
    """Display match selection UI for sending round reminders."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    try:
        await query.answer()
    except Exception:
        pass

    if round_number is None:
        round_number = int(query.data.replace("admin_remind_round_", ""))

    unplayed = await asyncio.to_thread(database.get_unplayed_matches_by_round, round_number)
    if not unplayed:
        keyboard = [[InlineKeyboardButton("« Назад к туру", callback_data=f"admin_manage_round_{round_number}")]]
        try:
            await query.edit_message_text("🎉 В этом туре нет несыгранных матчей!", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            await context.bot.send_message(chat_id=query.from_user.id, text="🎉 В этом туре нет несыгранных матчей!", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    selected_key = f"remind_selected_{round_number}"
    if selected_key not in context.user_data:
        context.user_data[selected_key] = {m['id'] for m in unplayed}

    selected_ids = context.user_data[selected_key]

    text = (
        f"🔔 <b>Выбор матчей для отправки напоминаний (Тур {round_number})</b>\n\n"
        f"Отметьте матчи участников, которым нужно отправить напоминание о дедлайне:"
    )

    keyboard = []
    for m in unplayed:
        m_id = m['id']
        is_checked = m_id in selected_ids
        icon = "✅" if is_checked else "⬜️"
        p1 = html.escape(m['player1_team'] or m['player1_nickname'] or "Хозяева")
        p2 = html.escape(m['player2_team'] or m['player2_nickname'] or "Гости")
        btn_text = f"{icon} {p1} vs {p2}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"admin_toggle_remind_match_{round_number}_{m_id}")])

    all_checked = (len(selected_ids) == len(unplayed))
    toggle_all_btn = "⏹ Снять все" if all_checked else "☑️ Выбрать все"
    keyboard.append([InlineKeyboardButton(toggle_all_btn, callback_data=f"admin_toggle_remind_all_{round_number}")])

    count_selected = len(selected_ids)
    if count_selected > 0:
        keyboard.append([InlineKeyboardButton(f"🚀 Отправить напоминания ({count_selected})", callback_data=f"admin_send_selected_reminders_{round_number}")])

    keyboard.append([InlineKeyboardButton("« Назад к туру", callback_data=f"admin_manage_round_{round_number}")])
    markup = InlineKeyboardMarkup(keyboard)

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

@admin_only
async def admin_toggle_remind_match(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle a single match selection for reminder dispatch."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    try:
        await query.answer()
    except Exception:
        pass

    parts = query.data.replace("admin_toggle_remind_match_", "").split("_")
    if len(parts) != 2:
        return
    round_number = int(parts[0])
    match_id = int(parts[1])

    selected_key = f"remind_selected_{round_number}"
    unplayed = await asyncio.to_thread(database.get_unplayed_matches_by_round, round_number)
    unplayed_ids = {m['id'] for m in unplayed}

    selected_ids = context.user_data.setdefault(selected_key, set(unplayed_ids))

    if match_id in selected_ids:
        selected_ids.remove(match_id)
    else:
        selected_ids.add(match_id)

    context.user_data[selected_key] = selected_ids
    await admin_remind_round(update, context, round_number=round_number)

@admin_only
async def admin_toggle_remind_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle select all / deselect all matches for reminder dispatch."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    try:
        await query.answer()
    except Exception:
        pass

    round_number = int(query.data.replace("admin_toggle_remind_all_", ""))
    unplayed = await asyncio.to_thread(database.get_unplayed_matches_by_round, round_number)
    unplayed_ids = {m['id'] for m in unplayed}

    selected_key = f"remind_selected_{round_number}"
    selected_ids = context.user_data.get(selected_key, set())

    if len(selected_ids) == len(unplayed_ids):
        context.user_data[selected_key] = set()
    else:
        context.user_data[selected_key] = set(unplayed_ids)

    await admin_remind_round(update, context, round_number=round_number)

@admin_only
async def admin_send_selected_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send reminders to only the selected matches."""
    query = update.callback_query
    if not query or not is_admin(query.from_user.id):
        return
    try:
        await query.answer()
    except Exception:
        pass

    round_number = int(query.data.replace("admin_send_selected_reminders_", ""))
    selected_key = f"remind_selected_{round_number}"
    selected_ids = context.user_data.get(selected_key, set())

    if not selected_ids:
        await context.bot.send_message(chat_id=query.from_user.id, text="⚠️ Не выбрано ни одного матча!")
        return

    pm_sent, count_matches = await send_round_reminders(context, round_number, target_match_ids=selected_ids)

    context.user_data.pop(selected_key, None)

    text = (
        f"✅ <b>Напоминания успешно отправлены!</b>\n\n"
        f"🏟 Выбранных матчей: {count_matches}\n"
        f"📨 Игроков оповещено в ЛС: {pm_sent}"
    )

    keyboard = [[InlineKeyboardButton("« Вернуться к туру", callback_data=f"admin_manage_round_{round_number}")]]
    markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        await context.bot.send_message(chat_id=query.from_user.id, text=text, reply_markup=markup, parse_mode="HTML")

async def job_check_deadlines_and_remind(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic job checking open rounds for approaching deadlines and sending automated reminders."""
    open_rounds = await asyncio.to_thread(database.get_open_rounds_with_deadlines)
    if not open_rounds:
        return

    now = datetime.datetime.now()

    for r in open_rounds:
        r_num = r["round_number"]
        dl_str = r["deadline"]

        try:
            dl_dt = datetime.datetime.strptime(dl_str, "%d.%m.%Y %H:%M")
        except ValueError:
            continue

        time_diff = dl_dt - now
        hours_left = time_diff.total_seconds() / 3600.0

        if hours_left <= 0:
            continue  # Deadline already passed

        # 24h reminder (between 23h and 25h left)
        if 23.0 <= hours_left <= 25.0:
            if not (await asyncio.to_thread(database.has_reminder_been_sent, r_num, "24h")):
                await send_round_reminders(context, r_num, time_left_str="24 часа")
                await asyncio.to_thread(database.record_reminder_sent, r_num, "24h")

        # 6h reminder (between 5h and 7h left)
        elif 5.0 <= hours_left <= 7.0:
            if not (await asyncio.to_thread(database.has_reminder_been_sent, r_num, "6h")):
                await send_round_reminders(context, r_num, time_left_str="6 часов")
                await asyncio.to_thread(database.record_reminder_sent, r_num, "6h")

        # 1h reminder (between 0.5h and 1.5h left)
        elif 0.5 <= hours_left <= 1.5:
            if not (await asyncio.to_thread(database.has_reminder_been_sent, r_num, "1h")):
                await send_round_reminders(context, r_num, time_left_str="1 час! 🚨")
                await asyncio.to_thread(database.record_reminder_sent, r_num, "1h")

async def job_post_debts_to_warns(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic job (every 12 hours) posting/updating the debts summary in the ПРЕДЫ thread."""
    await _post_or_update_debts_in_warns(context)


@admin_only
async def admin_set_squad_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set the topic where squads will be sent."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Нет прав.")
        return
        
    thread_id = update.message.message_thread_id
    if not thread_id:
        await update.message.reply_text("⚠️ Вызовите команду внутри топика (ветки), куда хотите получать составы.")
        return
        
    await asyncio.to_thread(database.set_config, "squad_topic_id", str(thread_id))
    await update.message.reply_text(f"✅ Топик для составов успешно установлен (ID: {thread_id}). Теперь составы будут присылаться сюда.")

@admin_only
async def admin_set_reports_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set the topic for reports/announcements."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Нет прав.")
        return
        
    thread_id = update.message.message_thread_id
    if not thread_id:
        await update.message.reply_text("⚠️ Вызовите команду внутри топика «Отчёты», куда хотите получать важные уведомления.")
        return
        
    await asyncio.to_thread(database.set_config, "reports_topic_id", str(thread_id))
    await update.message.reply_text(f"✅ Тема «Отчёты» успешно установлена (ID: {thread_id}). Анонсы туров и важные новости будут присылаться сюда!")

@admin_only
async def admin_set_results_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set the topic for match results."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Нет прав.")
        return
        
    thread_id = update.message.message_thread_id
    if not thread_id:
        await update.message.reply_text("⚠️ Вызовите команду внутри топика «Результаты», куда хотите получать результаты матчей.")
        return
        
    await asyncio.to_thread(database.set_config, "results_topic_id", str(thread_id))
    await update.message.reply_text(f"✅ Тема «Результаты» успешно установлена (ID: {thread_id}). Все результаты матчей с авторами голов и ассистов будут публиковаться сюда!")


@admin_only
async def admin_set_warns_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set the topic for warnings (ПРЕДЫ)."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Нет прав.")
        return

    thread_id = update.message.message_thread_id
    if not thread_id:
        await update.message.reply_text("⚠️ Вызовите команду внутри топика «ПРЕДЫ», куда хотите получать уведомления о варнах.")
        return

    await asyncio.to_thread(database.set_config, "warns_topic_id", str(thread_id))
    await update.message.reply_text(f"✅ Тема «ПРЕДЫ» успешно установлена (ID: {thread_id}). Уведомления о предупреждениях будут присылаться сюда!")


@admin_only
async def admin_fetch_photos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /fetch_photos or admin_fetch_photos_cb — download and cache player portraits
    from hybrid free providers. Skips players that are already cached.
    """
    query = update.callback_query
    if query:
        await query.answer()

    unique_players = await asyncio.to_thread(database.get_all_unique_players)

    already_cached = [item for item in unique_players if player_photos.is_cached(item[0], item[1])]
    to_fetch       = [item for item in unique_players if not player_photos.is_cached(item[0], item[1])]

    text_initial = (
        f"✅ Все {len(already_cached)} игроков уже имеют кэшированные фото."
        if not to_fetch else
        f"⏳ Загружаю фото для <b>{len(to_fetch)}</b> игроков (уже есть: {len(already_cached)})..."
    )

    if not to_fetch:
        if query:
            await query.edit_message_text(text_initial, parse_mode="HTML")
        elif update.message:
            await update.message.reply_text(text_initial, parse_mode="HTML")
        return

    if query:
        status_msg = await query.edit_message_text(text_initial, parse_mode="HTML")
    elif update.message:
        status_msg = await update.message.reply_text(text_initial, parse_mode="HTML")
    else:
        return

    ok_count   = 0
    fail_count = 0
    failed_names: list[str] = []

    for i, (name, team) in enumerate(to_fetch, 1):
        result = await asyncio.to_thread(player_photos.fetch_and_cache, name, team)

        if result:
            ok_count += 1
        else:
            fail_count += 1
            failed_names.append(f"{name} ({team})")

        # Update progress every 5 players
        if i % 5 == 0 or i == len(to_fetch):
            try:
                await status_msg.edit_text(
                    f"⏳ Прогресс: {i}/{len(to_fetch)} — "
                    f"✅ {ok_count} загружено, ❌ {fail_count} не найдено",
                    parse_mode="HTML"
                )
            except Exception:
                pass

    result_text = (
        f"✅ <b>Готово!</b>\n\n"
        f"Загружено: <b>{ok_count}</b>\n"
        f"Не найдено: <b>{fail_count}</b>\n"
        f"Уже были: <b>{len(already_cached)}</b>"
    )
    if failed_names:
        sample = failed_names[:10]
        result_text += "\n\n<b>Не найдены:</b>\n" + "\n".join(f"• {html.escape(n)}" for n in sample)
        if len(failed_names) > 10:
            result_text += f"\n<i>...и ещё {len(failed_names) - 10}</i>"

    try:
        await status_msg.edit_text(result_text, parse_mode="HTML")
    except Exception:
        pass


# ===================== WARNS SYSTEM =====================

async def _send_to_warns_thread(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Send a message to the ПРЕДЫ thread in the group. Falls back silently."""
    group_id = GROUP_ID or await asyncio.to_thread(database.get_group_id)
    if not group_id:
        return
    warns_topic_id = await asyncio.to_thread(database.get_config, "warns_topic_id")
    kwargs = {"chat_id": group_id, "text": text, "parse_mode": "HTML"}
    if warns_topic_id:
        kwargs["message_thread_id"] = int(warns_topic_id)
    try:
        await context.bot.send_message(**kwargs)
    except Exception:
        logger.exception("Failed to send message to ПРЕДЫ thread")


async def _auto_kick_player(context: ContextTypes.DEFAULT_TYPE, user_id: int, username: str | None, team_name: str | None) -> None:
    """Ban player from league and soft-kick from group when warn limit exceeded."""
    await asyncio.to_thread(database.ban_and_remove_from_league, user_id)

    # Soft kick from Telegram group
    group_id = GROUP_ID or await asyncio.to_thread(database.get_group_id)
    if group_id:
        try:
            await context.bot.ban_chat_member(chat_id=group_id, user_id=user_id)
            await context.bot.unban_chat_member(chat_id=group_id, user_id=user_id)
        except (BadRequest, TelegramError) as e:
            logger.warning(f"Could not kick user {user_id} from group: {e}")

    # DM to player
    uname = f"@{username}" if username else f"ID {user_id}"
    team_display = html.escape(team_name or "без клуба")
    dm_text = (
        f"🚨 <b>Вы исключены из лиги!</b>\n\n"
        f"Вы получили {MAX_WARNS_LIMIT}/{MAX_WARNS_LIMIT} предупреждений.\n"
        f"Клуб <b>{team_display}</b> освобожден.\n\n"
        f"Для возвращения обратитесь к администратору."
    )
    try:
        await context.bot.send_message(chat_id=user_id, text=dm_text, parse_mode="HTML")
    except (Forbidden, TelegramError):
        logger.warning(f"Cannot DM user {user_id} about auto-kick.")

    # Public notice in ПРЕДЫ thread
    thread_text = (
        f"🚨 Игрок <b>{html.escape(uname)}</b> [{team_display}] получил "
        f"<b>{MAX_WARNS_LIMIT}/{MAX_WARNS_LIMIT}</b> предупреждений и автоматически удален из лиги и группы! Клуб освобожден."
    )
    await _send_to_warns_thread(context, thread_text)


@admin_only
async def admin_warn_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show warn reason presets before issuing a warn."""
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except BadRequest:
        pass

    p_id = int(query.data.replace("warn_add_", ""))
    player = await asyncio.to_thread(database.get_user, p_id)
    if not player:
        await query.edit_message_text("❌ Игрок не найден.")
        return

    warn_count = player['warn_count'] or 0
    username_str = f"@{player['username']}" if player['username'] else f"ID {p_id}"
    team_str = player['team_name'] or 'Без клуба'

    text = (
        f"⚠️ <b>Выдача предупреждения</b>\n\n"
        f"Игрок: <b>{html.escape(username_str)}</b> [{html.escape(team_str)}]\n"
        f"Текущий счётчик: <b>{warn_count} / {MAX_WARNS_LIMIT}</b>\n\n"
        f"Выберите причину:"
    )

    keyboard = []
    for idx, reason in enumerate(WARN_REASONS):
        keyboard.append([InlineKeyboardButton(reason, callback_data=f"warn_exec_{p_id}_{idx}")])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data=f"admin_view_player_{p_id}")])

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def admin_warn_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute warn issuance with debounce protection."""
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except BadRequest:
        pass

    # Parse: warn_exec_{p_id}_{reason_idx}
    parts = query.data.replace("warn_exec_", "").rsplit("_", 1)
    if len(parts) != 2:
        return
    p_id = int(parts[0])
    reason_idx = int(parts[1])

    # Debounce
    if p_id in _warn_action_locks:
        await query.answer("⏳ Действие уже выполняется...", show_alert=True)
        return
    _warn_action_locks.add(p_id)

    try:
        player = await asyncio.to_thread(database.get_user, p_id)
        if not player:
            await query.edit_message_text("❌ Игрок не найден.")
            return

        reason = WARN_REASONS[reason_idx] if 0 <= reason_idx < len(WARN_REASONS) else WARN_REASONS[0]
        admin_id = query.from_user.id
        admin_username = query.from_user.username or str(admin_id)

        new_count, is_exceeded = await asyncio.to_thread(database.add_warn, p_id, admin_id, reason)

        username_str = f"@{player['username']}" if player['username'] else f"ID {p_id}"
        team_str = player['team_name'] or 'Без клуба'

        if is_exceeded:
            # Auto-kick
            await _auto_kick_player(context, p_id, player['username'], player['team_name'])
            result_text = (
                f"🚨 Игрок <b>{html.escape(username_str)}</b> [{html.escape(team_str)}] получил "
                f"<b>{new_count}/{MAX_WARNS_LIMIT}</b> предупреждений!\n\n"
                f"⛔ Автоматически исключен из лиги и группы. Клуб освобожден."
            )
        else:
            # DM to player
            dm_text = (
                f"⚠️ <b>Вам выдано предупреждение!</b>\n\n"
                f"Причина: {html.escape(reason)}\n"
                f"Счётчик: <b>{new_count} / {MAX_WARNS_LIMIT}</b>\n\n"
                f"Администратор: @{html.escape(admin_username)}"
            )
            try:
                await context.bot.send_message(chat_id=p_id, text=dm_text, parse_mode="HTML")
            except (Forbidden, TelegramError):
                logger.warning(f"Cannot DM user {p_id} about warn.")

            # Thread notification
            thread_text = (
                f"⚠️ Игроку <b>{html.escape(username_str)}</b> [{html.escape(team_str)}] "
                f"выдан варн (<b>{new_count}/{MAX_WARNS_LIMIT}</b>).\n"
                f"Причина: {html.escape(reason)}\n"
                f"Администратор: @{html.escape(admin_username)}"
            )
            await _send_to_warns_thread(context, thread_text)

            result_text = (
                f"✅ Варн выдан игроку <b>{html.escape(username_str)}</b>.\n"
                f"Счётчик: <b>{new_count} / {MAX_WARNS_LIMIT}</b>"
            )

        keyboard = [[InlineKeyboardButton("« К карточке игрока", callback_data=f"admin_view_player_{p_id}")]]
        await query.edit_message_text(result_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    finally:
        _warn_action_locks.discard(p_id)


@admin_only
async def admin_warn_remove_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove a warn from a player."""
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except BadRequest:
        pass

    p_id = int(query.data.replace("warn_remove_", ""))

    # Debounce
    if p_id in _warn_action_locks:
        await query.answer("⏳ Действие уже выполняется...", show_alert=True)
        return
    _warn_action_locks.add(p_id)

    try:
        player = await asyncio.to_thread(database.get_user, p_id)
        if not player:
            await query.edit_message_text("❌ Игрок не найден.")
            return

        warn_count = player['warn_count'] or 0
        if warn_count <= 0:
            await query.answer("У игрока нет активных предупреждений.", show_alert=True)
            _warn_action_locks.discard(p_id)
            return

        admin_id = query.from_user.id
        admin_username = query.from_user.username or str(admin_id)
        reason = "Снятие варна администратором"

        new_count, success = await asyncio.to_thread(database.remove_warn, p_id, admin_id, reason)

        if not success:
            await query.answer("У игрока нет активных предупреждений.", show_alert=True)
            _warn_action_locks.discard(p_id)
            return

        username_str = f"@{player['username']}" if player['username'] else f"ID {p_id}"
        team_str = player['team_name'] or 'Без клуба'

        # DM to player
        dm_text = (
            f"🟢 <b>Предупреждение снято!</b>\n\n"
            f"Ваш счётчик: <b>{new_count} / {MAX_WARNS_LIMIT}</b>\n"
            f"Администратор: @{html.escape(admin_username)}"
        )
        try:
            await context.bot.send_message(chat_id=p_id, text=dm_text, parse_mode="HTML")
        except (Forbidden, TelegramError):
            pass

        # Thread notification
        thread_text = (
            f"🟢 Игроку <b>{html.escape(username_str)}</b> [{html.escape(team_str)}] "
            f"снят варн (<b>{new_count}/{MAX_WARNS_LIMIT}</b>).\n"
            f"Администратор: @{html.escape(admin_username)}"
        )
        await _send_to_warns_thread(context, thread_text)

        result_text = f"✅ Варн снят. Счётчик: <b>{new_count} / {MAX_WARNS_LIMIT}</b>"
        keyboard = [[InlineKeyboardButton("« К карточке игрока", callback_data=f"admin_view_player_{p_id}")]]
        await query.edit_message_text(result_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    finally:
        _warn_action_locks.discard(p_id)


@admin_only
async def admin_warn_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show warn history for a player."""
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except BadRequest:
        pass

    p_id = int(query.data.replace("warn_hist_", ""))
    player = await asyncio.to_thread(database.get_user, p_id)
    if not player:
        await query.edit_message_text("❌ Игрок не найден.")
        return

    warns = await asyncio.to_thread(database.get_user_warns, p_id)
    username_str = f"@{player['username']}" if player['username'] else f"ID {p_id}"
    warn_count = player['warn_count'] or 0

    text = (
        f"📜 <b>История варнов</b>\n"
        f"Игрок: <b>{html.escape(username_str)}</b>\n"
        f"Текущий счётчик: <b>{warn_count} / {MAX_WARNS_LIMIT}</b>\n\n"
    )

    if not warns:
        text += "<i>История пуста.</i>"
    else:
        for w in warns[:20]:
            w_type = w['type']
            if w_type == 'WARN_ADD':
                icon = "⚠️"
            elif w_type == 'WARN_REMOVE':
                icon = "🟢"
            elif w_type == 'AUTO_KICK':
                icon = "🚨"
            else:
                icon = "❓"

            date_str = str(w['created_at'])[:16] if w['created_at'] else "?"
            reason_str = html.escape(w['reason'] or '-')
            text += f"{icon} <code>{date_str}</code> — {reason_str}\n"

    keyboard = [[InlineKeyboardButton("« К карточке игрока", callback_data=f"admin_view_player_{p_id}")]]
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def admin_amnesty_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset all warns for a player (amnesty)."""
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except BadRequest:
        pass

    p_id = int(query.data.replace("warn_amnesty_", ""))
    player = await asyncio.to_thread(database.get_user, p_id)
    if not player:
        await query.edit_message_text("❌ Игрок не найден.")
        return

    admin_id = query.from_user.id
    await asyncio.to_thread(database.amnesty_player, p_id, admin_id)

    username_str = f"@{player['username']}" if player['username'] else f"ID {p_id}"
    team_str = player['team_name'] or 'Без клуба'

    # DM
    try:
        await context.bot.send_message(
            chat_id=p_id,
            text="🕊 <b>Амнистия!</b>\n\nВаши предупреждения сброшены до 0.",
            parse_mode="HTML"
        )
    except (Forbidden, TelegramError):
        pass

    # Thread
    thread_text = (
        f"🕊 Игроку <b>{html.escape(username_str)}</b> [{html.escape(team_str)}] "
        f"применена амнистия. Счётчик варнов сброшен до 0.\n"
        f"Администратор: @{html.escape(query.from_user.username or str(admin_id))}"
    )
    await _send_to_warns_thread(context, thread_text)

    result_text = f"✅ Амнистия применена к <b>{html.escape(username_str)}</b>. Счётчик: <b>0 / {MAX_WARNS_LIMIT}</b>"
    keyboard = [[InlineKeyboardButton("« К карточке игрока", callback_data=f"admin_view_player_{p_id}")]]
    await query.edit_message_text(result_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


@admin_only
async def admin_reset_season_warns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset all warns for all players (new season)."""
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except BadRequest:
        pass

    await asyncio.to_thread(database.reset_season_warns)
    await query.edit_message_text(
        "✅ Все предупреждения сброшены (новый сезон).",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Назад в админку", callback_data="admin_main_menu")]])
    )

@admin_only
async def admin_ai_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate AI summary for the current tournament round."""
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer("Генерируем итоги... Это может занять несколько секунд.", show_alert=True)
    except BadRequest:
        pass

    # Fetch standings
    standings = await asyncio.to_thread(database.get_standings)
    top_scorers = await asyncio.to_thread(database.get_top_scorers, 1)
    top_assists = await asyncio.to_thread(database.get_top_assists, 1)

    top_scorer = top_scorers[0] if top_scorers else None
    top_assist = top_assists[0] if top_assists else None

    # Call AI
    from ai_chat import generate_tournament_summary
    summary = await asyncio.to_thread(generate_tournament_summary, standings, top_scorer, top_assist)
    summary = html.escape(summary)

    # Send to admin in DM
    user_id = query.from_user.id
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"🤖 <b>Сгенерированные итоги круга (AI):</b>\n\n{summary}\n\n<i>Скопируйте этот текст и отправьте в нужный чат/канал!</i>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to send AI summary to admin {user_id}: {e}")
        if query.message:
            await query.message.reply_text("❌ Ошибка при отправке итогов в ЛС. Проверьте, что бот может писать вам сообщения.")