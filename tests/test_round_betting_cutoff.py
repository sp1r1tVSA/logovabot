"""
tests/test_round_betting_cutoff.py

Регрессионные тесты единого правила приёма ставок на тур (PROBLEM 1).

Бизнес-правило:

    LINE OPEN -> USERS CAN BET -> ROUND OPENS -> LINE CLOSES -> MATCHES PLAYED

    До открытия тура:  rounds.is_open = 0, rounds.bets_open = 1  -> ставка РАЗРЕШЕНА
    После открытия:    rounds.is_open = 1, rounds.bets_open = 0  -> ставка ЗАПРЕЩЕНА

 1. is_open = 0, bets_open = 1 -> ставка принимается.
 2. is_open = 0, bets_open = 0 -> ставка отклоняется.
 3. is_open = 1, bets_open = 0 -> ставка отклоняется.
 4. is_open = 1, bets_open = 1 (состояние подделано в БД) -> ставка отклоняется.
 5. Админ открывает тур -> bets_open = 0 и ставка после этого отклоняется.
 6. markets.status = 'closed' -> ставка отклоняется.
 7. market_selections.status = 'locked' -> ставка отклоняется.
 8. Telegram-путь (database.place_user_bet напрямую) после закрытия линии -> отказ.
 9. Mini App / REST-путь (POST /api/predictions) после закрытия линии -> отказ.
10. Устаревший купон (открыт до закрытия линии, отправлен после) -> отказ.
11. Гонка «ставка против открытия тура» -> ставка после отсечки невозможна.
"""

import asyncio
import threading
import unittest
from unittest.mock import patch

import database
from api import routes_predictions
from services import odds_engine


DIV = 1
SEASON = 1

R_ALLOW = 291        # is_open=0, bets_open=1
R_NO_LINE = 292      # is_open=0, bets_open=0
R_PLAYING = 293      # is_open=1, bets_open=0
R_FORBIDDEN = 294    # is_open=1, bets_open=1 (недостижимое состояние, подделываем)
R_ADMIN = 295        # открывается админом в тесте 5
R_RACE = 296         # гонка

M_ALLOW = 99801
M_NO_LINE = 99802
M_PLAYING = 99803
M_FORBIDDEN = 99804
M_ADMIN = 99805
M_RACE = 99806

USER_ID = 998401

ROUNDS = (R_ALLOW, R_NO_LINE, R_PLAYING, R_FORBIDDEN, R_ADMIN, R_RACE)


class _StubRequest:
    """Минимальный аналог aiohttp.web.Request для прямого вызова хендлера."""

    def __init__(self, payload):
        self.headers = {"X-Telegram-Init-Data": "stub"}
        self._payload = payload

    async def json(self):
        return self._payload


