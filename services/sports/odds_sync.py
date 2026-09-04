"""
services/sports/odds_sync.py

Logovo.bet — Provider Odds Synchronization & Validation Pipeline (Phase 8).
Connects external bookmaker odds into the existing market & odds engine.

Strict Invariants:
1. Odds must be finite, non-NaN, non-Inf, and strictly > 1.00.
2. Markets in closed, settled, or void states must never be modified.
3. Every update increments odds_version and logs immutable odds_movement.
4. AI and sports providers remain read-only; no bet settlement or wallet actions.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional

import database
from services.odds_engine import get_or_create_market, get_or_create_selection, set_odds
from services.odds_movers import detect_odds_anomalies
from services.sports.models import ProviderOdds

logger = logging.getLogger(__name__)


def validate_odd_value(value: Any) -> float:
    """
    Validate that an odds value is a finite float strictly greater than 1.00.
    Raises ValueError if invalid, NaN, or infinite.
    """
    if value is None:
        raise ValueError("Odds value cannot be None.")

    try:
        val = float(value)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid odds number format: {value}") from e

    if math.isnan(val):
        raise ValueError("Odds value cannot be NaN.")

    if math.isinf(val):
        raise ValueError("Odds value cannot be Infinity.")

    if not math.isfinite(val):
        raise ValueError("Odds value must be finite.")

    if val <= 1.00:
        raise ValueError(f"Odds value must be strictly > 1.00, got {val}.")

    if val > 1000.00:
        raise ValueError(f"Odds value exceeds maximum allowed threshold (1000.00), got {val}.")

    return round(val, 2)


def sync_provider_odds(
    match_id: int,
    odds_list: list[ProviderOdds],
    provider_name: Optional[str] = None
) -> dict[str, Any]:
    """
    Synchronize provider odds into the existing market engine for a specific match.
    Validates values, verifies market status, increments version, and logs movement.
    """
    if not odds_list:
        return {"status": "ok", "synced_count": 0, "skipped_count": 0, "errors": []}

    # Verify internal match exists and get its status
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, status, division_id, season_id FROM matches WHERE id = ?", (match_id,))
        match_row = cursor.fetchone()
        if not match_row:
            raise ValueError(f"Match #{match_id} does not exist.")

        # If match is already completed/cancelled/archived, do not sync live odds
        if match_row["status"] in ("completed", "confirmed", "cancelled", "void"):
            logger.info("Skipping odds sync for finished/closed match #%s", match_id)
            return {
                "status": "skipped",
                "reason": f"Match status is {match_row['status']}",
                "synced_count": 0,
                "skipped_count": len(odds_list)
            }

    synced_count = 0
    skipped_count = 0
    errors: list[str] = []

    market_names_map = {
        "match_result": "Исход матча (1X2)",
        "1x2": "Исход матча (1X2)",
        "both_teams_to_score": "Обе забьют",
        "btts": "Обе забьют",
        "total_goals": "Тотал голов",
        "double_chance": "Двойной шанс"
    }

    selection_names_map = {
        "home": "П1",
        "draw": "X",
        "away": "П2",
        "yes": "Да",
        "no": "Нет",
        "over": "Больше",
        "under": "Меньше",
        "1x": "1X",
        "12": "12",
        "x2": "X2"
    }

    for item in odds_list:
        entries: list[tuple[str, str, Any]] = []
        if getattr(item, "selections", None) and isinstance(item.selections, list) and len(item.selections) > 0:
            m_type = getattr(item, "market_key", None) or getattr(item, "market_type", "match_result")
            for sel in item.selections:
                s_key = sel.get("selection_key") or sel.get("key") or sel.get("name")
                s_val = sel.get("odds") or sel.get("odds_value")
                entries.append((m_type, s_key, s_val))
        else:
            m_type = getattr(item, "market_type", None) or getattr(item, "market_key", "match_result")
            s_key = getattr(item, "selection", None) or getattr(item, "selection_key", "home")
            s_val = getattr(item, "odds_value", None)
            entries.append((m_type, s_key, s_val))

        for raw_market, raw_sel, raw_odd_val in entries:
            try:
                validated_odds = validate_odd_value(raw_odd_val)
            except ValueError as err:
                logger.warning("Rejected invalid provider odd for match %s: %s", match_id, err)
                skipped_count += 1
                errors.append(str(err))
                continue

            raw_market = (raw_market or "match_result").strip().lower()
            if raw_market in ("1x2", "match_winner", "h2h"):
                canonical_market = "match_result"
            elif raw_market in ("btts", "both_teams_score"):
                canonical_market = "both_teams_to_score"
            else:
                canonical_market = raw_market

            m_name = market_names_map.get(canonical_market, canonical_market.replace("_", " ").title())
            sel_key = (raw_sel or "").strip().lower()
            sel_name = selection_names_map.get(sel_key, sel_key.upper())

            try:
                market = get_or_create_market(
                    match_id=match_id,
                    market_key=canonical_market,
                    market_name=m_name
                )

                if market.get("status") in ("closed", "settled", "void"):
                    skipped_count += 1
                    continue

                mkt_id = market["id"]

                get_or_create_selection(
                    market_id=mkt_id,
                    selection_key=sel_key,
                    selection_name=sel_name,
                    initial_odds=validated_odds
                )

                p_name = provider_name or getattr(item, "provider", "provider")
                reason_str = f"provider_sync:{p_name}"
                set_odds(
                    market_id=mkt_id,
                    selection_key=sel_key,
                    value=validated_odds,
                    admin_id=None,
                    reason=reason_str
                )
                synced_count += 1

            except Exception as ex:
                logger.error("Error syncing provider odd (%s, %s) on match %s: %s",
                             canonical_market, sel_key, match_id, ex)
                skipped_count += 1
                errors.append(str(ex))

    anomalies = []
    try:
        anomalies = detect_odds_anomalies(match_id)
    except Exception as e:
        logger.debug("Failed anomaly check during odds sync: %s", e)

    return {
        "status": "ok",
        "match_id": match_id,
        "synced_count": synced_count,
        "skipped_count": skipped_count,
        "anomalies_detected": len(anomalies),
        "errors": errors[:5]
    }
