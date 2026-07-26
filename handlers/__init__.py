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

# Import base handlers
from handlers.base import (
    start,
    show_main_menu,
    show_tournaments,
    show_league_table,
    show_league_menu,
    show_top_scorers,
    show_top_assists,
    show_support,
    group_table_command,
    show_round_matches,
    cb_refresh_league_table_topic,
)

# Import cabinet handlers
from handlers.cabinet import (
    show_cabinet,
    show_club_stats,
    show_my_matches_stub,
    show_game_history_stub,
    show_edit_profile_menu,
    start_registration,
    reg_team_name,
    start_selective_edit,
    save_selective_edit,
    show_my_matches,
    cabinet_view_match,
    show_game_history,
    cancel_registration,
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
    save_guest_dispute_photo,
    cb_finish_dispute_photos,
    REPORT_SCORE_PHOTO,
    GUEST_DISPUTE_PHOTOS,
    SQUAD_PHOTO,
    MATCH_CUSTOM_TIME,
    show_my_squad,
    start_upload_squad,
    save_squad_photo,
    cancel_upload_squad,
    cabinet_view_squad,
    cb_propose_time_prompt,
    cb_quick_time,
    cb_accept_time,
    start_custom_time_prompt,
    save_custom_match_time,
    cb_request_admin_result,
    cb_admin_enter_result,
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
    admin_manage_matches_info,
    admin_manage_players_info,
    admin_list_players_page,
    admin_view_player,
    admin_confirm_delete_player,
    admin_delete_player_execute,
    admin_round_matches,
    admin_view_match,
    admin_reset_match_execute,
    admin_report_score_auto,
    admin_set_tp_home_execute,
    admin_set_tp_away_execute,
    admin_set_tp_draw_execute,
    admin_add_player_start,
    admin_add_player_username,
    admin_add_player_club_callback,
    admin_import_players_start,
    admin_import_players_text,
    admin_edit_club_start,
    admin_edit_club_text,
    admin_set_score_start,
    admin_set_score_text,
    admin_cancel_player_action,
    admin_cancel_match_action,
    admin_toggle_role,
    admin_delete_options,
    admin_confirm_wipe_player,
    admin_wipe_player_execute,
    admin_edit_username_start,
    admin_edit_username_text,
    admin_clear_league_start,
    admin_clear_league_text,
    admin_edit_club_select,
    admin_edit_club_execute,
    admin_delete_player_confirm,
    admin_manage_players_menu,
    admin_manage_squads,
    admin_view_squad,
    admin_squad_upload_start,
    admin_squad_upload_text,
    admin_squad_clear,
    admin_stub,
    admin_manage_round,
    admin_open_round_prompt,
    admin_open_round_save,
    admin_close_round,
    admin_remind_round,
    admin_toggle_remind_match,
    admin_toggle_remind_all,
    admin_send_selected_reminders,
    job_check_deadlines_and_remind,
    admin_open_batch_prompt,
    admin_open_batch_rounds,
    admin_open_batch_deadline,
    admin_list_overdue,
    admin_extend_match_execute,
    admin_set_squad_topic,
    admin_set_reports_topic,
    admin_set_results_topic,
    ADMIN_EXPECT_PLAYER_USERNAME,
    ADMIN_EXPECT_PLAYER_CLUB,
    ADMIN_EXPECT_IMPORT_TEXT,
    ADMIN_EXPECT_NEW_CLUB,
    ADMIN_EXPECT_NEW_USERNAME,
    ADMIN_EXPECT_RESET_CONFIRM,
    ADMIN_EXPECT_MATCH_SCORE,
    ADMIN_WAITING_FOR_DEADLINE,
    ADMIN_WAITING_FOR_BATCH_ROUNDS,
    ADMIN_WAITING_FOR_BATCH_DEADLINE,
    ADMIN_EXPECT_SQUAD_TEXT,
    admin_create_matches_start,
    admin_receive_schedule_input,
    ADMIN_EXPECT_MATCH_SCHEDULE_INPUT,
)

# Fallback generic callback query handler for placeholders
async def handle_placeholders(update, context) -> None:
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
        import database
        database.set_config("group_id", str(update.effective_chat.id))

