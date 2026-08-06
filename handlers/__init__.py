from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
import asyncio
import database
import logging
import telegram.error

from handlers.chat import handle_ai_chat

# Import base handlers
from handlers.base import (
    start,
    show_main_menu,
    show_tournaments,
    show_league_rounds,
    show_cup_menu,
    show_cup_stats,
    show_league_table,
    show_league_menu,
    show_top_scorers,
    show_top_assists,
    send_top_scorers_image,
    send_top_assisters_image,
    show_support,
    group_table_command,
    show_round_matches,
    cb_refresh_league_table_topic,
    cb_show_cup_graphic,
    send_cup_scorers_image,
    send_cup_assisters_image,
)

# Import cabinet handlers
from handlers.cabinet import (
    show_cabinet,
    show_club_stats,
    show_my_squad,
    show_my_matches_stub,
    show_game_history_stub,
    show_edit_profile_menu,
    start_registration,
    reg_team_name,
    start_selective_edit,
    save_selective_edit,
    show_my_matches,
    cabinet_view_match,
    cabinet_view_squad,
    show_game_history,
    cancel_registration,
    cabinet_cancel_report,
    TEAM_NAME,
    EDITING_FIELD,
    start_score_reporting,
    cb_report_choice_auto,
    cb_report_choice_manual,
    cb_confirm_ai_final,
    cb_report_home_goals,
    cb_report_away_goals,
    cb_pick_goal,
    cb_skip_goals,
    cb_pick_assist,
    cb_skip_assists,
    prompt_photo_upload,
    save_report_photo,
    submit_report_to_guest,
    handle_confirm_score,
    guest_pick_goal,
    guest_skip_goals,
    guest_pick_assist,
    guest_skip_assists,
    handle_dispute_score,
    cb_finish_dispute_photos,
    save_guest_dispute_photo,
    REPORT_SCORE_PHOTO,
    GUEST_DISPUTE_PHOTOS,
    SQUAD_PHOTO,
    MATCH_CUSTOM_TIME,
    start_upload_squad,
    save_squad_photo,
    cancel_upload_squad,
    start_custom_time_prompt,
    save_custom_match_time,
    cb_propose_time_prompt,
    cb_quick_time,
    cb_accept_time,
    cb_request_admin_result,
    cb_admin_approve_result,
    cancel_score_report_and_navigate,
    show_player_card,
)

