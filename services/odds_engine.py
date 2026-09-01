"""
services/odds_engine.py

Logovo.bet — Core Odds Engine & Market Management Service (v2.0).
Provides:
1. Dynamic odds calculation & margin adjustment.
2. Relational market & selection lifecycle (create, update, lock, suspend).
3. Immutable odds movement history & audit trails.
4. Server-authoritative odds validation with slippage/drift protection.
"""

import math
import logging
from typing import Optional
import database

logger = logging.getLogger(__name__)

BOOKMAKER_MARGIN = 1.055  # 5.5% built-in vigorish


def get_or_create_market(
    match_id: int,
    market_key: str,
    market_name: str,
    category: str = "main",
    sort_order: int = 0
) -> dict:
    """Ensure a market exists for a match and return its record."""
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM markets WHERE match_id = ? AND market_key = ?",
            (match_id, market_key)
        )
        row = cursor.fetchone()
        if row:
            return dict(row)

        cursor.execute("""
            INSERT INTO markets (match_id, market_key, market_name, category, status, sort_order)
            VALUES (?, ?, ?, ?, 'open', ?)
        """, (match_id, market_key, market_name, category, sort_order))
        m_id = cursor.lastrowid
        cursor.execute("SELECT * FROM markets WHERE id = ?", (m_id,))
        return dict(cursor.fetchone())


def get_or_create_selection(
    market_id: int,
    selection_key: str,
    selection_name: str,
    initial_odds: float
) -> dict:
    """Ensure a selection exists within a market and return its record."""
    initial_odds = round(float(initial_odds), 2)
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM market_selections WHERE market_id = ? AND selection_key = ?",
            (market_id, selection_key)
        )
        row = cursor.fetchone()
        if row:
            return dict(row)

        cursor.execute("""
            INSERT INTO market_selections (market_id, selection_key, selection_name, odds_value, odds_version, status, previous_odds)
            VALUES (?, ?, ?, ?, 1, 'active', NULL)
        """, (market_id, selection_key, selection_name, initial_odds))
        sel_id = cursor.lastrowid
        cursor.execute("SELECT * FROM market_selections WHERE id = ?", (sel_id,))
        return dict(cursor.fetchone())


