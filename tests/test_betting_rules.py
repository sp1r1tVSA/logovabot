"""
tests/test_betting_rules.py

Инварианты правил ставок Logovo.bet:

1-3. 322-защита: игрок не может ставить ни на один исход матча, в котором играет сам —
     ни ординаром (как player1, как player2), ни в составе экспресса.
4-5. Ставки на чужие матчи разрешены, в том числе на матчи ЛЮБОГО дивизиона:
     дивизион игрока и дивизион матча между собой не сравниваются.
6.   Новый кошелёк открывается со стартовым балансом config.INITIAL_WALLET_BALANCE = 677,
     та же сумма записывается приветственной транзакцией 'welcome_bonus'.
7.   677 монет — это ровно весь стартовый баланс: 678 не проходит, 677 проходит.
8.   Участие определяется и по именам клубов — legacy-строки матчей без player1_id/player2_id
     тоже закрыты для своих же участников.

Проверяются обе точки валидации: services/risk_engine.RiskEngine.evaluate_bet
и database.place_user_bet (дублирующая проверка внутри транзакции списания).
"""

import unittest

import config
import database
from services.risk_engine import RiskEngine


SEASON_ID = 1
DIV_1 = 1
DIV_2 = 2

ROUND_D1 = 322
ROUND_D2 = 323

USER_A = 322111   # дивизион 1, играет в MATCH_OWN как player1
USER_B = 322222   # дивизион 1, играет в MATCH_OWN как player2
USER_C = 322333   # дивизион 1, играет в MATCH_FOREIGN
USER_D = 322444   # дивизион 1, играет в MATCH_FOREIGN
USER_E = 322555   # дивизион 2, играет в MATCH_D2
USER_F = 322666   # дивизион 2, играет в MATCH_D2
USER_NEW = 322777 # без кошелька и без матчей — проверяет стартовый баланс
USER_LEGACY = 322888  # участник legacy-матча, где заполнены только имена клубов

ROSTER = (
    (USER_A, "rule_a", "Rules FC A", DIV_1),
    (USER_B, "rule_b", "Rules FC B", DIV_1),
    (USER_C, "rule_c", "Rules FC C", DIV_1),
    (USER_D, "rule_d", "Rules FC D", DIV_1),
    (USER_E, "rule_e", "Rules FC E", DIV_2),
    (USER_F, "rule_f", "Rules FC F", DIV_2),
    (USER_LEGACY, "rule_legacy", "Rules FC Legacy", DIV_1),
)
ALL_USERS = tuple(u[0] for u in ROSTER) + (USER_NEW,)

MATCH_OWN = 99811      # USER_A vs USER_B, дивизион 1
MATCH_FOREIGN = 99812  # USER_C vs USER_D, дивизион 1
MATCH_D2 = 99813       # USER_E vs USER_F, дивизион 2
MATCH_LEGACY = 99814   # только player1_team/player2_team, id участников не заполнены

START_BALANCE = 20_000