# Import admin handlers
from handlers.admin import (
    show_admin_panel,
    admin_list_players,
    admin_generate_matches_confirm,
    admin_generate_matches_execute,
    admin_list_disputed,
    admin_reset_dispute,
    admin_start_resolve_dispute,
    admin_save_dispute_score,
    admin_cancel_dispute_resolve,
    ADMIN_WAITING_FOR_DISPUTE_SCORE,
    admin_manage_players_info,
    admin_list_players_page,
    admin_view_player,
    admin_confirm_delete_player,
    admin_delete_player_execute,
    admin_manage_matches_info,
    admin_manage_round,
    admin_extend_match_execute,
    admin_list_overdue,
    admin_open_round_prompt,
    admin_open_round_save,
    ADMIN_WAITING_FOR_DEADLINE,
    admin_open_batch_prompt,
    admin_open_batch_rounds,
    admin_open_batch_deadline,
    ADMIN_WAITING_FOR_BATCH_ROUNDS,
    ADMIN_WAITING_FOR_BATCH_DEADLINE,
    admin_close_round,
    admin_round_matches,
    admin_view_match,
    admin_report_score_auto,
    admin_set_tp_home_execute,
    admin_set_tp_away_execute,
    admin_set_tp_draw_execute,
    admin_reset_match_execute,
    admin_add_player_start,
    admin_add_player_username,
    admin_show_free_clubs,
    admin_add_player_club_callback,
    ADMIN_EXPECT_PLAYER_USERNAME,
    ADMIN_EXPECT_PLAYER_CLUB,
    admin_import_players_start,
    admin_import_players_text,
    ADMIN_EXPECT_IMPORT_TEXT,
    admin_edit_club_start,
    admin_edit_club_text,
    ADMIN_EXPECT_NEW_CLUB,
    admin_create_matches_start,
    admin_receive_schedule_input,
    ADMIN_EXPECT_MATCH_SCHEDULE_INPUT,
    admin_set_score_start,
    admin_set_score_text,
    ADMIN_EXPECT_MATCH_SCORE,
    admin_cancel_player_action,
    admin_cancel_match_action,
    admin_toggle_role,
    admin_delete_options,
    admin_confirm_wipe_player,
    admin_wipe_player_execute,
    admin_edit_username_start,
    admin_edit_username_text,
    ADMIN_EXPECT_NEW_USERNAME,
    admin_clear_league_start,
    admin_clear_league_text,
    ADMIN_EXPECT_RESET_CONFIRM,
    admin_manage_players_menu,
    admin_edit_club_select,
    admin_edit_club_execute,
    admin_delete_player_confirm,
    admin_remind_round,
    admin_toggle_remind_match,
    admin_toggle_remind_all,
    admin_send_selected_reminders,
    job_check_deadlines_and_remind,
    job_post_debts_to_warns,
    admin_set_squad_topic,
    admin_set_reports_topic,
    admin_set_results_topic,
    admin_set_warns_topic,
    admin_manage_squads,
    admin_view_squad,
    admin_squad_upload_start,
    admin_squad_upload_text,
    admin_squad_clear,
    ADMIN_EXPECT_SQUAD_TEXT,
    admin_stub,
    admin_fetch_photos,
    admin_manage_cup,
    admin_init_cup_execute,
    admin_remind_cup_execute,
    admin_broadcast_menu,
    admin_broadcast_all_debts_execute,
    admin_send_debts_to_warns,
    admin_test_ai,
    admin_warn_confirm,
    admin_warn_execute,
    admin_warn_remove_execute,
    admin_warn_history,
    admin_amnesty_execute,
    admin_reset_season_warns,
)

logger = logging.getLogger(__name__)

async def handle_placeholders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    if query.data == "noop":
        await query.answer()
        return
    await query.answer("Эта функция находится в разработке.", show_alert=True)

async def track_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track the ID of the Telegram group the bot is in."""
    if update.effective_chat and update.effective_chat.type in ("group", "supergroup"):
        await asyncio.to_thread(database.set_config, "group_id", str(update.effective_chat.id))

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error that occurred during update handling."""
    if isinstance(context.error, (telegram.error.TimedOut, telegram.error.NetworkError)):
        logger.warning(f"Сетевая задержка Telegram (TimedOut/NetworkError): {context.error}")
        return
    if isinstance(context.error, telegram.error.BadRequest):
        err_str = str(context.error).lower()
        if ("query is too old" in err_str or "message is not modified" in err_str):
            logger.debug(f"Telegram BadRequest (игнорируется): {context.error}")
            return
    logger.exception("Исключение при обработке обновления:")

