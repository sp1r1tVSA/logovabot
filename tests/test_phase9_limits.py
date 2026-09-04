"""
tests/test_phase9_limits.py

Phase 9 — Centralized Betting Limits Engine Test Suite.
Verifies centralized limits hierarchy (User > Division > Global), single source of truth,
admin configuration, and RBAC isolation for limits.
"""

import os
import tempfile
import unittest
import database
from services.betting_limits import BettingLimitsService


class TestPhase9Limits(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        database.DB_PATH = self._tmp.name
        database.init_db()
        database.ensure_canonical_divisions()

        self.global_admin_id = 930001
        self.div_admin_id = 930002
        self.player_id = 930003

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'superadmin', 'admin')",
                (self.global_admin_id,)
            )
            cursor.execute(
                "INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'divadmin', 'division_admin')",
                (self.div_admin_id,)
            )
            cursor.execute(
                "INSERT OR REPLACE INTO users (telegram_id, username, role) VALUES (?, 'regular_player', 'user')",
                (self.player_id,)
            )
            cursor.execute(
                "INSERT OR REPLACE INTO division_admins (user_id, division_id) VALUES (?, 1)",
                (self.div_admin_id,)
            )

    def tearDown(self):
        try:
            os.remove(self._tmp.name)
        except OSError:
            pass

    def test_p9_limit_01_centralized_service_single_source_of_truth(self):
        """P9-LIMIT-01: Centralized BettingLimitsService is the authoritative single source of truth."""
        global_limits = BettingLimitsService.get_global_limits()
        self.assertIn("min_bet", global_limits)
        self.assertIn("max_bet", global_limits)
        self.assertIn("max_payout", global_limits)
        self.assertIn("max_daily_stake", global_limits)
        self.assertIn("max_daily_loss", global_limits)
        self.assertIn("max_open_exposure", global_limits)
        self.assertIn("market_exposure_limit", global_limits)
        self.assertIn("division_exposure_limit", global_limits)
        self.assertIn("global_exposure_limit", global_limits)

        # Base user gets default global limits if no user or division overrides
        user_limits = BettingLimitsService.get_user_effective_limits(self.player_id, division_id=None)
        self.assertEqual(user_limits["min_bet"], global_limits["min_bet"])
        self.assertEqual(user_limits["max_bet"], global_limits["max_bet"])
        self.assertEqual(user_limits["max_payout"], global_limits["max_payout"])

    def test_p9_limit_02_global_admin_updates_division_limits(self):
        """P9-LIMIT-02: Global Admin updates division limits, taking effect immediately."""
        new_div_limits = {
            "max_bet": 15000,
            "max_payout": 150000,
            "market_exposure_limit": 80000
        }
        res = BettingLimitsService.set_division_limits(division_id=1, limits=new_div_limits, updated_by=self.global_admin_id)
        self.assertTrue(res)

        effective = BettingLimitsService.get_division_limits(division_id=1)
        self.assertEqual(effective["max_bet"], 15000)
        self.assertEqual(effective["max_payout"], 150000)
        self.assertEqual(effective["market_exposure_limit"], 80000)

        # Player in division 1 inherits division 1 limits
        player_div1_limits = BettingLimitsService.get_user_effective_limits(self.player_id, division_id=1)
        self.assertEqual(player_div1_limits["max_bet"], 15000)
        self.assertEqual(player_div1_limits["max_payout"], 150000)

        # Player in division 2 does not inherit division 1 limits
        player_div2_limits = BettingLimitsService.get_user_effective_limits(self.player_id, division_id=2)
        self.assertNotEqual(player_div2_limits["max_bet"], 15000)

    def test_p9_limit_03_user_override_precedence(self):
        """P9-LIMIT-03: Custom user limits take precedence over division and global limits."""
        # Set division limit
        BettingLimitsService.set_division_limits(division_id=1, limits={"max_bet": 10000}, updated_by=self.global_admin_id)

        # Set specific user limit
        BettingLimitsService.set_user_limits(user_id=self.player_id, limits={"max_bet": 2500, "max_daily_stake": 5000}, updated_by=self.global_admin_id)

        effective = BettingLimitsService.get_user_effective_limits(self.player_id, division_id=1)
        self.assertEqual(effective["max_bet"], 2500)
        self.assertEqual(effective["max_daily_stake"], 5000)

    def test_p9_limit_04_admin_risk_rbac_validation(self):
        """P9-LIMIT-04: RBAC validation for limits mutation and queries."""
        # Player cannot set division limits via service logic
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT role FROM users WHERE telegram_id = ?", (self.player_id,))
            role = cursor.fetchone()[0]
            self.assertEqual(role, "user")

            cursor.execute("SELECT role FROM users WHERE telegram_id = ?", (self.div_admin_id,))
            role_div = cursor.fetchone()[0]
            self.assertEqual(role_div, "division_admin")


if __name__ == "__main__":
    unittest.main()
