"""
Guards that the division admin panel covers every topic a division's forum has.

The panel used to handle only drafts/results/tables, so a division could have
ПРЕДЫ / ОТЧЁТЫ / СОСТАВЫ bound in the group with no way to see or change that
from the bot. These tests pin the places that must stay in sync with
database.PRIMARY_DIVISION_TOPICS.
"""

import ast
import os
import re
import unittest

import database

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel_path: str) -> str:
    with open(os.path.join(WORKSPACE, rel_path), "r", encoding="utf-8") as f:
        return f.read()


def _function_body(source: str, name: str) -> str:
    start = source.index(f"async def {name}")
    return source[start:source.index("async def ", start + 10)]


class TestDivisionTopicCoverage(unittest.TestCase):
    def test_every_topic_has_a_display_name(self):
        for topic_type in database.PRIMARY_DIVISION_TOPICS:
            self.assertIn(
                topic_type, database.TOPIC_DISPLAY_NAMES,
                f"Топик «{topic_type}» не имеет отображаемого названия"
            )

    def test_every_topic_is_canonical(self):
        """A non-canonical type would be stored normalized and never read back."""
        for topic_type in database.PRIMARY_DIVISION_TOPICS:
            self.assertEqual(database.normalize_topic_type(topic_type), topic_type)

    def test_settopic_callback_pattern_covers_every_topic(self):
        """
        The pattern in handlers/__init__.py must stay a literal (the static button
        audit scrapes it from source), so it cannot be built from the constant —
        this test is what keeps the two from drifting apart.
        """
        source = _read(os.path.join("handlers", "__init__.py"))
        m = re.search(
            r'CallbackQueryHandler\(\s*admin_div_settopic_prompt\s*,\s*pattern\s*=\s*("[^"]+")',
            source,
        )
        self.assertIsNotNone(m, "Не найден CallbackQueryHandler для admin_div_settopic_prompt")
        pattern = re.compile(ast.literal_eval(m.group(1)))

        for topic_type in database.PRIMARY_DIVISION_TOPICS:
            self.assertTrue(
                pattern.match(f"admin_div_settopic_7_{topic_type}"),
                f"Кнопка привязки топика «{topic_type}» не поймается паттерном"
            )

    def test_settopic_callback_pattern_keeps_legacy_types(self):
        """Buttons in already-sent messages must keep working after the rename."""
        source = _read(os.path.join("handlers", "__init__.py"))
        m = re.search(
            r'CallbackQueryHandler\(\s*admin_div_settopic_prompt\s*,\s*pattern\s*=\s*("[^"]+")',
            source,
        )
        pattern = re.compile(ast.literal_eval(m.group(1)))
        for legacy in ("drafts", "tables"):
            self.assertTrue(pattern.match(f"admin_div_settopic_7_{legacy}"))

    def test_topics_menu_builds_a_button_per_topic(self):
        """admin_div_topics_menu must iterate the constant, not a hardcoded trio."""
        body = _function_body(_read(os.path.join("handlers", "admin.py")), "admin_div_topics_menu")
        self.assertIn("database.PRIMARY_DIVISION_TOPICS", body)

    def test_division_card_lists_every_topic(self):
        body = _function_body(_read(os.path.join("handlers", "admin.py")), "admin_div_view")
        self.assertIn("database.PRIMARY_DIVISION_TOPICS", body)

    def test_panel_does_not_offer_nonexistent_topics(self):
        """No division forum has a ТАБЛИЦЫ or ПРЕДУПРЕЖДЕНИЯ topic."""
        for absent in ("tables", "warns"):
            self.assertNotIn(absent, database.PRIMARY_DIVISION_TOPICS)

    def test_bindings_round_trip_for_every_topic(self):
        database.init_db()
        div_id = database.create_division(name="Топик-Покрытие", code="TEST_TOPIC_COVER")
        try:
            for offset, topic_type in enumerate(database.PRIMARY_DIVISION_TOPICS, start=1):
                database.set_division_topic(div_id, topic_type, 4400 + offset)

            topics_map = database.get_division_topics_map(div_id)
            for offset, topic_type in enumerate(database.PRIMARY_DIVISION_TOPICS, start=1):
                self.assertEqual(
                    topics_map.get(topic_type, {}).get("message_thread_id"), 4400 + offset,
                    f"Топик «{topic_type}» не читается обратно из division_topics"
                )
        finally:
            with database.transaction() as conn:
                conn.cursor().execute("DELETE FROM division_topics WHERE division_id = ?", (div_id,))
                conn.cursor().execute("DELETE FROM divisions WHERE id = ?", (div_id,))


if __name__ == "__main__":
    unittest.main()
