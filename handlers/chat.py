import re
import asyncio
import logging
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
import database
from services.ai import ai_chat
from services.topic_cache import topic_cache
from handlers.text_commands import handle_temshik_command


logger = logging.getLogger(__name__)


async def _none():
    """Awaitable placeholder so asyncio.gather slots stay positional when a query is skipped."""
    return None


async def _empty_list():
    """Awaitable placeholder for skipped list-returning queries."""
    return []


async def _empty_dict():
    """Awaitable placeholder for skipped dict-returning queries."""
    return {}


async def _resolve_division_id(update: Update, user_data) -> int | None:
    """
    Определить дивизион, в контексте которого говорит пользователь.

    Приоритет: топик дивизиона → группа дивизиона → дивизион самого игрока.
    В личке первые два шага не срабатывают, остаётся привязка из users.division_id.
    Возвращает None, если игрок никуда не приписан — тогда Темшик честно говорит,
    что турнирных данных не видит, вместо выдачи каши из чужих дивизионов.
    """
    chat = update.effective_chat
    msg = update.effective_message

    if chat and chat.type in ("group", "supergroup"):
        thread_id = getattr(msg, "message_thread_id", None) if msg else None
        if thread_id:
            try:
                binding = topic_cache.get_by_topic(chat.id, thread_id)
                if binding and binding.get("division_id"):
                    return binding["division_id"]
            except Exception:
                logger.warning("AI chat: topic_cache lookup failed", exc_info=True)

        try:
            div = await asyncio.to_thread(database.get_division_by_group, chat.id)
            if div and div.get("id"):
                return div["id"]
        except Exception:
            logger.warning("AI chat: division-by-group lookup failed", exc_info=True)

    try:
        if user_data is not None and user_data["division_id"]:
            return user_data["division_id"]
    except (KeyError, IndexError):
        pass

    return None


