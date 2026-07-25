import asyncio
import logging
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
import database
import ai_chat

logger = logging.getLogger(__name__)

async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик свободных сообщений. Реагирует, если сообщение начинается со слова "темшик".
    Подтягивает турнирную таблицу и информацию об игроке в качестве контекста.
    """
    if not update.message or not update.message.text:
        return

    user_text = update.message.text.strip()

    # Trigger word check (case-insensitive)
    if not user_text.lower().startswith("темшик"):
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Notify user that bot is "typing..."
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception as e:
        logger.warning(f"Failed to send typing action: {e}")

    # 1. Gather Context
    user_data = database.get_user(user_id)
    user_team = user_data["team_name"] if user_data else "Не зарегистрирован"
    username = user_data["username"] if user_data else update.effective_user.username or str(user_id)
    
    # Standings
    standings = database.get_standings()
    standings_text = "🏆 ТУРНИРНАЯ ТАБЛИЦА:\n"
    for i, st in enumerate(standings, 1):
        standings_text += f"{i}. {st['team_name']} (@{st['username'] or '—'}) — Очки: {st['points']} (И:{st['played']} В:{st['wins']} Н:{st['draws']} П:{st['losses']}, Г:{st['goals_scored']}-{st['goals_conceded']})\n"

    # Top Scorers
    top_scorers = database.get_top_scorers(limit=15)
    scorers_text = "⚽ ТОП БОМБАРДИРОВ:\n"
    if top_scorers:
        for i, sc in enumerate(top_scorers, 1):
            scorers_text += f"{i}. {sc['player_name']} ({sc['team_name']}) — {sc['total_goals']} голов\n"
    else:
        scorers_text += "Пока нет зарегистрированных голов.\n"

    # Top Assists
    top_assists = database.get_top_assists(limit=15)
    assists_text = "🎯 ТОП АССИСТЕНТОВ:\n"
    if top_assists:
        for i, asst in enumerate(top_assists, 1):
            assists_text += f"{i}. {asst['player_name']} ({asst['team_name']}) — {asst['total_assists']} ассистов\n"
    else:
        assists_text += "Пока нет зарегистрированных ассистов.\n"

    # Recent Matches
    recent_matches = database.get_recent_confirmed_matches(limit=10)
    matches_text = "📊 ПОСЛЕДНИЕ СЫГРАННЫЕ МАТЧИ:\n"
    if recent_matches:
        for m in recent_matches:
            matches_text += f"Тур {m['round_number']}: {m['team1']} {m['player1_score']} : {m['player2_score']} {m['team2']}\n"
    else:
        matches_text += "Сыгранных матчей пока нет.\n"

    # Squads Summary
    all_squads = database.get_all_squads()
    squads_text = "👥 СОСТАВЫ КЛУБОВ (ИГРОКИ ИХ КЛУБОВ):\n"
    if all_squads:
        for team, players in all_squads.items():
            squads_text += f"• {team}: {', '.join(players)}\n"
    else:
        squads_text += "Составы пока не занесены.\n"

    # Tournament rounds info
    all_rounds = database.get_all_rounds() or [30]
    total_rounds = max(all_rounds) if all_rounds else 30
    
    # Team Recent Form
    recent_form_map = database.get_teams_recent_form(5)
    form_text = "📈 ФОРМА КОМАНД (последние игры: W=Победа, D=Ничья, L=Поражение):\n"
    for st in standings:
        uid = st.get("telegram_id")
        form_list = recent_form_map.get(uid, [])
        form_str = "-".join(form_list) if form_list else "нет игр"
        form_text += f"• {st['team_name']}: {form_str}\n"

    # History of past seasons
    past_seasons_text = (
        "📜 ИСТОРИЯ ПРОШЛЫХ СЕЗОНОВ ЛИГИ (КПЛ):\n\n"
        "=== ИТОГИ ПРОШЛОГО СЕЗОНА (СЕЗОН 2) ===\n"
        "• Чемпион: Расинг (@Vazya4mo666) — 74 очка, 108 голов (забрал золото и +7 тренировок). Двукратный чемпион!\n"
        "• 2 место: Брага (@Saharokk8830) — 67 очков (серебро).\n"
        "• 3 место: АЕК (@Snikers2121) — 62 очка (бронза).\n"
        "• 4 место: Порту (@lvckri) — 58 очков.\n"
        "• 5 место: Бенфика (@vtrrgyg) — 57 очков. Выиграла Кубок КПЛ (3:1 vs Расинг) и 🏆Лигу Конференций (vs Аль-Наср).\n"
        "• 6 место: Фейеноорд (@GeorgiyKostenko) — 54 очка.\n"
        "• 7 место: Аякс (@LachesisQQQ) — 50 очков.\n"
        "• 8-9 места: Копенгаген (@crcsss) и Бока Хуниорс (@k1nkyua) — по 45 очков.\n"
        "• 10-13 места: Ривер Плейт (31), Селтик (28), Спортинг (27), Будё-Глимт (25).\n"
        "• 14-16 места (аутсайдеры): ПСВ (22), Рейнджерс (16), Брюгге (@malenkihyi) (14).\n"
        "• Герои Сезона 2: Igor Paixao (Бенфика, 50 голов), Gittens (44 гола). Ассистенты: Bardghji, Ndoye, Rafa (по 24).\n\n"
        "=== ИТОГИ ПОЗАПРОШЛОГО СЕЗОНА (СЕЗОН 1) ===\n"
        "• Чемпион: Расинг (@Vazya4mo666) — вырвал золото у Браги в 1 очко!\n"
        "• 2 место: Брага (@Saharokk8830) — 71 очко.\n"
        "• 3 место: АЕК (@Snikers2121) — 63 очка. Победитель Кубка КПЛ.\n"
        "• 4 место: Порту (@lvckri) — 55 очков.\n"
        "• 5-6 места: Копенгаген (50) и ПСВ (50).\n"
        "• Победитель Лиги Европы: Аякс (@LachesisQQQ).\n"
        "• 15-16 места: Рейнджерс и Брюгге.\n"
        "• Герои Сезона 1: Pineda (АЕК, 44 гола), Perisic (21 ассист).\n"
    )

    context_data = (
        f"Пользователь, который с тобой говорит: {username} (тренер команды '{user_team}').\n"
        f"Всего туров в турнире: {total_rounds}.\n\n"
        f"{standings_text}\n"
        f"{form_text}\n"
        f"{scorers_text}\n"
        f"{assists_text}\n"
        f"{matches_text}\n"
        f"{squads_text}\n"
        f"{past_seasons_text}"
    )

    # 2. History Management
    if "chat_history" not in context.user_data:
        context.user_data["chat_history"] = []
        
    chat_history = context.user_data["chat_history"]

    # 3. Call AI non-blocking via thread
    reply_text = await asyncio.to_thread(ai_chat.generate_chat_reply, user_id, user_text, chat_history, context_data)

    # 4. Save to history
    chat_history.append({"role": "user", "text": user_text})
    chat_history.append({"role": "model", "text": reply_text})
    
    # Keep only last 10 messages (5 pairs) to avoid context bloat
    if len(chat_history) > 10:
        context.user_data["chat_history"] = chat_history[-10:]

    # 5. Send reply
    await update.message.reply_text(reply_text)
