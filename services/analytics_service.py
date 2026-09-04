"""
services/analytics_service.py

Logovo.bet — Bettor Performance Analytics & Division-Scoped Leaderboard Service.
Strict Invariants:
1. Strict ROI formula: (net_profit / total_staked) * 100. If total_staked == 0, ROI = None (NULL, NEVER 0.0).
2. Win Rate = (wins / settled_bets) * 100. If settled_bets == 0, Win Rate = None.
3. Division & Season isolation: queries strictly isolate division_id and season_id.
4. Capper Leaderboard enforces MIN_LEADERBOARD_BETS (default: 5) to prevent 1-bet flukes.
"""

import logging
from typing import Any, Optional
import database

logger = logging.getLogger(__name__)

MIN_LEADERBOARD_BETS = 5


def get_user_betting_analytics(user_id: int) -> dict[str, Any]:
    """
    Calculate comprehensive betting performance analytics for a user:
    - total, settled, won, lost, voided bets
    - total staked, total payout, net profit
    - ROI % (strictly None if staked == 0)
    - win rate % (strictly None if settled == 0)
    - average odds, best win
    - favorite, best, and worst markets
    - recent form array (e.g. ['W', 'W', 'L', 'V', 'W'])
    """
    with database.transaction() as conn:
        cursor = conn.cursor()
        wallet = database.get_or_create_wallet(user_id)
        prog = database.get_or_create_progression(user_id)

        # 1. Bet counts by status
        cursor.execute("""
            SELECT status, COUNT(*) as cnt, COALESCE(SUM(amount), 0) as staked, COALESCE(SUM(potential_win), 0) as max_payout
            FROM user_bets
            WHERE user_id = ?
            GROUP BY status
        """, (user_id,))
        status_rows = {r["status"]: dict(r) for r in cursor.fetchall()}

        won_cnt = status_rows.get("won", {}).get("cnt", 0)
        lost_cnt = status_rows.get("lost", {}).get("cnt", 0)
        void_cnt = status_rows.get("voided", {}).get("cnt", 0) + status_rows.get("refunded", {}).get("cnt", 0)
        pending_cnt = status_rows.get("pending", {}).get("cnt", 0)
        settled_cnt = won_cnt + lost_cnt + void_cnt
        total_bets = settled_cnt + pending_cnt

        # 2. Total staked and total payout
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) as total_staked
            FROM user_bets
            WHERE user_id = ? AND status IN ('won', 'lost', 'voided', 'refunded')
        """, (user_id,))
        total_staked = int(cursor.fetchone()["total_staked"])

        cursor.execute("""
            SELECT COALESCE(SUM(potential_win), 0) as total_payout
            FROM user_bets
            WHERE user_id = ? AND status = 'won'
        """, (user_id,))
        total_payout = int(cursor.fetchone()["total_payout"])

        # Add refund from void/refunded bets to payout
        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) as void_refund
            FROM user_bets
            WHERE user_id = ? AND status IN ('voided', 'refunded')
        """, (user_id,))
        void_refund = int(cursor.fetchone()["void_refund"])
        total_payout += void_refund

        net_profit = total_payout - total_staked

        # 3. Strict ROI and Win Rate (None if divisor is zero, NEVER 0.0!)
        roi_pct: Optional[float] = None
        if total_staked > 0:
            roi_pct = round((net_profit / total_staked) * 100.0, 2)

        win_rate_pct: Optional[float] = None
        if settled_cnt > 0:
            win_rate_pct = round((won_cnt / settled_cnt) * 100.0, 1)

        # 4. Best Win & Average Odds
        cursor.execute("""
            SELECT MAX(potential_win) as max_win
            FROM user_bets
            WHERE user_id = ? AND status = 'won'
        """, (user_id,))
        win_stats = cursor.fetchone()
        best_win = int(win_stats["max_win"] or 0)

        cursor.execute("SELECT AVG(total_odd) as avg_odd FROM user_bets WHERE user_id = ?", (user_id,))
        avg_odd_row = cursor.fetchone()
        avg_odd = round(float(avg_odd_row["avg_odd"] or 1.0), 2)

        # 5. Market Pick Performance (favorite, best, worst)
        cursor.execute("""
            SELECT bi.outcome_type,
                   COUNT(*) as total_picks,
                   SUM(CASE WHEN ub.status = 'won' THEN 1 ELSE 0 END) as won_picks,
                   SUM(CASE WHEN ub.status = 'won' THEN ub.potential_win - ub.amount ELSE -ub.amount END) as profit
            FROM bet_items bi
            JOIN user_bets ub ON bi.bet_id = ub.id
            WHERE ub.user_id = ? AND ub.status IN ('won', 'lost', 'voided', 'refunded')
            GROUP BY bi.outcome_type
        """, (user_id,))
        market_stats = [dict(r) for r in cursor.fetchall()]

        favorite_market = "П1"
        best_market = "П1"
        worst_market = "П1"

        if market_stats:
            favorite_market = max(market_stats, key=lambda x: x["total_picks"])["outcome_type"].upper()
            best_market = max(market_stats, key=lambda x: x["profit"])["outcome_type"].upper()
            worst_market = min(market_stats, key=lambda x: x["profit"])["outcome_type"].upper()

        # 6. Recent Form (last 5 settled bets: W, L, V)
        cursor.execute("""
            SELECT status
            FROM user_bets
            WHERE user_id = ? AND status IN ('won', 'lost', 'voided', 'refunded')
            ORDER BY id DESC
            LIMIT 5
        """, (user_id,))
        form_rows = cursor.fetchall()
        recent_form = []
        for r in form_rows:
            st = r["status"]
            recent_form.append("W" if st == "won" else ("L" if st == "lost" else "V"))

    return {
        "user_id": user_id,
        "balance": wallet["balance"],
        "total_predictions": total_bets,
        "settled_predictions": settled_cnt,
        "won_predictions": won_cnt,
        "lost_predictions": lost_cnt,
        "void_predictions": void_cnt,
        "pending_predictions": pending_cnt,
        "total_staked": total_staked,
        "total_payout": total_payout,
        "net_profit": net_profit,
        "roi_pct": roi_pct,
        "win_rate_pct": win_rate_pct,
        "average_odds": avg_odd,
        "best_win": best_win,
        "favorite_market": favorite_market,
        "best_market": best_market,
        "worst_market": worst_market,
        "recent_form": recent_form,
        "current_streak": prog["current_streak"],
        "best_streak": prog["best_streak"],
        "level": prog["level"],
        "xp": prog["current_xp"]
    }