def set_odds(
    market_id: int,
    selection_key: str,
    value: float,
    admin_id: Optional[int] = None,
    reason: Optional[str] = None
) -> dict:
    """
    Update odds value for a selection, increment version, track previous odds,
    and append an immutable record to odds_history.
    """
    new_value = round(float(value), 2)
    if new_value < 1.01:
        raise ValueError("Коэффициент не может быть меньше 1.01")

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM market_selections WHERE market_id = ? AND selection_key = ?",
            (market_id, selection_key)
        )
        sel = cursor.fetchone()
        if not sel:
            raise ValueError(f"Selection '{selection_key}' in market #{market_id} not found.")

        sel_id = sel["id"]
        old_value = sel["odds_value"]
        new_version = sel["odds_version"] + 1

        if abs(old_value - new_value) > 0.001:
            cursor.execute("""
                UPDATE market_selections
                SET previous_odds = odds_value,
                    odds_value = ?,
                    odds_version = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_value, new_version, sel_id))

            cursor.execute("""
                INSERT INTO odds_history (selection_id, old_value, new_value, changed_by, reason)
                VALUES (?, ?, ?, ?, ?)
            """, (sel_id, old_value, new_value, admin_id, reason or "odds_update"))

        cursor.execute("SELECT * FROM market_selections WHERE id = ?", (sel_id,))
        return dict(cursor.fetchone())


def get_current_odds(market_id: int, selection_key: str) -> float:
    """Retrieve current decimal odds for a specific selection."""
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT odds_value, status FROM market_selections WHERE market_id = ? AND selection_key = ?",
            (market_id, selection_key)
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Selection '{selection_key}' in market #{market_id} not found.")
        if row["status"] != "active":
            raise ValueError(f"Selection is currently {row['status']}.")
        return float(row["odds_value"])


def get_odds_history(
    selection_id: Optional[int] = None,
    market_id: Optional[int] = None,
    selection_key: Optional[str] = None,
    limit: int = 20
) -> list[dict]:
    """Retrieve chronological history of odds movements."""
    with database.transaction() as conn:
        cursor = conn.cursor()
        if selection_id:
            cursor.execute("""
                SELECT h.*, u.username as admin_username
                FROM odds_history h
                LEFT JOIN users u ON h.changed_by = u.telegram_id
                WHERE h.selection_id = ?
                ORDER BY h.id DESC
                LIMIT ?
            """, (selection_id, limit))
        elif market_id and selection_key:
            cursor.execute("""
                SELECT h.*, u.username as admin_username
                FROM odds_history h
                JOIN market_selections s ON h.selection_id = s.id
                LEFT JOIN users u ON h.changed_by = u.telegram_id
                WHERE s.market_id = ? AND s.selection_key = ?
                ORDER BY h.id DESC
                LIMIT ?
            """, (market_id, selection_key, limit))
        else:
            return []

        return [dict(r) for r in cursor.fetchall()]


def suspend_market(market_id: int, admin_id: Optional[int] = None, reason: Optional[str] = None) -> bool:
    """Suspend a market (e.g. during live action or VAR check)."""
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE markets SET status = 'suspended' WHERE id = ?",
            (market_id,)
        )
        if admin_id:
            cursor.execute("""
                INSERT INTO admin_audit_log (admin_id, action, target_type, target_id, new_value, reason)
                VALUES (?, 'suspend_market', 'market', ?, 'suspended', ?)
            """, (admin_id, market_id, reason))
        return cursor.rowcount > 0


def unsuspend_market(market_id: int, admin_id: Optional[int] = None) -> bool:
    """Reopen a suspended market."""
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE markets SET status = 'open' WHERE id = ?",
            (market_id,)
        )
        if admin_id:
            cursor.execute("""
                INSERT INTO admin_audit_log (admin_id, action, target_type, target_id, new_value, reason)
                VALUES (?, 'unsuspend_market', 'market', ?, 'open', 'Manual unsuspend')
            """, (admin_id, market_id))
        return cursor.rowcount > 0


def lock_selection(selection_id: int, admin_id: Optional[int] = None) -> bool:
    """Lock a specific selection outcome."""
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE market_selections SET status = 'locked' WHERE id = ?",
            (selection_id,)
        )
        return cursor.rowcount > 0


def unlock_selection(selection_id: int, admin_id: Optional[int] = None) -> bool:
    """Unlock a locked selection outcome."""
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE market_selections SET status = 'active' WHERE id = ?",
            (selection_id,)
        )
        return cursor.rowcount > 0


def validate_odds(
    market_id: int,
    selection_key: str,
    expected_odd: Optional[float] = None,
    max_drift: float = 0.05
) -> float:
    """
    Validate that market is open, selection is active, and odds have not drifted
    beyond the acceptable threshold (default 5%).
    Returns server authoritative odds.
    """
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT m.status as market_status, s.status as sel_status, s.odds_value
            FROM market_selections s
            JOIN markets m ON s.market_id = m.id
            WHERE s.market_id = ? AND s.selection_key = ?
        """, (market_id, selection_key))
        row = cursor.fetchone()

        if not row:
            raise ValueError(f"Selection '{selection_key}' in market #{market_id} does not exist.")

        if row["market_status"] != "open":
            raise ValueError(f"Рынок недоступен (статус: {row['market_status']}).")

        if row["sel_status"] != "active":
            raise ValueError(f"Исход заблокирован (статус: {row['sel_status']}).")

        current_odd = float(row["odds_value"])

        if expected_odd is not None:
            drift = abs(current_odd - expected_odd)
            if drift > max_drift:
                raise ValueError(
                    f"Коэффициент изменился: было {expected_odd}, стало {current_odd}. Обновите купон."
                )

        return current_odd


