"""
handlers/topic_management.py
Управление Telegram Forum Topics для дивизионов (русские команды, RBAC, защита от конфликтов, TopicCache).
"""

import html
import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, CommandHandler, CallbackQueryHandler, filters

import database
from services.topic_cache import topic_cache
from handlers.base import is_admin

logger = logging.getLogger(__name__)

# Маппинг коротких кодов для компактных Telegram callbacks (<64 bytes)
SHORT_TO_TYPE = {
    "d": "draft",
    "p": "previews",
    "r": "results",
    "rep": "reports",
    "l": "lineups"
}

TYPE_TO_SHORT = {v: k for k, v in SHORT_TO_TYPE.items()}


def _build_topic_type_keyboard(division_id: int, action_prefix: str = "set_top") -> InlineKeyboardMarkup:
    """Генерация клавиатуры выбора типа топика с русскими названиями."""
    buttons = [
        [InlineKeyboardButton("🏟 ЧЕРНОВИК", callback_data=f"{action_prefix}:{division_id}:d")],
        [InlineKeyboardButton("👤 ПРЕДЫ", callback_data=f"{action_prefix}:{division_id}:p")],
        [InlineKeyboardButton("🎛 РЕЗУЛЬТАТЫ", callback_data=f"{action_prefix}:{division_id}:r")],
        [InlineKeyboardButton("📞 ОТЧЁТЫ", callback_data=f"{action_prefix}:{division_id}:rep")],
        [InlineKeyboardButton("🗺 СОСТАВЫ", callback_data=f"{action_prefix}:{division_id}:l")],
        [InlineKeyboardButton("❌ Отмена", callback_data="top_cancel")]
    ]
    return InlineKeyboardMarkup(buttons)


async def cmd_assign_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Команда /назначить_топик <division_id>
    Вызывается внутри Telegram Forum Topic.
    """
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not msg or not user or not chat:
        return

    # 1. Валидация использования строго внутри топика форума
    thread_id = msg.message_thread_id
    if thread_id is None:
        await msg.reply_text("❌ Эту команду необходимо использовать внутри Telegram-топика.")
        return

    if chat.type not in ("supergroup", "group"):
        await msg.reply_text("❌ Назначение топиков возможно только в супергруппах Telegram с включёнными темами.")
        return

    # 2. Валидация аргументов
    if not context.args or not context.args[0].isdigit():
        await msg.reply_text(
            "ℹ️ <b>Использование:</b> <code>/назначить_топик &lt;id_дивизиона&gt;</code>\n"
            "Пример: <code>/назначить_топик 1</code>",
            parse_mode="HTML"
        )
        return

    division_id = int(context.args[0])

    # 3. Валидация существования дивизиона
    div = database.get_division(division_id)
    if not div:
        await msg.reply_text(f"❌ Дивизион с ID <b>{division_id}</b> не найден.", parse_mode="HTML")
        return

    # 4. Проверка прав (RBAC): global admin ИЛИ admin этого division
    if not database.is_division_admin(user.id, division_id):
        await msg.reply_text(
            f"⛔️ У вас нет прав на управление топиками Дивизиона <b>{html.escape(div['name'])}</b>.",
            parse_mode="HTML"
        )
        return

    # 5. Вывод интерактивного меню выбора назначения топика
    div_name = html.escape(div["name"])
    text = (
        f"🎯 <b>Настройка топика для дивизиона:</b> {div_name} (ID {division_id})\n\n"
        f"📌 <b>ID чата:</b> <code>{chat.id}</code>\n"
        f"🧵 <b>ID топика:</b> <code>{thread_id}</code>\n\n"
        f"Выберите назначение для этого топика:"
    )
    keyboard = _build_topic_type_keyboard(division_id, action_prefix="set_top")
    await msg.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def cmd_reassign_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /переназначить_топик <division_id>"""
    await cmd_assign_topic(update, context)