class TestBettingRules(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self._cleanup()

        with database.transaction() as conn:
            c = conn.cursor()
            for tg_id, username, team, div in ROSTER:
                c.execute(
                    "INSERT OR REPLACE INTO users (telegram_id, username, team_name, division_id, role) "
                    "VALUES (?, ?, ?, ?, 'player')",
                    (tg_id, username, team, div)
                )

            # Линия открыта: тур закрыт для игры (is_open = 0) и принимает прогнозы (bets_open = 1).
            for rn, div in ((ROUND_D1, DIV_1), (ROUND_D2, DIV_2)):
                c.execute(
                    "INSERT INTO rounds (round_number, is_open, bets_open, deadline, division_id, season_id) "
                    "VALUES (?, 0, 1, NULL, ?, ?)",
                    (rn, div, SEASON_ID)
                )

            for m_id, rn, div, p1, p2 in (
                (MATCH_OWN, ROUND_D1, DIV_1, USER_A, USER_B),
                (MATCH_FOREIGN, ROUND_D1, DIV_1, USER_C, USER_D),
                (MATCH_D2, ROUND_D2, DIV_2, USER_E, USER_F),
            ):
                t1 = dict((u[0], u[2]) for u in ROSTER)[p1]
                t2 = dict((u[0], u[2]) for u in ROSTER)[p2]
                c.execute(
                    "INSERT INTO matches (id, round_number, division_id, season_id, player1_id, player2_id, "
                    "player1_team, player2_team, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scheduled')",
                    (m_id, rn, div, SEASON_ID, p1, p2, t1, t2)
                )

            # Legacy-строка: расписание знает только клубы, Telegram ID участников не заполнены.
            c.execute(
                "INSERT INTO matches (id, round_number, division_id, season_id, player1_id, player2_id, "
                "player1_team, player2_team, status) "
                "VALUES (?, ?, ?, ?, NULL, NULL, 'Rules FC Legacy', 'Rules FC C', 'scheduled')",
                (MATCH_LEGACY, ROUND_D1, DIV_1, SEASON_ID)
            )

            for m_id, rn in (
                (MATCH_OWN, ROUND_D1),
                (MATCH_FOREIGN, ROUND_D1),
                (MATCH_D2, ROUND_D2),
                (MATCH_LEGACY, ROUND_D1),
            ):
                c.execute(
                    "INSERT INTO bet_markets (match_id, tour, team1_name, team2_name, odd_p1, odd_x, odd_p2, is_active) "
                    "VALUES (?, ?, 'T1', 'T2', 2.10, 3.20, 2.80, 1)",
                    (m_id, rn)
                )

        for tg_id, _u, _t, _d in ROSTER:
            database.get_or_create_wallet(tg_id)
            database.add_coins(tg_id, START_BALANCE, "deposit")

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        with database.transaction() as conn:
            c = conn.cursor()
            marks = ",".join("?" * len(ALL_USERS))
            c.execute(
                f"DELETE FROM bet_items WHERE bet_id IN (SELECT id FROM user_bets WHERE user_id IN ({marks}))",
                ALL_USERS
            )
            c.execute(f"DELETE FROM user_bets WHERE user_id IN ({marks})", ALL_USERS)
            c.execute(f"DELETE FROM coin_transactions WHERE user_id IN ({marks})", ALL_USERS)
            c.execute(f"DELETE FROM user_wallets WHERE user_id IN ({marks})", ALL_USERS)
            c.execute(
                "DELETE FROM market_selections WHERE market_id IN "
                "(SELECT id FROM markets WHERE match_id BETWEEN 99811 AND 99819)"
            )
            c.execute("DELETE FROM markets WHERE match_id BETWEEN 99811 AND 99819")
            c.execute("DELETE FROM bet_markets WHERE match_id BETWEEN 99811 AND 99819")
            c.execute("DELETE FROM matches WHERE id BETWEEN 99811 AND 99819")
            c.execute(
                "DELETE FROM rounds WHERE round_number IN (?, ?) AND season_id = ?",
                (ROUND_D1, ROUND_D2, SEASON_ID)
            )
            c.execute(f"DELETE FROM users WHERE telegram_id IN ({marks})", ALL_USERS)

    # --- helpers ---------------------------------------------------------
    def _balance(self, user_id: int) -> int:
        return database.get_or_create_wallet(user_id)["balance"]

    def _bets_count(self, user_id: int) -> int:
        with database.transaction() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM user_bets WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row["n"]

    def _assert_self_bet_rejected(self, user_id: int, selections: list[dict], amount: int = 100):
        """Обе точки валидации отказывают, монеты не списаны, купона нет."""
        balance_before = self._balance(user_id)
        bets_before = self._bets_count(user_id)

        decision = RiskEngine.evaluate_bet(user_id=user_id, amount=amount, selections=selections)
        self.assertFalse(decision.allowed, "RiskEngine не должен разрешать ставку на свой матч")
        self.assertEqual(decision.decision, "REJECT")
        self.assertEqual(decision.reason, "SELF_BET_PROHIBITED")
        self.assertEqual(
            decision.message,
            "Запрещено делать ставки на матчи с собственным участием."
        )

        ok, result = database.place_user_bet(user_id, amount, selections)
        self.assertFalse(ok, "place_user_bet не должен принимать ставку на свой матч")
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("error"), "SELF_BET_PROHIBITED")
        self.assertEqual(
            result.get("message"),
            "Запрещено делать ставки на матчи с собственным участием."
        )

        self.assertEqual(self._balance(user_id), balance_before, "Баланс не должен быть списан")
        self.assertEqual(self._bets_count(user_id), bets_before, "Купон не должен быть создан")

    # --- 1-2. Self-bet: ординар ------------------------------------------
    def test_player_cannot_bet_on_own_match_as_player1(self):
        self._assert_self_bet_rejected(USER_A, [{"match_id": MATCH_OWN, "outcome": "p1"}])

    def test_player_cannot_bet_on_own_match_as_player2(self):
        # Ни победа соперника, ни тотал, ни ничья — весь матч закрыт для участника.
        for outcome in ("p1", "tb25", "x"):
            with self.subTest(outcome=outcome):
                self._assert_self_bet_rejected(USER_B, [{"match_id": MATCH_OWN, "outcome": outcome}])

    # --- 3. Self-bet: экспресс -------------------------------------------
    def test_player_cannot_bet_express_containing_own_match(self):
        self._assert_self_bet_rejected(
            USER_A,
            [
                {"match_id": MATCH_FOREIGN, "outcome": "p1"},   # чужой матч — сам по себе допустим
                {"match_id": MATCH_OWN, "outcome": "p2"},       # матч с собственным участием
            ]
        )

    # --- 4. Чужой матч своего дивизиона ----------------------------------
    def test_player_can_bet_on_other_matches_in_same_division(self):
        balance_before = self._balance(USER_A)
        selections = [{"match_id": MATCH_FOREIGN, "outcome": "p1"}]

        decision = RiskEngine.evaluate_bet(user_id=USER_A, amount=500, selections=selections, division_id=DIV_1)
        self.assertTrue(decision.allowed, f"RiskEngine отклонил чужой матч: {decision.reason} / {decision.message}")

        ok, result = database.place_user_bet(USER_A, 500, selections)
        self.assertTrue(ok, f"Ставка на чужой матч должна приниматься: {result}")
        self.assertIsInstance(result, int)
        self.assertEqual(self._balance(USER_A), balance_before - 500)

    # --- 5. Чужой дивизион ------------------------------------------------
    def test_cross_division_betting_allowed(self):
        balance_before = self._balance(USER_A)
        selections = [{"match_id": MATCH_D2, "outcome": "p2"}]

        decision = RiskEngine.evaluate_bet(user_id=USER_A, amount=300, selections=selections, division_id=DIV_2)
        self.assertTrue(
            decision.allowed,
            f"Дивизион игрока не должен блокировать линию чужого дивизиона: {decision.reason} / {decision.message}"
        )

        ok, result = database.place_user_bet(USER_A, 300, selections)
        self.assertTrue(ok, f"Ставка на матч чужого дивизиона должна приниматься: {result}")
        self.assertEqual(self._balance(USER_A), balance_before - 300)

        # И встречное направление: игрок дивизиона 2 ставит на матч дивизиона 1.
        e_before = self._balance(USER_E)
        ok, result = database.place_user_bet(USER_E, 300, [{"match_id": MATCH_FOREIGN, "outcome": "p1"}])
        self.assertTrue(ok, f"Ставка из дивизиона 2 на матч дивизиона 1 должна приниматься: {result}")
        self.assertEqual(self._balance(USER_E), e_before - 300)

    # --- 6. Стартовый баланс ---------------------------------------------
    def test_initial_wallet_balance_is_677(self):
        self.assertEqual(config.INITIAL_WALLET_BALANCE, 677)

        wallet = database.get_or_create_wallet(USER_NEW)
        self.assertEqual(wallet["balance"], 677)
        self.assertEqual(database.get_wallet_balance(USER_NEW), 677)

        with database.transaction() as conn:
            rows = conn.execute(
                "SELECT amount FROM coin_transactions WHERE user_id = ? AND transaction_type = 'welcome_bonus'",
                (USER_NEW,)
            ).fetchall()
            default_balance = {
                r["name"]: r["dflt_value"] for r in conn.execute("PRAGMA table_info(user_wallets)")
            }["balance"]

        self.assertEqual(len(rows), 1, "Приветственный бонус начисляется ровно один раз")
        self.assertEqual(rows[0]["amount"], 677)
        self.assertEqual(int(default_balance), 677, "DEFAULT схемы user_wallets.balance должен быть 677")

    # --- 7. 677 — это весь стартовый банк --------------------------------
    def test_insufficient_funds_above_677(self):
        selections = [{"match_id": MATCH_FOREIGN, "outcome": "p1"}]
        self.assertEqual(database.get_or_create_wallet(USER_NEW)["balance"], 677)

        decision = RiskEngine.evaluate_bet(user_id=USER_NEW, amount=678, selections=selections)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "INSUFFICIENT_BALANCE")

        ok, _result = database.place_user_bet(USER_NEW, 678, selections)
        self.assertFalse(ok, "678 монет при балансе 677 — отказ")
        self.assertEqual(self._balance(USER_NEW), 677, "Неудачная ставка баланс не трогает")

        ok, result = database.place_user_bet(USER_NEW, 677, selections)
        self.assertTrue(ok, f"Ставка на весь баланс (677) должна приниматься: {result}")
        self.assertEqual(self._balance(USER_NEW), 0)

    # --- 8. Legacy-матч без Telegram ID участников -----------------------
    def test_self_bet_detected_by_team_name_in_legacy_match(self):
        database.get_or_create_wallet(USER_LEGACY)
        self._assert_self_bet_rejected(USER_LEGACY, [{"match_id": MATCH_LEGACY, "outcome": "p1"}])

        # Для постороннего игрока тот же матч остаётся обычным.
        ok, result = database.place_user_bet(USER_B, 200, [{"match_id": MATCH_LEGACY, "outcome": "p1"}])
        self.assertTrue(ok, f"Чужой legacy-матч должен быть доступен: {result}")


if __name__ == "__main__":
    unittest.main()
