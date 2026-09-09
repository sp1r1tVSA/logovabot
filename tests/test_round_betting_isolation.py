"""
tests/test_round_betting_isolation.py

FIX-03 — строгая изоляция линии Logovo.bet по `season_id + division_id + round_number`.

Инвариант: операция над линией Тура 5 / Дивизиона 1 / Сезона A не должна касаться
ни Тура 5 / Дивизиона 2 / Сезона A, ни Тура 5 / Дивизиона 1 / Сезона B,
ни Тура 6 / Дивизиона 1 / Сезона A.

 1. Открытие тура для игры в Д1 гасит линию Д1 и не трогает линию Д2.
 2. Открытие тура для игры в Сезоне A гасит линию Сезона A и не трогает Сезон B.
 3. Закрытие линии одного scope не закрывает линии остальных scope.
 4. Повторное открытие линии одного scope не открывает линии остальных scope.
 5. Legacy `bet_markets` изолированы между дивизионами с одинаковым номером тура.
 6. Реляционные `markets` / `market_selections` изолированы так же.
 7. Betting gate изолирован по дивизиону.
 8. Betting gate изолирован по сезону.
 9. Отсутствие строки нужного scope — отказ; чужая строка того же тура не подставляется.
10. Одновременное открытие двух scope из разных потоков не смешивает их состояния.
"""

import os
import tempfile
import threading
import unittest

import database


DIV1 = 1
DIV2 = 2
R5 = 5
R6 = 6

# (season, division, round) -> match_id / market_id / selection_id
M_SA_D1_R5 = 970101
M_SA_D2_R5 = 970201
M_SB_D1_R5 = 970301
M_SA_D1_R6 = 970401

MK_SA_D1_R5 = 971101
MK_SA_D2_R5 = 971201
MK_SB_D1_R5 = 971301
MK_SA_D1_R6 = 971401

SEL_SA_D1_R5 = 972101
SEL_SA_D2_R5 = 972201
SEL_SB_D1_R5 = 972301
SEL_SA_D1_R6 = 972401

USER_ID = 970001