def register_all_handlers(application: Application) -> None:
    """Register all command, message, and callback handlers to the application."""
    
    # Track Group ID dynamically for group messages
    application.add_handler(MessageHandler(filters.ChatType.GROUPS, track_group_id), group=1)

    # Registration & Editing Conversation Handler (Ends at TEAM_NAME now)
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
        per_message=False
    )
    application.add_handler(reg_conv)

    # Score Reporting Conversation Handler
    score_report_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_score_reporting, pattern="^cabinet_report_score_\\d+$")
        ],
        states={
            REPORT_SCORE_PHOTO: [MessageHandler(filters.PHOTO | filters.Document.ALL, save_report_photo)]
        },
        fallbacks=[
            CallbackQueryHandler(cabinet_view_match, pattern="^cabinet_view_match_\\d+$"),
            CommandHandler("cancel", cancel_registration)
        ],
        allow_reentry=True,
        per_message=False
    )
    application.add_handler(score_report_conv)

    # Guest Dispute Photos Conversation Handler
    dispute_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_dispute_score, pattern="^dispute_score_\\d+$")
        ],
        states={
            GUEST_DISPUTE_PHOTOS: [MessageHandler(filters.PHOTO | filters.Document.ALL, save_guest_dispute_photo)]
        },
        fallbacks=[
            CallbackQueryHandler(cb_finish_dispute_photos, pattern="^cb_finish_dispute_photos$"),
            CommandHandler("cancel", cancel_registration)
        ],
        allow_reentry=True,
        per_message=False
    )
    application.add_handler(dispute_conv)

    # Squad Upload Conversation Handler
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
        per_message=False
    )
    application.add_handler(squad_conv)

    # Match Custom Time Conversation Handler
    custom_time_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_custom_time_prompt, pattern="^cb_custom_time_prompt_\\d+$")
        ],
        states={
            MATCH_CUSTOM_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_custom_match_time)]
        },
        fallbacks=[
            CallbackQueryHandler(cabinet_view_match, pattern="^cabinet_view_match_\\d+$"),
            CommandHandler("cancel", cancel_registration)
        ],
        allow_reentry=True,
        per_message=False
    )
    application.add_handler(custom_time_conv)

    # Admin Dispute Resolution Conversation Handler
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
        per_message=False
    )
    application.add_handler(admin_dispute_conv)

    # Admin Players management Conversation Handler
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
        per_message=False
    )
    application.add_handler(admin_player_conv)

    # Admin Match score manual entry Conversation Handler
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
        per_message=False
    )
    application.add_handler(admin_match_score_conv)

    # Admin Round Deadline Conversation Handler
    admin_round_deadline_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_open_round_prompt, pattern="^admin_open_round_\\d+$")
        ],
        states={
            ADMIN_WAITING_FOR_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_open_round_save)]
        },
        fallbacks=[
            CallbackQueryHandler(admin_cancel_match_action, pattern="^admin_cancel_match_action$"),
            CommandHandler("cancel", admin_cancel_match_action)
        ],
        allow_reentry=True,
        per_message=False
    )
    application.add_handler(admin_round_deadline_conv)

    # Admin Batch Round Deadline Conversation Handler
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
            CommandHandler("cancel", admin_cancel_match_action)
        ],
        allow_reentry=True,
        per_message=False
    )
    application.add_handler(admin_batch_round_conv)

    # Admin Create Matches Conversation Handler
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
        per_message=False
    )
    application.add_handler(admin_create_matches_conv)

    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("table", group_table_command))
    application.add_handler(CommandHandler("ratings", group_table_command))
    application.add_handler(CommandHandler("set_squad_topic", admin_set_squad_topic))
    application.add_handler(CommandHandler("set_reports_topic", admin_set_reports_topic))
    application.add_handler(CommandHandler("set_results_topic", admin_set_results_topic))

    # Reply keyboard text message handlers (for backward compatibility / text fallback)
    application.add_handler(MessageHandler(filters.Regex("^👤 Мой кабинет$"), show_cabinet))
    application.add_handler(MessageHandler(filters.Regex("^🏆 Турниры$"), show_tournaments))
    application.add_handler(MessageHandler(filters.Regex("^📊 Таблица лиги$"), show_league_table))
    application.add_handler(MessageHandler(filters.Regex("^💬 Поддержка$"), show_support))
    application.add_handler(MessageHandler(filters.Regex("^⚙️ Админ-панель$"), show_admin_panel))
    
    # AI Chat fallback handler
    from handlers.chat import handle_ai_chat
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_chat))

    # Inline Button Callback handlers
    application.add_handler(CallbackQueryHandler(cb_refresh_league_table_topic, pattern="^refresh_league_table_topic$"))
    application.add_handler(CallbackQueryHandler(show_cabinet, pattern="^menu_cabinet$"))
    application.add_handler(CallbackQueryHandler(show_tournaments, pattern="^menu_tournaments$"))
    application.add_handler(CallbackQueryHandler(show_league_menu, pattern="^menu_league$"))
    application.add_handler(CallbackQueryHandler(show_league_table, pattern="^(league_table|menu_ratings)$"))
    application.add_handler(CallbackQueryHandler(show_top_scorers, pattern="^league_scorers$"))
    application.add_handler(CallbackQueryHandler(show_top_assists, pattern="^league_assists$"))
    application.add_handler(CallbackQueryHandler(show_support, pattern="^menu_support$"))
    application.add_handler(CallbackQueryHandler(show_main_menu, pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(show_round_matches, pattern="^show_round_matches_\\d+$"))
    
    # Cabinet Sub-menu Callbacks
    application.add_handler(CallbackQueryHandler(show_edit_profile_menu, pattern="^edit_profile_menu$"))
    application.add_handler(CallbackQueryHandler(show_my_matches, pattern="^cabinet_my_matches$"))
    application.add_handler(CallbackQueryHandler(cabinet_view_match, pattern="^cabinet_view_match_\\d+$"))
    application.add_handler(CallbackQueryHandler(cabinet_view_squad, pattern="^cabinet_view_squad_\\d+$"))
    application.add_handler(CallbackQueryHandler(show_game_history, pattern="^cabinet_game_history$"))
    
    # Match Time Proposal Callbacks
    application.add_handler(CallbackQueryHandler(cb_propose_time_prompt, pattern="^cb_propose_time_prompt_\\d+$"))
    application.add_handler(CallbackQueryHandler(cb_quick_time, pattern="^cb_quick_time_\\d+_.+$"))
    application.add_handler(CallbackQueryHandler(cb_accept_time, pattern="^cb_accept_time_\\d+$"))

    # Overdue match admin request
    application.add_handler(CallbackQueryHandler(cb_request_admin_result, pattern="^cb_request_admin_result_\\d+$"))
    application.add_handler(CallbackQueryHandler(cb_admin_enter_result, pattern="^cb_admin_enter_result_\\d+$"))
    
    # Score Reporting & Stats Callbacks
    application.add_handler(CallbackQueryHandler(cb_report_choice_auto, pattern="^cb_report_choice_auto_\\d+$"))
    application.add_handler(CallbackQueryHandler(cb_report_choice_manual, pattern="^cb_report_choice_manual_\\d+$"))
    application.add_handler(CallbackQueryHandler(cb_confirm_ai_final, pattern="^cb_confirm_ai_final_\\d+$"))
    application.add_handler(CallbackQueryHandler(cb_report_home_goals, pattern="^cb_report_hg_\\d+$"))
    application.add_handler(CallbackQueryHandler(cb_report_away_goals, pattern="^cb_report_ag_\\d+$"))
    application.add_handler(CallbackQueryHandler(cb_pick_goal, pattern="^cb_pick_goal_.*$"))
    application.add_handler(CallbackQueryHandler(cb_skip_goals, pattern="^cb_skip_goals$"))
    application.add_handler(CallbackQueryHandler(cb_pick_assist, pattern="^cb_pick_assist_.*$"))
    application.add_handler(CallbackQueryHandler(cb_skip_assists, pattern="^cb_skip_assists$"))
    application.add_handler(CallbackQueryHandler(submit_report_to_guest, pattern="^cb_submit_report_to_guest(_\\d+)?$"))
    async def global_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or update.effective_chat.type != "private":
            return
        if context.user_data.get("awaiting_report_photo") or context.user_data.get("reporting_match_id"):
            await save_report_photo(update, context)

    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.PHOTO | filters.Document.ALL), global_photo_handler))

    # Guest Confirmation & Guest Stats Callbacks
    application.add_handler(CallbackQueryHandler(handle_confirm_score, pattern="^confirm_score_\\d+$"))
    application.add_handler(CallbackQueryHandler(guest_pick_goal, pattern="^guest_pick_goal_.*$"))
    application.add_handler(CallbackQueryHandler(guest_skip_goals, pattern="^guest_skip_goals$"))
    application.add_handler(CallbackQueryHandler(guest_pick_assist, pattern="^guest_pick_assist_.*$"))
    application.add_handler(CallbackQueryHandler(guest_skip_assists, pattern="^guest_skip_assists$"))
    application.add_handler(CallbackQueryHandler(handle_dispute_score, pattern="^dispute_score_\\d+$"))
    application.add_handler(CallbackQueryHandler(cb_finish_dispute_photos, pattern="^cb_finish_dispute_photos$"))

    # Admin Dispute Callbacks
    application.add_handler(CallbackQueryHandler(admin_reset_dispute, pattern="^admin_reset_dispute_\\d+$"))

    # Admin Panel Callbacks
    application.add_handler(CallbackQueryHandler(show_admin_panel, pattern="^admin_main_menu$"))
    application.add_handler(CallbackQueryHandler(admin_generate_matches_confirm, pattern="^admin_generate_matches_confirm$"))
    application.add_handler(CallbackQueryHandler(admin_generate_matches_execute, pattern="^admin_generate_matches_execute$"))
    application.add_handler(CallbackQueryHandler(admin_list_disputed, pattern="^admin_list_disputed$"))
    application.add_handler(CallbackQueryHandler(admin_manage_matches_info, pattern="^admin_manage_matches_info$"))
    application.add_handler(CallbackQueryHandler(admin_manage_players_menu, pattern="^admin_manage_players$"))
    application.add_handler(CallbackQueryHandler(admin_manage_players_menu, pattern="^admin_manage_players_info$"))
    application.add_handler(CallbackQueryHandler(admin_list_players_page, pattern="^admin_list_players_page_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_view_player, pattern="^admin_view_player_-?\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_edit_club_select, pattern="^admin_edit_club_select_-?\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_edit_club_execute, pattern="^admin_edit_club_execute_-?\\d+_.+$"))
    application.add_handler(CallbackQueryHandler(admin_delete_player_confirm, pattern="^admin_delete_player_confirm_-?\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_delete_player_execute, pattern="^admin_delete_player_execute_-?\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_manage_round, pattern="^admin_manage_round_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_close_round, pattern="^admin_close_round_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_remind_round, pattern="^admin_remind_round_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_toggle_remind_match, pattern="^admin_toggle_remind_match_\\d+_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_toggle_remind_all, pattern="^admin_toggle_remind_all_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_send_selected_reminders, pattern="^admin_send_selected_reminders_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_round_matches, pattern="^admin_round_matches_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_view_match, pattern="^admin_view_match_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_reset_match_execute, pattern="^admin_reset_match_execute_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_report_score_auto, pattern="^admin_report_score_auto_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_set_tp_home_execute, pattern="^admin_tp_home_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_set_tp_away_execute, pattern="^admin_tp_away_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_set_tp_draw_execute, pattern="^admin_tp_draw_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_list_overdue, pattern="^admin_list_overdue$"))
    application.add_handler(CallbackQueryHandler(admin_extend_match_execute, pattern="^admin_extend_match_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_toggle_role, pattern="^admin_toggle_role_-?\\d+_(player|admin)$"))
    application.add_handler(CallbackQueryHandler(admin_delete_options, pattern="^admin_delete_options_-?\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_confirm_wipe_player, pattern="^admin_confirm_wipe_player_-?\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_wipe_player_execute, pattern="^admin_wipe_player_execute_-?\\d+$"))

    # Squad management
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
    application.add_handler(admin_squad_conv)

    application.add_handler(CallbackQueryHandler(admin_manage_players_menu, pattern="^admin_manage_players$"))
    application.add_handler(CallbackQueryHandler(admin_manage_squads, pattern="^admin_manage_squads$"))
    application.add_handler(CallbackQueryHandler(admin_view_squad, pattern="^admin_squad_view_.*$"))
    application.add_handler(CallbackQueryHandler(admin_squad_clear, pattern="^admin_squad_clear_.*$"))
    application.add_handler(CallbackQueryHandler(admin_stub, pattern="^admin_matches_stub$"))
    application.add_handler(CallbackQueryHandler(admin_stub, pattern="^admin_broadcast_stub$"))

    # Cabinet sub-menu callbacks
    application.add_handler(CallbackQueryHandler(show_club_stats, pattern="^cabinet_club_stats$"))
    application.add_handler(CallbackQueryHandler(show_my_squad, pattern="^cabinet_my_squad$"))
    application.add_handler(CallbackQueryHandler(show_game_history, pattern="^cabinet_game_history$"))
    
    # Final catch-all for inline button clicks in development
    application.add_handler(CallbackQueryHandler(handle_placeholders, pattern=".*"))

    # Register global error handler
    application.add_error_handler(error_handler)

import logging
import telegram.error

logger = logging.getLogger(__name__)

async def error_handler(update: object, context) -> None:
    """Log the error that occurred during update handling."""
    if isinstance(context.error, (telegram.error.TimedOut, telegram.error.NetworkError)):
        logger.warning(f"Сетевая задержка Telegram (TimedOut/NetworkError): {context.error}")
        return
    if isinstance(context.error, telegram.error.BadRequest) and "query is too old" in str(context.error).lower():
        logger.warning(f"Устаревший запрос кнопки (Query is too old): {context.error}")
        return
    logger.error("Исключение при обработке обновления:", exc_info=context.error)
