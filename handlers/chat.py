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

    # 1. Gather Context concurrently
    (
        user_data,
        standings,
        top_scorers,
        top_assists,
        recent_matches,
        all_squads,
        all_rounds,
        recent_form_map,
        pending_matches
    ) = await asyncio.gather(
        asyncio.to_thread(database.get_user, user_id),
        asyncio.to_thread(database.get_standings),
        asyncio.to_thread(database.get_top_scorers, limit=15),
        asyncio.to_thread(database.get_top_assists, limit=15),
        asyncio.to_thread(database.get_recent_confirmed_matches, limit=10),
        asyncio.to_thread(database.get_all_squads),
        asyncio.to_thread(database.get_all_rounds),
        asyncio.to_thread(database.get_teams_recent_form, 5),
        asyncio.to_thread(database.get_open_pending_matches)
    )

    user_team = user_data["team_name"] if user_data else "Не зарегистрирован"
    username = user_data["username"] if user_data else update.effective_user.username or str(user_id)
    
    # Standings
    standings_text = "🏆 ТУРНИРНАЯ ТАБЛИЦА:\n"
    for i, st in enumerate(standings, 1):
        standings_text += f"{i}. {st['team_name']} (@{st['username'] or '—'}) — Очки: {st['points']} (И:{st['played']} В:{st['wins']} Н:{st['draws']} П:{st['losses']}, Г:{st['goals_scored']}-{st['goals_conceded']})\n"

    # Top Scorers
    scorers_text = "⚽ ТОП БОМБАРДИРОВ:\n"
    if top_scorers:
        for i, sc in enumerate(top_scorers, 1):
            scorers_text += f"{i}. {sc['player_name']} ({sc['team_name']}) — {sc['total_goals']} голов\n"
    else:
        scorers_text += "Пока нет зарегистрированных голов.\n"

    # Top Assists
    assists_text = "🎯 ТОП АССИСТЕНТОВ:\n"
    if top_assists:
        for i, asst in enumerate(top_assists, 1):
            assists_text += f"{i}. {asst['player_name']} ({asst['team_name']}) — {asst['total_assists']} ассистов\n"
    else:
        assists_text += "Пока нет зарегистрированных ассистов.\n"

    # Recent Matches
    matches_text = "📊 ПОСЛЕДНИЕ СЫГРАННЫЕ МАТЧИ:\n"
    if recent_matches:
        for m in recent_matches:
            matches_text += f"Тур {m['round_number']}: {m['team1']} {m['player1_score']} : {m['player2_score']} {m['team2']}\n"
    else:
        matches_text += "Сыгранных матчей пока нет.\n"

    # Squads Summary
    squads_text = "👥 СОСТАВЫ КЛУБОВ (ИГРОКИ ИХ КЛУБОВ):\n"
    if all_squads:
        for team, players in all_squads.items():
            squads_text += f"• {team}: {', '.join(players)}\n"
    else:
        squads_text += "Составы пока не занесены.\n"

    # Tournament rounds info
    all_rounds_list = all_rounds or [30]
    total_rounds = max(all_rounds_list) if all_rounds_list else 30
    
    # Team Recent Form
    form_text = "📈 ФОРМА КОМАНД (последние игры: W=Победа, D=Ничья, L=Поражение):\n"
    for st in standings:
        uid = st.get("telegram_id")
        form_list = recent_form_map.get(uid, [])
        form_str = "-".join(form_list) if form_list else "нет игр"
        form_text += f"• {st['team_name']}: {form_str}\n"

    # Full upcoming schedule (all pending/unplayed matches)
    schedule_by_round: dict[int, list[str]] = {}
    for pm in pending_matches:
        rnd = pm.get("round_number", "?")
        team1 = pm.get("player1_team", "?")
        team2 = pm.get("player2_team", "?")
        nick1 = pm.get("player1_nickname", "")
        nick2 = pm.get("player2_nickname", "")
        deadline = pm.get("deadline", "")
        line = f"{team1} (@{nick1}) vs {team2} (@{nick2})"
        if deadline:
            line += f" [дедлайн: {deadline}]"
        schedule_by_round.setdefault(rnd, []).append(line)

    schedule_text = "📅 РАСПИСАНИЕ ПРЕДСТОЯЩИХ МАТЧЕЙ (ещё не сыгранные):\n"
    if schedule_by_round:
        for rnd in sorted(schedule_by_round.keys()):
            schedule_text += f"\nТур {rnd}:\n"
            for entry in schedule_by_round[rnd]:
                schedule_text += f"  • {entry}\n"
    else:
        schedule_text += "Все матчи уже сыграны или расписание ещё не загружено.\n"


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

    # Official League Rules & Info
    league_rules_text = (
        "📜 ОФИЦИАЛЬНЫЙ РЕГЛАМЕНТ И ПРАВИЛА ТУРНИРА ('Топ 7 лиг'):\n"
        "• Составы и трансферы: Составы по Transfermarkt на 22.03.2026. Игроки без клуба или завершившие карьеру (Навас, Коутиньо) — ЗАПРЕЩЕНЫ. Карты Кумиров (Icons) и Героев (Heroes) — ЗАПРЕЩЕНЫ.\n"
        "• Карточки и OVR: Максимум 111 OVR (без учета рангов). Карты 111+ запрещены. В составе и на поле во время матча должно быть ровно 6 спешл-карт (7-я спешл-карта запрещена). Также на поле должно быть минимум 5 игроков вашей команды.\n"
        "• Прокачка и тренировки: Изначально дается 20 тренировок (минимальный порог 60 тренировок с победами). Прокачка игрока: максимум 20 тренировок и 3 усиления навыков (Фиолетовый ранг). Красный и Золотой ранги ЗАПРЕЩЕНЫ. За победы в Лиге/Кубке/Еврокубках дает +1 тренировка. За активный канал клуба — +10 тренировок (после 5 официальных матчей).\n"
        "• 🔴 ЗАПРЕЩЕННЫЕ ПРИЕМЫ В МАТЧЕ:\n"
        "  1. Голы с навесов и навесы со штрафных — ЗАПРЕЩЕНЫ.\n"
        "  2. Навесы с угловых — ЗАПРЕЩЕНЫ (разыгрываем угловые только на 'балансе').\n"
        "  3. Забросы с центра поля при розыгрыше и пасы низом на забегающего — ЗАПРЕЩЕНЫ.\n"
        "  4. Забросы в штрафную и забросы 'на ход' — ЗАПРЕЩЕНЫ.\n"
        "  5. Финт 'пятка об пятку' и 'переступ и выход' (на чужой половине нужно выбить мяч, на своей — пас назад).\n"
        "  6. Умышленное затягивание времени (особенно с 70 по 90 мин).\n"
        "• Судья турнира: @onvamneVSAplayer (принимает окончательные решения по спорам). Главный админ / правила: @antonv2801.\n"
        "• Ограничения: Ничьи переигрывать нельзя (тех. поражение/снятие очков). Уходить с поста тренера до конца сезона запрещено (ЧС турнира).\n"
        "• Кубок и Награды: Есть Кубок КПЛ (стадия 1/8). В конце сезона вручается премия 'Золотой Мяч'. Красивые голы отправлять @antonv2801.\n"
    )

    context_data = (
        f"Пользователь, который с тобой говорит: {username} (тренер команды '{user_team}').\n"
        f"Всего туров в турнире: {total_rounds}.\n\n"
        f"{standings_text}\n"
        f"{form_text}\n"
        f"{schedule_text}\n"
        f"{scorers_text}\n"
        f"{assists_text}\n"
        f"{matches_text}\n"
        f"{squads_text}\n"
        f"{past_seasons_text}\n"
        f"{league_rules_text}"
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