class TestRoundBettingIsolation(unittest.TestCase):
    """Каждый тест работает на отдельном файле БД — сезоны создаются свободно."""

    def setUp(self):
        self._orig_db_path = database.DB_PATH
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        database.DB_PATH = self._tmp.name
        database.init_db()
        database.ensure_canonical_divisions()

        with database.transaction() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO users (telegram_id, username, team_name, division_id, role) "
                "VALUES (?, 'iso_bettor', 'Iso FC', 1, 'user')",
                (USER_ID,)
            )
            c.execute("INSERT INTO seasons (name, status) VALUES ('Iso Season A', 'active')")
            self.season_a = c.lastrowid
            c.execute("INSERT INTO seasons (name, status) VALUES ('Iso Season B', 'active')")
            self.season_b = c.lastrowid

            # Четыре независимых scope с общим номером тура / дивизионом.
            self.scopes = {
                "SA_D1_R5": (self.season_a, DIV1, R5, M_SA_D1_R5, MK_SA_D1_R5, SEL_SA_D1_R5),
                "SA_D2_R5": (self.season_a, DIV2, R5, M_SA_D2_R5, MK_SA_D2_R5, SEL_SA_D2_R5),
                "SB_D1_R5": (self.season_b, DIV1, R5, M_SB_D1_R5, MK_SB_D1_R5, SEL_SB_D1_R5),
                "SA_D1_R6": (self.season_a, DIV1, R6, M_SA_D1_R6, MK_SA_D1_R6, SEL_SA_D1_R6),
            }
            for name, (s_id, d_id, r_num, m_id, mk_id, sel_id) in self.scopes.items():
                c.execute(
                    "INSERT INTO rounds (round_number, division_id, season_id, is_open, bets_open, deadline) "
                    "VALUES (?, ?, ?, 0, 0, NULL)",
                    (r_num, d_id, s_id)
                )
                c.execute(
                    "INSERT INTO matches (id, round_number, division_id, season_id, player1_team, player2_team, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'scheduled')",
                    (m_id, r_num, d_id, s_id, f"{name} Home", f"{name} Away")
                )
                c.execute(
                    "INSERT INTO bet_markets (match_id, tour, team1_name, team2_name, odd_p1, odd_x, odd_p2, is_active) "
                    "VALUES (?, ?, ?, ?, 2.0, 3.0, 3.5, 1)",
                    (m_id, r_num, f"{name} Home", f"{name} Away")
                )
                c.execute(
                    "INSERT INTO markets (id, match_id, market_key, market_name, status) "
                    "VALUES (?, ?, 'match_result', 'Match Winner', 'open')",
                    (mk_id, m_id)
                )
                c.execute(
                    "INSERT INTO market_selections (id, market_id, selection_key, selection_name, odds_value, status, odds_version) "
                    "VALUES (?, ?, 'p1', 'Home', 2.00, 'active', 1)",
                    (sel_id, mk_id)
                )

        database.get_or_create_wallet(USER_ID)
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE user_wallets SET balance = 50000 WHERE user_id = ?", (USER_ID,))

    def tearDown(self):
        database.close_thread_connection()
        database.DB_PATH = self._orig_db_path
        try:
            os.remove(self._tmp.name)
        except OSError:
            pass

    # --- helpers ---------------------------------------------------------

    def _open_line(self, scope_name):
        s_id, d_id, r_num, *_ = self.scopes[scope_name]
        ok = database.set_round_bets_open(r_num, True, division_id=d_id, season_id=s_id)
        self.assertTrue(ok, f"Не удалось открыть линию scope {scope_name}")

    def _open_all_lines(self):
        for name in self.scopes:
            self._open_line(name)

    def _bets_open(self, scope_name):
        s_id, d_id, r_num, *_ = self.scopes[scope_name]
        info = database.get_round_info(r_num, division_id=d_id, season_id=s_id)
        self.assertIsNotNone(info, f"Строка тура scope {scope_name} должна существовать")
        return info["bets_open"]

    def _is_open(self, scope_name):
        s_id, d_id, r_num, *_ = self.scopes[scope_name]
        return database.get_round_info(r_num, division_id=d_id, season_id=s_id)["is_open"]

    def _legacy_active(self, scope_name):
        m_id = self.scopes[scope_name][3]
        with database.transaction() as conn:
            row = conn.cursor().execute(
                "SELECT is_active FROM bet_markets WHERE match_id = ?", (m_id,)
            ).fetchone()
        return row["is_active"]

    def _market_status(self, scope_name):
        mk_id = self.scopes[scope_name][4]
        with database.transaction() as conn:
            row = conn.cursor().execute("SELECT status FROM markets WHERE id = ?", (mk_id,)).fetchone()
        return row["status"]

    def _selection_status(self, scope_name):
        sel_id = self.scopes[scope_name][5]
        with database.transaction() as conn:
            row = conn.cursor().execute(
                "SELECT status FROM market_selections WHERE id = ?", (sel_id,)
            ).fetchone()
        return row["status"]

    def _gate(self, scope_name):
        s_id, d_id, r_num, *_ = self.scopes[scope_name]
        with database.transaction() as conn:
            return database.evaluate_round_betting_gate(conn.cursor(), r_num, d_id, s_id)

    # --- 1. Открытие тура для игры: изоляция по дивизиону ------------------
    def test_01_opening_round_for_play_closes_only_its_division_line(self):
        self._open_all_lines()
        self.assertEqual(self._bets_open("SA_D1_R5"), 1)
        self.assertEqual(self._bets_open("SA_D2_R5"), 1)

        # Тур 5 Дивизиона 1 открыт для игры → его линия закрывается.
        database.update_round_status(R5, is_open=True, division_id=DIV1, season_id=self.season_a)

        self.assertEqual(self._is_open("SA_D1_R5"), 1)
        self.assertEqual(self._bets_open("SA_D1_R5"), 0, "Линия своего дивизиона должна закрыться")

        self.assertEqual(self._is_open("SA_D2_R5"), 0, "Тур соседнего дивизиона не открывался")
        self.assertEqual(self._bets_open("SA_D2_R5"), 1, "Линия Дивизиона 2 должна остаться открытой")
        self.assertEqual(self._legacy_active("SA_D2_R5"), 1)
        self.assertEqual(self._market_status("SA_D2_R5"), "open")

    # --- 2. Открытие тура для игры: изоляция по сезону ---------------------
    def test_02_opening_round_for_play_closes_only_its_season_line(self):
        self._open_all_lines()
        self.assertEqual(self._bets_open("SA_D1_R5"), 1)
        self.assertEqual(self._bets_open("SB_D1_R5"), 1)

        database.update_round_status(R5, is_open=True, division_id=DIV1, season_id=self.season_a)

        self.assertEqual(self._bets_open("SA_D1_R5"), 0)
        self.assertEqual(self._is_open("SB_D1_R5"), 0, "Тур другого сезона не открывался")
        self.assertEqual(self._bets_open("SB_D1_R5"), 1, "Линия другого сезона должна остаться открытой")
        self.assertEqual(self._legacy_active("SB_D1_R5"), 1)
        self.assertEqual(self._market_status("SB_D1_R5"), "open")

    # --- 3. Закрытие линии одного scope -----------------------------------
    def test_03_closing_line_touches_only_its_own_scope(self):
        self._open_all_lines()

        database.set_round_bets_open(R5, False, division_id=DIV1, season_id=self.season_a)

        self.assertEqual(self._bets_open("SA_D1_R5"), 0)
        self.assertEqual(self._legacy_active("SA_D1_R5"), 0)
        self.assertEqual(self._market_status("SA_D1_R5"), "closed")
        self.assertEqual(self._selection_status("SA_D1_R5"), "locked")

        for other in ("SA_D2_R5", "SB_D1_R5", "SA_D1_R6"):
            self.assertEqual(self._bets_open(other), 1, f"{other}: линия не должна закрываться")
            self.assertEqual(self._legacy_active(other), 1, f"{other}: legacy-рынок не должен гаситься")
            self.assertEqual(self._market_status(other), "open", f"{other}: рынок не должен закрываться")
            self.assertEqual(self._selection_status(other), "active", f"{other}: исход не должен блокироваться")

    # --- 4. Повторное открытие линии одного scope --------------------------
    def test_04_reopening_line_touches_only_its_own_scope(self):
        self._open_all_lines()
        for name in self.scopes:
            s_id, d_id, r_num, *_ = self.scopes[name]
            database.set_round_bets_open(r_num, False, division_id=d_id, season_id=s_id)
        for name in self.scopes:
            self.assertEqual(self._bets_open(name), 0, f"Предусловие: линия {name} закрыта")
            self.assertEqual(self._market_status(name), "closed")

        self._open_line("SA_D1_R5")

        self.assertEqual(self._bets_open("SA_D1_R5"), 1)
        self.assertEqual(self._market_status("SA_D1_R5"), "open")
        self.assertEqual(self._selection_status("SA_D1_R5"), "active")

        for other in ("SA_D2_R5", "SB_D1_R5", "SA_D1_R6"):
            self.assertEqual(self._bets_open(other), 0, f"{other}: линия не должна открываться")
            self.assertEqual(self._market_status(other), "closed", f"{other}: рынок не должен открываться")
            self.assertEqual(self._selection_status(other), "locked", f"{other}: исход не должен разблокироваться")
            self.assertEqual(self._legacy_active(other), 0, f"{other}: legacy-рынок не должен активироваться")

    # --- 5. Legacy bet_markets: два дивизиона с одним номером тура ----------
    def test_05_legacy_markets_isolated_between_divisions_of_same_round(self):
        self._open_all_lines()
        self.assertEqual(self._legacy_active("SA_D1_R5"), 1)
        self.assertEqual(self._legacy_active("SA_D2_R5"), 1)

        database.set_round_bets_open(R5, False, division_id=DIV1, season_id=self.season_a)

        self.assertEqual(self._legacy_active("SA_D1_R5"), 0, "Legacy-рынок своего дивизиона должен погаснуть")
        self.assertEqual(
            self._legacy_active("SA_D2_R5"), 1,
            "Legacy-рынок Дивизиона 2 с тем же номером тура гаситься не должен"
        )
        self.assertEqual(self._legacy_active("SB_D1_R5"), 1, "Legacy-рынок другого сезона гаситься не должен")

        # Он остаётся видимым в линии своего дивизиона.
        d2_markets = {
            m["match_id"] for m in database.get_active_bet_markets(
                tour=R5, division_id=DIV2, season_id=self.season_a
            )
        }
        self.assertIn(M_SA_D2_R5, d2_markets)
        self.assertNotIn(M_SA_D1_R5, d2_markets)

        # Список рынков одного сезона не показывает матчи другого сезона,
        # даже когда номер тура и дивизион совпадают.
        s_a_d1_markets = {
            m["match_id"] for m in database.get_active_bet_markets(
                tour=R5, division_id=DIV1, season_id=self.season_a
            )
        }
        self.assertNotIn(M_SB_D1_R5, s_a_d1_markets, "Матч другого сезона не должен попадать в линию")

        # Точечная выборка рынка тоже смотрит на строку СВОЕГО тура:
        # открытая линия Сезона B не должна делать матч Сезона A ставимым.
        self.assertIsNone(
            database.get_bet_market_by_match_id(M_SA_D1_R5),
            "Матч с закрытой линией своего scope не должен становиться доступным из-за чужого открытого тура"
        )
        self.assertIsNotNone(
            database.get_bet_market_by_match_id(M_SB_D1_R5),
            "Матч со своей открытой линией должен оставаться доступным"
        )

    # --- 6. Реляционные market_selections ----------------------------------
    def test_06_market_selections_isolated_between_scopes(self):
        self._open_all_lines()

        database.set_round_bets_open(R5, False, division_id=DIV1, season_id=self.season_a)

        self.assertEqual(self._selection_status("SA_D1_R5"), "locked")
        self.assertEqual(self._market_status("SA_D1_R5"), "closed")

        self.assertEqual(self._selection_status("SA_D2_R5"), "active", "Исход Дивизиона 2 должен остаться активным")
        self.assertEqual(self._market_status("SA_D2_R5"), "open")
        self.assertEqual(self._selection_status("SB_D1_R5"), "active", "Исход другого сезона должен остаться активным")
        self.assertEqual(self._market_status("SB_D1_R5"), "open")
        self.assertEqual(self._selection_status("SA_D1_R6"), "active", "Исход соседнего тура должен остаться активным")

    # --- 7. Betting gate: изоляция по дивизиону ----------------------------
    def test_07_betting_gate_isolated_between_divisions(self):
        self._open_all_lines()
        database.set_round_bets_open(R5, False, division_id=DIV1, season_id=self.season_a)

        allowed_d1, reason_d1, _ = self._gate("SA_D1_R5")
        self.assertFalse(allowed_d1, "Дивизион 1: линия закрыта → отказ")
        self.assertEqual(reason_d1, "LINE_CLOSED")

        allowed_d2, reason_d2, _ = self._gate("SA_D2_R5")
        self.assertTrue(allowed_d2, f"Дивизион 2: линия открыта → приём прогнозов (reason={reason_d2})")

        # То же самое на реальном пути размещения ставки.
        ok_d1, res_d1 = database.place_user_bet(
            user_id=USER_ID, amount=100,
            selections=[{"match_id": M_SA_D1_R5, "market_id": MK_SA_D1_R5,
                         "selection_id": SEL_SA_D1_R5, "outcome": "p1"}],
            idempotency_key="iso-gate-d1"
        )
        self.assertFalse(ok_d1, f"Ставка в Дивизионе 1 должна быть отклонена: {res_d1}")

        ok_d2, res_d2 = database.place_user_bet(
            user_id=USER_ID, amount=100,
            selections=[{"match_id": M_SA_D2_R5, "market_id": MK_SA_D2_R5,
                         "selection_id": SEL_SA_D2_R5, "outcome": "p1"}],
            idempotency_key="iso-gate-d2"
        )
        self.assertTrue(ok_d2, f"Ставка в Дивизионе 2 должна пройти: {res_d2}")

    # --- 8. Betting gate: изоляция по сезону -------------------------------
    def test_08_betting_gate_isolated_between_seasons(self):
        self._open_all_lines()
        database.set_round_bets_open(R5, False, division_id=DIV1, season_id=self.season_a)

        allowed_a, reason_a, _ = self._gate("SA_D1_R5")
        self.assertFalse(allowed_a, "Сезон A: линия закрыта → отказ")
        self.assertEqual(reason_a, "LINE_CLOSED")

        allowed_b, reason_b, _ = self._gate("SB_D1_R5")
        self.assertTrue(allowed_b, f"Сезон B: линия открыта → приём прогнозов (reason={reason_b})")

        ok_a, res_a = database.place_user_bet(
            user_id=USER_ID, amount=100,
            selections=[{"match_id": M_SB_D1_R5, "market_id": MK_SB_D1_R5,
                         "selection_id": SEL_SB_D1_R5, "outcome": "p1"}],
            idempotency_key="iso-gate-sb"
        )
        self.assertTrue(ok_a, f"Ставка в Сезоне B должна пройти: {res_a}")

        # Незаданный сезон не даёт «взять любую подходящую строку»: гейт обязан
        # привязаться к активному сезону, а не к открытому туру чужого сезона.
        # Активный сезон здесь — Season B; его линию закрываем, а линию
        # неактивного Season A (её строка тура создана раньше и имеет меньший id)
        # открываем. Разрешающий fallback подставил бы именно её.
        self.assertEqual(
            int(database.get_active_season()), self.season_b,
            "Предусловие: активный сезон — Season B"
        )
        database.set_round_bets_open(R5, False, division_id=DIV1, season_id=self.season_b)
        self._open_line("SA_D1_R5")
        self.assertEqual(self._bets_open("SA_D1_R5"), 1, "Предусловие: линия неактивного Season A открыта")
        self.assertEqual(self._bets_open("SB_D1_R5"), 0, "Предусловие: линия активного Season B закрыта")

        with database.transaction() as conn:
            allowed_none, reason_none, _ = database.evaluate_round_betting_gate(
                conn.cursor(), R5, DIV1, None
            )
        self.assertFalse(
            allowed_none,
            "Без season_id гейт обязан взять активный сезон, а не открытый тур чужого сезона"
        )
        self.assertEqual(reason_none, "LINE_CLOSED")

    # --- 9. Отсутствие строки нужного scope --------------------------------
    def test_09_missing_scoped_round_rejects_and_does_not_borrow_other_scope(self):
        """S_A/D2/R5 открыт, строки S_A/D1/R5 нет → ставка в Д1 отклоняется."""
        self._open_line("SA_D2_R5")
        with database.transaction() as conn:
            conn.cursor().execute(
                "DELETE FROM rounds WHERE round_number = ? AND division_id = ? AND season_id = ?",
                (R5, DIV1, self.season_a)
            )

        self.assertEqual(self._bets_open("SA_D2_R5"), 1, "Предусловие: чужая линия открыта")

        with database.transaction() as conn:
            allowed, reason, _ = database.evaluate_round_betting_gate(
                conn.cursor(), R5, DIV1, self.season_a
            )
        self.assertFalse(allowed, "Без своей строки тура ставка должна быть отклонена")
        self.assertEqual(reason, "ROUND_NOT_FOUND", "Строка Дивизиона 2 не должна подставляться вместо своей")

        ok, res = database.place_user_bet(
            user_id=USER_ID, amount=100,
            selections=[{"match_id": M_SA_D1_R5, "market_id": MK_SA_D1_R5,
                         "selection_id": SEL_SA_D1_R5, "outcome": "p1"}],
            idempotency_key="iso-missing-round"
        )
        self.assertFalse(ok, f"place_user_bet обязан отклонить ставку без строки своего тура: {res}")

        # Чужой scope при этом не изменился.
        self.assertEqual(self._bets_open("SA_D2_R5"), 1)

    # --- 10. Одновременное открытие двух scope -----------------------------
    def test_10_concurrent_line_opening_keeps_scopes_separate(self):
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def open_scope(scope_name):
            s_id, d_id, r_num, *_ = self.scopes[scope_name]
            try:
                barrier.wait(timeout=10)
                database.set_round_bets_open(r_num, True, division_id=d_id, season_id=s_id)
            except BaseException as exc:  # noqa: BLE001 — переносим в основной поток
                errors.append(exc)
            finally:
                database.close_thread_connection()

        threads = [
            threading.Thread(target=open_scope, args=("SA_D1_R5",)),
            threading.Thread(target=open_scope, args=("SA_D2_R5",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertFalse(errors, f"Параллельное открытие линий не должно падать: {errors}")

        # Оба scope открылись, каждый — только свой.
        self.assertEqual(self._bets_open("SA_D1_R5"), 1)
        self.assertEqual(self._bets_open("SA_D2_R5"), 1)
        self.assertEqual(self._bets_open("SB_D1_R5"), 0, "Сезон B не должен открыться попутно")
        self.assertEqual(self._bets_open("SA_D1_R6"), 0, "Тур 6 не должен открыться попутно")

        # Теперь закрываем один — второй остаётся нетронутым.
        database.set_round_bets_open(R5, False, division_id=DIV1, season_id=self.season_a)
        self.assertEqual(self._bets_open("SA_D1_R5"), 0)
        self.assertEqual(self._bets_open("SA_D2_R5"), 1)
        self.assertEqual(self._market_status("SA_D2_R5"), "open")


if __name__ == "__main__":
    unittest.main()
