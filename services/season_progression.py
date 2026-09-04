"""
services/season_progression.py

Season Progression, Promotion/Relegation & Idempotent Finalization Engine.
Strict Invariants:
1. Idempotency: finalize_season() can only execute once per season.
   Subsequent runs return ALREADY_FINALIZED and never double-credit rewards.
2. Financial Isolation: Coin rewards MUST route exclusively through database.add_coins()
   with transaction_type = 'season_reward'. No direct balance manipulation.
3. Promotion / Relegation: Inactive players (< min_bets) cannot receive promotion.
4. Snapshot Immutability: Once finalized, season snapshots cannot be altered.
5. Concurrency Safe: Uses transaction locking during finalization.
"""

import json
import logging
import threading
from typing import Optional, Any
import database
from services.leaderboard_service import invalidate_leaderboard_cache

logger = logging.getLogger(__name__)

_season_finalization_lock = threading.Lock()


class SeasonProgressionEngine:
    """Manages division zones, promotion/relegation, and idempotent season finalization."""

    @classmethod
    def get_division_standings(cls, season_id: int, division_id: int) -> list[dict]:
        """
        Get current sorted competitive standings for division in season,
        including promotion, safe, and relegation zone tags.
        """
        with database.transaction() as conn:
            cursor = conn.cursor()
            rules = database.get_season_rules(season_id, division_id)
            prom_slots = rules.get("promotion_slots", 3)
            rel_slots = rules.get("relegation_slots", 3)
            min_b = rules.get("min_bets_qualification", 5)

            cursor.execute("""
                SELECT sps.*, u.username, u.team_name, p.level, p.equipped_title as title
                FROM season_player_stats sps
                LEFT JOIN users u ON sps.user_id = u.telegram_id
                LEFT JOIN user_progression p ON sps.user_id = p.user_id
                WHERE sps.season_id = ? AND sps.division_id = ?
                ORDER BY (CASE WHEN sps.settled_bets >= ? THEN 1 ELSE 0 END) DESC,
                         sps.rating DESC,
                         sps.season_points DESC,
                         sps.settled_bets DESC
            """, (season_id, division_id, min_b))
            rows = [dict(r) for r in cursor.fetchall()]

            total_count = len(rows)
            standings = []
            for idx, r in enumerate(rows):
                rank = idx + 1
                settled = r["settled_bets"]
                is_active = (settled >= min_b)

                # Determine zone
                if not is_active:
                    zone = "INACTIVE"
                    zone_label = "Неактивен"
                elif rank <= prom_slots:
                    zone = "PROMOTION"
                    zone_label = "Зона повышения 🚀"
                elif rank > (total_count - rel_slots):
                    zone = "RELEGATION"
                    zone_label = "Зона вылета ⚠️"
                else:
                    zone = "SAFE"
                    zone_label = "Безопасная зона"

                r["rank"] = rank
                r["zone"] = zone
                r["zone_label"] = zone_label
                r["is_qualified"] = is_active
                standings.append(r)

            return standings

    @classmethod
    def finalize_season(cls, season_id: int, actor_id: Optional[int] = None) -> tuple[bool, str, dict[str, Any]]:
        """
        Idempotently finalize an active season.
        Freezes standings, stores immutable snapshots, computes rewards,
        credits wallet and XP safely, and transitions season to 'finished'.
        """
        with _season_finalization_lock:
            with database.transaction() as conn:
                cursor = conn.cursor()

                # 1. Verify season state
                cursor.execute("SELECT * FROM seasons WHERE id = ?", (season_id,))
                season = cursor.fetchone()
                if not season:
                    return False, f"Сезон #{season_id} не найден.", {}
                if season["status"] != "active":
                    return False, f"Сезон #{season_id} не активен (текущий статус: {season['status']}).", {
                        "status": season["status"]
                    }

                # Check if snapshots already exist (idempotency safety check)
                cursor.execute("SELECT COUNT(*) as cnt FROM season_snapshots WHERE season_id = ?", (season_id,))
                if cursor.fetchone()["cnt"] > 0:
                    return False, f"Сезон #{season_id} уже зафиксирован ранее.", {}

                # 2. Process all active divisions
                cursor.execute("SELECT DISTINCT id FROM divisions WHERE is_active = 1")
                div_rows = cursor.fetchall()
                divisions = [r["id"] for r in div_rows] if div_rows else [1, 2, 3, 4, 5]

                total_snapshots = 0
                total_rewards_distributed = 0
                coins_distributed_total = 0
                xp_distributed_total = 0

                for div_id in divisions:
                    standings = cls.get_division_standings(season_id, div_id)
                    rules = database.get_season_rules(season_id, div_id)
                    prom_slots = rules.get("promotion_slots", 3)
                    rel_slots = rules.get("relegation_slots", 3)
                    min_b = rules.get("min_bets_qualification", 5)

                    total_in_div = len(standings)

                    for player in standings:
                        u_id = player["user_id"]
                        rank = player["rank"]
                        settled = player["settled_bets"]
                        is_active = (settled >= min_b)

                        # Determine final promotion status
                        if not is_active:
                            prom_status = "INACTIVE"
                        elif rank <= prom_slots:
                            prom_status = "PROMOTED"
                        elif rank > (total_in_div - rel_slots):
                            prom_status = "RELEGATED"
                        else:
                            prom_status = "STAY"

                        # Determine rewards to award
                        player_rewards = []
                        if is_active:
                            if rank == 1:
                                player_rewards.append("REW_CHAMPION")
                            elif rank <= 3:
                                player_rewards.append("REW_TOP_3")
                            elif rank <= 10:
                                player_rewards.append("REW_TOP_10")

                            if prom_status == "PROMOTED":
                                player_rewards.append("REW_PROMOTION")

                            player_rewards.append("REW_PARTICIPATION")

                        # 3. Create immutable snapshot
                        database.create_season_snapshot(
                            season_id=season_id,
                            division_id=div_id,
                            user_id=u_id,
                            final_rank=rank,
                            final_rating=float(player["rating"]),
                            season_points=float(player["season_points"]),
                            wins=player["wins"],
                            losses=player["losses"],
                            voids=player["voids"],
                            settled_bets=player["settled_bets"],
                            win_rate=float(player["win_rate"]),
                            roi=float(player["roi"]),
                            total_stake=player["total_stake"],
                            total_payout=player["total_payout"],
                            best_streak=player["best_streak"],
                            promotion_status=prom_status,
                            rewards_json=json.dumps(player_rewards)
                        )
                        total_snapshots += 1

                        # 4. Idempotently distribute rewards
                        for rew_id in player_rewards:
                            cursor.execute("SELECT * FROM season_rewards_catalog WHERE id = ?", (rew_id,))
                            rew_def = cursor.fetchone()
                            if not rew_def:
                                continue

                            amt = rew_def["amount"]
                            r_type = rew_def["reward_type"]
                            badge = rew_def["badge_id"]

                            coins_to_award = amt if r_type == "coins" else 0
                            xp_to_award = amt if r_type == "xp" else 0

                            # Ledger record
                            recorded = database.record_season_reward_in_ledger(
                                season_id=season_id,
                                division_id=div_id,
                                user_id=u_id,
                                reward_id=rew_id,
                                reward_type=r_type,
                                coins_awarded=coins_to_award,
                                xp_awarded=xp_to_award,
                                badge_awarded=badge
                            )

                            if recorded:
                                total_rewards_distributed += 1
                                if coins_to_award > 0:
                                    database.add_coins(u_id, coins_to_award, tx_type="season_reward", ref_id=f"season_{season_id}_{rew_id}")
                                    coins_distributed_total += coins_to_award
                                if xp_to_award > 0:
                                    database.add_user_xp(u_id, xp_to_award)
                                    xp_distributed_total += xp_to_award

                        # 5. Unlock seasonal achievements
                        if is_active:
                            if rank == 1:
                                database.unlock_achievement(u_id, "ACH_SEASON_CHAMPION")
                            if rank <= 10:
                                database.unlock_achievement(u_id, "ACH_SEASON_TOP_10")
                            if prom_status == "PROMOTED":
                                database.unlock_achievement(u_id, "ACH_PROMOTED")

                # 6. Finish season
                database.finish_season(season_id, actor_user_id=actor_id)

                # Invalidate cache
                invalidate_leaderboard_cache(season_id=season_id)

                # Log admin action
                database.log_admin_action(
                    admin_id=actor_id or 0,
                    action="finalize_season",
                    target_type="season",
                    target_id=season_id,
                    new_value=json.dumps({
                        "snapshots": total_snapshots,
                        "rewards": total_rewards_distributed,
                        "coins": coins_distributed_total,
                        "xp": xp_distributed_total
                    }),
                    season_id=season_id
                )

                logger.info(f"🏆 Season #{season_id} finalized successfully: {total_snapshots} snapshots, {total_rewards_distributed} rewards.")
                return True, f"Сезон #{season_id} успешно завершен и зафиксирован.", {
                    "season_id": season_id,
                    "snapshots_created": total_snapshots,
                    "rewards_distributed": total_rewards_distributed,
                    "coins_awarded": coins_distributed_total,
                    "xp_awarded": xp_distributed_total
                }

    @classmethod
    def recalculate_competitive_stats_for_match(cls, match_id: int) -> dict[str, Any]:
        """
        Safely re-evaluate competitive player stats, rankings, and streaks
        after an administrative match score correction without altering financial payouts.
        """
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT season_id, division_id FROM matches WHERE id = ?", (match_id,))
            m_row = cursor.fetchone()
            if not m_row:
                return {"recalculated": False, "reason": "Match not found"}

            s_id = m_row["season_id"] or 1
            d_id = m_row["division_id"] or 1

            # Fetch all user bets tied to this match
            cursor.execute("""
                SELECT DISTINCT ub.user_id
                FROM bet_items bi
                JOIN user_bets ub ON bi.bet_id = ub.id
                WHERE bi.match_id = ?
            """, (match_id,))
            users = [r["user_id"] for r in cursor.fetchall()]

            for u_id in users:
                cursor.execute("""
                    SELECT 
                        COUNT(id) as total_bets,
                        SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) as wins,
                        SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) as losses,
                        SUM(CASE WHEN status = 'refunded' THEN 1 ELSE 0 END) as voids,
                        SUM(amount) as stake,
                        SUM(CASE WHEN status = 'won' THEN actual_payout ELSE 0 END) as payout
                    FROM user_bets
                    WHERE user_id = ? AND status IN ('won', 'lost', 'refunded')
                """, (u_id,))
                agg = cursor.fetchone()

                wins = agg["wins"] or 0
                losses = agg["losses"] or 0
                voids = agg["voids"] or 0
                settled = wins + losses
                stake = agg["stake"] or 0
                payout = agg["payout"] or 0

                win_rate = round((wins / max(1, settled)) * 100, 1) if settled > 0 else 0.0
                roi = round(((payout - stake) / max(1, stake)) * 100, 1) if stake > 0 else 0.0

                rules = database.get_season_rules(s_id, d_id)
                min_b = rules.get("min_bets_qualification", 5)
                status = "ACTIVE" if settled >= min_b else "QUALIFYING"

                database.update_season_player_stats(
                    user_id=u_id,
                    season_id=s_id,
                    division_id=d_id,
                    settled_bets=settled,
                    wins=wins,
                    losses=losses,
                    voids=voids,
                    win_rate=win_rate,
                    roi=roi,
                    status=status
                )

            invalidate_leaderboard_cache(season_id=s_id, division_id=d_id)
            return {"recalculated": True, "users_updated": len(users)}
