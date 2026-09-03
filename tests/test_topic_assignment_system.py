"""
tests/test_topic_assignment_system.py
Комплексное тестирование системы назначения топиков дивизионам:
TEST 1: Global admin назначает Division 1 / ЧЕРНОВИК. -> SUCCESS
TEST 2: Division 1 admin назначает Division 1 / ПРЕДЫ. -> SUCCESS
TEST 3: Division 1 admin пытается назначить Division 2. -> DENIED
TEST 4: Обычный пользователь пытается выполнить назначение. -> DENIED
TEST 5: Команда вне топика (message_thread_id is None). -> DENIED
TEST 6: Несуществующий division. -> DENIED
TEST 7: Один topic назначается повторно. -> NO DUPLICATE (Идемпотентность)
TEST 8: Один topic пытаются назначить другому division. -> DENIED (Конфликт топика)
TEST 9: Один division получает второй topic того же типа. -> DENIED (Конфликт типа)
TEST 10: Одинаковый thread_id в разных group_chat_id. -> NO CONFLICT (Мульти-групповая изоляция)
TEST 11: Restart application: DB -> TopicCache восстановление.
TEST 12: TopicCache синхронизирован с DB (set, remove, reload).
TEST 13: Старые записи с division_id = NULL продолжают работать без сбоев.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock
import uuid
import time
import database
from services.topic_cache import topic_cache
from handlers.topic_management import (
    cmd_assign_topic,
    cmd_reassign_topic,
    cmd_current_topic,
    cmd_division_topics,
    cmd_divisions_summary,
    cmd_unbind_topic,
    cb_set_topic,
    cb_reassign_topic_confirm,
    cb_unbind_topic_confirm
)


class TestTopicAssignmentSystem(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self.uid = uuid.uuid4().hex[:6].upper()
        
        # Создаем тестовые дивизионы
        self.div1_id = database.create_division(name=f"Дивизион 1 {self.uid}", code=f"D1_{self.uid}")
        self.div2_id = database.create_division(name=f"Дивизион 2 {self.uid}", code=f"D2_{self.uid}")
        
        # Тестовые пользователи
        self.global_admin_id = 90000001
        self.div1_admin_id = 90000002
        self.regular_user_id = 90000003

        # Регистрируем пользователей в БД
        database.register_user(self.global_admin_id, f"global_admin_{self.uid}", team_name="AdminTeam")
        database.register_user(self.div1_admin_id, f"div1_admin_{self.uid}", team_name="Div1Team")
        database.register_user(self.regular_user_id, f"regular_{self.uid}", team_name="RegularTeam")

        # Настраиваем роли
        # 1. Global admin: роль 'admin' без дивизиона
        with database.transaction() as conn:
            conn.execute("UPDATE users SET role = 'admin', division_id = NULL WHERE telegram_id = ?", (self.global_admin_id,))
        
        # 2. Div 1 admin: привязан к division_admins
        database.add_division_admin(self.div1_id, self.div1_admin_id)

        # 3. Regular user: роль 'user'
        with database.transaction() as conn:
            conn.execute("UPDATE users SET role = 'user', division_id = ? WHERE telegram_id = ?", (self.div1_id, self.regular_user_id))

        # Сброс и перезагрузка кеша
        topic_cache.reload_cache()

    def tearDown(self):
        # Очистка созданных привязок
        with database.transaction() as conn:
            conn.execute("DELETE FROM division_topics WHERE division_id IN (?, ?)", (self.div1_id, self.div2_id))
            conn.execute("DELETE FROM division_admins WHERE division_id IN (?, ?)", (self.div1_id, self.div2_id))
            conn.execute("DELETE FROM divisions WHERE id IN (?, ?)", (self.div1_id, self.div2_id))
        topic_cache.reload_cache()

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 1: Global admin назначает Division 1 / ЧЕРНОВИК -> SUCCESS
    # ──────────────────────────────────────────────────────────────────────────
    def test_01_global_admin_assign_draft_success(self):
        self.assertTrue(database.is_division_admin(self.global_admin_id, self.div1_id))
        
        chat_id = -100111222333
        thread_id = 101
        res = database.bind_division_topic(
            division_id=self.div1_id,
            group_chat_id=chat_id,
            message_thread_id=thread_id,
            topic_type="draft",
            force=False
        )
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["division_id"], self.div1_id)
        self.assertEqual(res["topic_type"], "draft")

        # Проверка чтения из БД
        binding = database.get_topic_binding(chat_id, thread_id)
        self.assertIsNotNone(binding)
        self.assertEqual(binding["division_id"], self.div1_id)
        self.assertEqual(binding["topic_type"], "draft")

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 2: Division 1 admin назначает Division 1 / ПРЕДЫ -> SUCCESS
    # ──────────────────────────────────────────────────────────────────────────
    def test_02_div1_admin_assign_previews_success(self):
        self.assertTrue(database.is_division_admin(self.div1_admin_id, self.div1_id))

        chat_id = -100111222333
        thread_id = 102
        res = database.bind_division_topic(
            division_id=self.div1_id,
            group_chat_id=chat_id,
            message_thread_id=thread_id,
            topic_type="previews",
            force=False
        )
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["division_id"], self.div1_id)
        self.assertEqual(res["topic_type"], "previews")

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 3: Division 1 admin пытается назначить Division 2 -> DENIED
    # ──────────────────────────────────────────────────────────────────────────
    def test_03_div1_admin_assign_div2_denied(self):
        # Div 1 admin не является админом Div 2!
        self.assertFalse(database.is_division_admin(self.div1_admin_id, self.div2_id))

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 4: Обычный пользователь пытается выполнить назначение -> DENIED
    # ──────────────────────────────────────────────────────────────────────────
    def test_04_regular_user_denied(self):
        self.assertFalse(database.is_division_admin(self.regular_user_id, self.div1_id))
        self.assertFalse(database.is_division_admin(self.regular_user_id, self.div2_id))

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 5: Команда вне топика (message_thread_id is None) -> DENIED
    # ──────────────────────────────────────────────────────────────────────────
    def test_05_command_outside_topic_denied(self):
        update = MagicMock()
        context = MagicMock()
        update.effective_message.message_thread_id = None
        update.effective_message.reply_text = AsyncMock()
        update.effective_user.id = self.global_admin_id
        update.effective_chat.type = "supergroup"
        context.args = [str(self.div1_id)]

        import asyncio
        asyncio.run(cmd_assign_topic(update, context))

        update.effective_message.reply_text.assert_called_once()
        args, _ = update.effective_message.reply_text.call_args
        self.assertIn("внутри Telegram-топика", args[0])

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 6: Несуществующий division -> DENIED
    # ──────────────────────────────────────────────────────────────────────────
    def test_06_non_existent_division_denied(self):
        non_existent_id = 9999999
        res = database.bind_division_topic(
            division_id=non_existent_id,
            group_chat_id=-100111222333,
            message_thread_id=105,
            topic_type="draft",
            force=False
        )
        self.assertEqual(res["status"], "error")
        self.assertIn("not found", res["error"])

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 7: Один topic назначается повторно -> NO DUPLICATE (Идемпотентность)
    # ──────────────────────────────────────────────────────────────────────────
    def test_07_idempotent_topic_assignment(self):
        chat_id = -100111222333
        thread_id = 107
        res1 = database.bind_division_topic(self.div1_id, chat_id, thread_id, "results")
        self.assertEqual(res1["status"], "ok")

        # Повторное назначение того же топика на тот же тип и дивизион
        res2 = database.bind_division_topic(self.div1_id, chat_id, thread_id, "results")
        self.assertEqual(res2["status"], "already_bound")
        self.assertEqual(res2["division_id"], self.div1_id)

        # Проверяем, что в БД ровно 1 запись для этого топика
        with database.transaction() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM division_topics WHERE group_chat_id = ? AND message_thread_id = ?", (chat_id, thread_id))
            self.assertEqual(cur.fetchone()["cnt"], 1)

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 8: Один topic пытаются назначить другому division -> DENIED (Конфликт)
    # ──────────────────────────────────────────────────────────────────────────
    def test_08_topic_already_belongs_to_another_division_denied(self):
        chat_id = -100111222333
        thread_id = 108
        # Топик занят Division 1 (ЧЕРНОВИК)
        res1 = database.bind_division_topic(self.div1_id, chat_id, thread_id, "draft")
        self.assertEqual(res1["status"], "ok")

        # Попытка назначить этот же топик Division 2 (РЕЗУЛЬТАТЫ) без force
        res2 = database.bind_division_topic(self.div2_id, chat_id, thread_id, "results", force=False)
        self.assertEqual(res2["status"], "conflict_topic")
        self.assertEqual(res2["current_division_id"], self.div1_id)
        self.assertEqual(res2["requested_division_id"], self.div2_id)

        # С force=True переназначение проходит успешно
        res3 = database.bind_division_topic(self.div2_id, chat_id, thread_id, "results", force=True)
        self.assertEqual(res3["status"], "ok")
        self.assertEqual(res3["division_id"], self.div2_id)

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 9: Один division получает второй topic того же типа -> DENIED (Конфликт)
    # ──────────────────────────────────────────────────────────────────────────
    def test_09_division_already_has_topic_type_denied(self):
        chat_id = -100111222333
        thread_1 = 1091
        thread_2 = 1092

        # Назначаем thread_1 для reports в Div 1
        res1 = database.bind_division_topic(self.div1_id, chat_id, thread_1, "reports")
        self.assertEqual(res1["status"], "ok")

        # Пытаемся назначить thread_2 также для reports в Div 1 без force
        res2 = database.bind_division_topic(self.div1_id, chat_id, thread_2, "reports", force=False)
        self.assertEqual(res2["status"], "conflict_type")
        self.assertEqual(res2["current_thread_id"], thread_1)
        self.assertEqual(res2["requested_thread_id"], thread_2)

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 10: Одинаковый thread_id в разных group_chat_id -> NO CONFLICT
    # ──────────────────────────────────────────────────────────────────────────
    def test_10_same_thread_id_in_different_chats_no_conflict(self):
        chat_a = -100111111111
        chat_b = -100222222222
        same_thread_id = 999

        # Топик 999 в группе A -> Div 1 / lineups
        res_a = database.bind_division_topic(self.div1_id, chat_a, same_thread_id, "lineups")
        self.assertEqual(res_a["status"], "ok")

        # Топик 999 в группе B -> Div 2 / lineups
        res_b = database.bind_division_topic(self.div2_id, chat_b, same_thread_id, "lineups")
        self.assertEqual(res_b["status"], "ok")

        # Проверяем раздельные привязки в БД
        binding_a = database.get_topic_binding(chat_a, same_thread_id)
        binding_b = database.get_topic_binding(chat_b, same_thread_id)
        self.assertEqual(binding_a["division_id"], self.div1_id)
        self.assertEqual(binding_b["division_id"], self.div2_id)

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 11: Restart application: DB -> TopicCache восстановление
    # ──────────────────────────────────────────────────────────────────────────
    def test_11_restart_cache_reload_from_db(self):
        chat_id = -100111222333
        thread_id = 111
        database.bind_division_topic(self.div1_id, chat_id, thread_id, "draft")

        # Симулируем перезапуск приложения: очистка кеша и reload_cache
        topic_cache._by_topic.clear()
        topic_cache._by_division.clear()
        topic_cache._initialized = False

        topic_cache.reload_cache()

        cached = topic_cache.get_by_topic(chat_id, thread_id)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["division_id"], self.div1_id)
        self.assertEqual(cached["topic_type"], "draft")

        # Обратный поиск из кеша
        rev = topic_cache.get_by_division(self.div1_id, "draft")
        self.assertIsNotNone(rev)
        self.assertEqual(rev["message_thread_id"], thread_id)
        self.assertEqual(rev["group_chat_id"], chat_id)

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 12: TopicCache синхронизирован с DB (set, remove, reload)
    # ──────────────────────────────────────────────────────────────────────────
    def test_12_cache_sync_on_set_and_remove(self):
        chat_id = -100111222333
        thread_id = 112

        # 1. Привязка в БД и обновление кеша
        database.bind_division_topic(self.div1_id, chat_id, thread_id, "previews")
        topic_cache.set_topic(self.div1_id, chat_id, thread_id, "previews", division_name="Div1")

        cached = topic_cache.get_by_topic(chat_id, thread_id)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["topic_type"], "previews")

        # 2. Снятие привязки в БД и удаление из кеша
        deleted = database.unbind_division_topic(chat_id, thread_id)
        self.assertIsNotNone(deleted)
        topic_cache.remove_topic(chat_id, thread_id)

        self.assertIsNone(topic_cache.get_by_topic(chat_id, thread_id))
        self.assertIsNone(database.get_topic_binding(chat_id, thread_id))

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 13: Старые записи с division_id = NULL продолжают работать без сбоев
    # ──────────────────────────────────────────────────────────────────────────
    def test_13_legacy_null_division_id_compatibility(self):
        # Создаем легаси-матч с division_id = NULL
        with database.transaction() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO matches (round_number, player1_id, player2_id, status, division_id)
                VALUES (99, ?, ?, 'pending', NULL)
            """, (self.global_admin_id, self.regular_user_id))
            legacy_match_id = cur.lastrowid

        m = database.get_match(legacy_match_id)
        self.assertIsNotNone(m)
        self.assertIsNone(m["division_id"])

        # Вызов standings без дивизиона не вызывает исключений
        standings = database.get_standings()
        self.assertIsInstance(standings, list)

        # Очистка легаси-матча
        with database.transaction() as conn:
            conn.execute("DELETE FROM matches WHERE id = ?", (legacy_match_id,))

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 14: Парсинг русских слеш-команд и аргументов
    # ──────────────────────────────────────────────────────────────────────────
    def test_14_cyrillic_command_parsing(self):
        from handlers.topic_management import _make_cyrillic_command_handler
        mock_func = AsyncMock()
        h = _make_cyrillic_command_handler("назначить_топик", mock_func)

        update = MagicMock()
        update.effective_message.text = "/назначить_топик 1"
        ctx = MagicMock()

        import asyncio
        asyncio.run(h.callback(update, ctx))
        mock_func.assert_called_once()
        self.assertEqual(ctx.args, ["1"])