async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик свободных сообщений и голосовых сообщений для ИИ Темшика.
    Подтягивает турнирную таблицу и информацию об игроке в качестве контекста.
    """
    if not update.message:
        return

    # Check if text is a tournament text command (e.g. "Темшик таблица", "Темшик состав")
    if update.message.text:
        handled = await handle_temshik_command(update, context)
        if handled:
            return

    is_voice_input = bool(update.message.voice)
    user_text = update.message.text.strip() if update.message.text else ""
    audio_input_bytes = None

    if is_voice_input:
        wants_voice = True
        try:
            vfile = await update.message.voice.get_file()
            audio_input_bytes = bytes(await vfile.download_as_bytearray())
            user_text = "(Голосовое сообщение)"
        except Exception as e:
            logger.error(f"Failed to download user voice message: {e}")
    else:
        if not user_text:
            return
        # Сообщение приходит как ответ (reply) на сообщение бота
        is_reply_to_bot = False
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            try:
                is_reply_to_bot = update.message.reply_to_message.from_user.id == context.bot.id
            except Exception:
                is_reply_to_bot = False
        # Если текстовое сообщение НЕ начинается с "темшик" и НЕ является ответом на бота
        if not user_text.lower().startswith("темшик") and not is_reply_to_bot:
            if re.match(r"^\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}$", user_text):
                await update.message.reply_text(
                    "⚠️ **Сессия ввода прервана из-за перезапуска бота.**\n\n"
                    "Пожалуйста, откройте админ-панель заново и повторите ввод дедлайна.",
                    parse_mode="Markdown"
                )
            return

        voice_keywords = ["голос", "озвучь", "проговори", "аудио", "скажи голосом", "поговори"]
        wants_voice = any(kw in user_text.lower() for kw in voice_keywords)

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Notify user that bot is "typing..."
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception as e:
        logger.warning(f"Failed to send typing action: {e}")

    # 1. Resolve the scope first: everything below is strictly one division + one season.
    user_data, active_season, chat_history, chat_mode = await asyncio.gather(
        asyncio.to_thread(database.get_user, user_id),
        asyncio.to_thread(database.get_active_season),
        asyncio.to_thread(database.get_chat_history, user_id, 10),
        asyncio.to_thread(database.get_config, "chat_mode")
    )

    season_id = active_season["id"] if active_season else None
    season_name = (active_season["name"] if active_season else None) or "текущий сезон"
    division_id = await _resolve_division_id(update, user_data)

    # 2. Gather division-scoped context concurrently
    (
        division,
        divisions,
        standings,
        top_scorers,
        top_assists,
        recent_matches,
        all_squads,
        division_rounds,
        recent_form_map,
        pending_matches,
        cup_series,
        division_players,
        season_rules
    ) = await asyncio.gather(
        asyncio.to_thread(database.get_division, division_id) if division_id else _none(),
        asyncio.to_thread(database.get_divisions, True),
        # Без дивизиона эти запросы ушли бы в legacy-ветку и смешали все дивизионы разом,
        # поэтому при неопределённом скоупе не отдаём турнирных данных вообще.
        asyncio.to_thread(database.get_standings, division_id, season_id) if division_id else _empty_list(),
        asyncio.to_thread(database.get_top_scorers, 15, division_id, season_id) if division_id else _empty_list(),
        asyncio.to_thread(database.get_top_assists, 15, division_id, season_id) if division_id else _empty_list(),
        asyncio.to_thread(database.get_recent_confirmed_matches, 10, division_id, season_id) if division_id else _empty_list(),
        asyncio.to_thread(database.get_all_squads) if division_id else _empty_dict(),
        asyncio.to_thread(database.get_division_rounds, division_id) if division_id else _empty_list(),
        asyncio.to_thread(database.get_teams_recent_form, 5, division_id, season_id) if division_id else _empty_dict(),
        asyncio.to_thread(database.get_open_pending_matches) if division_id else _empty_list(),
        asyncio.to_thread(database.get_all_cup_series) if division_id else _empty_list(),
        asyncio.to_thread(database.get_division_users, division_id) if division_id else _empty_list(),
        asyncio.to_thread(database.get_season_rules, season_id, division_id) if (season_id and division_id) else _none(),
    )

    division_name = (division or {}).get("name") if division else None
    if not division_name and division_id:
        division_name = f"Дивизион {division_id}"

    # Teams of this division — used to filter globally-stored data (squads, cup bracket).
    division_team_names = {
        (st.get("team_name") or "").lower() for st in standings if st.get("team_name")
    }
    for p in division_players:
        if p.get("team_name"):
            division_team_names.add(p["team_name"].lower())

    user_team = user_data["team_name"] if user_data else "Не зарегистрирован"
    username = user_data["username"] if user_data else update.effective_user.username or str(user_id)
    user_warn_count = user_data["warn_count"] if user_data and user_data["warn_count"] else 0
    
    # Promotion / relegation zones: division 1 has nothing above it, the last has nothing below.
    sorted_divs = sorted(divisions or [], key=lambda d: (d.get("sort_order") or 0, d.get("id") or 0))
    div_index = next((i for i, d in enumerate(sorted_divs) if d.get("id") == division_id), None)
    has_division_above = div_index is not None and div_index > 0
    has_division_below = div_index is not None and div_index < len(sorted_divs) - 1
    prom_slots = (season_rules or {}).get("promotion_slots", 3) if has_division_above else 0
    rel_slots = (season_rules or {}).get("relegation_slots", 3) if has_division_below else 0

    # Standings (division-scoped) with zone markers
    standings_text = f"🏆 ТУРНИРНАЯ ТАБЛИЦА — {division_name or 'дивизион не определён'} ({season_name}):\n"
    if standings:
        total_teams = len(standings)
        for i, st in enumerate(standings, 1):
            zone = ""
            if prom_slots and i <= prom_slots:
                zone = " 🚀[зона повышения]"
            elif rel_slots and i > total_teams - rel_slots:
                zone = " 🔻[зона вылета]"
            standings_text += (
                f"{i}. {st['team_name']} (@{st['username'] or '—'}) — Очки: {st['points']} "
                f"(И:{st['played']} В:{st['wins']} Н:{st['draws']} П:{st['losses']}, "
                f"Г:{st['goals_scored']}-{st['goals_conceded']}){zone}\n"
            )
    else:
        standings_text += "Таблица пока пустая — сыгранных матчей в этом дивизионе нет.\n"

    # Top Scorers
    scorers_text = "⚽ ТОП БОМБАРДИРОВ ДИВИЗИОНА:\n"
    if top_scorers:
        for i, sc in enumerate(top_scorers, 1):
            scorers_text += f"{i}. {sc['player_name']} ({sc['team_name']}) — {sc['total_goals']} голов\n"
    else:
        scorers_text += "Пока нет зарегистрированных голов.\n"

    # Top Assists
    assists_text = "🎯 ТОП АССИСТЕНТОВ ДИВИЗИОНА:\n"
    if top_assists:
        for i, asst in enumerate(top_assists, 1):
            assists_text += f"{i}. {asst['player_name']} ({asst['team_name']}) — {asst['total_assists']} ассистов\n"
    else:
        assists_text += "Пока нет зарегистрированных ассистов.\n"

    # Recent Matches
    matches_text = "📊 ПОСЛЕДНИЕ СЫГРАННЫЕ МАТЧИ ДИВИЗИОНА:\n"
    if recent_matches:
        for m in recent_matches:
            matches_text += f"Тур {m['round_number']}: {m['team1']} {m['player1_score']} : {m['player2_score']} {m['team2']}\n"
    else:
        matches_text += "Сыгранных матчей пока нет.\n"

    # Squads — stored globally by team name, so keep only clubs of this division.
    squads_text = "👥 СОСТАВЫ КЛУБОВ ДИВИЗИОНА:\n"
    division_squads = {
        team: players for team, players in (all_squads or {}).items()
        if not division_team_names or (team or "").lower() in division_team_names
    }
    if division_squads:
        for team, players in division_squads.items():
            squads_text += f"• {team}: {', '.join(players)}\n"
    else:
        squads_text += "Составы пока не занесены.\n"

    # Rounds played in this division
    rounds_list = division_rounds or []
    total_rounds = max(rounds_list) if rounds_list else 0

    # Team Recent Form (get_teams_recent_form keys by lowercased team name)
    form_text = "📈 ФОРМА КОМАНД (последние игры: W=Победа, D=Ничья, L=Поражение):\n"
    for st in standings:
        form_list = recent_form_map.get((st.get("team_name") or "").lower(), [])
        form_str = "-".join(form_list) if form_list else "нет игр"
        form_text += f"• {st['team_name']}: {form_str}\n"

    # Upcoming schedule — get_open_pending_matches is league-wide, so scope it here.
    schedule_by_round: dict[int, list[str]] = {}
    for pm in pending_matches:
        if division_id is not None and pm.get("division_id") != division_id:
            continue
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

    schedule_text = "📅 РАСПИСАНИЕ ПРЕДСТОЯЩИХ МАТЧЕЙ ДИВИЗИОНА (ещё не сыгранные):\n"
    if schedule_by_round:
        for rnd in sorted(schedule_by_round.keys()):
            schedule_text += f"\nТур {rnd}:\n"
            for entry in schedule_by_round[rnd]:
                schedule_text += f"  • {entry}\n"
    else:
        schedule_text += "Все матчи уже сыграны или расписание ещё не загружено.\n"


    # History of past seasons — archive from the pre-division era.
    # Единая лига КПЛ на 16 клубов больше не существует: турнир разбит на дивизионы.
    # Эти итоги годятся для подколов и историй про старожилов, но НЕ описывают текущий сезон.
    past_seasons_text = (
        "📜 АРХИВ: ИСТОРИЯ ЕДИНОЙ ЛИГИ КПЛ (ЭПОХА ДО ДИВИЗИОНОВ).\n"
        "⚠️ ВАЖНО: это ЗАКРЫТАЯ глава. Тогда была ОДНА общая лига на 16 клубов без дивизионов.\n"
        "Сейчас турнир устроен иначе — дивизионы с повышениями и вылетами, состав участников шире.\n"
        "Используй этот блок ТОЛЬКО как историю и материал для подколов старожилов.\n"
        "НИКОГДА не выдавай эти таблицы и титулы за текущее положение дел.\n\n"
        "=== ИТОГИ ПОСЛЕДНЕГО СЕЗОНА ЕДИНОЙ ЛИГИ ===\n"
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
        "• Герои того сезона: Igor Paixao (Бенфика, 50 голов), Gittens (44 гола). Ассистенты: Bardghji, Ndoye, Rafa (по 24).\n\n"
        "=== ИТОГИ ПРЕДЫДУЩЕГО СЕЗОНА ЕДИНОЙ ЛИГИ ===\n"
        "• Чемпион: Расинг (@Vazya4mo666) — вырвал золото у Браги в 1 очко!\n"
        "• 2 место: Брага (@Saharokk8830) — 71 очко.\n"
        "• 3 место: АЕК (@Snikers2121) — 63 очка. Победитель Кубка КПЛ.\n"
        "• 4 место: Порту (@lvckri) — 55 очков.\n"
        "• 5-6 места: Копенгаген (50) и ПСВ (50).\n"
        "• Победитель Лиги Европы: Аякс (@LachesisQQQ).\n"
        "• 15-16 места: Рейнджерс и Брюгге.\n"
        "• Герои того сезона: Pineda (АЕК, 44 гола), Perisic (21 ассист).\n"
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
        "• Награды: В конце сезона вручается премия 'Золотой Мяч'. Красивые голы отправлять @antonv2801.\n"
    )

    # Cup bracket — единая сетка на весь турнир, не имеет division_id.
    # Оставляем только серии, где участвует клуб из этого дивизиона.
    cup_info_text = "🏆 КУБОК (общий на весь турнир, сетка Best-of-3 — серии клубов ЭТОГО дивизиона):\n"
    cup_lines = []
    for cs in cup_series or []:
        t1, t2 = cs.get("team1_name", "?"), cs.get("team2_name", "?")
        if division_team_names and not (
            (t1 or "").lower() in division_team_names or (t2 or "").lower() in division_team_names
        ):
            continue
        w1, w2 = cs.get("team1_wins", 0) or 0, cs.get("team2_wins", 0) or 0
        stage = cs.get("stage") or "?"
        winner = cs.get("winner_name")
        line = f"• [{stage}] {t1} {w1}:{w2} {t2}"
        if winner:
            line += f" — прошёл дальше: {winner}"
        elif (cs.get("status") or "") == "active":
            line += " — серия ещё идёт"
        cup_lines.append(line)

    if cup_lines:
        cup_info_text += "\n".join(cup_lines) + "\n"
    else:
        cup_info_text += "Клубы этого дивизиона в кубковой сетке сейчас не представлены.\n"

    # Opponents list: club -> coach username (только тренеры этого дивизиона)
    opponents_text = ""
    for p in division_players or []:
        team = p.get("team_name")
        uname = p.get("username")
        if team and uname:
            opponents_text += f"• {team} — @{uname}\n"
    if not opponents_text:
        opponents_text = "Информация о тренерах дивизиона ещё не занесена.\n"

    # Tournament structure — то, что модель обязана понимать про устройство турнира.
    if division_id:
        structure_lines = [
            f"• Турнир разбит на ДИВИЗИОНЫ (всего активных: {len(sorted_divs) or '—'}). Каждый дивизион — отдельная лига со своей таблицей, своими турами и своими дедлайнами.",
            f"• Ты сейчас работаешь СТРОГО в контексте: {division_name}, {season_name}.",
            f"• Клубов в этом дивизионе: {len(standings)}. Это полный список участников — "
            f"кого нет в таблице ниже, того нет и в дивизионе.",
            f"• Сыграно/заведено туров в этом дивизионе: {total_rounds if total_rounds else 'туры ещё не заведены'}.",
        ]
        if prom_slots:
            structure_lines.append(f"• Повышение: верхние {prom_slots} мест уходят дивизионом ВЫШЕ. 🚀")
        else:
            structure_lines.append("• Это ВЕРХНИЙ дивизион — выше подниматься некуда, тут играют за титул.")
        if rel_slots:
            structure_lines.append(f"• Вылет: нижние {rel_slots} мест падают дивизионом НИЖЕ. 🔻")
        else:
            structure_lines.append("• Это НИЖНИЙ дивизион — ниже падать некуда.")
        structure_lines.append(
            "• Данные других дивизионов тебе НЕ переданы. Если спрашивают про чужой дивизион, "
            "про сквозную таблицу всей лиги или про чемпиона всего турнира — честно скажи, "
            "что видишь только свой дивизион, и не выдумывай цифры."
        )
        structure_text = "🗂 СТРУКТУРА ТУРНИРА:\n" + "\n".join(structure_lines) + "\n"
    else:
        structure_text = (
            "🗂 СТРУКТУРА ТУРНИРА:\n"
            "• Турнир разбит на дивизионы — каждый со своей таблицей, турами и дедлайнами.\n"
            "• ⚠️ ЭТОТ собеседник НЕ приписан ни к одному дивизиону, поэтому турнирных данных у тебя НЕТ.\n"
            "• Не выдумывай таблицу, места, очки и расписание. Скажи, что он не в дивизионе, "
            "и отправь к админу за распределением. Болтать на общие темы при этом можно.\n"
        )

    user_div_note = ""
    if division_id and user_data is not None:
        try:
            if user_data["division_id"] and user_data["division_id"] != division_id:
                user_div_note = (
                    f" ⚠️ Сам он приписан к другому дивизиону (#{user_data['division_id']}), "
                    f"а спрашивает в {division_name} — отвечай по данным {division_name}."
                )
        except (KeyError, IndexError):
            pass

    context_data = (
        f"Пользователь, который с тобой говорит: {username} (тренер команды '{user_team}').\n"
        f"Его дивизион в этом разговоре: {division_name or 'не определён'}.{user_div_note}\n"
        f"СНИКИ ЛИ ЭТО? {'ДА! Это сам @Snikers2121 (sniki) — великий! Относись к нему максимально уважительно и по-братски, защищай его, называй великим.' if (username or '').lower() == 'snikers2121' else 'НЕТ, это не сники — это обычный собеседник.'}\n"
        f"АДМИН ЛИ ЛИГИ? {'ДА! Это админ @sp1r1tVSA — его не троллить, относись уважительно, по-дружески.' if (username or '').lower() == 'sp1r1tvsa' else 'НЕТ, это не админ лиги.'}\n"
        f"Предупреждения (варны) у этого пользователя: {user_warn_count}/4."
        f"{' ⚠️ ВНИМАНИЕ: у игрока 3/4 варна! Следующий варн (например, ещё один долг по матчу) приведёт к автоматическому лишению клуба и кику из группы!' if user_warn_count == 3 else ''}\n\n"
        f"{structure_text}\n"
        f"ВЛАДЕЛЕЦ ТУРНИРА: @antonv2801 — он хозяин и главный по правилам, но троллить и подкалывать его можно как любого другого.\n"
        f"СОПЕРНИКИ ПО ДИВИЗИОНУ (клуб — тренер):\n{opponents_text}\n\n"
        f"{standings_text}\n"
        f"{form_text}\n"
        f"{schedule_text}\n"
        f"{scorers_text}\n"
        f"{assists_text}\n"
        f"{matches_text}\n"
        f"{squads_text}\n"
        f"{cup_info_text}\n\n"
        f"{past_seasons_text}\n"
        f"{league_rules_text}"
    )

    # 2. Call AI non-blocking via thread (history & mode were fetched above)
    chat_mode = chat_mode or "temshik"
    reply_text = await asyncio.to_thread(
        ai_chat.generate_chat_reply, 
        user_id, 
        user_text, 
        chat_history, 
        context_data,
        audio_input_bytes,
        "audio/ogg",
        chat_mode
    )

    # 3. Save to history
    await asyncio.to_thread(database.append_chat_history, user_id, "user", user_text)
    await asyncio.to_thread(database.append_chat_history, user_id, "model", reply_text)
    # Keep only last 10 messages (5 pairs) to avoid context bloat
    await asyncio.to_thread(database.trim_chat_history, user_id, keep=10)

    # 4. Send reply
    await update.message.reply_text(reply_text)




