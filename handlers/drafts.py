import asyncio
import logging
import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database
import config
from ai_recognizer import recognize_match_screenshots_bytes
from handlers.cabinet import match_and_enrich_squad, build_formatted_match_post

logger = logging.getLogger(__name__)

# In-memory storage for collecting media groups
# { "buffer_key": { "photos": [...], "photo_file_ids": [...], "caption": "", "user_id": int, "message_ids": [...] } }
draft_media_groups = {}
draft_tasks = {}

async def handle_draft_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Listen for photos in the drafts topic and collect them."""
    if not update.effective_chat or update.effective_chat.type not in ("group", "supergroup"):
        return
        
    msg = update.message
    if not msg:
        return
        
    drafts_topic_id_str = database.get_config("drafts_topic_id")
    if not drafts_topic_id_str or not msg.is_topic_message:
        return
        
    try:
        topic_id = int(drafts_topic_id_str)
    except ValueError:
        return
        
    if msg.message_thread_id != topic_id:
        return
        
    user_id = update.effective_user.id
    
    # Key by media_group_id or by user in topic for consecutive photos
    if msg.media_group_id:
        buffer_key = f"mg_{msg.media_group_id}"
    else:
        buffer_key = f"user_{update.effective_chat.id}_{msg.message_thread_id}_{user_id}"
        
    if buffer_key not in draft_media_groups:
        draft_media_groups[buffer_key] = {
            "photos": [],
            "photo_file_ids": [],
            "caption": "",
            "user_id": user_id,
            "message_ids": []
        }
        
    group_data = draft_media_groups[buffer_key]
    group_data["message_ids"].append(msg.message_id)
    
    text = msg.caption or msg.text
    if text:
        if group_data["caption"]:
            if text not in group_data["caption"]:
                group_data["caption"] += f"\n{text}"
        else:
            group_data["caption"] = text
        
    if msg.photo:
        # get highest resolution
        photo = msg.photo[-1]
        group_data["photo_file_ids"].append(photo.file_id)
        f_obj = await context.bot.get_file(photo.file_id)
        f_bytes = await f_obj.download_as_bytearray()
        group_data["photos"].append(bytes(f_bytes))
        
    # Cancel previous timer if still waiting and restart debounce timer
    if buffer_key in draft_tasks:
        draft_tasks[buffer_key].cancel()
        
    draft_tasks[buffer_key] = asyncio.create_task(
        _process_draft_group_delayed(buffer_key, update, context)
    )

async def _process_draft_group_delayed(buffer_key: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Wait to let all media in the group or rapid consecutive photos arrive
    await asyncio.sleep(4.5)
    
    group_data = draft_media_groups.pop(buffer_key, None)
    draft_tasks.pop(buffer_key, None)
    
    if not group_data:
        return
        
    photos = group_data["photos"]
    caption = group_data["caption"]
    user_id = group_data["user_id"]
    msg_ids = group_data["message_ids"]
    photo_file_ids = group_data["photo_file_ids"]
    reply_to_id = msg_ids[0] if msg_ids else None
    
    if not photos:
        # Just text, ignore. Or prompt user for screenshots?
        # Let's just ignore to not spam.
        return
        
    status_msg = await update.effective_message.reply_text(
        "⏳ Обрабатываю результат через ИИ...", 
        reply_to_message_id=reply_to_id
    )
    
    squad_hints = {}
    if caption:
        try:
            caption_norm = database.normalize_team_name(caption)
            all_teams = await asyncio.to_thread(database.get_all_teams)
            for t in all_teams:
                t_norm = database.normalize_team_name(t)
                if t_norm and (t_norm in caption_norm or t.lower() in caption.lower()):
                    sq = await asyncio.to_thread(database.get_squad, t)
                    if sq:
                        squad_hints[t] = sq
        except Exception as e:
            logger.warning(f"Failed to load squad hints from DB: {e}")

    try:
        ai_res = await asyncio.to_thread(
            recognize_match_screenshots_bytes,
            photos,
            caption=caption,
            squad_hints=squad_hints
        )
    except Exception as e:
        logger.exception("Error in draft AI processing")
        await status_msg.edit_text("❌ Ошибка при распознавании скриншота.")
        return
        
    if not ai_res:
        await status_msg.edit_text("🤖 ИИ не смог распознать результаты матча. Убедитесь, что скриншоты чёткие.")
        return
        
    matches_list = ai_res.get("matches") or [ai_res]
    
    # 1. Check team names
    t1_raw = matches_list[0].get("team1")
    t2_raw = matches_list[0].get("team2")
    if not t1_raw or not t2_raw:
        await status_msg.edit_text("🤖 ИИ распознал счет, но не смог определить названия команд. Пожалуйста, напишите их текстом в описании к фото.")
        return
        
    t1 = database.resolve_team_name(t1_raw) or t1_raw
    t2 = database.resolve_team_name(t2_raw) or t2_raw
        
    # 2. Find first active match
    first_match = database.get_active_match_by_teams(t1, t2, caption=caption)
    if not first_match:
        await status_msg.edit_text(f"❌ Не найден активный матч между командами {html.escape(t1)} и {html.escape(t2)}.\nВозможно, названия команд в подписи указаны неточно.")
        return

    is_cup = first_match.get("tournament_type") == "cup"
    s_id = first_match.get("cup_series_id")
    cup_stage = first_match.get("cup_stage", "1/8")
    
    prepared_games = []
    
    for idx, m_info in enumerate(matches_list):
        if idx == 0:
            cur_match = first_match
        else:
            if is_cup and s_id:
                cur_match = database.get_cup_match_by_series_and_game(s_id, idx + 1)
                if not cur_match:
                    cur_match = {
                        "id": None,
                        "tournament_type": "cup",
                        "cup_stage": cup_stage,
                        "cup_series_id": s_id,
                        "game_num_in_series": idx + 1,
                        "round_number": -1,
                        "player1_team": first_match["player2_team"] if (idx % 2 == 1) else first_match["player1_team"],
                        "player2_team": first_match["player1_team"] if (idx % 2 == 1) else first_match["player2_team"],
                        "player1_username": first_match.get("player2_username") if (idx % 2 == 1) else first_match.get("player1_username"),
                        "player2_username": first_match.get("player1_username") if (idx % 2 == 1) else first_match.get("player2_username"),
                    }
            else:
                cur_match = database.get_active_match_by_teams(t1, t2, caption=caption) or first_match

        home_team = cur_match.get("player1_team") or cur_match.get("player1_nickname") or t1
        away_team = cur_match.get("player2_team") or cur_match.get("player2_nickname") or t2
        
        s1_goals = m_info.get("side1_goals") or m_info.get("home_goals") or []
        s2_goals = m_info.get("side2_goals") or m_info.get("away_goals") or []
        s1_assists = m_info.get("side1_assists") or m_info.get("home_assists") or []
        s2_assists = m_info.get("side2_assists") or m_info.get("away_assists") or []
        is_single_timeline = bool(m_info.get("is_single_timeline", False))
        
        try:
            h_goals, a_goals, h_assists, a_assists, is_side1_home = await asyncio.to_thread(
                match_and_enrich_squad,
                s1_goals, s2_goals, s1_assists, s2_assists,
                home_team, away_team,
                is_single_timeline=is_single_timeline,
            )
        except Exception as e:
            logger.exception(f"Error matching squad in draft: {e}")
            await status_msg.edit_text("❌ Ошибка при сопоставлении состава. Возможно, игроки не зарегистрированы.")
            return
            
        l_score = int(m_info.get("left_score", 0))
        r_score = int(m_info.get("right_score", 0))
        h_g_count = sum(h_goals.values())
        a_g_count = sum(a_goals.values())
        
        if is_side1_home:
            h_score = l_score if (l_score > 0 or r_score > 0) else h_g_count
            a_score = r_score if (l_score > 0 or r_score > 0) else a_g_count
        else:
            h_score = r_score if (l_score > 0 or r_score > 0) else h_g_count
            a_score = l_score if (l_score > 0 or r_score > 0) else a_g_count

        # SANITY CHECK: The team that scored more goals MUST have the higher score!
        if a_g_count > h_g_count and h_score > a_score:
            h_score, a_score = a_score, h_score
            is_side1_home = not is_side1_home
        elif h_g_count > a_g_count and a_score > h_score:
            h_score, a_score = a_score, h_score
            is_side1_home = not is_side1_home

        if h_score < h_g_count:
            h_score = h_g_count
        if a_score < a_g_count:
            a_score = a_g_count
                
        events = []
        for p, c in h_goals.items(): events.append((home_team, p, "goal", c))
        for p, c in a_goals.items(): events.append((away_team, p, "goal", c))
        for p, c in h_assists.items(): events.append((home_team, p, "assist", c))
        for p, c in a_assists.items(): events.append((away_team, p, "assist", c))

        p1_un = cur_match.get('player1_username')
        p2_un = cur_match.get('player2_username')
        p1_clean = html.escape(p1_un.lstrip('@')) if p1_un else ""
        p2_clean = html.escape(p2_un.lstrip('@')) if p2_un else ""
        p1_str = f" (@{p1_clean})" if p1_clean else ""
        p2_str = f" (@{p2_clean})" if p2_clean else ""

        prepared_games.append({
            "match_id": cur_match.get("id"),
            "round_number": cur_match.get("round_number"),
            "tournament_type": cur_match.get("tournament_type"),
            "cup_stage": cur_match.get("cup_stage"),
            "cup_series_id": s_id,
            "game_num": cur_match.get("game_num_in_series", idx + 1),
            "home_team": home_team,
            "away_team": away_team,
            "h_score": h_score,
            "a_score": a_score,
            "p1_username": p1_un,
            "p2_username": p2_un,
            "p1_str": p1_str,
            "p2_str": p2_str,
            "h_goals": h_goals,
            "a_goals": a_goals,
            "h_assists": h_assists,
            "a_assists": a_assists,
            "is_single_timeline": is_single_timeline,
            "events": events,
            "reporter_id": user_id,
            "photo_id": photo_file_ids[idx] if idx < len(photo_file_ids) else (photo_file_ids[0] if photo_file_ids else None)
        })

    import uuid
    draft_uuid = str(uuid.uuid4())[:8]
    
    is_multi = len(prepared_games) > 1
    
    if not is_multi:
        g = prepared_games[0]
        draft_data = {
            "is_multi": False,
            "match_id": g["match_id"],
            "round_number": g["round_number"],
            "home_team": g["home_team"],
            "away_team": g["away_team"],
            "h_score": g["h_score"],
            "a_score": g["a_score"],
            "p1_username": g["p1_username"],
            "p2_username": g["p2_username"],
            "h_goals": g["h_goals"],
            "a_goals": g["a_goals"],
            "h_assists": g["h_assists"],
            "a_assists": g["a_assists"],
            "is_single_timeline": g["is_single_timeline"],
            "events": g["events"],
            "reporter_id": g["reporter_id"],
            "photo_id": g["photo_id"],
            "games": prepared_games
        }
        group_text = build_formatted_match_post(
            round_number=g["round_number"],
            home_team=g["home_team"],
            away_team=g["away_team"],
            h_score=g["h_score"],
            a_score=g["a_score"],
            p1_username=g["p1_username"],
            p2_username=g["p2_username"],
            h_goals=g["h_goals"],
            a_goals=g["a_goals"],
            h_assists=g["h_assists"],
            a_assists=g["a_assists"],
            is_single_timeline=g["is_single_timeline"],
            is_pm=False,
            match_id=g["match_id"],
            is_draft=True
        )
    else:
        draft_data = {
            "is_multi": True,
            "s_id": s_id,
            "games": prepared_games
        }
        
        if is_cup:
            stage_title = f"{cup_stage} Финала" if cup_stage != "final" else "ФИНАЛ"
            post_lines = [f"📝 <b>ЧЕРНОВИК РЕЗУЛЬТАТОВ СЕРИИ | КУБОК КПЛ - {stage_title}</b>\n"]
        else:
            post_lines = ["📝 <b>ЧЕРНОВИК РЕЗУЛЬТАТОВ МАТЧЕЙ</b>\n"]

        def _fmt(data):
            if not data: return ""
            return ", ".join([f"{p} ({c})" if c > 1 else f"{p} (1)" for p, c in data.items() if c > 0])

        for g in prepared_games:
            h_team_esc = html.escape(g["home_team"])
            a_team_esc = html.escape(g["away_team"])
            
            post_lines.append(f"🏟 <b>Игра {g['game_num']}:</b>")
            post_lines.append(f"🏠 <b>{h_team_esc}</b>{g['p1_str']} <b>{g['h_score']} : {g['a_score']}</b> <b>{a_team_esc}</b>{g['p2_str']} ✈️")
            
            h_g_str = _fmt(g["h_goals"])
            a_g_str = _fmt(g["a_goals"])
            h_a_str = _fmt(g["h_assists"])
            a_a_str = _fmt(g["a_assists"])
            
            if g["h_score"] > 0:
                post_lines.append(f"⚽ <b>Голы ({h_team_esc}):</b> {html.escape(h_g_str) if h_g_str else 'не указаны'}")
                if not g["is_single_timeline"]:
                    post_lines.append(f"🎯 <b>Ассисты ({h_team_esc}):</b> {html.escape(h_a_str) if h_a_str else 'Нет'}")
            if g["a_score"] > 0:
                post_lines.append(f"⚽ <b>Голы ({a_team_esc}):</b> {html.escape(a_g_str) if a_g_str else 'не указаны'}")
                if not g["is_single_timeline"]:
                    post_lines.append(f"🎯 <b>Ассисты ({a_team_esc}):</b> {html.escape(a_a_str) if a_a_str else 'Нет'}")
            post_lines.append("")

        if is_cup and s_id:
            s_row = database.get_cup_series(s_id)
            if s_row:
                team1_n = s_row["team1_name"]
                team2_n = s_row["team2_name"]
                
                with database.transaction() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id, player1_team, player2_team, player1_score, player2_score FROM matches WHERE cup_series_id = ? AND status = 'confirmed'", (s_id,))
                    conf_matches = cursor.fetchall()
                
                prep_game_ids = set(g.get("match_id") for g in prepared_games if g.get("match_id"))
                
                t1_wins = 0
                t2_wins = 0
                
                for cm in conf_matches:
                    if cm["id"] in prep_game_ids:
                        continue
                    s1, s2 = cm["player1_score"] or 0, cm["player2_score"] or 0
                    if s1 > s2: w = cm["player1_team"]
                    elif s2 > s1: w = cm["player2_team"]
                    else: continue
                    if w and database.teams_match(w, team1_n): t1_wins += 1
                    elif w and database.teams_match(w, team2_n): t2_wins += 1
                
                for g in prepared_games:
                    if g["h_score"] > g["a_score"]: w = g["home_team"]
                    elif g["a_score"] > g["h_score"]: w = g["away_team"]
                    else: continue
                    if database.teams_match(w, team1_n): t1_wins += 1
                    elif database.teams_match(w, team2_n): t2_wins += 1
                s_stage = (s_row.get("stage") or "1/8").lower()
                wins_needed = 3 if s_stage == 'final' else 2
                best_of_text = "Best-of-5" if s_stage == 'final' else "Best-of-3"

                post_lines.append(f"📊 <b>Счёт серии ({best_of_text}): {html.escape(team1_n)} {t1_wins} : {t2_wins} {html.escape(team2_n)}</b>")
                if t1_wins >= wins_needed or t2_wins >= wins_needed:
                    series_win = team1_n if t1_wins >= wins_needed else team2_n
                    if s_stage == 'final':
                        post_lines.append(f"🏆 <b>ЧЕМПИОН КУБКА КПЛ 2026: {html.escape(series_win)}! ПОЗДРАВЛЯЕМ С ПОБЕДОЙ В ТУРНИРЕ! 🎉</b>")
                    else:
                        post_lines.append(f"🏆 <b>Победитель серии: {html.escape(series_win)}! Проходит в следующий раунд!</b>")

        post_lines.append("\n⏳ <i>Ожидает подтверждения администратором...</i>")
        group_text = "\n".join(post_lines)

    if "drafts" not in context.bot_data:
        context.bot_data["drafts"] = {}
    context.bot_data["drafts"][draft_uuid] = draft_data

    btn_label = "✅ Подтвердить все игры" if is_multi else "✅ Подтвердить"
    keyboard = [
        [InlineKeyboardButton(btn_label, callback_data=f"draft_conf_{draft_uuid}")],
        [InlineKeyboardButton("❌ Отклонить", callback_data=f"draft_rej_{draft_uuid}")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    
    await status_msg.delete()
    
    photo_id_to_send = photo_file_ids[0] if photo_file_ids else None
    try:
        kwargs = {"chat_id": update.effective_chat.id, "parse_mode": "HTML", "reply_markup": markup}
        if msg_ids:
            kwargs["reply_to_message_id"] = msg_ids[0]
            
        if photo_id_to_send and len(group_text) <= 1024:
            try:
                kwargs["photo"] = photo_id_to_send
                kwargs["caption"] = group_text
                await context.bot.send_photo(**kwargs)
            except Exception as e:
                logger.warning(f"Failed to send draft photo preview ({e}), falling back to text message")
                kwargs.pop("photo", None)
                kwargs.pop("caption", None)
                kwargs["text"] = group_text
                await context.bot.send_message(**kwargs)
        else:
            kwargs["text"] = group_text
            await context.bot.send_message(**kwargs)
    except Exception as e:
        logger.exception("Failed to send draft preview")

from telegram.ext import CallbackQueryHandler
from handlers.admin import is_admin

async def cb_draft_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query: return
    await query.answer()
    
    if not is_admin(query.from_user.id):
        await query.answer("Только администратор может подтверждать черновики!", show_alert=True)
        return
        
    draft_uuid = query.data.replace("draft_conf_", "")
    drafts = context.bot_data.get("drafts", {})
    if draft_uuid not in drafts:
        if query.message.photo: await query.edit_message_caption(caption="❌ Данные черновика устарели или не найдены.")
        else: await query.edit_message_text(text="❌ Данные черновика устарели или не найдены.")
        return
        
    draft = drafts.pop(draft_uuid)
    is_multi = draft.get("is_multi", False)
    games = draft.get("games", [draft])
    s_id = draft.get("s_id")
    
    last_next_stage = None
    main_group_id = await asyncio.to_thread(database.get_group_id)
    results_topic_id = (await asyncio.to_thread(database.get_config, "results_topic_id")) or (await asyncio.to_thread(database.get_config, "reports_topic_id"))

    for idx, g in enumerate(games):
        m_id = g.get("match_id")
        if not m_id and s_id:
            m_id = await asyncio.to_thread(database.ensure_cup_match_exists, s_id, g.get("game_num", idx + 1))
            
        if not m_id:
            logger.error(f"Could not resolve match_id for game {idx+1}")
            continue
            
        try:
            next_stage = await asyncio.to_thread(
                database.confirm_and_finalize_match,
                m_id, g["h_score"], g["a_score"], g["events"],
                reporter_id=g["reporter_id"], photo_id=g["photo_id"]
            )
            if next_stage:
                last_next_stage = next_stage
        except Exception as e:
            logger.exception(f"Failed to confirm match {m_id}")
            
        official_text = build_formatted_match_post(
            round_number=g.get('round_number'),
            home_team=g.get('home_team'),
            away_team=g.get('away_team'),
            h_score=g.get('h_score'),
            a_score=g.get('a_score'),
            p1_username=g.get('p1_username'),
            p2_username=g.get('p2_username'),
            h_goals=g.get('h_goals'),
            a_goals=g.get('a_goals'),
            h_assists=g.get('h_assists'),
            a_assists=g.get('a_assists'),
            is_single_timeline=g.get('is_single_timeline', False),
            is_pm=False,
            match_id=m_id,
            is_draft=False
        )
        
        if main_group_id:
            try:
                kwargs = {"chat_id": main_group_id, "parse_mode": "HTML"}
                if results_topic_id: kwargs["message_thread_id"] = int(results_topic_id)
                if g.get("photo_id") and len(official_text) <= 1024:
                    kwargs["photo"] = g["photo_id"]
                    kwargs["caption"] = official_text
                    await context.bot.send_photo(**kwargs)
                else:
                    kwargs["text"] = official_text
                    await context.bot.send_message(**kwargs)
            except Exception as e:
                logger.error(f"Failed to send match post to group: {e}")

    if last_next_stage:
        from handlers.admin import notify_cup_stage_opened
        await notify_cup_stage_opened(context.bot, last_next_stage)
        
    admin_name = f"@{query.from_user.username}" if query.from_user.username else (query.from_user.first_name or "Администратор")
    original_text = query.message.caption if query.message.photo else query.message.text
    cleaned_text = (original_text or "").replace("⏳ <i>Ожидает подтверждения администратором...</i>", "").strip()
    new_caption = f"{cleaned_text}\n\n✅ <b>Одобрено администратором {html.escape(admin_name)}.</b>"
    if query.message.photo:
        try:
            if len(new_caption) <= 1024:
                await query.edit_message_caption(caption=new_caption, parse_mode="HTML")
            else:
                await query.edit_message_caption(caption=new_caption[:1015] + "...", parse_mode="HTML")
        except Exception:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(new_caption, parse_mode="HTML")
    else:
        await query.edit_message_text(text=new_caption, parse_mode="HTML")
        
    from handlers.cabinet import refresh_league_table, refresh_debts_summary
    await refresh_debts_summary(context)
    await refresh_league_table(context)

async def cb_draft_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query: return
    await query.answer()
    
    if not is_admin(query.from_user.id):
        await query.answer("Только администратор может отклонять черновики!", show_alert=True)
        return
        
    draft_uuid = query.data.replace("draft_rej_", "")
    drafts = context.bot_data.get("drafts", {})
    drafts.pop(draft_uuid, None)
    
    admin_name = f"@{query.from_user.username}" if query.from_user.username else (query.from_user.first_name or "Администратор")
    original_text = query.message.caption if query.message.photo else query.message.text
    cleaned_text = (original_text or "").replace("⏳ <i>Ожидает подтверждения администратором...</i>", "").strip()
    new_caption = f"{cleaned_text}\n\n❌ <b>Черновик отклонен администратором {html.escape(admin_name)}.</b>"
    if query.message.photo:
        try:
            if len(new_caption) <= 1024:
                await query.edit_message_caption(caption=new_caption, parse_mode="HTML")
            else:
                await query.edit_message_caption(caption=new_caption[:1015] + "...", parse_mode="HTML")
        except Exception:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(new_caption, parse_mode="HTML")
    else:
        await query.edit_message_text(text=new_caption, parse_mode="HTML")
