"""
tests/test_early_betting_line.py

Регрессионные тесты ранней линии Logovo.bet (`rounds.bets_open`):
приём прогнозов может быть открыт до того, как тур открыт для внесения результатов.

1. Схема: у `rounds` есть колонки bets_open / bets_opened_at.
2. set_round_bets_open() открывает линию на закрытый тур.
3. Закрытый тур с открытой линией попадает в get_open_betting_tours с флагом is_early.
4. Тур без линии и без is_open в линию не попадает.
5. Risk engine пропускает ставку на закрытый тур с открытой линией.
6. Risk engine отклоняет ставку после закрытия линии.
7. set_round_bets_open() возвращает False для тура без матчей.
8. update_round_status(is_open=True) закрывает линию тура: открытый для игры тур
   прогнозы не принимает; закрытие тура линию тоже не открывает.
9. Открытие тура N автоматически открывает раннюю линию на тур N+1.
"""

import unittest

import database
from services.risk_engine import RiskEngine


ROUND_EARLY = 191        # закрыт для игры, линия открыта заранее
ROUND_NO_LINE = 192      # закрыт для игры, линия закрыта
ROUND_NO_MATCHES = 193   # матчей нет вообще
ROUND_PLAY = 194         # открывается через update_round_status
ROUND_NEXT = 195         # должен получить раннюю линию автоматически

MATCH_EARLY = 99701
MATCH_NO_LINE = 99702
MATCH_PLAY = 99703
MATCH_NEXT = 99704

USER_ID = 998301


