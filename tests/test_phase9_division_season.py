"""
tests/test_phase9_division_season.py

Phase 9 — Division & Season Risk Isolation and Financial Read-Only Invariant Test Suite.
Verifies:
1. Strict multi-tenant risk partitioning: Division 1 bets do not affect Division 2 exposure.
2. Strict season boundaries: Season 1 liabilities do not bleed into Season 2.
3. Absolute Financial Read-Only Invariant: AST inspection ensuring Risk Engine, Exposure,
   Dynamic Confidence, and Risk Alerts never directly mutate user wallets or coin transactions.
"""

import ast
import os
import tempfile
import unittest
import database
from services.exposure_service import get_division_exposure
from services.betting_limits import BettingLimitsService


class TestPhase9DivisionSeasonIsolation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        database.DB_PATH = self._tmp.name
        database.init_db()
        database.ensure_canonical_divisions()

        self.user_id = 990001

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'iso_user', 'user')", (self.user_id,))

            # Seasons
            cursor.execute("INSERT INTO seasons (name, status) VALUES ('Season 1', 'active')")
            self.season1_id = cursor.lastrowid
            cursor.execute("INSERT INTO seasons (name, status) VALUES ('Season 2', 'active')")
            self.season2_id = cursor.lastrowid

            # Div 1 Season 1 Match
            cursor.execute("INSERT INTO rounds (division_id, round_number, season_id, is_open) VALUES (1, 1, ?, 1)", (self.season1_id,))
            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, status)
                VALUES (991, 1, ?, 1, 'Div1TeamA', 'Div1TeamB', 'open')
            """, (self.season1_id,))
            cursor.execute("INSERT INTO markets (id, match_id, market_key, market_name, status) VALUES (9910, 991, '1x2', 'Winner', 'open')")
            cursor.execute("""
                INSERT INTO market_selections (id, market_id, selection_key, selection_name, odds_value, status, odds_version)
                VALUES (99101, 9910, 'p1', 'Div1TeamA', 2.00, 'active', 1)
            """)

            # Div 2 Season 1 Match
            cursor.execute("INSERT INTO rounds (division_id, round_number, season_id, is_open) VALUES (2, 1, ?, 1)", (self.season1_id,))
            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, status)
                VALUES (992, 2, ?, 1, 'Div2TeamA', 'Div2TeamB', 'open')
            """, (self.season1_id,))
            cursor.execute("INSERT INTO markets (id, match_id, market_key, market_name, status) VALUES (9920, 992, '1x2', 'Winner', 'open')")
            cursor.execute("""
                INSERT INTO market_selections (id, market_id, selection_key, selection_name, odds_value, status, odds_version)
                VALUES (99201, 9920, 'p1', 'Div2TeamA', 2.50, 'active', 1)
            """)

            # Div 1 Season 2 Match
            cursor.execute("INSERT INTO rounds (division_id, round_number, season_id, is_open) VALUES (1, 1, ?, 1)", (self.season2_id,))
            cursor.execute("""
                INSERT INTO matches (id, division_id, season_id, round_number, player1_team, player2_team, status)
                VALUES (993, 1, ?, 1, 'Div1S2TeamA', 'Div1S2TeamB', 'open')
            """, (self.season2_id,))
            cursor.execute("INSERT INTO markets (id, match_id, market_key, market_name, status) VALUES (9930, 993, '1x2', 'Winner', 'open')")
            cursor.execute("""
                INSERT INTO market_selections (id, market_id, selection_key, selection_name, odds_value, status, odds_version)
                VALUES (99301, 9930, 'p1', 'Div1S2TeamA', 3.00, 'active', 1)
            """)

        database.get_or_create_wallet(self.user_id)
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE user_wallets SET balance = 50000 WHERE user_id = ?", (self.user_id,))

    def tearDown(self):
        try:
            os.remove(self._tmp.name)
        except OSError:
            pass

    def test_p9_isol_01_division_boundary_isolation(self):
        """P9-ISOL-01: Bets placed in Division 1 do not affect Division 2 exposure."""
        # Place 5,000 coins on Div 1 match
        ok, bet_id = database.place_user_bet(
            user_id=self.user_id,
            amount=5000,
            selections=[{"match_id": 991, "market_id": 9910, "selection_id": 99101, "outcome": "p1", "odds": 2.00}],
            idempotency_key="iso-div1-1"
        )
        self.assertTrue(ok)

        # Div 1 exposure must reflect the bet
        expo_div1 = get_division_exposure(division_id=1, season_id=self.season1_id)
        self.assertEqual(expo_div1["total_stake"], 5000)
        self.assertEqual(expo_div1["potential_payout"], 10000)
        self.assertEqual(expo_div1["net_exposure"], 5000)

        # Div 2 exposure must remain completely zero
        expo_div2 = get_division_exposure(division_id=2, season_id=self.season1_id)
        self.assertEqual(expo_div2["total_stake"], 0)
        self.assertEqual(expo_div2["potential_payout"], 0)
        self.assertEqual(expo_div2["net_exposure"], 0)

    def test_p9_isol_02_season_boundary_isolation(self):
        """P9-ISOL-02: Season 1 bets do not bleed into Season 2 exposure."""
        # Place bet in Season 1
        ok, _ = database.place_user_bet(
            user_id=self.user_id,
            amount=3000,
            selections=[{"match_id": 991, "market_id": 9910, "selection_id": 99101, "outcome": "p1", "odds": 2.00}],
            idempotency_key="iso-season1-1"
        )
        self.assertTrue(ok)

        # Season 1 exposure has the 3,000 stake
        expo_s1 = get_division_exposure(division_id=1, season_id=self.season1_id)
        self.assertEqual(expo_s1["total_stake"], 3000)

        # Season 2 exposure must be 0
        expo_s2 = get_division_exposure(division_id=1, season_id=self.season2_id)
        self.assertEqual(expo_s2["total_stake"], 0)
        self.assertEqual(expo_s2["potential_payout"], 0)

    def test_p9_fin_01_ast_financial_read_only_invariant(self):
        """
        P9-FIN-01: Static AST verification ensuring Risk and Telemetry modules
        never execute direct mutations on user_wallets or coin_transactions.
        """
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        read_only_services = [
            os.path.join(project_root, "services", "risk_engine.py"),
            os.path.join(project_root, "services", "exposure_service.py"),
            os.path.join(project_root, "services", "dynamic_confidence.py"),
            os.path.join(project_root, "services", "risk_alerts.py"),
            os.path.join(project_root, "services", "betting_limits.py"),
        ]

        forbidden_patterns = [
            "UPDATE user_wallets",
            "INSERT INTO user_wallets",
            "DELETE FROM user_wallets",
            "INSERT INTO coin_transactions",
            "UPDATE coin_transactions",
            "deduct_coins",
            "credit_coins",
        ]

        for s_path in read_only_services:
            self.assertTrue(os.path.isfile(s_path), f"Service file {s_path} must exist")
            with open(s_path, "r", encoding="utf-8") as f:
                content = f.read()
                tree = ast.parse(content, filename=s_path)

            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for forbidden in forbidden_patterns:
                        self.assertNotIn(
                            forbidden.lower(),
                            node.value.lower(),
                            f"Violation in {os.path.basename(s_path)}: Found unauthorized financial mutation string '{forbidden}'"
                        )


if __name__ == "__main__":
    unittest.main()
