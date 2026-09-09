"""
tests/test_betting_market_persistence.py

FIX-05 — FLAG-01: значение feature-флага `betting_market` должно переживать рестарт.

Инвариант: init_db() ИДЕМПОТЕНТЕН относительно betting_market.

    записи нет            -> создаётся DEFAULT ('public')
    запись есть           -> НЕ ТРОГАЕТСЯ, каким бы ни был её статус

Допустимые статусы в этом проекте — ровно три: 'disabled', 'admin_only', 'public'
(см. database.is_feature_accessible). Отдельного значения 'private' в проекте нет;
ограничительное состояние betting_market — это 'admin_only', именно оно и
проверяется как «настройка администратора».

Сценарии:
 1. Первый запуск на чистой БД -> создаётся default 'public'.
 2. Повторный init_db() ничего не меняет.
 3. Админское значение 'admin_only' переживает init_db().            <- главный тест
 4. Оно же переживает несколько подряд идущих init_db().
 5. Явно выставленный 'public' тоже не перезаписывается.
 6. Каждое реально существующее допустимое значение переживает init_db().
 7. Админский механизм (set_feature_flag) продолжает работать, и его результат
    сохраняется после init_db().
 8. Полный restart-сценарий: смена значения -> закрытие соединения -> init_db()
    -> новое соединение -> чтение.
 9. init_db() не сбрасывает другие feature-флаги.
10. Статическая проверка: в init_db() не осталось безусловной перезаписи
    betting_market (DO UPDATE / REPLACE / UPDATE / DELETE).
11. Все потребители флага (main.py, api/auth.py, handlers/betting.py читают его
    с разными default) после рестарта видят админское значение, а не default.
"""

import os
import re
import tempfile
import unittest

import database


VALID_STATUSES = ("disabled", "admin_only", "public")
DEFAULT_STATUS = "public"