def _register_user_handlers(app: Application) -> None:
    """Register general user command and navigation handlers."""
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("table", group_table_command))
    app.add_handler(CommandHandler("ratings", group_table_command))
    app.add_handler(CommandHandler("fetch_photos", admin_fetch_photos))

    app.add_handler(MessageHandler(filters.Regex("^👤 Мой кабинет$"), show_cabinet))
    app.add_handler(MessageHandler(filters.Regex("^🏆 Турниры$"), show_tournaments))
    app.add_handler(MessageHandler(filters.Regex("^📊 Таблица лиги$"), show_league_table))
    app.add_handler(MessageHandler(filters.Regex("^💬 Поддержка$"), show_support))
    app.add_handler(MessageHandler(filters.Regex("^⚙️ Админ-панель$"), show_admin_panel))

    app.add_handler(CallbackQueryHandler(cb_refresh_league_table_topic, pattern="^refresh_league_table_topic$"))
    app.add_handler(CallbackQueryHandler(show_cabinet, pattern="^menu_cabinet$"))
    app.add_handler(CallbackQueryHandler(show_tournaments, pattern="^menu_tournaments$"))
    app.add_handler(CallbackQueryHandler(show_league_menu, pattern="^menu_league$"))
    app.add_handler(CallbackQueryHandler(show_league_table, pattern="^(league_table|menu_ratings)$"))
    app.add_handler(CallbackQueryHandler(show_top_scorers, pattern="^league_scorers$"))
    app.add_handler(CallbackQueryHandler(show_top_assists, pattern="^league_assists$"))
    app.add_handler(CallbackQueryHandler(send_top_scorers_image, pattern="^img_top_scorers$"))
    app.add_handler(CallbackQueryHandler(send_top_assisters_image, pattern="^img_top_assisters$"))
    app.add_handler(CallbackQueryHandler(show_support, pattern="^menu_support$"))
    app.add_handler(CallbackQueryHandler(show_main_menu, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(show_league_rounds, pattern="^tournaments_league_rounds$"))
    app.add_handler(CallbackQueryHandler(show_cup_menu, pattern="^tournaments_cup_menu$"))
    app.add_handler(CallbackQueryHandler(show_cup_menu, pattern="^show_cup_stage_.*$"))
    app.add_handler(CallbackQueryHandler(cb_show_cup_graphic, pattern="^show_cup_graphic_.*$"))
    app.add_handler(CallbackQueryHandler(show_cup_stats, pattern="^show_cup_stats$"))
    app.add_handler(CallbackQueryHandler(send_cup_scorers_image, pattern="^img_cup_scorers$"))
    app.add_handler(CallbackQueryHandler(send_cup_assisters_image, pattern="^img_cup_assisters$"))
    app.add_handler(CallbackQueryHandler(show_round_matches, pattern="^show_round_matches_\\d+$"))

def _register_cabinet_handlers(app: Application) -> None:
    """Register player cabinet FSM and interactive match handlers."""
    reg_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_registration, pattern="^register_profile$"),
            CallbackQueryHandler(start_selective_edit, pattern="^edit_field_.*$"),
            CommandHandler("register", start_registration)
        ],
        states={
            TEAM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_team_name)],
            EDITING_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_selective_edit)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_registration),
            MessageHandler(filters.Regex("^Отмена$"), cancel_registration)
        ],
        allow_reentry=True,
        per_message=False,
        conversation_timeout=300
    )
    app.add_handler(reg_conv)

    score_report_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_score_reporting, pattern="^cabinet_report_score_\\d+$")
        ],
        states={
            REPORT_SCORE_PHOTO: [MessageHandler(filters.PHOTO | filters.Document.ALL, save_report_photo)]
        },
        fallbacks=[
            CallbackQueryHandler(cabinet_view_match, pattern="^cabinet_view_match_\\d+$"),
            CallbackQueryHandler(cancel_score_report_and_navigate, pattern="^cabinet_my_matches$"),
            CallbackQueryHandler(cancel_score_report_and_navigate, pattern="^main_menu$"),
            CallbackQueryHandler(cancel_score_report_and_navigate, pattern="^menu_cabinet$"),
            CommandHandler("cancel", cancel_registration)
        ],
        allow_reentry=True,
        per_message=False,
        conversation_timeout=300
    )
    app.add_handler(score_report_conv)

    dispute_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_dispute_score, pattern="^dispute_score_\\d+$")
        ],
        states={
            GUEST_DISPUTE_PHOTOS: [MessageHandler(filters.PHOTO | filters.Document.ALL, save_guest_dispute_photo)]
        },
        fallbacks=[
            CallbackQueryHandler(cb_finish_dispute_photos, pattern="^cb_finish_dispute_photos$"),
            CallbackQueryHandler(cancel_score_report_and_navigate, pattern="^cabinet_my_matches$"),
            CallbackQueryHandler(cancel_score_report_and_navigate, pattern="^main_menu$"),
            CallbackQueryHandler(cancel_score_report_and_navigate, pattern="^menu_cabinet$"),
            CommandHandler("cancel", cancel_registration)
        ],
        allow_reentry=True,
        per_message=False,
        conversation_timeout=300
    )
    app.add_handler(dispute_conv)

    squad_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_upload_squad, pattern="^cabinet_upload_squad$")
        ],
        states={
            SQUAD_PHOTO: [MessageHandler(filters.PHOTO, save_squad_photo)]
        },
        fallbacks=[
            CallbackQueryHandler(cancel_upload_squad, pattern="^cabinet_my_squad$"),
            CommandHandler("cancel", cancel_upload_squad)
        ],
        allow_reentry=True,
        per_message=False,
        conversation_timeout=300
    )
    app.add_handler(squad_conv)

    custom_time_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_custom_time_prompt, pattern="^cb_custom_time_prompt_\\d+$")
        ],
        states={
            MATCH_CUSTOM_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_custom_match_time)]
        },
        fallbacks=[
            CallbackQueryHandler(cabinet_view_match, pattern="^cabinet_view_match_\\d+$"),
            CallbackQueryHandler(cancel_score_report_and_navigate, pattern="^cabinet_my_matches$"),
            CallbackQueryHandler(cancel_score_report_and_navigate, pattern="^main_menu$"),
            CallbackQueryHandler(cancel_score_report_and_navigate, pattern="^menu_cabinet$"),
            CommandHandler("cancel", cancel_registration)
        ],
        allow_reentry=True,
        per_message=False,
        conversation_timeout=300
    )
    app.add_handler(custom_time_conv)

    app.add_handler(CallbackQueryHandler(show_edit_profile_menu, pattern="^edit_profile_menu$"))
    app.add_handler(CallbackQueryHandler(show_my_matches, pattern="^cabinet_my_matches$"))
    app.add_handler(CallbackQueryHandler(cabinet_view_match, pattern="^cabinet_view_match_\\d+$"))
    app.add_handler(CallbackQueryHandler(cabinet_view_squad, pattern="^cabinet_view_squad_\\d+$"))
    app.add_handler(CallbackQueryHandler(show_game_history, pattern="^cabinet_game_history$"))

    app.add_handler(CallbackQueryHandler(cb_propose_time_prompt, pattern="^cb_propose_time_prompt_\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_quick_time, pattern="^cb_quick_time_\\d+_.+$"))
    app.add_handler(CallbackQueryHandler(cb_accept_time, pattern="^cb_accept_time_\\d+$"))

    app.add_handler(CallbackQueryHandler(cb_request_admin_result, pattern="^cb_request_admin_result_\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_admin_approve_result, pattern="^cb_admin_approve_\\d+_\\d+$"))

    app.add_handler(CallbackQueryHandler(cb_report_choice_auto, pattern="^cb_report_choice_auto_\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_report_choice_manual, pattern="^cb_report_choice_manual_\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_confirm_ai_final, pattern="^cb_confirm_ai_final_\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_report_home_goals, pattern="^cb_report_hg_\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_report_away_goals, pattern="^cb_report_ag_\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_pick_goal, pattern="^cb_pick_goal_idx_\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_skip_goals, pattern="^cb_skip_goals$"))
    app.add_handler(CallbackQueryHandler(cb_pick_assist, pattern="^cb_pick_assist_idx_\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_skip_assists, pattern="^cb_skip_assists$"))
    app.add_handler(CallbackQueryHandler(submit_report_to_guest, pattern="^cb_submit_report_to_guest(_\\d+)?$"))
    app.add_handler(CallbackQueryHandler(cabinet_cancel_report, pattern="^cabinet_cancel_report_\\d+$"))

    async def global_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or update.effective_chat.type != "private":
            return
        if context.user_data.get("awaiting_report_photo") or context.user_data.get("reporting_match_id"):
            await save_report_photo(update, context)

    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.PHOTO | filters.Document.ALL), global_photo_handler))

    app.add_handler(CallbackQueryHandler(handle_confirm_score, pattern="^confirm_score_\\d+$"))
    app.add_handler(CallbackQueryHandler(guest_pick_goal, pattern="^guest_pick_goal_idx_\\d+$"))
    app.add_handler(CallbackQueryHandler(guest_skip_goals, pattern="^guest_skip_goals$"))
    app.add_handler(CallbackQueryHandler(guest_pick_assist, pattern="^guest_pick_assist_idx_\\d+$"))
    app.add_handler(CallbackQueryHandler(guest_skip_assists, pattern="^guest_skip_assists$"))
    app.add_handler(CallbackQueryHandler(handle_dispute_score, pattern="^dispute_score_\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_finish_dispute_photos, pattern="^cb_finish_dispute_photos$"))

    app.add_handler(CallbackQueryHandler(show_club_stats, pattern="^cabinet_club_stats$"))
    app.add_handler(CallbackQueryHandler(show_my_squad, pattern="^cabinet_my_squad$"))
    app.add_handler(CallbackQueryHandler(show_game_history, pattern="^cabinet_game_history$"))
    app.add_handler(CallbackQueryHandler(show_player_card, pattern="^(player_card|pcard)_.+$"))

def _register_admin_handlers(app: Application) -> None:
    """Register administrator panel, tournament management, and dispute resolution handlers."""
    admin_dispute_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_start_resolve_dispute, pattern="^admin_resolve_dispute_\\d+$")
        ],
        states={
            ADMIN_WAITING_FOR_DISPUTE_SCORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_save_dispute_score)]
        },
        fallbacks=[
            CommandHandler("cancel", admin_cancel_dispute_resolve),
            MessageHandler(filters.Regex("^Отмена$"), admin_cancel_dispute_resolve)
        ],
        allow_reentry=True,
        per_message=False,
        conversation_timeout=300
    )
    app.add_handler(admin_dispute_conv)

    admin_player_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_add_player_start, pattern="^admin_add_player_start$"),
            CallbackQueryHandler(admin_import_players_start, pattern="^admin_import_players_start$"),
            CommandHandler("add_player", admin_add_player_start),
            CommandHandler("import_players", admin_import_players_start),
            CallbackQueryHandler(admin_edit_club_start, pattern="^admin_edit_club_start_-?\\d+$"),
            CallbackQueryHandler(admin_edit_username_start, pattern="^admin_edit_username_start_\\d+$"),
            CallbackQueryHandler(admin_clear_league_start, pattern="^admin_clear_league_start$")
        ],
        states={
            ADMIN_EXPECT_PLAYER_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_player_username)],
            ADMIN_EXPECT_PLAYER_CLUB: [CallbackQueryHandler(admin_add_player_club_callback, pattern="^assign_club_.*$")],
            ADMIN_EXPECT_IMPORT_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_import_players_text)],
            ADMIN_EXPECT_NEW_CLUB: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_club_text)],
            ADMIN_EXPECT_NEW_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_username_text)],
            ADMIN_EXPECT_RESET_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_clear_league_text)]
        },
        fallbacks=[
            CallbackQueryHandler(admin_cancel_player_action, pattern="^admin_cancel_player_action$"),
            CommandHandler("cancel", admin_cancel_player_action)
        ],
        allow_reentry=True,
        per_message=False,
        conversation_timeout=300
    )
    app.add_handler(admin_player_conv)

    admin_match_score_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_set_score_start, pattern="^admin_set_score_start_\\d+$")
        ],
        states={
            ADMIN_EXPECT_MATCH_SCORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_score_text)]
        },
        fallbacks=[
            CallbackQueryHandler(admin_cancel_match_action, pattern="^admin_cancel_match_action$"),
            CommandHandler("cancel", admin_cancel_match_action)
        ],
        allow_reentry=True,
        per_message=False,
        conversation_timeout=300
    )
    app.add_handler(admin_match_score_conv)

    admin_round_deadline_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_open_round_prompt, pattern="^admin_open_round_\\d+$")
        ],
        states={
            ADMIN_WAITING_FOR_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_open_round_save)]
        },
        fallbacks=[
            CallbackQueryHandler(admin_cancel_match_action, pattern="^admin_cancel_match_action$"),
            CommandHandler("cancel", admin_cancel_match_action),
            MessageHandler(filters.Regex("^(Отмена|отмена)$"), admin_cancel_match_action) # <-- Добавлено!
        ],
        allow_reentry=True,
        per_message=False,
        conversation_timeout=300
    )
    app.add_handler(admin_round_deadline_conv)

    admin_batch_round_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_open_batch_prompt, pattern="^admin_open_batch_prompt$")
        ],
        states={
            ADMIN_WAITING_FOR_BATCH_ROUNDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_open_batch_rounds)],
            ADMIN_WAITING_FOR_BATCH_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_open_batch_deadline)]
        },
        fallbacks=[
            CallbackQueryHandler(admin_cancel_match_action, pattern="^admin_cancel_match_action$"),
            CommandHandler("cancel", admin_cancel_match_action),
            MessageHandler(filters.Regex("^(Отмена|отмена)$"), admin_cancel_match_action) # <-- Добавлено!
        ],
        allow_reentry=True,
        per_message=False,
        conversation_timeout=300
    )
    app.add_handler(admin_batch_round_conv)

    admin_create_matches_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_create_matches_start, pattern="^admin_create_matches_start$")
        ],
        states={
            ADMIN_EXPECT_MATCH_SCHEDULE_INPUT: [
                MessageHandler(filters.TEXT | filters.Document.ALL, admin_receive_schedule_input)
            ]
        },
        fallbacks=[
            CallbackQueryHandler(admin_manage_matches_info, pattern="^admin_manage_matches_info$"),
            CommandHandler("cancel", admin_manage_matches_info)
        ],
        allow_reentry=True,
        per_message=False,
        conversation_timeout=300
    )
    app.add_handler(admin_create_matches_conv)

    admin_squad_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_squad_upload_start, pattern="^admin_squad_upload_.*$")
        ],
        states={
            ADMIN_EXPECT_SQUAD_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_squad_upload_text)],
        },
        fallbacks=[
            CallbackQueryHandler(admin_manage_squads, pattern="^admin_manage_squads$"),
            CommandHandler("cancel", admin_cancel_player_action)
        ],
        allow_reentry=True,
        per_message=False,
        per_user=True,
        conversation_timeout=300
    )
    app.add_handler(admin_squad_conv)

    app.add_handler(CommandHandler("set_squad_topic", admin_set_squad_topic))
    app.add_handler(CommandHandler("set_reports_topic", admin_set_reports_topic))
    app.add_handler(CommandHandler("set_results_topic", admin_set_results_topic))
    app.add_handler(CommandHandler("set_warns_topic", admin_set_warns_topic))

    app.add_handler(CallbackQueryHandler(admin_reset_dispute, pattern="^admin_reset_dispute_\\d+$"))
    app.add_handler(CallbackQueryHandler(show_admin_panel, pattern="^admin_main_menu$"))
    app.add_handler(CallbackQueryHandler(admin_generate_matches_confirm, pattern="^admin_generate_matches_confirm$"))
    app.add_handler(CallbackQueryHandler(admin_generate_matches_execute, pattern="^admin_generate_matches_execute$"))
    app.add_handler(CallbackQueryHandler(admin_list_disputed, pattern="^admin_list_disputed$"))
    app.add_handler(CallbackQueryHandler(admin_manage_matches_info, pattern="^admin_manage_matches_info$"))
    app.add_handler(CallbackQueryHandler(admin_manage_players_menu, pattern="^admin_manage_players$"))
    app.add_handler(CallbackQueryHandler(admin_manage_players_menu, pattern="^admin_manage_players_info$"))
    app.add_handler(CallbackQueryHandler(admin_list_players_page, pattern="^admin_list_players_page_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_view_player, pattern="^admin_view_player_-?\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_edit_club_select, pattern="^admin_edit_club_select_-?\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_edit_club_execute, pattern="^admin_eclub_-?\\d+_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_delete_player_confirm, pattern="^admin_delete_player_confirm_-?\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_delete_player_execute, pattern="^admin_delete_player_execute_-?\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_manage_round, pattern="^admin_manage_round_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_close_round, pattern="^admin_close_round_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_remind_round, pattern="^admin_remind_round_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_toggle_remind_match, pattern="^admin_toggle_remind_match_\\d+_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_toggle_remind_all, pattern="^admin_toggle_remind_all_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_send_selected_reminders, pattern="^admin_send_selected_reminders_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_round_matches, pattern="^admin_round_matches_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_view_match, pattern="^admin_view_match_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_reset_match_execute, pattern="^admin_reset_match_execute_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_report_score_auto, pattern="^admin_report_score_auto_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_set_tp_home_execute, pattern="^admin_tp_home_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_set_tp_away_execute, pattern="^admin_tp_away_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_set_tp_draw_execute, pattern="^admin_tp_draw_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_list_overdue, pattern="^admin_list_overdue$"))
    app.add_handler(CallbackQueryHandler(admin_extend_match_execute, pattern="^admin_extend_match_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_toggle_role, pattern="^admin_toggle_role_-?\\d+_(player|admin)$"))
    app.add_handler(CallbackQueryHandler(admin_delete_options, pattern="^admin_delete_options_-?\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_confirm_wipe_player, pattern="^admin_confirm_wipe_player_-?\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_wipe_player_execute, pattern="^admin_wipe_player_execute_-?\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_manage_players_menu, pattern="^admin_manage_players$"))
    app.add_handler(CallbackQueryHandler(admin_manage_squads, pattern="^admin_manage_squads$"))
    app.add_handler(CallbackQueryHandler(admin_view_squad, pattern="^admin_squad_view_.*$"))
    app.add_handler(CallbackQueryHandler(admin_squad_clear, pattern="^admin_squad_clear_.*$"))
    app.add_handler(CallbackQueryHandler(admin_manage_cup, pattern="^admin_manage_cup$"))
    app.add_handler(CallbackQueryHandler(admin_manage_cup, pattern="^admin_cup_stage_.*$"))
    app.add_handler(CallbackQueryHandler(admin_init_cup_execute, pattern="^admin_init_cup_execute$"))
    app.add_handler(CallbackQueryHandler(admin_remind_cup_execute, pattern="^admin_remind_cup_.*$"))
    app.add_handler(CommandHandler("test_ai", admin_test_ai))
    app.add_handler(CallbackQueryHandler(admin_broadcast_menu, pattern="^admin_broadcast_menu$"))
    app.add_handler(CallbackQueryHandler(admin_broadcast_all_debts_execute, pattern="^admin_broadcast_all_debts_execute$"))
    app.add_handler(CallbackQueryHandler(admin_send_debts_to_warns, pattern="^admin_send_debts_to_warns$"))
    app.add_handler(CallbackQueryHandler(admin_fetch_photos, pattern="^admin_fetch_photos_cb$"))
    app.add_handler(CallbackQueryHandler(admin_stub, pattern="^admin_matches_stub$"))

    # Warns system
    app.add_handler(CallbackQueryHandler(admin_warn_confirm, pattern="^warn_add_-?\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_warn_execute, pattern="^warn_exec_-?\\d+_\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_warn_remove_execute, pattern="^warn_remove_-?\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_warn_history, pattern="^warn_hist_-?\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_amnesty_execute, pattern="^warn_amnesty_-?\\d+$"))
    app.add_handler(CallbackQueryHandler(admin_reset_season_warns, pattern="^admin_reset_season_warns$"))

def register_all_handlers(application: Application) -> None:
    """Register all command, message, and callback handlers to the application."""
    application.add_handler(MessageHandler(filters.ChatType.GROUPS, track_group_id), group=1)
    
    # 1. Сначала регистрируем кнопки и основные команды
    _register_user_handlers(application)
    
    # 2. Затем диалоги кабинета и админки (FSM conversation handlers)
    _register_cabinet_handlers(application)
    _register_admin_handlers(application)

    # 3. И ТОЛЬКО В САМОМ КОНЦЕ перехватчик текста и голосовых сообщений для ИИ Темшика!
    application.add_handler(MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, handle_ai_chat))

    # Final catch-all for inline button clicks in development
    application.add_handler(CallbackQueryHandler(handle_placeholders, pattern=".*"))

    # Register global error handler
    application.add_error_handler(error_handler)