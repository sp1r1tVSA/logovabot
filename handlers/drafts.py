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
# { "media_group_id": { "photos": [...], "caption": "", "user_id": int, "message_ids": [...] } }
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
    media_group_id = msg.media_group_id
    
    if not media_group_id:
        # Fallback for single photo
        media_group_id = f"single_{msg.message_id}"
        
    if media_group_id not in draft_media_groups:
        draft_media_groups[media_group_id] = {
            "photos": [],
            "caption": "",
            "user_id": user_id,
            "message_ids": []
        }
        
    group_data = draft_media_groups[media_group_id]
    group_data["message_ids"].append(msg.message_id)
    
    text = msg.caption or msg.text
    if text and not group_data["caption"]:
        group_data["caption"] = text
        
    if msg.photo:
        # get highest resolution
        photo = msg.photo[-1]
        f_obj = await context.bot.get_file(photo.file_id)
        f_bytes = await f_obj.download_as_bytearray()
        group_data["photos"].append(bytes(f_bytes))
        
    # Schedule processing task if it's the first message of the group
    if media_group_id not in draft_tasks:
        draft_tasks[media_group_id] = asyncio.create_task(
            _process_draft_group_delayed(media_group_id, update, context)
        )

async def _process_draft_group_delayed(media_group_id: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Wait a few seconds to let all media in the group arrive
    await asyncio.sleep(4)
    
    group_data = draft_media_groups.pop(media_group_id, None)
    draft_tasks.pop(media_group_id, None)
    
    if not group_data:
        return
        
    photos = group_data["photos"]
    caption = group_data["caption"]
    user_id = group_data["user_id"]
    msg_ids = group_data["message_ids"]
    reply_to_id = msg_ids[0] if msg_ids else None
    
    if not photos:
        # Just text, ignore. Or prompt user for screenshots?
        # Let's just ignore to not spam.
        return
        
    status_msg = await update.effective_message.reply_text(
        "⏳ Обрабатываю результат через ИИ...", 
        reply_to_message_id=reply_to_id
    )
    
    try:
        ai_res = await asyncio.to_thread(
            recognize_match_screenshots_bytes,
            photos,
            caption=caption
        )
    except Exception as e:
        logger.exception("Error in draft AI processing")
        await status_msg.edit_text("❌ Ошибка при распознавании скриншота.")
        return
        
    if not ai_res:
        await status_msg.edit_text("🤖 ИИ не смог распознать результаты матча. Убедитесь, что скриншоты чёткие.")
        return
        
    t1 = ai_res.get("team1")
    t2 = ai_res.get("team2")
    
    if not t1 or not t2:
        await status_msg.edit_text("🤖 ИИ распознал счет, но не смог определить названия команд. Пожалуйста, напишите их текстом в описании к фото.")
        return
        
    # Search for match
    match = database.get_active_match_by_teams(t1, t2)
    if not match:
        await status_msg.edit_text(f"❌ Не найден активный матч между командами {html.escape(t1)} и {html.escape(t2)}.\nВозможно, названия команд в подписи указаны неточно.")
        return
        
    match_id = match["id"]
    home_team = match["player1_team"] or match["player1_nickname"]
    away_team = match["player2_team"] or match["player2_nickname"]
    
    s1_goals = ai_res.get("side1_goals") or ai_res.get("home_goals") or []
    s2_goals = ai_res.get("side2_goals") or ai_res.get("away_goals") or []
    s1_assists = ai_res.get("side1_assists") or ai_res.get("home_assists") or []
    s2_assists = ai_res.get("side2_assists") or ai_res.get("away_assists") or []
    is_single_timeline = bool(ai_res.get("is_single_timeline", False))
    
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
            
    events = []
    for p, c in h_goals.items(): events.append((home_team, p, "goal", c))
    for p, c in a_goals.items(): events.append((away_team, p, "goal", c))
    for p, c in h_assists.items(): events.append((home_team, p, "assist", c))
    for p, c in a_assists.items(): events.append((away_team, p, "assist", c))
    
    # Send draft confirmation message in Drafts topic
    # In drafts we confirm the match right away for the admins/group since it's drafted
    photo_id_to_save = update.message.photo[-1].file_id if update.message and update.message.photo else None
    
    try:
        next_stage = await asyncio.to_thread(database.confirm_and_finalize_match, match_id, h_score, a_score, events, reporter_id=user_id, photo_id=photo_id_to_save)
        if next_stage:
            from handlers.admin import notify_cup_stage_opened
            await notify_cup_stage_opened(context.bot, next_stage)
    except Exception as e:
        logger.exception("Failed to confirm drafted match")
        await status_msg.edit_text("❌ Ошибка при сохранении матча в базу.")
        return
        
    await status_msg.edit_text(f"✅ Матч #{match_id} ({html.escape(home_team)} vs {html.escape(away_team)}) успешно распознан и внесён в базу!")
    
    # Broadcast to Results/Reports topic
    main_group_id = await asyncio.to_thread(database.get_group_id)
    results_topic_id = (await asyncio.to_thread(database.get_config, "results_topic_id")) or (await asyncio.to_thread(database.get_config, "reports_topic_id"))
    
    if main_group_id:
        group_text = build_formatted_match_post(
            round_number=match.get('round_number'),
            home_team=home_team,
            away_team=away_team,
            h_score=h_score,
            a_score=a_score,
            p1_username=match.get('player1_username', 'Хозяева'),
            p2_username=match.get('player2_username', 'Гости'),
            h_goals=h_goals,
            a_goals=a_goals,
            h_assists=h_assists,
            a_assists=a_assists,
            is_single_timeline=is_single_timeline,
            is_pm=False,
            match_id=match_id
        )
        try:
            kwargs = {"chat_id": main_group_id, "parse_mode": "HTML"}
            if results_topic_id:
                kwargs["message_thread_id"] = int(results_topic_id)
            if photo_id_to_save:
                kwargs["photo"] = photo_id_to_save
                kwargs["caption"] = group_text
                await context.bot.send_photo(**kwargs)
            else:
                kwargs["text"] = group_text
                await context.bot.send_message(**kwargs)
        except Exception as e:
            logger.exception("Failed to post result to group from draft")
