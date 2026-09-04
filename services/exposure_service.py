"""
services/exposure_service.py

Logovo.bet — Market & Division Liability & Exposure Engine.
Calculates:
- Total stake per selection
- Potential payout per selection
- Net bookmaker exposure (liability - counter-stakes)
- Division-scoped risk aggregation
- Global bookmaker liability tracking
"""

import logging
from typing import Any, Optional
import database

logger = logging.getLogger(__name__)


def get_market_exposure(market_id: int) -> dict[str, Any]:
    """
    Calculate comprehensive bookmaker exposure for a single market.
    Returns breakdown per selection and maximum net liability.
    """
    with database.transaction() as conn:
        cursor = conn.cursor()

        # Fetch market metadata
        cursor.execute("""
            SELECT m.id, m.match_id, m.market_key, m.market_name, m.status,
                   mat.division_id, mat.season_id, mat.player1_team, mat.player2_team
            FROM markets m
            JOIN matches mat ON m.match_id = mat.id
            WHERE m.id = ?
        """, (market_id,))
        market = cursor.fetchone()
        if not market:
            return {"error": "MARKET_NOT_FOUND", "market_id": market_id}

        # Query selections and aggregate pending stakes & liabilities
        cursor.execute("""
            SELECT ms.id as selection_id, ms.selection_key, ms.selection_name, ms.odds_value, ms.status as sel_status,
                   COUNT(bi.id) as bets_count,
                   COALESCE(SUM(ub.amount), 0) as total_stake,
                   COALESCE(SUM(CAST(ub.amount * bi.odd AS INTEGER)), 0) as potential_payout
            FROM market_selections ms
            LEFT JOIN bet_items bi ON ms.id = bi.selection_id AND bi.status = 'pending'
            LEFT JOIN user_bets ub ON bi.bet_id = ub.id AND ub.status = 'pending' AND ub.settled_at IS NULL
            WHERE ms.market_id = ?
            GROUP BY ms.id
            ORDER BY ms.id ASC
        """, (market_id,))
        selection_rows = cursor.fetchall()

        selections = []
        market_total_stake = 0
        max_payout = 0

        for r in selection_rows:
            stake = int(r["total_stake"] or 0)
            payout = int(r["potential_payout"] or 0)
            market_total_stake += stake
            if payout > max_payout:
                max_payout = payout

            selections.append({
                "selection_id": r["selection_id"],
                "selection_key": r["selection_key"],
                "selection_name": r["selection_name"],
                "odds_value": float(r["odds_value"]),
                "status": r["sel_status"],
                "bets_count": int(r["bets_count"] or 0),
                "total_stake": stake,
                "potential_payout": payout,
            })

        # Calculate net exposure per selection
        # Net Exposure = Potential Payout - Counter-stakes on other outcomes
        max_net_exposure = 0
        for s in selections:
            counter_stakes = market_total_stake - s["total_stake"]
            net_expo = max(0, s["potential_payout"] - counter_stakes)
            s["net_exposure"] = net_expo
            if net_expo > max_net_exposure:
                max_net_exposure = net_expo

        return {
            "market_id": market["id"],
            "match_id": market["match_id"],
            "market_key": market["market_key"],
            "market_name": market["market_name"],
            "market_status": market["status"],
            "division_id": market["division_id"],
            "season_id": market["season_id"],
            "match": f"{market['player1_team']} vs {market['player2_team']}",
            "total_stake": market_total_stake,
            "max_potential_payout": max_payout,
            "max_net_exposure": max_net_exposure,
            "selections": selections
        }


def get_division_exposure(division_id: int, season_id: Optional[int] = None) -> dict[str, Any]:
    """
    Calculate aggregate exposure across all active matches/markets in a specific division.
    Strictly isolated: division 1 does not reflect division 2.
    """
    with database.transaction() as conn:
        cursor = conn.cursor()

        query = """
            SELECT 
                COUNT(DISTINCT m.id) as active_markets_count,
                COUNT(DISTINCT ub.id) as pending_bets_count,
                COALESCE(SUM(ub.amount), 0) as total_staked,
                COALESCE(SUM(ub.potential_win), 0) as total_potential_payout
            FROM markets m
            JOIN matches mat ON m.match_id = mat.id
            JOIN market_selections ms ON m.id = ms.market_id
            JOIN bet_items bi ON ms.id = bi.selection_id AND bi.status = 'pending'
            JOIN user_bets ub ON bi.bet_id = ub.id AND ub.status = 'pending' AND ub.settled_at IS NULL
            WHERE mat.division_id = ? AND m.status IN ('open', 'active', 'suspended')
        """
        params: list[Any] = [division_id]
        if season_id is not None:
            query += " AND mat.season_id = ?"
            params.append(season_id)

        cursor.execute(query, params)
        row = cursor.fetchone()

        total_staked = int(row["total_staked"] or 0)
        total_payout = int(row["total_potential_payout"] or 0)
        net_liability = max(0, total_payout - total_staked)

        return {
            "division_id": division_id,
            "season_id": season_id,
            "active_markets_count": int(row["active_markets_count"] or 0),
            "pending_bets_count": int(row["pending_bets_count"] or 0),
            "total_staked": total_staked,
            "total_stake": total_staked,
            "total_potential_payout": total_payout,
            "potential_payout": total_payout,
            "net_liability": net_liability,
            "net_exposure": net_liability
        }


def get_global_exposure() -> dict[str, Any]:
    """Calculate system-wide total pending exposure across all divisions."""
    with database.transaction() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT 
                COUNT(DISTINCT id) as pending_bets_count,
                COALESCE(SUM(amount), 0) as total_staked,
                COALESCE(SUM(potential_win), 0) as total_potential_payout
            FROM user_bets
            WHERE status = 'pending' AND settled_at IS NULL
        """)
        row = cursor.fetchone()

        total_staked = int(row["total_staked"] or 0)
        total_payout = int(row["total_potential_payout"] or 0)

        # Get breakdown by division
        cursor.execute("""
            SELECT mat.division_id, 
                   COUNT(DISTINCT ub.id) as bets_count,
                   COALESCE(SUM(ub.amount), 0) as staked,
                   COALESCE(SUM(ub.potential_win), 0) as payout
            FROM user_bets ub
            JOIN bet_items bi ON ub.id = bi.bet_id
            JOIN matches mat ON bi.match_id = mat.id
            WHERE ub.status = 'pending' AND ub.settled_at IS NULL
            GROUP BY mat.division_id
        """)
        div_rows = cursor.fetchall()
        divisions_breakdown = [
            {
                "division_id": r["division_id"],
                "bets_count": int(r["bets_count"]),
                "staked": int(r["staked"]),
                "payout": int(r["payout"]),
                "net_exposure": max(0, int(r["payout"]) - int(r["staked"]))
            }
            for r in div_rows
        ]

        return {
            "pending_bets_count": int(row["pending_bets_count"] or 0),
            "total_staked": total_staked,
            "total_stake": total_staked,
            "total_potential_payout": total_payout,
            "potential_payout": total_payout,
            "global_net_liability": max(0, total_payout - total_staked),
            "net_exposure": max(0, total_payout - total_staked),
            "divisions": divisions_breakdown
        }