def get_match_markets(match_id: int) -> list[dict]:
    """Retrieve all markets and selections for a match formatted for API & UI."""
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, match_id, market_key, market_name, category, status, sort_order
            FROM markets
            WHERE match_id = ?
            ORDER BY sort_order ASC, id ASC
        """, (match_id,))
        market_rows = [dict(r) for r in cursor.fetchall()]

        for m in market_rows:
            cursor.execute("""
                SELECT id, market_id, selection_key, selection_name, odds_value, 
                       odds_version, status, previous_odds, updated_at
                FROM market_selections
                WHERE market_id = ?
                ORDER BY id ASC
            """, (m["id"],))
            m["selections"] = [dict(s) for s in cursor.fetchall()]

        return market_rows


def generate_match_markets(
    match_id: int,
    team1_name: str,
    team2_name: str,
    standings: Optional[list[dict]] = None
) -> list[dict]:
    """
    Generate Tier 1 standard markets for a match:
    - 1X2 (Match Winner)
    - Double Chance (1X, 12, X2)
    - Total Goals (Over/Under 1.5, 2.5, 3.5)
    - Individual Totals (Team 1 & Team 2 Over/Under 1.5)
    - Both Teams to Score (Yes/No)
    - Handicap (-1.5 / +1.5)
    """
    if standings is None:
        try:
            standings = database.get_standings()
        except Exception:
            standings = []

    from services.betting_engine import _get_team_strength_score

    s1 = _get_team_strength_score(standings, team1_name)
    s2 = _get_team_strength_score(standings, team2_name)

    # 1. Base win probabilities
    s1_adj = s1 * 1.05
    prob_p1_raw = s1_adj / (s1_adj + s2)
    prob_p2_raw = s2 / (s1_adj + s2)
    closeness = 1.0 - abs(prob_p1_raw - prob_p2_raw)
    prob_x_raw = 0.26 * closeness

    tot_p = prob_p1_raw + prob_x_raw + prob_p2_raw
    p1 = prob_p1_raw / tot_p
    px = prob_x_raw / tot_p
    p2 = prob_p2_raw / tot_p

    odd_p1 = round(max(1.10, min(12.0, 1.0 / (p1 * BOOKMAKER_MARGIN))), 2)
    odd_x = round(max(2.10, min(8.0, 1.0 / (px * BOOKMAKER_MARGIN))), 2)
    odd_p2 = round(max(1.10, min(12.0, 1.0 / (p2 * BOOKMAKER_MARGIN))), 2)

    # 2. Double chance probabilities
    p_1x = p1 + px
    p_12 = p1 + p2
    p_x2 = px + p2
    odd_1x = round(max(1.05, min(5.0, 1.0 / (p_1x * BOOKMAKER_MARGIN))), 2)
    odd_12 = round(max(1.05, min(5.0, 1.0 / (p_12 * BOOKMAKER_MARGIN))), 2)
    odd_x2 = round(max(1.05, min(5.0, 1.0 / (p_x2 * BOOKMAKER_MARGIN))), 2)

    # 3. Totals calculation
    total_strength = (s1 + s2) / 2.0
    if total_strength > 12.0:
        odd_tb25, odd_tm25 = 1.55, 2.30
        odd_tb15, odd_tm15 = 1.20, 4.10
        odd_tb35, odd_tm35 = 2.45, 1.50
        odd_btts_yes, odd_btts_no = 1.60, 2.20
    elif total_strength < 8.0:
        odd_tb25, odd_tm25 = 2.05, 1.70
        odd_tb15, odd_tm15 = 1.35, 3.00
        odd_tb35, odd_tm35 = 3.20, 1.30
        odd_btts_yes, odd_btts_no = 1.85, 1.85
    else:
        odd_tb25, odd_tm25 = 1.75, 1.95
        odd_tb15, odd_tm15 = 1.25, 3.60
        odd_tb35, odd_tm35 = 2.85, 1.38
        odd_btts_yes, odd_btts_no = 1.68, 2.05

    # 4. Individual totals
    ind1_over = round(max(1.20, min(4.50, 1.85 / (p1 / max(0.1, p2)))), 2)
    ind1_under = round(max(1.20, min(4.50, 1.85 * (p1 / max(0.1, p2)))), 2)
    ind2_over = round(max(1.20, min(4.50, 1.85 / (p2 / max(0.1, p1)))), 2)
    ind2_under = round(max(1.20, min(4.50, 1.85 * (p2 / max(0.1, p1)))), 2)

    # 5. Handicap (-1.5 on stronger team)
    if p1 >= p2:
        h1_minus = round(max(1.40, min(8.0, odd_p1 * 1.8)), 2)
        h2_plus = round(max(1.15, min(4.0, 1.0 / (0.85 * BOOKMAKER_MARGIN))), 2)
    else:
        h1_minus = round(max(1.15, min(4.0, 1.0 / (0.85 * BOOKMAKER_MARGIN))), 2)
        h2_plus = round(max(1.40, min(8.0, odd_p2 * 1.8)), 2)

    # Create / Update Markets
    created_markets = []

    # Market 1: 1X2
    m_1x2 = get_or_create_market(match_id, "1x2", "Исход матча", category="main", sort_order=1)
    get_or_create_selection(m_1x2["id"], "p1", f"П1 ({team1_name})", odd_p1)
    get_or_create_selection(m_1x2["id"], "x", "Ничья (X)", odd_x)
    get_or_create_selection(m_1x2["id"], "p2", f"П2 ({team2_name})", odd_p2)
    created_markets.append(m_1x2)

    # Market 2: Double Chance
    m_dc = get_or_create_market(match_id, "double_chance", "Двойной шанс", category="main", sort_order=2)
    get_or_create_selection(m_dc["id"], "1x", "1X (П1 или Х)", odd_1x)
    get_or_create_selection(m_dc["id"], "12", "12 (П1 или П2)", odd_12)
    get_or_create_selection(m_dc["id"], "x2", "X2 (Х или П2)", odd_x2)
    created_markets.append(m_dc)

    # Market 3: Total Goals
    m_tot = get_or_create_market(match_id, "total_goals", "Тотал голов", category="goals", sort_order=3)
    get_or_create_selection(m_tot["id"], "over_1.5", "Тотал больше (1.5)", odd_tb15)
    get_or_create_selection(m_tot["id"], "under_1.5", "Тотал меньше (1.5)", odd_tm15)
    get_or_create_selection(m_tot["id"], "over_2.5", "Тотал больше (2.5)", odd_tb25)
    get_or_create_selection(m_tot["id"], "under_2.5", "Тотал меньше (2.5)", odd_tm25)
    get_or_create_selection(m_tot["id"], "over_3.5", "Тотал больше (3.5)", odd_tb35)
    get_or_create_selection(m_tot["id"], "under_3.5", "Тотал меньше (3.5)", odd_tm35)
    created_markets.append(m_tot)

    # Market 4: BTTS
    m_btts = get_or_create_market(match_id, "btts", "Обе забьют", category="goals", sort_order=4)
    get_or_create_selection(m_btts["id"], "btts_yes", "Обе забьют: Да", odd_btts_yes)
    get_or_create_selection(m_btts["id"], "btts_no", "Обе забьют: Нет", odd_btts_no)
    created_markets.append(m_btts)

    # Market 5: Individual Total 1
    m_it1 = get_or_create_market(match_id, "individual_total_1", f"Инд. тотал: {team1_name}", category="goals", sort_order=5)
    get_or_create_selection(m_it1["id"], "it1_over_1.5", "ИТБ1 (1.5)", ind1_over)
    get_or_create_selection(m_it1["id"], "it1_under_1.5", "ИТМ1 (1.5)", ind1_under)
    created_markets.append(m_it1)

    # Market 6: Individual Total 2
    m_it2 = get_or_create_market(match_id, "individual_total_2", f"Инд. тотал: {team2_name}", category="goals", sort_order=6)
    get_or_create_selection(m_it2["id"], "it2_over_1.5", "ИТБ2 (1.5)", ind2_over)
    get_or_create_selection(m_it2["id"], "it2_under_1.5", "ИТМ2 (1.5)", ind2_under)
    created_markets.append(m_it2)

    # Market 7: Handicap
    m_handicap = get_or_create_market(match_id, "handicap", "Фора (1.5)", category="main", sort_order=7)
    get_or_create_selection(m_handicap["id"], "h1_minus_1.5", f"Фора 1 (-1.5)", h1_minus)
    get_or_create_selection(m_handicap["id"], "h2_plus_1.5", f"Фора 2 (+1.5)", h2_plus)
    created_markets.append(m_handicap)

    return get_match_markets(match_id)