class TestRoundBettingCutoff(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self._cleanup()

        with database.transaction() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO users (telegram_id, username, team_name, division_id, role) "
                "VALUES (?, ?, ?, ?, ?)",
                (USER_ID, "cutoff_user", "Cutoff FC", DIV, "player")
            )
            for rn in ROUNDS:
                c.execute(
                    "INSERT INTO rounds (round_number, is_open, bets_open, deadline, division_id, season_id) "
                    "VALUES (?, 0, 0, NULL, ?, ?)",
                    (rn, DIV, SEASON)
                )
            for m_id, rn, t1, t2 in (
                (M_ALLOW, R_ALLOW, "Cutoff FC", "Sparta CO"),
                (M_NO_LINE, R_NO_LINE, "Dynamo CO", "Torpedo CO"),
                (M_PLAYING, R_PLAYING, "Zenit CO", "Rubin CO"),
                (M_FORBIDDEN, R_FORBIDDEN, "Ural CO", "Amkar CO"),
                (M_ADMIN, R_ADMIN, "Lokomotiv CO", "Krylia CO"),
                (M_RACE, R_RACE, "Rostov CO", "Khimki CO"),
            ):
                c.execute(
                    "INSERT INTO matches (id, round_number, division_id, season_id, player1_id, player2_id, "
                    "player1_team, player2_team, status) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, 'scheduled')",
                    (m_id, rn, DIV, SEASON, t1, t2)
                )

        # Реляционная линия (Mini App) создаётся отдельно от legacy `bet_markets`
        # — так же, как в проде это делает GET /api/markets.
        for m_id, t1, t2 in (
            (M_ALLOW, "Cutoff FC", "Sparta CO"),
            (M_NO_LINE, "Dynamo CO", "Torpedo CO"),
            (M_PLAYING, "Zenit CO", "Rubin CO"),
            (M_FORBIDDEN, "Ural CO", "Amkar CO"),
            (M_ADMIN, "Lokomotiv CO", "Krylia CO"),
            (M_RACE, "Rostov CO", "Khimki CO"),
        ):
            odds_engine.generate_match_markets(m_id, t1, t2)

        database.get_or_create_wallet(USER_ID)
        database.add_coins(USER_ID, 100000, "deposit")

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM bet_items WHERE bet_id IN (SELECT id FROM user_bets WHERE user_id = ?)", (USER_ID,))
            c.execute("DELETE FROM user_bets WHERE user_id = ?", (USER_ID,))
            c.execute("DELETE FROM coin_transactions WHERE user_id = ?", (USER_ID,))
            c.execute("DELETE FROM user_wallets WHERE user_id = ?", (USER_ID,))
            c.execute(
                "DELETE FROM market_selections WHERE market_id IN "
                "(SELECT id FROM markets WHERE match_id >= 99800 AND match_id < 99900)"
            )
            c.execute("DELETE FROM markets WHERE match_id >= 99800 AND match_id < 99900")
            c.execute("DELETE FROM bet_markets WHERE match_id >= 99800 AND match_id < 99900")
            c.execute("DELETE FROM matches WHERE id >= 99800 AND id < 99900")
            c.execute("DELETE FROM rounds WHERE round_number BETWEEN 291 AND 297")
            c.execute("DELETE FROM users WHERE telegram_id = ?", (USER_ID,))

    # --- helpers ---------------------------------------------------------
    def _open_line(self, round_number):
        opened = database.set_round_bets_open(round_number, True, division_id=DIV, season_id=SEASON)
        self.assertTrue(opened, f"Линия на тур {round_number} должна открыться")

    def _force_round_state(self, round_number, is_open, bets_open):
        with database.transaction() as conn:
            conn.execute(
                "UPDATE rounds SET is_open = ?, bets_open = ? WHERE round_number = ? AND division_id = ?",
                (is_open, bets_open, round_number, DIV)
            )

    def _bet(self, match_id, amount=100, idempotency_key=None):
        return database.place_user_bet(
            user_id=USER_ID,
            amount=amount,
            selections=[{"match_id": match_id, "outcome": "p1"}],
            idempotency_key=idempotency_key
        )

    def _bets_count(self):
        with database.transaction() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM user_bets WHERE user_id = ?", (USER_ID,)).fetchone()
        return row["c"]

    # --- 1-4. Четыре комбинации is_open / bets_open ----------------------
    def test_01_closed_round_open_line_accepts_bet(self):
        """is_open = 0, bets_open = 1 — единственное состояние, в котором ставка принимается."""
        self._open_line(R_ALLOW)

        info = database.get_round_info(R_ALLOW, division_id=DIV, season_id=SEASON)
        self.assertEqual(info["is_open"], 0)
        self.assertEqual(info["bets_open"], 1)

        ok, result = self._bet(M_ALLOW)
        self.assertTrue(ok, f"Ставка на открытую линию должна приниматься: {result}")
        self.assertEqual(self._bets_count(), 1)

    def test_02_closed_round_closed_line_rejects_bet(self):
        """is_open = 0, bets_open = 0 — линии нет, ставка отклоняется."""
        ok, result = self._bet(M_NO_LINE)
        self.assertFalse(ok, "Ставка без открытой линии должна отклоняться")
        self.assertEqual(self._bets_count(), 0)

    def test_03_open_round_closed_line_rejects_bet(self):
        """is_open = 1, bets_open = 0 — тур уже играется, ставка отклоняется."""
        self._open_line(R_PLAYING)
        database.update_round_status(R_PLAYING, is_open=True, division_id=DIV, season_id=SEASON)

        info = database.get_round_info(R_PLAYING, division_id=DIV, season_id=SEASON)
        self.assertEqual(info["is_open"], 1)
        self.assertEqual(info["bets_open"], 0, "Открытие тура должно закрывать линию")

        ok, result = self._bet(M_PLAYING)
        self.assertFalse(ok, "Ставка на уже открытый тур должна отклоняться")
        self.assertEqual(self._bets_count(), 0)

    def test_04_forbidden_state_still_rejects_bet(self):
        """is_open = 1, bets_open = 1 недостижимо штатно; даже подделанное — отказ."""
        self._open_line(R_FORBIDDEN)
        self._force_round_state(R_FORBIDDEN, is_open=1, bets_open=1)

        ok, result = self._bet(M_FORBIDDEN)
        self.assertFalse(ok, "is_open = 1 запрещает ставку независимо от bets_open")
        self.assertEqual(self._bets_count(), 0)

    def test_04b_forbidden_state_unreachable_through_public_api(self):
        """Ни один штатный путь не создаёт is_open = 1 AND bets_open = 1."""
        self._open_line(R_FORBIDDEN)
        database.update_round_status(R_FORBIDDEN, is_open=True, division_id=DIV, season_id=SEASON)
        info = database.get_round_info(R_FORBIDDEN, division_id=DIV, season_id=SEASON)
        self.assertEqual((info["is_open"], info["bets_open"]), (1, 0))

        # Попытка вернуть открытый тур в линию должна быть отклонена.
        self.assertFalse(
            database.set_round_bets_open(R_FORBIDDEN, True, division_id=DIV, season_id=SEASON),
            "Тур, открытый для игры, нельзя вернуть в линию"
        )
        info = database.get_round_info(R_FORBIDDEN, division_id=DIV, season_id=SEASON)
        self.assertEqual((info["is_open"], info["bets_open"]), (1, 0))

    # --- 5. Админский сценарий открытия тура -----------------------------
    def test_05_admin_opening_round_closes_line_everywhere(self):
        """Открытие тура админом закрывает линию в обеих схемах."""
        self._open_line(R_ADMIN)

        ok, _ = self._bet(M_ADMIN)
        self.assertTrue(ok, "До открытия тура ставка должна проходить")

        database.update_round_status(R_ADMIN, is_open=True, division_id=DIV, season_id=SEASON)

        with database.transaction() as conn:
            mkt = conn.execute("SELECT status FROM markets WHERE match_id = ?", (M_ADMIN,)).fetchall()
            sel = conn.execute(
                "SELECT ms.status FROM market_selections ms JOIN markets m ON ms.market_id = m.id "
                "WHERE m.match_id = ?", (M_ADMIN,)
            ).fetchall()
            legacy = conn.execute("SELECT is_active FROM bet_markets WHERE match_id = ?", (M_ADMIN,)).fetchall()

        self.assertTrue(mkt, "Рынки на матч должны существовать")
        for row in mkt:
            self.assertEqual(row["status"], "closed", "markets.status должен стать 'closed'")
        for row in sel:
            self.assertEqual(row["status"], "locked", "market_selections.status должен стать 'locked'")
        for row in legacy:
            self.assertEqual(row["is_active"], 0, "legacy bet_markets должны деактивироваться")

        ok, _ = self._bet(M_ADMIN)
        self.assertFalse(ok, "После открытия тура ставка недопустима")
        self.assertEqual(self._bets_count(), 1, "Принята должна быть только досрочная ставка")

    # --- 6-7. Состояние рынка и исхода -----------------------------------
    def test_06_closed_market_rejects_bet(self):
        """markets.status = 'closed' блокирует ставку даже при открытой линии."""
        self._open_line(R_ALLOW)
        with database.transaction() as conn:
            conn.execute("UPDATE markets SET status = 'closed' WHERE match_id = ?", (M_ALLOW,))
            conn.execute("UPDATE bet_markets SET is_active = 0 WHERE match_id = ?", (M_ALLOW,))

        ok, _ = self._bet(M_ALLOW)
        self.assertFalse(ok, "Закрытый рынок не должен принимать ставку")
        self.assertEqual(self._bets_count(), 0)

    def test_07_locked_selection_rejects_bet(self):
        """market_selections.status = 'locked' блокирует ставку на этот исход."""
        self._open_line(R_ALLOW)
        with database.transaction() as conn:
            conn.execute(
                "UPDATE market_selections SET status = 'locked' WHERE market_id IN "
                "(SELECT id FROM markets WHERE match_id = ?)", (M_ALLOW,)
            )
            conn.execute("UPDATE bet_markets SET is_active = 0 WHERE match_id = ?", (M_ALLOW,))

        ok, _ = self._bet(M_ALLOW)
        self.assertFalse(ok, "Заблокированный исход не должен принимать ставку")
        self.assertEqual(self._bets_count(), 0)

    # --- 8. Telegram-путь -------------------------------------------------
    def test_08_telegram_path_blocked_after_cutoff(self):
        """Telegram вызывает database.place_user_bet напрямую — тот же инвариант."""
        self._open_line(R_ALLOW)
        database.set_round_bets_open(R_ALLOW, False, division_id=DIV, season_id=SEASON)

        ok, _ = self._bet(M_ALLOW)
        self.assertFalse(ok, "Telegram-путь должен отклонять ставку после закрытия линии")
        self.assertEqual(self._bets_count(), 0)

    # --- 9. Mini App / REST-путь ------------------------------------------
    def test_09_api_path_blocked_after_cutoff(self):
        """POST /api/predictions защищён тем же серверным правилом."""
        self._open_line(R_ALLOW)
        database.set_round_bets_open(R_ALLOW, False, division_id=DIV, season_id=SEASON)

        request = _StubRequest({
            "amount": 100,
            "selections": [{"match_id": M_ALLOW, "outcome": "p1"}]
        })

        with patch.object(routes_predictions, "get_authenticated_user", return_value={"id": USER_ID}), \
             patch.object(routes_predictions, "check_user_access", return_value=True):
            response = asyncio.run(routes_predictions.handle_place_prediction(request))

        self.assertEqual(response.status, 400, "API должен вернуть ошибку, а не принять ставку")
        self.assertEqual(self._bets_count(), 0)

    def test_09b_api_path_accepts_bet_while_line_open(self):
        """Тот же API принимает ставку, пока линия открыта — правило не ломает штатный путь."""
        self._open_line(R_ALLOW)

        request = _StubRequest({
            "amount": 100,
            "selections": [{"match_id": M_ALLOW, "outcome": "p1"}]
        })

        with patch.object(routes_predictions, "get_authenticated_user", return_value={"id": USER_ID}), \
             patch.object(routes_predictions, "check_user_access", return_value=True):
            response = asyncio.run(routes_predictions.handle_place_prediction(request))

        self.assertEqual(response.status, 200)
        self.assertEqual(self._bets_count(), 1)

    # --- 10. Устаревший купон --------------------------------------------
    def test_10_stale_coupon_rejected(self):
        """Купон, собранный до отсечки и отправленный после, не проходит."""
        self._open_line(R_ALLOW)

        # Пользователь «собрал» купон с актуальными на тот момент коэффициентами.
        with database.transaction() as conn:
            row = conn.execute(
                "SELECT ms.id AS sel_id, ms.market_id, ms.odds_value FROM market_selections ms "
                "JOIN markets m ON ms.market_id = m.id WHERE m.match_id = ? AND ms.selection_key = 'p1'",
                (M_ALLOW,)
            ).fetchone()
        self.assertIsNotNone(row, "Линия должна содержать исход p1")
        stale_selection = {
            "match_id": M_ALLOW,
            "outcome": "p1",
            "market_id": row["market_id"],
            "selection_id": row["sel_id"],
            "odd": row["odds_value"],
        }

        # Тур открывается для игры — линия закрывается.
        database.update_round_status(R_ALLOW, is_open=True, division_id=DIV, season_id=SEASON)

        ok, _ = database.place_user_bet(user_id=USER_ID, amount=100, selections=[stale_selection])
        self.assertFalse(ok, "Устаревший купон не должен приниматься после отсечки")
        self.assertEqual(self._bets_count(), 0)

    # --- 11. Гонка ставки и открытия тура ---------------------------------
    def test_11_race_bet_versus_round_opening(self):
        """Параллельные ставки и открытие тура не дают принять ставку после отсечки."""
        self._open_line(R_RACE)

        n_bettors = 8
        barrier = threading.Barrier(n_bettors + 1)
        results = []
        results_lock = threading.Lock()

        def bettor(idx):
            barrier.wait()
            ok, res = database.place_user_bet(
                user_id=USER_ID,
                amount=50,
                selections=[{"match_id": M_RACE, "outcome": "p1"}],
                idempotency_key=f"race-{idx}"
            )
            with results_lock:
                results.append(ok)

        def opener():
            barrier.wait()
            database.update_round_status(R_RACE, is_open=True, division_id=DIV, season_id=SEASON)

        threads = [threading.Thread(target=bettor, args=(i,)) for i in range(n_bettors)]
        threads.append(threading.Thread(target=opener))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            self.assertFalse(t.is_alive(), "Поток не завершился — вероятная взаимоблокировка")

        info = database.get_round_info(R_RACE, division_id=DIV, season_id=SEASON)
        self.assertEqual(info["is_open"], 1)
        self.assertEqual(info["bets_open"], 0)

        accepted = sum(1 for ok in results if ok)
        self.assertEqual(
            self._bets_count(), accepted,
            "Число сохранённых ставок должно совпадать с числом успешных ответов"
        )

        # После завершения гонки ни одна новая ставка не проходит.
        ok, _ = self._bet(M_RACE, idempotency_key="race-after")
        self.assertFalse(ok, "После открытия тура ставка невозможна")

    def test_11b_round_opening_serialises_with_bet_placement(self):
        """Открытие тура не может вклиниться между проверкой и вставкой ставки."""
        self._open_line(R_RACE)

        opened = threading.Event()

        def opener():
            database.update_round_status(R_RACE, is_open=True, division_id=DIV, season_id=SEASON)
            opened.set()

        with database._bet_placement_lock:
            t = threading.Thread(target=opener)
            t.start()
            blocked = not opened.wait(timeout=0.5)
            self.assertTrue(blocked, "Открытие тура должно ждать освобождения замка приёма ставок")

        t.join(timeout=30)
        self.assertTrue(opened.is_set(), "Открытие тура должно завершиться после освобождения замка")


if __name__ == "__main__":
    unittest.main()
