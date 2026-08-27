import asyncio
import logging
import html
import re
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

import database
from handlers.base import is_admin, generate_league_table_image

logger = logging.getLogger(__name__)

# Trigger pattern: matches messages starting with "темшик", "темщик", "temshik", or @bot_username
TRIGGER_REGEX = re.compile(r"^(?:темшик|темщик|temshik|@[\w_]+bot)\b[\s,:]*", re.IGNORECASE)


async def handle_temshik_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handle structured tournament text commands prefixed with 'Темшик' without slashes.
    Returns True if a tournament command was recognized and handled, False otherwise.
    """
    msg = update.effective_message
    if not msg or not msg.text:
        return False

    text = msg.text.strip()
    match = TRIGGER_REGEX.match(text)
    if not match:
        return False

    # Extract command part after the trigger
    cmd_text = text[match.end():].strip()
    if not cmd_text:
        await msg.reply_text(
            "👋 Привет! Я <b>Темшик</b> — бот лиги КПЛ.\n"
            "Напиши <code>Темшик помощь</code> или <code>Темшик команды</code>, чтобы посмотреть список доступных команд.",
            parse_mode="HTML"
        )
        return True

    user_id = update.effective_user.id if update.effective_user else 0
    is_adm = is_admin(user_id)

    parts = cmd_text.split(None, 1)
    action = parts[0].lower()
    args_str = parts[1].strip() if len(parts) > 1 else ""
    full_cmd = cmd_text.lower()

    # =========================================================================
    # 📊 СТАТИСТИКА, ТАБЛИЦЫ, ДОЛГИ, ПОМОЩЬ (Публичные)
    # =========================================================================

    if action in ("помощь", "help", "команды", "команда"):
        help_text = (
            "📋 <b>ТЕКСТОВЫЕ КОМАНДЫ БОТА:</b>\n\n"
            "⚽ <b>Для всех участников:</b>\n"
            "• <code>Темшик таблица</code> — турнирная таблица лиги\n"
            "• <code>Темшик состав [клуб]</code> — состав клуба\n"
            "• <code>Темшик бомбардиры [число]</code> — топ бомбардиров\n"
            "• <code>Темшик ассистенты [число]</code> — топ ассистентов\n"
            "• <code>Темшик кубок</code> — сетка и серии кубка\n"
            "• <code>Темшик долги</code> — несыгранные матчи с тегами\n"
        )
        if is_adm:
            help_text += (
                "\n👑 <b>Команды администратора:</b>\n"
                "• <code>Темшик +игрок [клуб] [имена]</code> — добавить в состав\n"
                "• <code>Темшик -игрок [клуб] [имя]</code> — удалить из состава\n"
                "• <code>Темшик переименовать игрока [клуб] [старое] -> [новое]</code>\n"
                "• <code>Темшик открыть тур [номер]</code>\n"
                "• <code>Темшик закрыть тур [номер]</code>\n"
                "• <code>Темшик дедлайн [номер] [дата/время]</code>\n"
                "• <code>Темшик синх кубок</code> — синхронизировать победителей\n"
                "• <code>Темшик варн @username [причина]</code> — выдать варн\n"
                "• <code>Темшик снять варн @username</code> — снять варн\n"
                "• <code>Темшик варны</code> — список игроков с варнами\n"
                "• <code>Темшик привязать клуб @username [клуб]</code>"
            )
        await msg.reply_text(help_text, parse_mode="HTML")
        return True

    if action in ("таблица", "турнирка", "table", "standings"):
        img_buf = await asyncio.to_thread(generate_league_table_image)
        caption = "🏆 <b>Турнирная таблица лиги КПЛ 2026</b>"
        keyboard = [[InlineKeyboardButton("🔄 Обновить", callback_data="refresh_league_table_topic")]]
        await msg.reply_photo(photo=img_buf, caption=caption, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return True

    if action in ("бомбардиры", "голы", "топ_голы", "scorers"):
        from telegram import InputFile
        import top_stats_generator

        args_lower = args_str.lower()
        nums = re.findall(r"\d+", args_str)
        is_cup = "кубок" in args_lower or "cup" in args_lower
        tourn_type = "cup" if is_cup else "league"
        tourn_title = "КУБКА" if is_cup else "ЛИГИ"

        if nums:
            # Text list mode if explicit number is given
            limit = min(30, max(3, int(nums[0])))
            fetch_func = database.get_cup_top_scorers if is_cup else database.get_top_scorers
            top_list = await asyncio.to_thread(fetch_func, limit)
            if not top_list:
                await msg.reply_text(f"⚽ Список бомбардиров {tourn_title.lower()} пока пуст.", parse_mode="HTML")
                return True
            lines = [f"⚽ <b>ТОП-{len(top_list)} БОМБАРДИРОВ {tourn_title} КПЛ:</b>\n"]
            for idx, p in enumerate(top_list, 1):
                badge = "🥇 " if idx == 1 else ("🥈 " if idx == 2 else ("🥉 " if idx == 3 else f"{idx}. "))
                team_str = f" ({p['team_name']})" if p.get('team_name') else ""
                goals_cnt = p.get('total_goals', p.get('goals', 0))
                lines.append(f"{badge}<b>{html.escape(p.get('player_name', '—'))}</b>{html.escape(team_str)} — <b>{goals_cnt}</b> ⚽")
            await msg.reply_text("\n".join(lines), parse_mode="HTML")
            return True
        else:
            # Graphic card mode!
            buf = await asyncio.to_thread(top_stats_generator.generate_top_stats_image, "goals", 10, tourn_type)
            caption = f"<b>⚽ ТОП БОМБАРДИРОВ {tourn_title} КПЛ 2026</b>"
            filename = f"{tourn_type}_top_scorers.png"
            await msg.reply_photo(photo=InputFile(buf, filename=filename), caption=caption, parse_mode="HTML")
            return True

    if action in ("ассистенты", "пасы", "топ_пас", "assists"):
        from telegram import InputFile
        import top_stats_generator

        args_lower = args_str.lower()
        nums = re.findall(r"\d+", args_str)
        is_cup = "кубок" in args_lower or "cup" in args_lower
        tourn_type = "cup" if is_cup else "league"
        tourn_title = "КУБКА" if is_cup else "ЛИГИ"

        if nums:
            # Text list mode if explicit number is given
            limit = min(30, max(3, int(nums[0])))
            fetch_func = database.get_cup_top_assists if is_cup else database.get_top_assists
            top_list = await asyncio.to_thread(fetch_func, limit)
            if not top_list:
                await msg.reply_text(f"🎯 Список ассистентов {tourn_title.lower()} пока пуст.", parse_mode="HTML")
                return True
            lines = [f"🎯 <b>ТОП-{len(top_list)} АССИСТЕНТОВ {tourn_title} КПЛ:</b>\n"]
            for idx, p in enumerate(top_list, 1):
                badge = "🥇 " if idx == 1 else ("🥈 " if idx == 2 else ("🥉 " if idx == 3 else f"{idx}. "))
                team_str = f" ({p['team_name']})" if p.get('team_name') else ""
                assists_cnt = p.get('total_assists', p.get('assists', 0))
                lines.append(f"{badge}<b>{html.escape(p.get('player_name', '—'))}</b>{html.escape(team_str)} — <b>{assists_cnt}</b> 🎯")
            await msg.reply_text("\n".join(lines), parse_mode="HTML")
            return True
        else:
            # Graphic card mode!
            buf = await asyncio.to_thread(top_stats_generator.generate_top_stats_image, "assists", 10, tourn_type)
            caption = f"<b>🎯 ТОП АССИСТЕНТОВ {tourn_title} КПЛ 2026</b>"
            filename = f"{tourn_type}_top_assisters.png"
            await msg.reply_photo(photo=InputFile(buf, filename=filename), caption=caption, parse_mode="HTML")
            return True

    if action in ("долги", "debts", "должники"):
        debts = await asyncio.to_thread(database.get_all_unplayed_league_matches)
        if not debts:
            await msg.reply_text("✅ <b>Все матчи сыграны! Долгов по турниру нет.</b>", parse_mode="HTML")
            return True
        lines = ["⏳ <b>СПИСОК НЕЗАКРЫТЫХ МАТЧЕЙ (ДОЛГИ):</b>\n"]
        rounds_map = {}
        for d in debts:
            rn = d.get("round_number", 0)
            if rn not in rounds_map:
                rounds_map[rn] = []
            rounds_map[rn].append(d)

        for rn in sorted(rounds_map.keys()):
            lines.append(f"📌 <b>Тур {rn}:</b>")
            for m in rounds_map[rn]:
                t1 = html.escape(m.get("player1_team") or "—")
                t2 = html.escape(m.get("player2_team") or "—")
                p1_u = f"@{m['p1_username']}" if m.get("p1_username") else t1
                p2_u = f"@{m['p2_username']}" if m.get("p2_username") else t2
                lines.append(f"• {t1} ({p1_u}) 🆚 {t2} ({p2_u})")
            lines.append("")
        await msg.reply_text("\n".join(lines), parse_mode="HTML")
        return True

    # =========================================================================
    # 👥 СОСТАВЫ КЛУБОВ (Фото состава)
    # =========================================================================

    if action in ("состав", "составы", "squad"):
        team_to_find = args_str.strip()
        if not team_to_find:
            team_to_find = await asyncio.to_thread(database.get_user_team, user_id)
            if not team_to_find:
                await msg.reply_text(
                    "ℹ️ Укажите название клуба, например: <code>Темшик состав Расинг</code>",
                    parse_mode="HTML"
                )
                return True

        photo_id = await asyncio.to_thread(database.get_team_squad_photo, team_to_find)

        if not photo_id:
            # Try searching team by partial match
            all_teams = await asyncio.to_thread(database.get_all_teams)
            matched_t = next((t for t in all_teams if team_to_find.lower() in t.lower() or t.lower() in team_to_find.lower()), None)
            if matched_t:
                team_to_find = matched_t
                photo_id = await asyncio.to_thread(database.get_team_squad_photo, team_to_find)

        if photo_id:
            caption = f"📸 <b>Состав клуба {html.escape(team_to_find)}</b>"
            await msg.reply_photo(photo=photo_id, caption=caption, parse_mode="HTML")
        else:
            await msg.reply_text(
                f"📸 У клуба <b>{html.escape(team_to_find)}</b> ещё не загружено фото состава.",
                parse_mode="HTML"
            )
        return True

    # =========================================================================
    # ⚔️ КУБОК (Сетка)
    # =========================================================================

    if action in ("сетка", "кубок", "cup", "bracket"):
        from telegram import InputFile
        
        stage_arg = args_str.strip().lower()
        if any(st in stage_arg for st in ("1/8", "1/4", "1/2", "финал", "final")):
            if "1/8" in stage_arg:
                stage = "1/8"
            elif "1/4" in stage_arg:
                stage = "1/4"
            elif "1/2" in stage_arg:
                stage = "1/2"
            else:
                stage = "final"
            
            from table_generator import generate_cup_bracket_image
            img_buf = await asyncio.to_thread(generate_cup_bracket_image, stage)
            stage_title_map = {'1/8': '1/8 Финала', '1/4': '1/4 Финала', '1/2': '1/2 Финала', 'final': '🏆 Финал'}
            title = stage_title_map.get(stage, stage)
            caption = f"🏆 <b>КУБОК КПЛ 2026 | {title}</b>\n<i>Графическая сетка турнира</i>"
            filename = f"cup_bracket_{stage}.png"
        else:
            # Default to full bracket graphic!
            from services.cup_bracket_generator import generate_bracket_image
            img_buf = await asyncio.to_thread(generate_bracket_image)
            caption = "🏆 <b>КУБОК КПЛ 2026 | ПОЛНАЯ СЕТКА</b>\n<i>От 1/8 до Финала</i>"
            filename = "full_cup_bracket.png"

        keyboard = [
            [
                InlineKeyboardButton("1/8", callback_data="show_cup_graphic_1/8"),
                InlineKeyboardButton("1/4", callback_data="show_cup_graphic_1/4"),
                InlineKeyboardButton("1/2", callback_data="show_cup_graphic_1/2"),
                InlineKeyboardButton("Финал", callback_data="show_cup_graphic_final"),
            ],
            [
                InlineKeyboardButton("📊 Полная сетка", callback_data="show_full_cup_bracket")
            ]
        ]
        await msg.reply_photo(
            photo=InputFile(img_buf, filename=filename),
            caption=caption,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return True

    # =========================================================================
    # 👑 КОМАНДЫ АДМИНИСТРАТОРА (ТРЕБУЮТ ПРАВ ADMIN)
    # =========================================================================

    if (
        action in ("добавить", "добавь", "+игрок", "add_player") or
        full_cmd.startswith("добавить игрока") or
        full_cmd.startswith("добавь игрока")
    ):
        if not is_adm:
            await msg.reply_text("⚠️ Эта команда доступна только администраторам турнира.")
            return True

        clean_args = re.sub(r"^(?:добавить|добавь)?\s*(?:игрока|игроков)?\s*", "", cmd_text, flags=re.IGNORECASE).strip()
        parts_s = clean_args.split(None, 1)
        if len(parts_s) < 2:
            await msg.reply_text(
                "ℹ️ Формат: <code>Темшик добавить игрока [Клуб] [Имя игрока]</code>\n"
                "Пример: <code>Темшик добавить игрока Расинг Matías Zaracho</code>",
                parse_mode="HTML"
            )
            return True

        team_name, players_raw = parts_s[0], parts_s[1]
        player_names = [p.strip() for p in players_raw.split(",") if p.strip()]
        added_cnt = await asyncio.to_thread(database.add_squad, team_name, player_names)
        await msg.reply_text(
            f"✅ В состав клуба <b>{html.escape(team_name)}</b> успешно добавлено игроков: <b>{added_cnt}</b>.",
            parse_mode="HTML"
        )
        return True

    if (
        action in ("удалить", "удали", "-игрок", "del_player", "remove_player") or
        full_cmd.startswith("удалить игрока") or
        full_cmd.startswith("удали игрока")
    ):
        if not is_adm:
            await msg.reply_text("⚠️ Эта команда доступна только администраторам турнира.")
            return True

        clean_args = re.sub(r"^(?:удалить|удали)?\s*(?:игрока|игроков)?\s*", "", cmd_text, flags=re.IGNORECASE).strip()
        parts_s = clean_args.split(None, 1)
        if len(parts_s) < 2:
            await msg.reply_text(
                "ℹ️ Формат: <code>Темшик удалить игрока [Клуб] [Имя игрока]</code>\n"
                "Пример: <code>Темшик удалить игрока Расинг Colombo</code>",
                parse_mode="HTML"
            )
            return True

        team_name, player_name = parts_s[0], parts_s[1].strip()
        removed = await asyncio.to_thread(database.remove_player_from_squad, team_name, player_name)
        if removed:
            await msg.reply_text(
                f"🗑 Игрок <b>{html.escape(player_name)}</b> удалён из состава клуба <b>{html.escape(team_name)}</b>.",
                parse_mode="HTML"
            )
        else:
            await msg.reply_text(
                f"❌ Игрок <b>{html.escape(player_name)}</b> не найден в составе <b>{html.escape(team_name)}</b>.",
                parse_mode="HTML"
            )
        return True

    if (
        action in ("переименовать", "rename_player") or
        full_cmd.startswith("переименовать игрока")
    ):
        if not is_adm:
            await msg.reply_text("⚠️ Эта команда доступна только администраторам турнира.")
            return True

        clean_args = re.sub(r"^(?:переименовать)?\s*(?:игрока)?\s*", "", cmd_text, flags=re.IGNORECASE).strip()
        if "->" in clean_args:
            left_p, new_n = clean_args.split("->", 1)
            left_parts = left_p.strip().split(None, 1)
            if len(left_parts) == 2:
                team_n, old_n = left_parts[0], left_parts[1]
            else:
                team_n, old_n = None, left_parts[0]
            new_n = new_n.strip()
        else:
            await msg.reply_text(
                "ℹ️ Формат: <code>Темшик переименовать игрока [Клуб] [Старое имя] -> [Новое имя]</code>\n"
                "Пример: <code>Темшик переименовать игрока Расинг Lang -> Noa Lang</code>",
                parse_mode="HTML"
            )
            return True

        ok, text_res = await asyncio.to_thread(database.rename_player, old_n, new_n, team_n)
        await msg.reply_text(f"{'✅' if ok else '❌'} {text_res}", parse_mode="HTML")
        return True

    if (
        action in ("открыть", "открой", "open_round") or
        full_cmd.startswith("открыть тур") or
        full_cmd.startswith("открой тур")
    ):
        if not is_adm:
            await msg.reply_text("⚠️ Эта команда доступна только администраторам турнира.")
            return True

        nums = re.findall(r"\d+", cmd_text)
        if not nums:
            await msg.reply_text("ℹ️ Укажите номер тура. Пример: <code>Темшик открыть тур 18</code>", parse_mode="HTML")
            return True
        rn = int(nums[0])
        await asyncio.to_thread(database.update_round_status, rn, is_open=True)
        await msg.reply_text(f"🔓 <b>Тур {rn} успешно открыт!</b> Участники могут вносить результаты.", parse_mode="HTML")
        return True

    if (
        action in ("закрыть", "закрой", "close_round") or
        full_cmd.startswith("закрыть тур") or
        full_cmd.startswith("закрой тур")
    ):
        if not is_adm:
            await msg.reply_text("⚠️ Эта команда доступна только администраторам турнира.")
            return True

        nums = re.findall(r"\d+", cmd_text)
        if not nums:
            await msg.reply_text("ℹ️ Укажите номер тура. Пример: <code>Темшик закрыть тур 17</code>", parse_mode="HTML")
            return True
        rn = int(nums[0])
        await asyncio.to_thread(database.update_round_status, rn, is_open=False)
        await msg.reply_text(f"🔒 <b>Тур {rn} закрыт.</b>", parse_mode="HTML")
        return True

    if action in ("дедлайн", "deadline"):
        if not is_adm:
            await msg.reply_text("⚠️ Эта команда доступна только администраторам турнира.")
            return True

        nums = re.findall(r"\d+", args_str)
        if not nums:
            await msg.reply_text(
                "ℹ️ Формат: <code>Темшик дедлайн [номер_тура] [дата и время]</code>\n"
                "Пример: <code>Темшик дедлайн 18 18.08 23:59</code>",
                parse_mode="HTML"
            )
            return True

        rn = int(nums[0])
        dl_text = re.sub(r"^\d+\s*(?:тур)?\s*", "", args_str, flags=re.IGNORECASE).strip()
        if not dl_text:
            await msg.reply_text("ℹ️ Укажите дату и время дедлайна, например: <code>18.08 23:59</code>", parse_mode="HTML")
            return True

        await asyncio.to_thread(database.update_round_status, rn, is_open=True, deadline=dl_text)
        await msg.reply_text(
            f"⏰ <b>Дедлайн для тура {rn} установлен на:</b> <code>{html.escape(dl_text)}</code>.",
            parse_mode="HTML"
        )
        return True

    if action in ("синх", "синх_кубок", "sync_cup") or full_cmd.startswith("синх кубок"):
        if not is_adm:
            await msg.reply_text("⚠️ Эта команда доступна только администраторам турнира.")
            return True

        advanced = await asyncio.to_thread(database.sync_cup_bracket)
        await msg.reply_text(
            f"🔄 <b>Кубковая сетка синхронизирована.</b> Перенесено победителей в следующие стадии: <b>{advanced}</b>.",
            parse_mode="HTML"
        )
        return True

    if action in ("автоварны", "проверить_долги", "чекер_долгов") or full_cmd.startswith("автоварны") or full_cmd.startswith("проверить долги") or full_cmd.startswith("проверка долгов"):
        if not is_adm:
            await msg.reply_text("⚠️ Эта команда доступна только администраторам турнира.")
            return True

        from handlers.admin import job_debt_lifecycle_tracker
        await msg.reply_text("⏳ <b>Запуск проверки долгов и начисления авто-варнов...</b>", parse_mode="HTML")
        await job_debt_lifecycle_tracker(context)
        await msg.reply_text("✅ <b>Проверка долгов и авто-варнов успешно завершена!</b>", parse_mode="HTML")
        return True

    if action in ("клуб", "карточка_клуба", "клуб_инфо", "club") or full_cmd.startswith("клуб") or full_cmd.startswith("карточка клуба"):
        chat = update.effective_chat
        if chat and chat.type in ("group", "supergroup", "channel") and not is_adm:
            bot_me = await context.bot.get_me()
            bot_username = bot_me.username or "logovobot"
            await msg.reply_text(
                f"ℹ️ Просмотр карточек клубов доступен в личном кабинете бота: @{bot_username}\n"
                f"В общем чате эта команда доступна только администраторам.",
                parse_mode="HTML"
            )
            return True

        target_club_raw = re.sub(r"^(?:карточка\s+клуба|клуб(?:\s+инфо)?)\s*", "", cmd_text, flags=re.IGNORECASE).strip()
        if not target_club_raw:
            user = update.effective_user
            team = await asyncio.to_thread(database.get_user_team, user.id) if user else None
            target_club_raw = team or ""

        if not target_club_raw:
            from handlers.cabinet import show_clubs_catalog
            await show_clubs_catalog(update, context)
            return True

        canon = database.resolve_team_name(target_club_raw)
        if not canon:
            await msg.reply_text(
                f"❌ Клуб <b>{html.escape(target_club_raw)}</b> не найден в Лиге КПЛ. Напишите <code>/club</code>, чтобы посмотреть весь список.",
                parse_mode="HTML"
            )
            return True

        from handlers.cabinet import send_or_edit_club_card
        await send_or_edit_club_card(update, context, canon, back_cb="cb_clubs_catalog")
        return True

    if action in ("анонс_кубок", "анонс_финал", "анонс") or full_cmd.startswith("анонс кубок") or full_cmd.startswith("анонс финал") or full_cmd.startswith("кубок анонс"):
        if not is_adm:
            await msg.reply_text("⚠️ Эта команда доступна только администраторам турнира.")
            return True

        stage_req = "final"
        if "1/8" in full_cmd:
            stage_req = "1/8"
        elif "1/4" in full_cmd:
            stage_req = "1/4"
        elif "1/2" in full_cmd or "полуфинал" in full_cmd:
            stage_req = "1/2"
        elif "финал" in full_cmd or "final" in full_cmd:
            stage_req = "final"

        from handlers.admin import notify_cup_stage_opened
        await notify_cup_stage_opened(context.bot, stage_req)
        await msg.reply_text(f"🚀 <b>Официальное уведомление и сетка для стадии «{stage_req}» отправлены в тему отчётов!</b>", parse_mode="HTML")
        return True

    if action in ("напомнить_кубок", "кубок_напомнить") or full_cmd.startswith("напомнить кубок") or full_cmd.startswith("кубок напомнить") or full_cmd.startswith("напомни кубок"):
        if not is_adm:
            await msg.reply_text("⚠️ Эта команда доступна только администраторам турнира.")
            return True

        stage_req = "final"
        if "1/8" in full_cmd:
            stage_req = "1/8"
        elif "1/4" in full_cmd:
            stage_req = "1/4"
        elif "1/2" in full_cmd or "полуфинал" in full_cmd:
            stage_req = "1/2"
        elif "финал" in full_cmd or "final" in full_cmd:
            stage_req = "final"

        from handlers.admin import admin_remind_cup_execute
        series_list = await asyncio.to_thread(database.get_cup_series_list, stage_req)
        unplayed_matches = []
        for s in series_list:
            if s["status"] != "completed":
                for m in s.get("matches", []):
                    if m["status"] == "pending":
                        unplayed_matches.append((s, m))

        if not unplayed_matches:
            await msg.reply_text(f"✅ В стадии {stage_req} нет несыгранных матчей!", parse_mode="HTML")
            return True

        from handlers.admin import safe_send_notification
        pm_sent = 0
        for s, m in unplayed_matches:
            t1, t2 = s["team1_name"], s["team2_name"]
            w1, w2 = s["team1_wins"], s["team2_wins"]
            g_num = m["game_num_in_series"]
            wins_needed = 3 if stage_req == 'final' else 2
            best_of_text = "Best-of-5" if stage_req == 'final' else "Best-of-3"
            rule_desc = "Матчи играются в стандартном режиме (90 мин, без доп. времени и серии пенальти)." if stage_req == 'final' else "Каждая игра до победы (с доп. временем и пенальти)."

            p1_id, p2_id = None, None
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
                f"⚔️ <b>Стадия:</b> {stage_req} Финала (Игра {g_num})\n"
                f"🏠 <b>{html.escape(t1)}</b> 🆚 <b>{html.escape(t2)}</b> ✈️\n"
                f"📊 <b>Счёт серии ({best_of_text}):</b> {w1} : {w2}\n\n"
                f"Пожалуйста, сыграйте свой кубковый матч! {rule_desc}"
            )
            kb = [[InlineKeyboardButton("📋 Внести результат", callback_data=f"cabinet_report_score_{m['id']}")]]
            if p1_id and p1_id > 0:
                if await safe_send_notification(context.bot, p1_id, pm_text, InlineKeyboardMarkup(kb)):
                    pm_sent += 1
            if p2_id and p2_id > 0:
                if await safe_send_notification(context.bot, p2_id, pm_text, InlineKeyboardMarkup(kb)):
                    pm_sent += 1

        main_group_id = await asyncio.to_thread(database.get_group_id)
        reports_topic_id = await asyncio.to_thread(database.get_config, "reports_topic_id")
        if main_group_id:
            lines = [
                f"🏆 <b>НАПОМИНАНИЕ О КУБКЕ КПЛ | {stage_req} Финала</b>\n",
                f"Несыгранные кубковые матчи ({len(unplayed_matches)}):"
            ]
            for s, m in unplayed_matches:
                t1_esc, t2_esc = html.escape(s["team1_name"]), html.escape(s["team2_name"])
                w1, w2 = s["team1_wins"], s["team2_wins"]
                g_num = m["game_num_in_series"]
                lines.append(f"• ⚔️ <b>Игра {g_num}:</b> <b>{t1_esc}</b> 🆚 <b>{t2_esc}</b> (Счёт серии: {w1} : {w2})")

            if stage_req == 'final':
                lines.append("\n⚠️ Напоминаем: в финале серия до 3-х побед (Best-of-5), матчи играются в обычном режиме (90 мин, без доп. времени и серии пенальти).")
            else:
                lines.append("\n⚠️ Напоминаем: в каждом кубковом матче обязательно доп. время и пенальти (ничьих нет).")
            lines.append("Пожалуйста, внесите результаты в бота!")

            try:
                kwargs = {"chat_id": main_group_id, "text": "\n".join(lines), "parse_mode": "HTML"}
                if reports_topic_id:
                    kwargs["message_thread_id"] = int(reports_topic_id)
                await context.bot.send_message(**kwargs)
            except Exception as e:
                logger.exception("Failed to post cup reminder summary to group")

        await msg.reply_text(f"🚀 <b>Напоминания по стадии «{stage_req}» отправлены!</b> (В ЛС: {pm_sent}, Тема отчетов: ✅)", parse_mode="HTML")
        return True

    if action in ("варн", "warn"):
        if not is_adm:
            await msg.reply_text("⚠️ Эта команда доступна только администраторам турнира.")
            return True

        parts_w = args_str.split(None, 1)
        if not parts_w:
            await msg.reply_text(
                "ℹ️ Формат: <code>Темшик варн @username [причина]</code>\n"
                "Пример: <code>Темшик варн @ch1lyx Срыв дедлайна</code>",
                parse_mode="HTML"
            )
            return True

        target_ref = parts_w[0]
        reason = parts_w[1].strip() if len(parts_w) > 1 else "Нарушение регламента турнира"

        target_user = await asyncio.to_thread(database.find_user_by_ref, target_ref)
        if not target_user:
            await msg.reply_text(f"❌ Пользователь <b>{html.escape(target_ref)}</b> не найден в базе данных.", parse_mode="HTML")
            return True

        target_user = dict(target_user)
        t_id = target_user["telegram_id"]
        new_cnt, exceeded = await asyncio.to_thread(database.add_warn, t_id, user_id, reason)
        from config import MAX_WARNS_LIMIT

        warn_msg = (
            f"⚠️ <b>ВЫДАНО ПРЕДУПРЕЖДЕНИЕ:</b>\n\n"
            f"👤 <b>Игрок:</b> @{html.escape(target_user.get('username') or str(t_id))}\n"
            f"🛡 <b>Клуб:</b> {html.escape(target_user.get('team_name') or '—')}\n"
            f"📊 <b>Текущие варны:</b> {new_cnt}/{MAX_WARNS_LIMIT}\n"
            f"📝 <b>Причина:</b> {html.escape(reason)}"
        )
        if exceeded:
            warn_msg += f"\n\n🚨 <b>ВНИМАНИЕ: Достигнут лимит варнов ({MAX_WARNS_LIMIT}/{MAX_WARNS_LIMIT})!</b>"

        await msg.reply_text(warn_msg, parse_mode="HTML")

        # Also forward to warns topic if configured
        group_id = await asyncio.to_thread(database.get_group_id)
        warns_topic_id = await asyncio.to_thread(database.get_config, "warns_topic_id")
        if group_id and warns_topic_id and msg.chat_id != group_id:
            try:
                await context.bot.send_message(
                    chat_id=group_id,
                    text=warn_msg,
                    parse_mode="HTML",
                    message_thread_id=int(warns_topic_id)
                )
            except Exception as e:
                logger.warning(f"Failed to post warn to warns topic: {e}")

        try:
            from handlers.admin import _post_or_update_debts_in_warns
            await _post_or_update_debts_in_warns(context)
        except Exception as e:
            logger.warning(f"Failed to refresh debts in warns topic: {e}")

        return True

    if action in ("снять_варн", "unwarn", "разварн") or full_cmd.startswith("снять варн"):
        if not is_adm:
            await msg.reply_text("⚠️ Эта команда доступна только администраторам турнира.")
            return True

        clean_ref = re.sub(r"^(?:снять|сними)?\s*(?:варн)?\s*", "", cmd_text, flags=re.IGNORECASE).strip()
        if not clean_ref:
            await msg.reply_text("ℹ️ Укажите игрока: <code>Темшик снять варн @username</code>", parse_mode="HTML")
            return True

        target_user = await asyncio.to_thread(database.find_user_by_ref, clean_ref)
        if not target_user:
            await msg.reply_text(f"❌ Пользователь <b>{html.escape(clean_ref)}</b> не найден в базе данных.", parse_mode="HTML")
            return True

        target_user = dict(target_user)
        t_id = target_user["telegram_id"]
        new_cnt, removed = await asyncio.to_thread(database.remove_warn, t_id, user_id, "Снято администратором")
        if removed:
            await msg.reply_text(
                f"✅ Предупреждение снято с @{html.escape(target_user.get('username') or str(t_id))}. "
                f"Текущие варны: <b>{new_cnt}</b>.",
                parse_mode="HTML"
            )
        else:
            await msg.reply_text(
                f"ℹ️ У игрока @{html.escape(target_user.get('username') or str(t_id))} нет активных варнов.",
                parse_mode="HTML"
            )

        try:
            from handlers.admin import _post_or_update_debts_in_warns
            await _post_or_update_debts_in_warns(context)
        except Exception as e:
            logger.warning(f"Failed to refresh debts in warns topic: {e}")

        return True

    if action in ("варны", "список_варнов", "warns"):
        if not is_adm:
            await msg.reply_text("⚠️ Эта команда доступна только администраторам турнира.")
            return True

        warn_users = await asyncio.to_thread(database.get_all_active_warns)
        if not warn_users:
            await msg.reply_text("✨ <b>Участников с активными предупреждениями нет.</b>", parse_mode="HTML")
            return True

        from config import MAX_WARNS_LIMIT
        lines = ["⚠️ <b>СПИСОК ИГРОКОВ С ПРЕДУПРЕЖДЕНИЯМИ:</b>\n"]
        for u in warn_users:
            un = f"@{u['username']}" if u.get("username") else str(u['telegram_id'])
            tm = f" ({u['team_name']})" if u.get("team_name") else ""
            lines.append(f"• <b>{html.escape(un)}</b>{html.escape(tm)} — <b>{u['warn_count']}/{MAX_WARNS_LIMIT}</b>")
        await msg.reply_text("\n".join(lines), parse_mode="HTML")
        return True

    if action in ("привязать_клуб", "привязать", "set_team") or full_cmd.startswith("привязать клуб"):
        if not is_adm:
            await msg.reply_text("⚠️ Эта команда доступна только администраторам турнира.")
            return True

        clean_args = re.sub(r"^(?:привязать)?\s*(?:клуб)?\s*", "", cmd_text, flags=re.IGNORECASE).strip()
        parts_p = clean_args.split(None, 1)
        if len(parts_p) < 2:
            await msg.reply_text(
                "ℹ️ Формат: <code>Темшик привязать клуб @username [Название клуба]</code>\n"
                "Пример: <code>Темшик привязать клуб @ch1lyx Расинг</code>",
                parse_mode="HTML"
            )
            return True

        user_ref, club_name = parts_p[0], parts_p[1].strip()
        ok, res_text = await asyncio.to_thread(database.set_player_club, user_ref, club_name)
        if ok:
            try:
                from handlers.admin import _post_or_update_debts_in_warns
                await _post_or_update_debts_in_warns(context)
            except Exception as e:
                logger.warning(f"Failed to update debts in warns: {e}")
        await msg.reply_text(f"{'✅' if ok else '❌'} {res_text}", parse_mode="HTML")
        return True

    # Not a specific tournament command -> return False to allow conversational AI chat to handle it
    return False