async def cb_set_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка нажатия кнопки назначения топика (set_top:{div_id}:{short_code})."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    parts = query.data.split(":")
    if len(parts) != 3:
        return

    _, div_id_str, short_code = parts
    if not div_id_str.isdigit():
        return
    division_id = int(div_id_str)
    topic_type = SHORT_TO_TYPE.get(short_code)
    if not topic_type:
        return

    user = query.from_user
    msg = query.message
    chat_id = msg.chat.id
    thread_id = msg.message_thread_id

    # 1. Повторная серверная валидация прав пользователя (Callback Security)
    if not database.is_division_admin(user.id, division_id):
        await query.answer("⛔️ Недостаточно прав для управления этим дивизионом!", show_alert=True)
        return

    if thread_id is None:
        await query.edit_message_text("❌ Ошибка: сообщение не находится внутри темы Telegram.")
        return

    # 2. Попытка безопасной привязки в БД
    res = database.bind_division_topic(
        division_id=division_id,
        group_chat_id=chat_id,
        message_thread_id=thread_id,
        topic_type=topic_type,
        force=False
    )

    type_display = database.TOPIC_DISPLAY_NAMES.get(topic_type, topic_type.upper())

    # Сценарий А: Идемпотентность (топик уже назначен)
    if res.get("status") == "already_bound":
        div_name = html.escape(res.get("division_name", str(division_id)))
        text = (
            f"✅ <b>Этот топик уже назначен:</b>\n\n"
            f"🏆 <b>Дивизион:</b> {div_name}\n"
            f"<b>Назначение:</b> {type_display}\n"
            f"📌 <b>Чат:</b> <code>{chat_id}</code>\n"
            f"🧵 <b>Топик:</b> <code>{thread_id}</code>"
        )
        await query.edit_message_text(text, parse_mode="HTML")
        return

    # Сценарий Б: Конфликт 1 (Топик уже занят другим дивизионом или типом)
    if res.get("status") == "conflict_topic":
        cur_div = res.get("current_division_name", "Неизвестный")
        cur_type = database.TOPIC_DISPLAY_NAMES.get(res.get("current_topic_type"), res.get("current_topic_type"))
        req_div = res.get("requested_division_name", "Неизвестный")
        req_type = database.TOPIC_DISPLAY_NAMES.get(res.get("requested_topic_type"), res.get("requested_topic_type"))

        text = (
            f"⚠️ <b>ТОПИК УЖЕ НАЗНАЧЕН</b>\n\n"
            f"<b>Текущая привязка:</b>\n"
            f"🏆 Дивизион: {html.escape(cur_div)} (ID {res.get('current_division_id')})\n"
            f"Назначение: {cur_type}\n\n"
            f"<b>Новая привязка:</b>\n"
            f"🏆 Дивизион: {html.escape(req_div)} (ID {res.get('requested_division_id')})\n"
            f"Назначение: {req_type}\n\n"
            f"Вы хотите переназначить этот топик?"
        )
        buttons = [
            [InlineKeyboardButton("🔄 Подтвердить переназначение", callback_data=f"reassign_top:{division_id}:{short_code}")],
            [InlineKeyboardButton("❌ Отмена", callback_data="top_cancel")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return

    # Сценарий В: Конфликт 2 (У дивизиона этот тип уже привязан к другому топику)
    if res.get("status") == "conflict_type":
        div_name = html.escape(res.get("division_name", str(division_id)))
        old_thread = res.get("current_thread_id")
        text = (
            f"⚠️ <b>ТИП ТОПИКА УЖЕ НАЗНАЧЕН</b>\n\n"
            f"В Дивизионе <b>{div_name}</b> назначение <b>{type_display}</b> "
            f"уже привязано к топику <code>#{old_thread}</code>.\n\n"
            f"Хотите перенести назначение в этот топик (<code>#{thread_id}</code>)?"
        )
        buttons = [
            [InlineKeyboardButton("🔄 Перенести в этот топик", callback_data=f"reassign_top:{division_id}:{short_code}")],
            [InlineKeyboardButton("❌ Отмена", callback_data="top_cancel")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return

    # Сценарий Г: Успешная первичная привязка
    if res.get("status") == "ok":
        div_name = html.escape(res.get("division_name", str(division_id)))
        # Синхронизация TopicCache
        topic_cache.set_topic(
            division_id=division_id,
            group_chat_id=chat_id,
            message_thread_id=thread_id,
            topic_type=topic_type,
            division_name=res.get("division_name", "")
        )
        text = (
            f"✅ <b>Топик успешно назначен!</b>\n\n"
            f"🏆 <b>Дивизион:</b> {div_name} (ID {division_id})\n"
            f"<b>Назначение:</b> {type_display}\n"
            f"📌 <b>Чат:</b> <code>{chat_id}</code>\n"
            f"🧵 <b>Топик:</b> <code>{thread_id}</code>"
        )
        await query.edit_message_text(text, parse_mode="HTML")
        return

    # Ошибка
    err_msg = res.get("error", "Неизвестная ошибка")
    await query.edit_message_text(f"❌ Ошибка при назначении топика: {html.escape(err_msg)}")


async def cb_reassign_topic_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Явное подтверждение переназначения топика с принудительной очисткой старых коллизий."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    parts = query.data.split(":")
    if len(parts) != 3:
        return

    _, div_id_str, short_code = parts
    if not div_id_str.isdigit():
        return
    division_id = int(div_id_str)
    topic_type = SHORT_TO_TYPE.get(short_code)
    if not topic_type:
        return

    user = query.from_user
    msg = query.message
    chat_id = msg.chat.id
    thread_id = msg.message_thread_id

    # RBAC валидация:
    # 1. Пользователь должен быть админом целевого дивизиона (или глобальным админом)
    import config
    user_rec = database.get_user(user.id)
    is_global_admin = (user.id in config.ADMIN_IDS) or bool(user_rec and user_rec["role"] == "admin" and user_rec["division_id"] is None)
    if not (is_global_admin or database.is_division_admin(user.id, division_id)):
        await query.answer("⛔️ Недостаточно прав для управления этим дивизионом!", show_alert=True)
        return

    # 2. Если этот топик уже привязан к ДРУГОМУ дивизиону, только Global Admin или админ того дивизиона может его забрать
    existing_binding = database.get_topic_binding(chat_id, thread_id)
    if existing_binding and existing_binding.get("division_id") != division_id:
        prev_div_id = existing_binding["division_id"]
        if not (is_global_admin or database.is_division_admin(user.id, prev_div_id)):
            await query.answer(
                f"⛔️ Этот топик принадлежит дивизиону «{existing_binding.get('division_name', prev_div_id)}»! "
                "Переназначить его может только главный администратор.",
                show_alert=True
            )
            return

    if thread_id is None:
        await query.edit_message_text("❌ Ошибка: топик не определён.")
        return

    # Атомарное принудительное переназначение
    res = database.bind_division_topic(
        division_id=division_id,
        group_chat_id=chat_id,
        message_thread_id=thread_id,
        topic_type=topic_type,
        force=True
    )

    if res.get("status") == "ok":
        topic_cache.set_topic(
            division_id=division_id,
            group_chat_id=chat_id,
            message_thread_id=thread_id,
            topic_type=topic_type,
            division_name=res.get("division_name", "")
        )
        type_display = database.TOPIC_DISPLAY_NAMES.get(topic_type, topic_type.upper())
        div_name = html.escape(res.get("division_name", str(division_id)))
        text = (
            f"🔄 <b>Топик успешно переназначен!</b>\n\n"
            f"🏆 <b>Дивизион:</b> {div_name} (ID {division_id})\n"
            f"<b>Назначение:</b> {type_display}\n"
            f"📌 <b>Чат:</b> <code>{chat_id}</code>\n"
            f"🧵 <b>Топик:</b> <code>{thread_id}</code>"
        )
        await query.edit_message_text(text, parse_mode="HTML")
    else:
        err = res.get("error", "Ошибка переназначения")
        await query.edit_message_text(f"❌ Не удалось переназначить топик: {html.escape(err)}")


async def cb_top_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отмена настройки топика."""
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("❌ Действие отменено.")


async def cmd_current_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Команда /текущий_топик
    Показывает привязку текущего топика форума.
    """
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return

    thread_id = msg.message_thread_id
    if thread_id is None:
        await msg.reply_text("❌ Эту команду необходимо использовать внутри Telegram-топика.")
        return

    # Проверяем привязку через TopicCache / БД
    binding = topic_cache.get_by_topic(chat.id, thread_id)
    if not binding:
        binding = database.get_topic_binding(chat.id, thread_id)

    if not binding:
        text = (
            "📌 <b>ТЕКУЩИЙ ТОПИК</b>\n\n"
            "❌ Этот топик ещё не назначен."
        )
        await msg.reply_text(text, parse_mode="HTML")
        return

    div_name = html.escape(binding.get("division_name") or f"ID {binding.get('division_id')}")
    top_type = binding.get("topic_type", "")
    type_display = database.TOPIC_DISPLAY_NAMES.get(top_type, top_type.upper())

    text = (
        f"📌 <b>ТЕКУЩИЙ ТОПИК</b>\n\n"
        f"🏆 <b>Дивизион:</b> {div_name} (ID {binding.get('division_id')})\n"
        f"🎛 <b>Назначение:</b> {type_display}\n"
        f"✅ <b>Статус:</b> настроен\n\n"
        f"<i>Chat ID: <code>{chat.id}</code> | Thread ID: <code>{thread_id}</code></i>"
    )
    await msg.reply_text(text, parse_mode="HTML")


async def cmd_division_topics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Команда /топики <division_id>
    Показывает статус всех 5 основных топиков для дивизиона.
    """
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg:
        return

    # Определение division_id из аргумента или по текущему топику
    division_id = None
    if context.args and context.args[0].isdigit():
        division_id = int(context.args[0])
    elif msg.message_thread_id is not None:
        binding = topic_cache.get_by_topic(chat.id, msg.message_thread_id)
        if binding:
            division_id = binding.get("division_id")

    if division_id is None:
        await msg.reply_text(
            "ℹ️ <b>Использование:</b> <code>/топики &lt;id_дивизиона&gt;</code>\n"
            "Пример: <code>/топики 1</code>",
            parse_mode="HTML"
        )
        return

    div = database.get_division(division_id)
    if not div:
        await msg.reply_text(f"❌ Дивизион с ID <b>{division_id}</b> не найден.", parse_mode="HTML")
        return

    is_user_admin = is_admin(user.id) or database.is_division_admin(user.id, division_id)
    summary = topic_cache.get_division_topics_summary(division_id)
    if not summary:
        summary = database.get_division_topics_map(division_id)

    div_name = html.escape(div["name"]).upper()
    lines = [f"🏆 <b>ДИВИЗИОН {div_name}</b>\n"]

    for t in database.PRIMARY_DIVISION_TOPICS:
        display = database.TOPIC_DISPLAY_NAMES.get(t, t)
        bound = summary.get(t)
        status_icon = "✅" if bound else "❌"
        line = f"{display:<16} {status_icon}"
        if bound and is_user_admin:
            c_id = bound.get("group_chat_id")
            th_id = bound.get("message_thread_id")
            line += f"  <i>(Thread: <code>{th_id}</code>)</i>"
        lines.append(line)

    await msg.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_divisions_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Команда /дивизионы
    Показывает глобальному администратору общую сводку настроенных топиков по всем дивизионам.
    """
    user = update.effective_user
    msg = update.effective_message
    if not msg or not user:
        return

    if not is_admin(user.id):
        await msg.reply_text("⛔️ Данная команда доступна только администраторам.")
        return

    divisions = database.get_divisions()
    if not divisions:
        await msg.reply_text("ℹ️ В системе пока нет созданных дивизионов.")
        return

    lines = ["🏆 <b>ДИВИЗИОНЫ И ТОПИКИ</b>\n"]
    for idx, d in enumerate(divisions, start=1):
        summary = topic_cache.get_division_topics_summary(d["id"])
        if not summary:
            summary = database.get_division_topics_map(d["id"])
        
        configured_count = len(summary)
        total_count = len(database.PRIMARY_DIVISION_TOPICS)
        
        status_dot = "🟢" if d.get("is_active") else "🔴"
        name = html.escape(d["name"])
        lines.append(f"{idx}. {status_dot} <b>{name}</b> (ID {d['id']})")
        lines.append(f"   Топики: <b>{configured_count}/{total_count}</b>\n")

    await msg.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_unbind_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Команда /снять_топик
    Снимает привязку текущего топика форума с подтверждением.
    """
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat:
        return

    thread_id = msg.message_thread_id
    if thread_id is None:
        await msg.reply_text("❌ Эту команду необходимо использовать внутри Telegram-топика.")
        return

    binding = topic_cache.get_by_topic(chat.id, thread_id)
    if not binding:
        binding = database.get_topic_binding(chat.id, thread_id)

    if not binding:
        await msg.reply_text("ℹ️ Этот топик не привязан ни к одному дивизиону.")
        return

    div_id = binding.get("division_id")
    if not database.is_division_admin(user.id, div_id):
        await msg.reply_text("⛔️ У вас нет прав на снятие привязки топика этого дивизиона.")
        return

    div_name = html.escape(binding.get("division_name") or str(div_id))
    type_display = database.TOPIC_DISPLAY_NAMES.get(binding.get("topic_type"), binding.get("topic_type"))

    text = (
        f"🗑 <b>СНЯТИЕ ПРИВЯЗКИ ТОПИКА</b>\n\n"
        f"<b>Текущая привязка:</b>\n"
        f"🏆 Дивизион: <b>{div_name}</b> (ID {div_id})\n"
        f"Назначение: {type_display}\n"
        f"📌 Чат: <code>{chat.id}</code> | Топик: <code>{thread_id}</code>\n\n"
        f"Вы действительно хотите снять привязку?"
    )
    buttons = [
        [InlineKeyboardButton("🗑 Снять привязку", callback_data=f"unbind_confirm:{chat.id}:{thread_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="top_cancel")]
    ]
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")


async def cb_unbind_topic_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка подтверждения снятия привязки."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    parts = query.data.split(":")
    if len(parts) != 3:
        return

    _, chat_id_str, thread_id_str = parts
    try:
        chat_id = int(chat_id_str)
        thread_id = int(thread_id_str)
    except ValueError:
        return

    user = query.from_user
    binding = database.get_topic_binding(chat_id, thread_id)
    if not binding:
        topic_cache.remove_topic(chat_id, thread_id)
        await query.edit_message_text("ℹ️ Привязка уже была удалена.")
        return

    if not database.is_division_admin(user.id, binding["division_id"]):
        await query.answer("⛔️ Недостаточно прав для снятия привязки!", show_alert=True)
        return

    deleted = database.unbind_division_topic(chat_id, thread_id)
    topic_cache.remove_topic(chat_id, thread_id)

    await query.edit_message_text(
        "✅ <b>Привязка топика успешно снята.</b>\nТопик больше не закреплён за дивизионом.",
        parse_mode="HTML"
    )


def _make_cyrillic_command_handler(command_name: str, handler_func):
    """
    Creates a MessageHandler that responds to Cyrillic slash commands (e.g. /назначить_топик 1)
    and populates context.args identical to CommandHandler.
    """
    pattern = re.compile(rf"^/{command_name}(?:@\w+)?(?:\s+(.*))?$", re.DOTALL | re.IGNORECASE)

    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_message or not update.effective_message.text:
            return
        text = update.effective_message.text.strip()
        m = pattern.match(text)
        if m:
            args_str = m.group(1)
            context.args = args_str.split() if args_str else []
            return await handler_func(update, context)

    return MessageHandler(filters.Regex(rf"^/{command_name}(?:@\w+)?(?:\s|$)"), wrapper)


def register_topic_management_handlers(app) -> None:
    """Register all topic management commands and callbacks into Telegram Application."""
    # 1. Cyrillic Slash Commands (via Regex MessageHandler)
    app.add_handler(_make_cyrillic_command_handler("назначить_топик", cmd_assign_topic))
    app.add_handler(_make_cyrillic_command_handler("переназначить_топик", cmd_reassign_topic))
    app.add_handler(_make_cyrillic_command_handler("текущий_топик", cmd_current_topic))
    app.add_handler(_make_cyrillic_command_handler("топики", cmd_division_topics))
    app.add_handler(_make_cyrillic_command_handler("дивизионы", cmd_divisions_summary))
    app.add_handler(_make_cyrillic_command_handler("снять_топик", cmd_unbind_topic))

    # 2. Latin / Translit Aliases (via native CommandHandler)
    app.add_handler(CommandHandler(["naznachit_topik", "assign_topic"], cmd_assign_topic))
    app.add_handler(CommandHandler(["perenaznachit_topik", "reassign_topic"], cmd_reassign_topic))
    app.add_handler(CommandHandler(["tekushiy_topik", "current_topic"], cmd_current_topic))
    app.add_handler(CommandHandler(["topiki", "topics"], cmd_division_topics))
    app.add_handler(CommandHandler(["diviziony", "divisions"], cmd_divisions_summary))
    app.add_handler(CommandHandler(["snyat_topik", "unbind_topic"], cmd_unbind_topic))

    # 3. Callbacks
    app.add_handler(CallbackQueryHandler(cb_set_topic, pattern="^set_top:\\d+:(d|p|r|rep|l)$"))
    app.add_handler(CallbackQueryHandler(cb_reassign_topic_confirm, pattern="^reassign_top:\\d+:(d|p|r|rep|l)$"))
    app.add_handler(CallbackQueryHandler(cb_unbind_topic_confirm, pattern="^unbind_confirm:-?\\d+:-?\\d+$"))
    app.add_handler(CallbackQueryHandler(cb_top_cancel, pattern="^top_cancel$"))