class TestEarlyBettingLine(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self._cleanup()

        with database.transaction() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO users (telegram_id, username, team_name, division_id, role) VALUES (?, ?, ?, ?, ?)",
                (USER_ID, "early_line_user", "Early Line FC", 1, "player")
            )
            for rn in (ROUND_EARLY, ROUND_NO_LINE, ROUND_PLAY, ROUND_NEXT):
                c.execute(
                    "INSERT INTO rounds (round_number, is_open, deadline, division_id, season_id) VALUES (?, 0, NULL, 1, 1)",
                    (rn,)
                )
            for m_id, rn, t1, t2 in (
                (MATCH_EARLY, ROUND_EARLY, "Early Line FC", "Sparta EL"),
                (MATCH_NO_LINE, ROUND_NO_LINE, "Dynamo EL", "Torpedo EL"),
                (MATCH_PLAY, ROUND_PLAY, "Zenit EL", "Rubin EL"),
                (MATCH_NEXT, ROUND_NEXT, "Ural EL", "Amkar EL"),
            ):
                c.execute(
                    "INSERT INTO matches (id, round_number, division_id, season_id, player1_id, player2_id, player1_team, player2_team, status) "
                    "VALUES (?, ?, 1, 1, NULL, NULL, ?, ?, 'scheduled')",
                    (m_id, rn, t1, t2)
                )

        database.get_or_create_wallet(USER_ID)

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        with database.transaction() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM bet_items WHERE bet_id IN (SELECT id FROM user_bets WHERE user_id = ?)", (USER_ID,))
            c.execute("DELETE FROM user_bets WHERE user_id = ?", (USER_ID,))
            c.execute("DELETE FROM coin_transactions WHERE user_id = ?", (USER_ID,))
            c.execute("DELETE FROM user_wallets WHERE user_id = ?", (USER_ID,))
            c.execute("DELETE FROM market_selections WHERE market_id IN (SELECT id FROM markets WHERE match_id >= 99700 AND match_id < 99800)")
            c.execute("DELETE FROM markets WHERE match_id >= 99700 AND match_id < 99800")
            c.execute("DELETE FROM bet_markets WHERE match_id >= 99700 AND match_id < 99800")
            c.execute("DELETE FROM matches WHERE id >= 99700 AND id < 99800")
            c.execute("DELETE FROM rounds WHERE round_number BETWEEN 191 AND 195")
            c.execute("DELETE FROM users WHERE telegram_id = ?", (USER_ID,))

    # --- 1. Schema -------------------------------------------------------
    def test_01_rounds_has_bets_open_columns(self):
        with database.transaction() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(rounds)")}
        self.assertIn("bets_open", cols)
        self.assertIn("bets_opened_at", cols)

    # --- 2-3. Opening the early line -------------------------------------
    def test_02_set_round_bets_open_opens_line_on_closed_round(self):
        self.assertTrue(database.set_round_bets_open(ROUND_EARLY, True, division_id=1, season_id=1))

        info = database.get_round_info(ROUND_EARLY, division_id=1, season_id=1)
        self.assertEqual(info["is_open"], 0, "Тур не должен открываться для игры")
        self.assertEqual(info["bets_open"], 1, "Линия должна быть открыта")
        self.assertIsNotNone(info["bets_opened_at"])

    def test_03_early_round_appears_in_open_betting_tours(self):
        database.set_round_bets_open(ROUND_EARLY, True, division_id=1, season_id=1)

        tours = {t["round_number"]: t for t in database.get_open_betting_tours(division_id=1, season_id=1)}
        self.assertIn(ROUND_EARLY, tours)
        self.assertTrue(tours[ROUND_EARLY]["is_early"])
        self.assertFalse(tours[ROUND_EARLY]["is_open"])

    def test_04_round_without_line_stays_out_of_betting_tours(self):
        tours = {t["round_number"] for t in database.get_open_betting_tours(division_id=1, season_id=1)}
        self.assertNotIn(ROUND_NO_LINE, tours)

    # --- 5-6. Risk engine gate -------------------------------------------
    def test_05_risk_engine_allows_bet_on_early_line(self):
        database.set_round_bets_open(ROUND_EARLY, True, division_id=1, season_id=1)
        database.add_coins(USER_ID, 5000, "deposit")

        decision = RiskEngine.evaluate_bet(
            user_id=USER_ID,
            amount=100,
            selections=[{"match_id": MATCH_EARLY, "outcome": "p1"}],
            division_id=1
        )
        self.assertNotEqual(
            decision.reason, "MARKET_SUSPENDED",
            f"Ранняя линия не должна блокироваться риск-движком: {decision.message}"
        )

    def test_06_risk_engine_rejects_when_line_closed(self):
        database.add_coins(USER_ID, 5000, "deposit")

        decision = RiskEngine.evaluate_bet(
            user_id=USER_ID,
            amount=100,
            selections=[{"match_id": MATCH_NO_LINE, "outcome": "p1"}],
            division_id=1
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "MARKET_SUSPENDED")

    # --- 7. Guard rails ---------------------------------------------------
    def test_07_cannot_open_line_for_round_without_matches(self):
        self.assertFalse(database.set_round_bets_open(ROUND_NO_MATCHES, True, division_id=1, season_id=1))

    # --- 8-9. Sync with is_open ------------------------------------------
    def test_08_opening_round_closes_its_betting_line(self):
        # Линия выставлена заранее — тур ещё не начался, прогнозы принимаются.
        self.assertTrue(database.set_round_bets_open(ROUND_PLAY, True, division_id=1, season_id=1))
        info = database.get_round_info(ROUND_PLAY, division_id=1, season_id=1)
        self.assertEqual(info["bets_open"], 1, "Предусловие: линия тура открыта")

        # Тур открыт для игры → приём прогнозов на него прекращается.
        database.update_round_status(ROUND_PLAY, is_open=True, division_id=1, season_id=1)
        info = database.get_round_info(ROUND_PLAY, division_id=1, season_id=1)
        self.assertEqual(info["is_open"], 1)
        self.assertEqual(info["bets_open"], 0, "Открытый для игры тур прогнозы не принимает")

        # Закрытие тура линию обратно не открывает.
        database.update_round_status(ROUND_PLAY, is_open=False, division_id=1, season_id=1)
        info = database.get_round_info(ROUND_PLAY, division_id=1, season_id=1)
        self.assertEqual(info["is_open"], 0)
        self.assertEqual(info["bets_open"], 0)

    def test_09_opening_round_pre_opens_next_round_line(self):
        database.update_round_status(ROUND_PLAY, is_open=True, division_id=1, season_id=1)

        nxt = database.get_round_info(ROUND_NEXT, division_id=1, season_id=1)
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt["is_open"], 0, "Следующий тур не должен открываться для игры")
        self.assertEqual(nxt["bets_open"], 1, "Линия на следующий тур должна открыться автоматически")


if __name__ == "__main__":
    unittest.main()
