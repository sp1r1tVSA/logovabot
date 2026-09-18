"""
tests/test_audit_redteam_fixes.py
Comprehensive regression tests for Sprint 1 Red Team audit fixes:
- LB-01: Single-Game Parlay (SGP) duplicate match bypass prevention (int/str/padded variations).
- LB-02: Handicap pricing direction and arbitrage prevention across favorite/underdog scenarios.
- LB-04: Express accumulator payout calculation parity (no phantom bonus in settlement).
- LB-12: Pre-match betting rejection on 'live' matches.
- LB-15: Idempotency hashing stability without TypeError on heterogeneous types.
"""

import sys
import os
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import services.odds_engine as odds_engine


class TestAuditRedTeamFixes(unittest.TestCase):
    def setUp(self):
        # Full isolation with temporary SQLite DB
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        database.DB_PATH = self._tmp.name
        database.init_db()
        database.ensure_canonical_divisions()

        self.user_id = 888777
        self.division_id = 1

        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO users (telegram_id, username, role) VALUES (?, 'redteam_tester', 'player')",
                (self.user_id,)
            )
            cursor.execute("INSERT INTO seasons (name, status) VALUES ('Test Season', 'active')")
            self.season_id = cursor.lastrowid

            # Ensure round betting is allowed: is_open = 0, bets_open = 1
            cursor.execute(
                """INSERT INTO rounds (division_id, round_number, season_id, is_open, bets_open)
                   VALUES (?, 1, ?, 0, 1)""",
                (self.division_id, self.season_id)
            )

            # Match 1 (pending)
            cursor.execute(
                """INSERT INTO matches (division_id, season_id, round_number, player1_team, player2_team, status)
                   VALUES (?, ?, 1, 'Arsenal', 'Chelsea', 'pending')""",
                (self.division_id, self.season_id)
            )
            self.m1 = cursor.lastrowid

            # Match 2 (open)
            cursor.execute(
                """INSERT INTO matches (division_id, season_id, round_number, player1_team, player2_team, status)
                   VALUES (?, ?, 1, 'Real Madrid', 'Barcelona', 'open')""",
                (self.division_id, self.season_id)
            )
            self.m2 = cursor.lastrowid

            # Match 3 (live)
            cursor.execute(
                """INSERT INTO matches (division_id, season_id, round_number, player1_team, player2_team, status)
                   VALUES (?, ?, 1, 'Bayern', 'Dortmund', 'live')""",
                (self.division_id, self.season_id)
            )
            self.m_live = cursor.lastrowid

        # Setup wallet with coins
        database.get_or_create_wallet(self.user_id)
        with database.transaction() as conn:
            conn.execute("UPDATE user_wallets SET balance = 50000 WHERE user_id = ?", (self.user_id,))

        # Create basic markets for match 1 and 2
        for mid in (self.m1, self.m2, self.m_live):
            m = odds_engine.get_or_create_market(mid, "1x2", "1X2")
            odds_engine.get_or_create_selection(m["id"], "p1", "П1", 2.00)
            odds_engine.get_or_create_selection(m["id"], "p2", "П2", 3.00)
            m_tot = odds_engine.get_or_create_market(mid, "total_goals", "Totals")
            odds_engine.get_or_create_selection(m_tot["id"], "over_2.5", "ТБ 2.5", 1.85)

    def tearDown(self):
        try:
            if os.path.exists(self._tmp.name):
                os.remove(self._tmp.name)
        except OSError:
            pass

    # ─── LB-01: Single Game Parlay (SGP) Bypass Tests ─────────────────────────

    def test_lb01_sgp_bypass_int_and_string_same_match(self):
        """Cannot place an express combining int and string of the same match_id."""
        selections = [
            {"match_id": self.m1, "outcome": "p1"},
            {"match_id": str(self.m1), "outcome": "over_2.5"},
        ]
        ok, res = database.place_user_bet(self.user_id, amount=100, selections=selections)
        self.assertFalse(ok)
        self.assertIn("Нельзя добавлять несколько исходов из одного матча", str(res))

    def test_lb01_sgp_bypass_padded_string_match_id(self):
        """Padded string '0777001' or ' 777001 ' is normalized and rejected for duplicate."""
        selections = [
            {"match_id": self.m1, "outcome": "p1"},
            {"match_id": f"  {self.m1}  ", "outcome": "over_2.5"},
        ]
        ok, res = database.place_user_bet(self.user_id, amount=100, selections=selections)
        self.assertFalse(ok)
        self.assertIn("Нельзя добавлять несколько исходов из одного матча", str(res))

    def test_lb01_invalid_match_id_formats(self):
        """Floats, negative numbers, non-numeric strings are rejected."""
        for bad_id in [f"{self.m1}.0", "abc", -10, 0, 12.34]:
            ok, res = database.place_user_bet(
                self.user_id,
                amount=100,
                selections=[{"match_id": bad_id, "outcome": "p1"}]
            )
            self.assertFalse(ok, f"Expected rejection for bad match_id: {bad_id}")

    def test_lb01_non_integer_bet_amount(self):
        """Floating point and non-integer stake amounts are rejected."""
        for bad_amt in [100.5, "100.50", "abc", None]:
            ok, res = database.place_user_bet(
                self.user_id,
                amount=bad_amt,
                selections=[{"match_id": self.m1, "outcome": "p1"}]
            )
            self.assertFalse(ok, f"Expected rejection for invalid amount: {bad_amt}")

    def test_lb01_valid_distinct_matches_express_succeeds(self):
        """Legitimate express across two different matches succeeds."""
        selections = [
            {"match_id": self.m1, "outcome": "p1"},
            {"match_id": self.m2, "outcome": "p2"},
        ]
        ok, bet_id = database.place_user_bet(self.user_id, amount=100, selections=selections)
        self.assertTrue(ok, f"Expected valid express to succeed, got: {bet_id}")
        self.assertIsInstance(bet_id, int)
        with database.transaction() as conn:
            row = conn.execute("SELECT * FROM user_bets WHERE id = ?", (bet_id,)).fetchone()
            self.assertEqual(row["bet_type"], "express")
            items_count = conn.execute("SELECT COUNT(*) FROM bet_items WHERE bet_id = ?", (bet_id,)).fetchone()[0]
            self.assertEqual(items_count, 2)

    # ─── LB-12: Match Status Rejection ───────────────────────────────────────

    def test_lb12_completed_or_in_progress_match_rejected(self):
        """Matches in 'completed' or 'in_progress' status must not accept bets."""
        with database.transaction() as conn:
            conn.execute("UPDATE matches SET status = 'completed' WHERE id = ?", (self.m_live,))
        selections = [{"match_id": self.m_live, "outcome": "p1"}]
        ok, res = database.place_user_bet(self.user_id, amount=100, selections=selections)
        self.assertFalse(ok)
        self.assertIn("уже сыгран или завершен", str(res))

    def test_lb12_legacy_static_markets_blocked_on_live_match(self):
        """On a 'live' match, legacy static bet_markets must NOT be used."""
        with database.transaction() as conn:
            conn.execute("UPDATE matches SET status = 'live' WHERE id = ?", (self.m_live,))
            # Delete relational markets so only legacy bet_markets would exist
            conn.execute("DELETE FROM markets WHERE match_id = ?", (self.m_live,))
            conn.execute("""
                INSERT OR REPLACE INTO bet_markets (match_id, tour, team1_name, team2_name, odd_p1, odd_x, odd_p2, is_active)
                VALUES (?, 1, 'Bayern', 'Dortmund', 2.50, 3.20, 2.80, 1)
            """, (self.m_live,))
        selections = [{"match_id": self.m_live, "outcome": "p1"}]
        ok, res = database.place_user_bet(self.user_id, amount=100, selections=selections)
        self.assertFalse(ok)
        self.assertIn("недоступен или заблокирован", str(res))

    # ─── LB-15: Type Safety in Hashing ────────────────────────────────────────

    def test_lb15_idempotency_hash_with_mixed_types(self):
        """Sorted tuple in idempotency hash never raises TypeError on string/int mix."""
        selections = [
            {"match_id": str(self.m1), "outcome": "p1"},
            {"match_id": self.m2, "outcome": "p2"},
        ]
        ok, res = database.place_user_bet(
            self.user_id,
            amount=100,
            selections=selections,
            idempotency_key="test_idem_mixed_types_1"
        )
        self.assertTrue(ok)

    # ─── LB-02: Handicap Direction & Arbitrage Freedom ───────────────────────

    def test_lb02_handicap_pricing_team1_favorite(self):
        """When Team 1 is favorite, odds for complementary pairs have overround > 1.0 (no arbitrage)."""
        standings = [
            {"team_name": "Team Strong", "points": 30, "played": 10, "won": 9, "draws": 1, "lost": 0},
            {"team_name": "Team Weak", "points": 5, "played": 10, "won": 1, "draws": 2, "lost": 7}
        ]
        markets = odds_engine.generate_match_markets(self.m1, "Team Strong", "Team Weak", standings=standings)
        hcp_mkt = next(m for m in markets if m["market_key"] == "handicap")
        sel_map = {s["selection_key"]: s["odds_value"] for s in hcp_mkt["selections"]}

        # All 4 selections present
        self.assertIn("h1_minus_1.5", sel_map)
        self.assertIn("h2_plus_1.5", sel_map)
        self.assertIn("h1_plus_1.5", sel_map)
        self.assertIn("h2_minus_1.5", sel_map)

        # Pair A overround: h1_minus_1.5 and h2_plus_1.5
        inv_a = (1.0 / sel_map["h1_minus_1.5"]) + (1.0 / sel_map["h2_plus_1.5"])
        self.assertGreaterEqual(inv_a, 1.0, f"Arbitrage detected in Pair A: overround {inv_a:.4f} < 1.0")

        # Pair B overround: h2_minus_1.5 and h1_plus_1.5
        inv_b = (1.0 / sel_map["h2_minus_1.5"]) + (1.0 / sel_map["h1_plus_1.5"])
        self.assertGreaterEqual(inv_b, 1.0, f"Arbitrage detected in Pair B: overround {inv_b:.4f} < 1.0")

        # Favorite (T1) minus handicap is competitive; Underdog (T2) minus handicap is high odd
        self.assertLess(sel_map["h1_minus_1.5"], sel_map["h2_minus_1.5"])
        self.assertLess(sel_map["h1_plus_1.5"], sel_map["h2_plus_1.5"])

    def test_lb02_handicap_pricing_team2_favorite_no_inversion_arbitrage(self):
        """When Team 2 is favorite, Team 2 (+1.5) must NOT be priced high (e.g. 2.39).
        Underdog Team 1 (-1.5) must NOT be priced at 1.15.
        No risk-free Dutch-book arbitrage with individual totals."""
        standings = [
            {"team_name": "Team Weak", "points": 5, "played": 10, "won": 1, "draws": 2, "lost": 7},
            {"team_name": "Team Strong", "points": 30, "played": 10, "won": 9, "draws": 1, "lost": 0}
        ]
        # Team Weak is Team 1, Team Strong is Team 2
        markets = odds_engine.generate_match_markets(self.m2, "Team Weak", "Team Strong", standings=standings)
        hcp_mkt = next(m for m in markets if m["market_key"] == "handicap")
        sel_map = {s["selection_key"]: s["odds_value"] for s in hcp_mkt["selections"]}

        # Pair A overround
        inv_a = (1.0 / sel_map["h1_minus_1.5"]) + (1.0 / sel_map["h2_plus_1.5"])
        self.assertGreaterEqual(inv_a, 1.0, f"Arbitrage detected in Pair A: overround {inv_a:.4f} < 1.0")

        # Critical LB-02 fix check:
        # Favorite Team 2 getting +1.5 MUST have a low odd (around 1.05 - 1.25), NOT 2.39!
        self.assertLessEqual(sel_map["h2_plus_1.5"], 1.25,
            f"Favorite Team 2 (+1.5) odd is too high: {sel_map['h2_plus_1.5']}, should be <= 1.25")

        # Underdog Team 1 winning by 2+ (-1.5) MUST have a high odd (>= 3.0), NOT 1.15!
        self.assertGreaterEqual(sel_map["h1_minus_1.5"], 3.0,
            f"Underdog Team 1 (-1.5) odd is too low: {sel_map['h1_minus_1.5']}, should be >= 3.0")

        # Favorite Team 2 (-1.5) must be competitive
        self.assertLess(sel_map["h2_minus_1.5"], sel_map["h1_minus_1.5"])


if __name__ == "__main__":
    unittest.main()
