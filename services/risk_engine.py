"""
services/risk_engine.py

Logovo.bet — Risk Engine & Central Bet Validation Service.
Determines bet eligibility, exposure ceilings, and user/market stake limits.

Strict Invariants:
- AI is strictly read-only; Risk Engine determines financial eligibility.
- Betting Engine executes only bets accepted by Risk Engine.
- Wallet balances are verified atomically.
- Returns ALLOW, REJECT, or LIMITED with canonical reason codes.
"""

import math
import logging
from dataclasses import dataclass, field
from typing import Any, Optional
import database
from services.betting_limits import BettingLimitsService
from services.exposure_service import get_market_exposure
from services.risk_alerts import create_risk_alert

logger = logging.getLogger(__name__)


@dataclass
class RiskDecision:
    decision: str  # "ALLOW", "REJECT", "LIMITED"
    allowed: bool
    reason: Optional[str] = None
    message: Optional[str] = None
    max_allowed_stake: Optional[int] = None
    details: dict[str, Any] = field(default_factory=dict)


class RiskEngine:
    """Evaluates bet proposals against centralized risk policies and exposure boundaries."""

    @classmethod
    def evaluate_bet(
        cls,
        user_id: int,
        amount: int,
        selections: list[dict[str, Any]],
        division_id: Optional[int] = None,
        season_id: Optional[int] = None
    ) -> RiskDecision:
        """
        Evaluate full risk profile of a bet coupon before execution.
        Returns RiskDecision(decision, allowed, reason, message, max_allowed_stake).
        """
        # 1. Structure Check
        if not selections or not isinstance(selections, list):
            return RiskDecision(
                decision="REJECT",
                allowed=False,
                reason="INVALID_MARKET",
                message="Купон не содержит выбранных исходов."
            )

        # 2. Centralized Limits Check
        limits = BettingLimitsService.get_user_effective_limits(user_id, division_id=division_id)

        # Minimum Stake Check
        if amount < limits["min_bet"]:
            return RiskDecision(
                decision="REJECT",
                allowed=False,
                reason="MIN_STAKE",
                message=f"Минимальная сумма ставки — {limits['min_bet']} 🪙.",
                details={"min_bet": limits["min_bet"], "amount": amount}
            )

        # Maximum Stake Check
        if amount > limits["max_bet"]:
            return RiskDecision(
                decision="LIMITED",
                allowed=False,
                reason="MAX_STAKE",
                message=f"Сумма ставки превышает максимум {limits['max_bet']:,} 🪙.",
                max_allowed_stake=limits["max_bet"],
                details={"max_bet": limits["max_bet"], "amount": amount}
            )

        # 3. User Wallet Balance Check
        wallet = database.get_or_create_wallet(user_id)
        current_balance = wallet.get("balance", 0)
        if current_balance < amount:
            return RiskDecision(
                decision="REJECT",
                allowed=False,
                reason="INSUFFICIENT_BALANCE",
                message=f"Недостаточно монет на балансе ({current_balance:,} 🪙).",
                details={"balance": current_balance, "required": amount}
            )

        # 4. Rapid Betting Anomaly Protection (Rate Limiting per User)
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) as recent_count
                FROM user_bets
                WHERE user_id = ? AND created_at >= datetime('now', '-60 seconds')
            """, (user_id,))
            recent_count = cursor.fetchone()["recent_count"]
            if recent_count >= 15:
                create_risk_alert(
                    alert_type="RAPID_BETTING",
                    severity="high",
                    message=f"Пользователь #{user_id} отправил {recent_count} ставок за 60 секунд",
                    division_id=division_id,
                    details={"user_id": user_id, "recent_count": recent_count}
                )
                return RiskDecision(
                    decision="REJECT",
                    allowed=False,
                    reason="RISK_LIMIT",
                    message="Слишком много запросов на ставки. Подождите минуту."
                )

            # 5. User Daily Staking Limit Check
            cursor.execute("""
                SELECT COALESCE(SUM(amount), 0) as today_staked
                FROM user_bets
                WHERE user_id = ? AND date(created_at) = date('now') AND status != 'refunded'
            """, (user_id,))
            today_staked = int(cursor.fetchone()["today_staked"])
            if today_staked + amount > limits["max_daily_stake"]:
                remaining_daily = max(0, limits["max_daily_stake"] - today_staked)
                if remaining_daily < limits["min_bet"]:
                    return RiskDecision(
                        decision="REJECT",
                        allowed=False,
                        reason="DAILY_LIMIT",
                        message=f"Превышен дневной лимит ставок ({limits['max_daily_stake']:,} 🪙).",
                        details={"max_daily_stake": limits["max_daily_stake"], "today_staked": today_staked}
                    )
                return RiskDecision(
                    decision="LIMITED",
                    allowed=False,
                    reason="DAILY_LIMIT",
                    message=f"Сумма превышает остаток дневного лимита ({remaining_daily:,} 🪙).",
                    max_allowed_stake=remaining_daily,
                    details={"max_daily_stake": limits["max_daily_stake"], "today_staked": today_staked, "remaining": remaining_daily}
                )

            # 6. Selections Validation (Market State, Selection State, Odds Validity & Freshness)
            total_odd = 1.0
            for s in selections:
                m_id = s.get("match_id")
                out_type = s.get("outcome") or s.get("selection_key")
                mkt_id = s.get("market_id")
                sel_id = s.get("selection_id")

                if not m_id or not out_type:
                    return RiskDecision(
                        decision="REJECT",
                        allowed=False,
                        reason="INVALID_MARKET",
                        message="Некорректная структура исхода в купоне."
                    )

                # Validate match status
                cursor.execute("SELECT status, division_id, season_id, round_number FROM matches WHERE id = ?", (m_id,))
                match_row = cursor.fetchone()
                if not match_row:
                    return RiskDecision(
                        decision="REJECT",
                        allowed=False,
                        reason="INVALID_MARKET",
                        message=f"Матч #{m_id} не найден."
                    )
                if match_row["status"] not in ("scheduled", "pending", "live", "open"):
                    return RiskDecision(
                        decision="REJECT",
                        allowed=False,
                        reason="INVALID_MARKET",
                        message=f"Матч #{m_id} уже сыгран или завершен."
                    )

                # Check round deadline and is_open
                r_num = match_row["round_number"] if "round_number" in match_row.keys() else None
                m_div_id = match_row["division_id"] if "division_id" in match_row.keys() and match_row["division_id"] is not None else 1
                if r_num:
                    cursor.execute("SELECT is_open, deadline FROM rounds WHERE division_id = ? AND round_number = ?", (m_div_id, r_num))
                    r_row = cursor.fetchone()
                    if not r_row:
                        cursor.execute("SELECT is_open, deadline FROM rounds WHERE round_number = ? ORDER BY is_open DESC, id DESC LIMIT 1", (r_num,))
                        r_row = cursor.fetchone()
                    if r_row:
                        if not r_row["is_open"]:
                            return RiskDecision(
                                decision="REJECT",
                                allowed=False,
                                reason="MARKET_SUSPENDED",
                                message=f"Приём прогнозов на Тур {r_num} закрыт."
                            )
                        if r_row["deadline"]:
                            raw_dl = str(r_row["deadline"]).strip()
                            dl_dt = None
                            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                                try:
                                    dl_dt = datetime.datetime.strptime(raw_dl[:19], fmt)
                                    break
                                except ValueError:
                                    pass
                            if dl_dt and datetime.datetime.now() > dl_dt:
                                return RiskDecision(
                                    decision="REJECT",
                                    allowed=False,
                                    reason="MARKET_SUSPENDED",
                                    message=f"Дедлайн для прогнозов на Тур {r_num} истек."
                                )

                # Fetch market & selection records
                odd_val = None
                mkt_status = None
                sel_status = None
                odds_updated_at = None

                if sel_id and mkt_id:
                    cursor.execute("""
                        SELECT ms.odds_value, ms.status as sel_status, m.status as mkt_status, ms.updated_at
                        FROM market_selections ms
                        JOIN markets m ON ms.market_id = m.id
                        WHERE ms.id = ? AND ms.market_id = ?
                    """, (sel_id, mkt_id))
                    row = cursor.fetchone()
                    if row:
                        odd_val = row["odds_value"]
                        mkt_status = row["mkt_status"]
                        sel_status = row["sel_status"]
                        odds_updated_at = row["updated_at"]

                if odd_val is None:
                    cursor.execute("""
                        SELECT ms.id as sel_id, ms.market_id, ms.odds_value, ms.status as sel_status, 
                               m.status as mkt_status, ms.updated_at
                        FROM market_selections ms
                        JOIN markets m ON ms.market_id = m.id
                        WHERE m.match_id = ? AND ms.selection_key = ?
                    """, (m_id, out_type))
                    row = cursor.fetchone()
                    if row:
                        odd_val = row["odds_value"]
                        mkt_status = row["mkt_status"]
                        sel_status = row["sel_status"]
                        odds_updated_at = row["updated_at"]
                        mkt_id = row["market_id"]

                # Check suspension states
                if mkt_status in ("suspended", "closed", "settled") or sel_status in ("locked", "suspended", "settled"):
                    return RiskDecision(
                        decision="REJECT",
                        allowed=False,
                        reason="MARKET_SUSPENDED",
                        message=f"Рынок на исход '{out_type}' временно приостановлен или закрыт.",
                        details={"match_id": m_id, "market_id": mkt_id, "market_status": mkt_status, "sel_status": sel_status}
                    )

                # Fallback to legacy bet_markets if relational market not found
                if odd_val is None:
                    cursor.execute("SELECT * FROM bet_markets WHERE match_id = ? AND is_active = 1", (m_id,))
                    bm_row = cursor.fetchone()
                    if bm_row:
                        key_map = {
                            "p1": "odd_p1", "x": "odd_x", "p2": "odd_p2",
                            "tb25": "odd_tb25", "tm25": "odd_tm25",
                            "btts_yes": "odd_btts_yes", "btts_no": "odd_btts_no"
                        }
                        col = key_map.get(out_type)
                        if col:
                            odd_val = bm_row[col]

                if odd_val is None:
                    return RiskDecision(
                        decision="REJECT",
                        allowed=False,
                        reason="INVALID_MARKET",
                        message=f"Исход '{out_type}' на матч #{m_id} недоступен."
                    )

                try:
                    odd_float = float(odd_val)
                except (ValueError, TypeError):
                    return RiskDecision(
                        decision="REJECT",
                        allowed=False,
                        reason="INVALID_MARKET",
                        message=f"Некорректное значение коэффициента ({odd_val})."
                    )

                # Numerical sanity check
                if not math.isfinite(odd_float) or odd_float <= 1.00 or odd_float > 1000.0:
                    return RiskDecision(
                        decision="REJECT",
                        allowed=False,
                        reason="INVALID_MARKET",
                        message=f"Недопустимый коэффициент {odd_float}."
                    )

                # Odds Stale Check (if updated_at exists and match is live)
                if odds_updated_at and match_row["status"] == "live":
                    cursor.execute("""
                        SELECT (strftime('%s', 'now') - strftime('%s', ?)) as age_sec
                    """, (odds_updated_at,))
                    age_row = cursor.fetchone()
                    age_sec = age_row["age_sec"] if age_row and age_row["age_sec"] is not None else 0
                    if age_sec > 300:
                        return RiskDecision(
                            decision="REJECT",
                            allowed=False,
                            reason="ODDS_STALE",
                            message="Коэффициенты на матч устарели. Ожидается обновление линии.",
                            details={"age_seconds": age_sec, "match_id": m_id}
                        )

                total_odd *= max(1.01, odd_float)

            # 7. Maximum Payout Cap Check
            potential_win = int(round(amount * total_odd))
            if potential_win > limits["max_payout"]:
                max_allowed = int(limits["max_payout"] / max(1.01, total_odd))
                if max_allowed < limits["min_bet"]:
                    return RiskDecision(
                        decision="REJECT",
                        allowed=False,
                        reason="MAX_PAYOUT",
                        message=f"Потенциальный выигрыш превышает лимит {limits['max_payout']:,} 🪙.",
                        details={"potential_win": potential_win, "max_payout": limits["max_payout"]}
                    )
                return RiskDecision(
                    decision="LIMITED",
                    allowed=False,
                    reason="MAX_PAYOUT",
                    message=f"Потенциальный выигрыш превышает лимит {limits['max_payout']:,} 🪙. Максимальная ставка: {max_allowed:,} 🪙.",
                    max_allowed_stake=max_allowed,
                    details={"potential_win": potential_win, "max_payout": limits["max_payout"], "max_allowed_stake": max_allowed}
                )

            # 8. Market Net Exposure Limit Check
            for s in selections:
                mkt_id = s.get("market_id")
                odd_float = float(s.get("odd") or 2.0)
                if mkt_id:
                    expo = get_market_exposure(mkt_id)
                    cur_net_expo = expo.get("max_net_exposure", 0)
                    added_potential = int(amount * odd_float)
                    if cur_net_expo + added_potential > limits["market_exposure_limit"]:
                        create_risk_alert(
                            alert_type="HIGH_EXPOSURE",
                            severity="high",
                            message=f"Превышение лимита ответственности рынка #{mkt_id} ({cur_net_expo + added_potential} > {limits['market_exposure_limit']})",
                            division_id=division_id,
                            match_id=s.get("match_id"),
                            market_id=mkt_id,
                            details={"exposure": cur_net_expo + added_potential, "limit": limits["market_exposure_limit"]}
                        )
                        return RiskDecision(
                            decision="REJECT",
                            allowed=False,
                            reason="EXPOSURE_LIMIT",
                            message="Превышен допустимый лимит ставок на данный исход рынка.",
                            details={"market_id": mkt_id, "current_exposure": cur_net_expo, "limit": limits["market_exposure_limit"]}
                        )

        # All Risk Checks Passed
        return RiskDecision(
            decision="ALLOW",
            allowed=True,
            reason=None,
            details={"potential_win": potential_win, "total_odd": round(total_odd, 2)}
        )
