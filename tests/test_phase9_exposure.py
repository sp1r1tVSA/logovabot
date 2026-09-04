"""
tests/test_phase9_exposure.py

Phase 9 — Market & Division Exposure & Liability Engine Test Suite.
Verifies total stake, potential payout, net exposure, division isolation,
and global liability aggregation.
"""

import os
import tempfile
import unittest
import database
from services.exposure_service import get_market_exposure, get_division_exposure, get_global_exposure
from services.betting_limits import BettingLimitsService


class TestPhase9Exposure(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        database.DB_PATH = self._tmp.name
        database.init_db()
        database.ensure_canonical_divisions()

        self.user1_id = 920001
        self.user2_id = 920002

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'expo_user1', 'user')", (self.user1_id,))
            cursor.execute("INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'expo_user2', 'user')", (self.user2_id,))
            cursor.execute("INSERT INTO seasons (name, status) VALUES ('Expo Season', 'active')")
            self.season_id = cursor.lastrowid

            # Div 1 Match & Market
            cursor.execute("INSERT INTO rounds (division_id, round_number, season_id, is_open) VALUES (1, 1, ?, 1)", (self.season_id,))
            cursor.execute("INSERT INTO matches (division_id, season_id, round_number, player1_team, player2_team, status) VALUES (1, ?, 1, 'Real', 'Barca', 'open')", (self.season_id,))
            self.match_div1 = cursor.lastrowid
            cursor.execute("INSERT INTO markets (match_id, market_key, market_name, status) VALUES (?, '1x2', 'Match Winner', 'open')", (self.match_div1,))
            self.market_div1 = cursor.lastrowid
            cursor.execute("INSERT INTO market_selections (market_id, selection_key, selection_name, odds_value, status, odds_version) VALUES (?, 'p1', 'Real', 2.00, 'active', 1)", (self.market_div1,))
            self.sel_p1_div1 = cursor.lastrowid
            cursor.execute("INSERT INTO market_selections (market_id, selection_key, selection_name, odds_value, status, odds_version) VALUES (?, 'p2', 'Barca', 3.00, 'active', 1)", (self.market_div1,))
            self.sel_p2_div1 = cursor.lastrowid

            # Div 2 Match & Market
            cursor.execute("INSERT INTO rounds (division_id, round_number, season_id, is_open) VALUES (2, 1, ?, 1)", (self.season_id,))
            cursor.execute("INSERT INTO matches (division_id, season_id, round_number, player1_team, player2_team, status) VALUES (2, ?, 1, 'Milan', 'Inter', 'open')", (self.season_id,))
            self.match_div2 = cursor.lastrowid
            cursor.execute("INSERT INTO markets (match_id, market_key, market_name, status) VALUES (?, '1x2', 'Match Winner', 'open')", (self.match_div2,))
            self.market_div2 = cursor.lastrowid
            cursor.execute("INSERT INTO market_selections (market_id, selection_key, selection_name, odds_value, status, odds_version) VALUES (?, 'p1', 'Milan', 2.50, 'active', 1)", (self.market_div2,))
            self.sel_p1_div2 = cursor.lastrowid

        database.get_or_create_wallet(self.user1_id)
        database.get_or_create_wallet(self.user2_id)
        with database.transaction() as conn:
            conn.cursor().execute("UPDATE user_wallets SET balance = 100000 WHERE user_id IN (?, ?)", (self.user1_id, self.user2_id))

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except Exception:
            pass

    def test_01_market_exposure_breakdown(self):
        """P9-EXPO-01: Calculate market total stake and liability per selection."""
        # User 1 bets 1000 on Real (p1, odd 2.0)
        database.place_user_bet(
            user_id=self.user1_id,
            amount=1000,
            selections=[{"match_id": self.match_div1, "outcome": "p1", "market_id": self.market_div1, "selection_id": self.sel_p1_div1}]
        )
        # User 2 bets 500 on Barca (p2, odd 3.0)
        database.place_user_bet(
            user_id=self.user2_id,
            amount=500,
            selections=[{"match_id": self.match_div1, "outcome": "p2", "market_id": self.market_div1, "selection_id": self.sel_p2_div1}]
        )

        expo = get_market_exposure(self.market_div1)
        self.assertEqual(expo["total_stake"], 1500)
        self.assertEqual(expo["max_potential_payout"], 2000)  # Real: 1000 * 2.0 = 2000; Barca: 500 * 3.0 = 1500

        sel_map = {s["selection_key"]: s for s in expo["selections"]}
        # Real net exposure: 2000 payout - 500 Barca stake = 1500
        self.assertEqual(sel_map["p1"]["net_exposure"], 1500)
        # Barca net exposure: 1500 payout - 1000 Real stake = 500
        self.assertEqual(sel_map["p2"]["net_exposure"], 500)

    def test_02_market_net_exposure_ceiling(self):
        """P9-EXPO-02: Market net exposure exceeds ceiling triggers EXPOSURE_LIMIT."""
        # Set market exposure limit to 5,000
        BettingLimitsService.set_limit("division", 1, "market_exposure_limit", 5000)

        # Place 2,000 on odd 2.0 -> payout 4,000 (below 5000) -> Allowed
        ok1, _ = database.place_user_bet(
            user_id=self.user1_id,
            amount=2000,
            selections=[{"match_id": self.match_div1, "outcome": "p1", "market_id": self.market_div1, "selection_id": self.sel_p1_div1, "odd": 2.00}]
        )
        self.assertTrue(ok1)

        # Attempt another 2,000 on odd 2.0 -> would push net exposure to 8,000 > 5,000 -> Rejected
        ok2, res2 = database.place_user_bet(
            user_id=self.user2_id,
            amount=2000,
            selections=[{"match_id": self.match_div1, "outcome": "p1", "market_id": self.market_div1, "selection_id": self.sel_p1_div1, "odd": 2.00}]
        )
        self.assertFalse(ok2)
        self.assertIsInstance(res2, dict)
        self.assertEqual(res2.get("error"), "EXPOSURE_LIMIT")

    def test_03_division_exposure_isolation(self):
        """P9-EXPO-03: Division exposure aggregation strictly isolated to requested division."""
        # Bet 3000 on Div 1
        database.place_user_bet(
            user_id=self.user1_id,
            amount=3000,
            selections=[{"match_id": self.match_div1, "outcome": "p1", "market_id": self.market_div1, "selection_id": self.sel_p1_div1}]
        )
        # Bet 1000 on Div 2
        database.place_user_bet(
            user_id=self.user2_id,
            amount=1000,
            selections=[{"match_id": self.match_div2, "outcome": "p1", "market_id": self.market_div2, "selection_id": self.sel_p1_div2}]
        )

        div1_expo = get_division_exposure(1)
        div2_expo = get_division_exposure(2)

        self.assertEqual(div1_expo["total_staked"], 3000)
        self.assertEqual(div1_expo["total_potential_payout"], 6000)
        self.assertEqual(div2_expo["total_staked"], 1000)
        self.assertEqual(div2_expo["total_potential_payout"], 2500)

    def test_04_global_exposure_aggregation(self):
        """P9-EXPO-04: Global exposure correctly sums across divisions."""
        # Div 1: 2000 stake, Div 2: 1000 stake
        database.place_user_bet(
            user_id=self.user1_id,
            amount=2000,
            selections=[{"match_id": self.match_div1, "outcome": "p1", "market_id": self.market_div1, "selection_id": self.sel_p1_div1}]
        )
        database.place_user_bet(
            user_id=self.user2_id,
            amount=1000,
            selections=[{"match_id": self.match_div2, "outcome": "p1", "market_id": self.market_div2, "selection_id": self.sel_p1_div2}]
        )

        glob_expo = get_global_exposure()
        self.assertEqual(glob_expo["pending_bets_count"], 2)
        self.assertEqual(glob_expo["total_staked"], 3000)
        self.assertEqual(glob_expo["total_potential_payout"], 4000 + 2500)
        self.assertEqual(len(glob_expo["divisions"]), 2)