def get_capper_leaderboard(
    division_id: Optional[int] = None,
    season_id: Optional[int] = None,
    min_bets: int = MIN_LEADERBOARD_BETS,
    limit: int = 20
) -> list[dict[str, Any]]:
    """
    Retrieve capper / bettor leaderboard scoped to division and season.
    Enforces min_bets threshold to prevent 1-bet flukes from topping the table.
    Ranks primarily by net profit, then by ROI.
    """
    with database.transaction() as conn:
        cursor = conn.cursor()

        # Build parameterized query
        where_clauses = ["ub.status IN ('won', 'lost', 'voided', 'refunded')"]
        params: list[Any] = []

        if division_id is not None:
            where_clauses.append("u.division_id = ?")
            params.append(division_id)
        if season_id is not None:
            # Check bet items matches season if present
            where_clauses.append("EXISTS (SELECT 1 FROM bet_items bi JOIN matches m ON bi.match_id = m.id WHERE bi.bet_id = ub.id AND m.season_id = ?)")
            params.append(season_id)

        where_sql = " AND ".join(where_clauses)

        query = f"""
            SELECT ub.user_id, u.username, u.division_id,
                   COUNT(*) as settled_bets,
                   SUM(CASE WHEN ub.status = 'won' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN ub.status = 'lost' THEN 1 ELSE 0 END) as losses,
                   SUM(CASE WHEN ub.status IN ('voided', 'refunded') THEN 1 ELSE 0 END) as voids,
                   SUM(ub.amount) as total_staked,
                   SUM(CASE WHEN ub.status = 'won' THEN ub.potential_win WHEN ub.status IN ('voided', 'refunded') THEN ub.amount ELSE 0 END) as total_payout
            FROM user_bets ub
            JOIN users u ON ub.user_id = u.telegram_id
            WHERE {where_sql}
            GROUP BY ub.user_id
            HAVING COUNT(*) >= ?
        """
        params.append(min_bets)
        cursor.execute(query, params)
        rows = cursor.fetchall()

        leaderboard = []
        for r in rows:
            staked = int(r["total_staked"] or 0)
            payout = int(r["total_payout"] or 0)
            profit = payout - staked
            settled = int(r["settled_bets"] or 0)
            wins = int(r["wins"] or 0)

            roi = round((profit / staked) * 100.0, 2) if staked > 0 else None
            win_rate = round((wins / settled) * 100.0, 1) if settled > 0 else None

            leaderboard.append({
                "user_id": r["user_id"],
                "username": r["username"] or f"Игрок #{r['user_id']}",
                "division_id": r["division_id"],
                "settled_bets": settled,
                "wins": wins,
                "losses": int(r["losses"] or 0),
                "voids": int(r["voids"] or 0),
                "total_staked": staked,
                "total_payout": payout,
                "net_profit": profit,
                "roi_pct": roi,
                "win_rate_pct": win_rate
            })

        # Rank by net_profit DESC, then roi_pct DESC
        leaderboard.sort(key=lambda x: (x["net_profit"], x["roi_pct"] if x["roi_pct"] is not None else -999999.0), reverse=True)

        for idx, entry in enumerate(leaderboard[:limit]):
            entry["rank"] = idx + 1

        return leaderboard[:limit]