def _fresh_db_path() -> str:
    """Пустой временный файл БД; database.DB_PATH переключается на него."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    database.close_thread_connection()
    database.DB_PATH = tmp.name
    return tmp.name


def _read_flag_raw() -> str | None:
    """Прочитать статус строго из таблицы, без подмешивания DEFAULT_FEATURE_FLAGS."""
    with database.transaction() as conn:
        row = conn.cursor().execute(
            "SELECT status FROM feature_flags WHERE feature_key = 'betting_market'"
        ).fetchone()
        return row[0] if row else None


def _row_count() -> int:
    with database.transaction() as conn:
        return conn.cursor().execute(
            "SELECT COUNT(*) FROM feature_flags WHERE feature_key = 'betting_market'"
        ).fetchone()[0]


def _simulate_restart() -> None:
    """
    Рестарт процесса: соединение закрывается, затем новый процесс снова
    вызывает init_db() и открывает новое соединение.
    """
    database.close_thread_connection()
    database.init_db()
    database.close_thread_connection()


class TestBettingMarketPersistence(unittest.TestCase):

    def setUp(self) -> None:
        self._orig_db_path = database.DB_PATH
        self._tmp_path = _fresh_db_path()

    def tearDown(self) -> None:
        database.close_thread_connection()
        database.DB_PATH = self._orig_db_path
        try:
            os.unlink(self._tmp_path)
        except OSError:
            pass

    # --- 1. Первый запуск -------------------------------------------------
    def test_01_default_created_on_first_initialization(self):
        """Чистая БД -> init_db() создаёт betting_market с текущим default."""
        database.init_db()

        self.assertEqual(_row_count(), 1, "Строка betting_market должна быть создана")
        self.assertEqual(_read_flag_raw(), DEFAULT_STATUS)
        self.assertEqual(database.get_feature_flag("betting_market"), DEFAULT_STATUS)

    # --- 2. Идемпотентность -----------------------------------------------
    def test_02_init_db_is_idempotent(self):
        """Повторный init_db() не меняет значение и не плодит строки."""
        database.init_db()
        first = _read_flag_raw()

        database.init_db()
        second = _read_flag_raw()

        self.assertEqual(first, second)
        self.assertEqual(_row_count(), 1)

    # --- 3. ГЛАВНЫЙ ТЕСТ FIX-05 -------------------------------------------
    def test_03_admin_value_survives_restart(self):
        """betting_market = admin_only переживает init_db(). Это ядро FIX-05."""
        database.init_db()
        database.set_feature_flag("betting_market", "admin_only")
        self.assertEqual(_read_flag_raw(), "admin_only")

        database.init_db()

        self.assertEqual(
            _read_flag_raw(), "admin_only",
            "init_db() затёр админскую настройку betting_market — регрессия FLAG-01"
        )
        self.assertEqual(database.get_feature_flag("betting_market"), "admin_only")

    # --- 4. Несколько рестартов подряд ------------------------------------
    def test_04_admin_value_survives_multiple_restarts(self):
        """Три подряд идущих init_db() не размывают админское значение."""
        database.init_db()
        database.set_feature_flag("betting_market", "admin_only")

        database.init_db()
        database.init_db()
        database.init_db()

        self.assertEqual(_read_flag_raw(), "admin_only")
        self.assertEqual(_row_count(), 1)

    # --- 5. public тоже не перезаписывается --------------------------------
    def test_05_existing_public_is_not_rewritten(self):
        """Существующее значение public остаётся public (запись не пересоздаётся)."""
        database.init_db()
        database.set_feature_flag("betting_market", "public")
        with database.transaction() as conn:
            before_updated_at = conn.cursor().execute(
                "SELECT updated_at FROM feature_flags WHERE feature_key = 'betting_market'"
            ).fetchone()[0]

        database.init_db()

        self.assertEqual(_read_flag_raw(), "public")
        with database.transaction() as conn:
            after_updated_at = conn.cursor().execute(
                "SELECT updated_at FROM feature_flags WHERE feature_key = 'betting_market'"
            ).fetchone()[0]
        self.assertEqual(
            before_updated_at, after_updated_at,
            "init_db() не должен вообще прикасаться к существующей строке"
        )

    # --- 6. Все реально существующие допустимые значения -------------------
    def test_06_every_valid_configured_value_survives(self):
        """Каждый из трёх реальных статусов переживает init_db()."""
        database.init_db()
        for status in VALID_STATUSES:
            with self.subTest(status=status):
                database.set_feature_flag("betting_market", status)
                database.init_db()
                self.assertEqual(_read_flag_raw(), status)
                self.assertEqual(database.get_feature_flag("betting_market"), status)

    # --- 7. Админский механизм не сломан -----------------------------------
    def test_07_admin_update_still_works(self):
        """public -> admin_only через штатный set_feature_flag, и это переживает init_db()."""
        database.init_db()
        self.assertEqual(database.get_feature_flag("betting_market"), "public")

        database.set_feature_flag("betting_market", "admin_only")
        self.assertEqual(database.get_feature_flag("betting_market"), "admin_only")

        database.init_db()
        self.assertEqual(database.get_feature_flag("betting_market"), "admin_only")

        # И обратно: админ может снова открыть рынок.
        database.set_feature_flag("betting_market", "public")
        database.init_db()
        self.assertEqual(database.get_feature_flag("betting_market"), "public")

    # --- 8. Полный restart-сценарий ----------------------------------------
    def test_08_full_restart_cycle_preserves_admin_value(self):
        """
        Создать БД -> init_db() -> изменить флаг -> закрыть соединение ->
        init_db() в «новом процессе» -> открыть новое соединение -> прочитать.
        """
        database.init_db()
        database.set_feature_flag("betting_market", "admin_only")

        _simulate_restart()
        self.assertEqual(_read_flag_raw(), "admin_only")

        _simulate_restart()
        self.assertEqual(_read_flag_raw(), "admin_only")
        self.assertEqual(database.get_feature_flag("betting_market"), "admin_only")

    # --- 9. Остальные флаги не сбрасываются --------------------------------
    def test_09_other_feature_flags_are_not_reset(self):
        """init_db() не трогает ни другие дефолтные флаги, ни кастомные."""
        database.init_db()
        database.set_feature_flag("fc_player_cards", "public")
        database.set_feature_flag("fantasy_league", "admin_only")
        database.set_feature_flag("custom_flag_fix05", "disabled")

        database.init_db()

        self.assertEqual(database.get_feature_flag("fc_player_cards"), "public")
        self.assertEqual(database.get_feature_flag("fantasy_league"), "admin_only")
        self.assertEqual(database.get_feature_flag("custom_flag_fix05"), "disabled")

    # --- 10. Статический контроль SQL --------------------------------------
    def test_10_init_db_contains_no_unconditional_write(self):
        """
        В init_db() не должно остаться операции, безусловно перезаписывающей
        betting_market. Допустим только seed-only INSERT ... DO NOTHING.
        """
        src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database.py")
        with open(src_path, "r", encoding="utf-8") as f:
            src = f.read()

        start = src.index("def init_db(")
        end = src.index("\ndef ", start + 1)
        init_src = src[start:end]

        # Ищем именно SQL-литерал, а не упоминания в комментариях.
        occurrences = [m.start() for m in re.finditer(r"'betting_market'", init_src)]
        self.assertEqual(
            len(occurrences), 1,
            "betting_market должен фигурировать в SQL внутри init_db() ровно один раз (seed)"
        )

        stmt_start = init_src.rindex('cursor.execute("""', 0, occurrences[0])
        stmt = init_src[stmt_start:init_src.index('""")', stmt_start)]

        self.assertIn("ON CONFLICT", stmt)
        self.assertIn("DO NOTHING", stmt)
        for forbidden in ("DO UPDATE", "REPLACE", "DELETE"):
            self.assertNotIn(
                forbidden, stmt.upper(),
                f"seed betting_market не должен содержать {forbidden}"
            )
        self.assertNotIn("UPDATE FEATURE_FLAGS", init_src.upper())
        self.assertNotIn("DELETE FROM FEATURE_FLAGS", init_src.upper())

    # --- 11. Потребители флага видят админское значение --------------------
    def test_11_flag_consumers_read_admin_value_after_restart(self):
        """
        main.py читает флаг с default='admin_only', api/auth.py — с default='public',
        handlers/betting.py — без default. После рестарта все три должны увидеть
        сохранённое админское значение, а не свой default.
        """
        database.init_db()
        database.set_feature_flag("betting_market", "admin_only")
        _simulate_restart()

        self.assertEqual(database.get_feature_flag("betting_market", default="admin_only"), "admin_only")
        self.assertEqual(database.get_feature_flag("betting_market", default="public"), "admin_only")
        self.assertEqual(database.get_feature_flag("betting_market"), "admin_only")
        self.assertEqual(database.get_all_feature_flags()["betting_market"], "admin_only")

        # И «disabled» тоже доживает до потребителей.
        database.set_feature_flag("betting_market", "disabled")
        _simulate_restart()
        self.assertEqual(database.get_feature_flag("betting_market", default="public"), "disabled")


if __name__ == "__main__":
    unittest.main()
