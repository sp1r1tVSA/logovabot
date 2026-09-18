"""
Маршрутизация сообщений по дивизионам: чат и тред всегда из одной привязки.

Дивизионы живут в отдельных супергруппах, а `system_config.group_id` хранит
ровно одну из них. Пара «глобальный чат + message_thread_id дивизиона» уводила
анонсы в чужую группу либо роняла отправку с "message thread not found" — эти
тесты фиксируют, что такой пары больше не возникает.
"""

import itertools
import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import database
from handlers.base import resolve_division_target
from services.topic_cache import topic_cache

_CHAT_SEQ = itertools.count(-1009910100)
_THREAD_SEQ = itertools.count(3100)


class RoutingTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        database.init_db()
        self.uid = uuid.uuid4().hex[:6].upper()
        # Глобальная группа в тестах задаётся только через system_config:
        # config.GROUP_ID читается из окружения и перекрыл бы её.
        self._orig_group_id = config.GROUP_ID
        config.GROUP_ID = None
        self.addCleanup(setattr, config, "GROUP_ID", self._orig_group_id)
        self.addCleanup(topic_cache.reload_cache)

    def _division(self, tag: str) -> int:
        return database.create_division(name=f"{tag} {self.uid}", code=f"{tag}_{self.uid}")

    def _set_global_group(self, chat_id: int) -> None:
        database.set_config("group_id", str(chat_id))
        self.addCleanup(database.set_config, "group_id", "")


class TestPerDivisionRouting(RoutingTestBase):
    async def test_chat_and_thread_come_from_the_same_binding(self):
        div_id = self._division("PAIR")
        chat_id, thread_id = next(_CHAT_SEQ), next(_THREAD_SEQ)
        database.set_division_topic(div_id, "results", thread_id, chat_id)
        topic_cache.reload_cache()

        # Глобальная группа — вообще другая; она не должна попасть в ответ.
        self._set_global_group(next(_CHAT_SEQ))

        self.assertEqual(
            await resolve_division_target(div_id, "results", legacy_topic_keys=("results_topic_id",)),
            (chat_id, thread_id),
        )

    async def test_five_divisions_in_five_groups(self):
        """
        Боевая конфигурация: пять дивизионов в пяти супергруппах, а глобальный
        group_id указывает на группу последнего из них.
        """
        expected = {}
        for n in range(1, 6):
            div_id = self._division(f"G{n}")
            expected[div_id] = (next(_CHAT_SEQ), next(_THREAD_SEQ))
            database.set_division_topic(div_id, "drafts", expected[div_id][1], expected[div_id][0])
        topic_cache.reload_cache()

        self._set_global_group(list(expected.values())[-1][0])

        for div_id, target in expected.items():
            self.assertEqual(await resolve_division_target(div_id, "drafts"), target)

    async def test_first_matching_topic_type_wins(self):
        div_id = self._division("ORDER")
        chat_id = next(_CHAT_SEQ)
        results_thread, reports_thread = next(_THREAD_SEQ), next(_THREAD_SEQ)
        database.set_division_topic(div_id, "results", results_thread, chat_id)
        database.set_division_topic(div_id, "reports", reports_thread, chat_id)
        topic_cache.reload_cache()

        self.assertEqual(
            await resolve_division_target(div_id, "results", "reports"), (chat_id, results_thread)
        )
        self.assertEqual(
            await resolve_division_target(div_id, "reports", "results"), (chat_id, reports_thread)
        )

    async def test_falls_back_to_general_of_the_division_group(self):
        """Топика нужного типа нет — пишем в General своей группы, а не в чужую тему."""
        div_id = self._division("GENERAL")
        chat_id = next(_CHAT_SEQ)
        database.set_division_topic(div_id, "results", next(_THREAD_SEQ), chat_id)
        topic_cache.reload_cache()
        self._set_global_group(next(_CHAT_SEQ))

        self.assertEqual(await resolve_division_target(div_id, "lineups"), (chat_id, None))

    async def test_global_topic_id_is_not_pasted_onto_a_foreign_chat(self):
        """
        Легаси-ключ `*_topic_id` — номер треда внутри глобальной группы. Для
        дивизиона в своей группе он бессмыслен и не должен применяться.
        """
        div_id = self._division("NOLEAK")
        chat_id = next(_CHAT_SEQ)
        database.set_division_group(div_id, chat_id)
        topic_cache.reload_cache()

        self._set_global_group(next(_CHAT_SEQ))
        database.set_config("warns_topic_id", "777")
        self.addCleanup(database.set_config, "warns_topic_id", "")

        self.assertEqual(
            await resolve_division_target(div_id, "warns", legacy_topic_keys=("warns_topic_id",)),
            (chat_id, None),
        )

    async def test_silent_skip_when_division_has_no_binding(self):
        """Ничего не привязано — лучше промолчать, чем написать чужому дивизиону."""
        div_id = self._division("UNBOUND")
        topic_cache.reload_cache()
        self._set_global_group(next(_CHAT_SEQ))

        self.assertEqual(
            await resolve_division_target(div_id, "results", legacy_topic_keys=("results_topic_id",)),
            (None, None),
        )


class TestLegacyRouting(RoutingTestBase):
    async def test_no_division_uses_global_group_and_topic_key(self):
        chat_id = next(_CHAT_SEQ)
        self._set_global_group(chat_id)
        database.set_config("results_topic_id", "555")
        self.addCleanup(database.set_config, "results_topic_id", "")

        self.assertEqual(
            await resolve_division_target(None, "results", legacy_topic_keys=("results_topic_id",)),
            (chat_id, 555),
        )

    async def test_first_present_legacy_key_wins(self):
        chat_id = next(_CHAT_SEQ)
        self._set_global_group(chat_id)
        database.set_config("results_topic_id", "")
        database.set_config("reports_topic_id", "556")
        self.addCleanup(database.set_config, "reports_topic_id", "")

        self.assertEqual(
            await resolve_division_target(
                None, "results", legacy_topic_keys=("results_topic_id", "reports_topic_id")
            ),
            (chat_id, 556),
        )

    async def test_thread_without_chat_pairs_with_the_legacy_group(self):
        """Одногрупповая инсталляция: у привязки нет chat_id, группа ровно одна."""
        div_id = self._division("ONEGRP")
        chat_id, thread_id = next(_CHAT_SEQ), next(_THREAD_SEQ)
        database.set_division_topic(div_id, "results", thread_id)
        topic_cache.reload_cache()
        self._set_global_group(chat_id)

        self.assertEqual(await resolve_division_target(div_id, "results"), (chat_id, thread_id))

    async def test_no_group_at_all_returns_nothing(self):
        self.assertEqual(await resolve_division_target(None, "results"), (None, None))


if __name__ == "__main__":
    unittest.main()
