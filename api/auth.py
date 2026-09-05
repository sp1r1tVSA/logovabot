"""
api/auth.py

Cryptographic authentication and validation for Telegram WebApp initData.
Uses HMAC-SHA256 with the bot token according to Telegram Mini Apps specifications.
"""

import os
import hmac
import hashlib
import urllib.parse
import json
import time
import logging
import database
import config
from config import is_global_lockdown_enabled, is_lockdown_enabled
from handlers.base import is_admin, is_global_admin, is_admin_user, is_logovo_access_allowed

logger = logging.getLogger(__name__)


def validate_telegram_init_data(init_data_str: str, bot_token: str | None = None) -> dict | None:
    """
    Validate Telegram WebApp initData string using HMAC-SHA256 signature verification.
    Returns user dict if valid, None otherwise.
    """
    if not init_data_str:
        return None

    token = bot_token or config.TOKEN
    if not token:
        logger.error("Telegram bot token not configured for initData validation.")
        return None

    try:
        parsed = dict(urllib.parse.parse_qsl(init_data_str, keep_blank_values=True))
        if "hash" not in parsed:
            return None

        received_hash = parsed.pop("hash")
        
        # 1. Build data_check_string (sorted alphabetically by key)
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        
        # 2. Compute secret key: HMAC_SHA256("WebAppData", bot_token)
        secret_key = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
        
        # 3. Calculate signature
        calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        
        # 4. Compare constant-time
        if not hmac.compare_digest(calculated_hash, received_hash):
            logger.warning("Invalid initData hash signature.")
            return None

        # 5. Check freshness (auth_date must be present, positive, not future-dated, and not older than 24 hours)
        if "auth_date" not in parsed:
            logger.warning("Missing auth_date in initData.")
            return None

        try:
            auth_date = int(parsed["auth_date"])
        except (ValueError, TypeError):
            logger.warning("Invalid auth_date in initData.")
            return None

        now = time.time()
        if auth_date <= 0 or auth_date > now + 300:  # Allow max 5 min clock skew into future
            logger.warning(f"Future or non-positive initData auth_date: {auth_date} (now: {now})")
            return None

        if (now - auth_date) > 86400:  # 24 hours max
            logger.warning(f"Expired initData auth_date: {auth_date}")
            return None

        # 6. Parse user info
        user_json = parsed.get("user")
        if user_json:
            user_info = json.loads(user_json)
            return user_info
        
        return parsed
    except Exception as e:
        logger.warning(f"Failed to validate telegram initData: {e}")
        return None


def get_authenticated_user(init_data_str: str) -> dict | None:
    """
    Extract and authenticate user info from initData.
    Also handles development/lab bypass if ALLOW_DEV_AUTH_BYPASS is explicitly enabled.
    """
    user_info = validate_telegram_init_data(init_data_str)
    if not user_info:
        # Dev / Sandbox Fallback for local testing, STRICTLY gated by environment flag
        allow_dev_bypass = os.getenv("ALLOW_DEV_AUTH_BYPASS", "").strip().lower() in ("1", "true", "yes")
        if allow_dev_bypass and init_data_str and init_data_str.startswith("mock_admin_"):
            try:
                u_id = int(init_data_str.replace("mock_admin_", ""))
                if is_admin(u_id):
                    return {"id": u_id, "first_name": "Admin", "username": "admin", "is_mock": True}
            except Exception:
                pass
        return None

    return user_info


validate_telegram_data = validate_telegram_init_data


def check_user_access(user_id: int) -> bool:
    """Check if user has access to Logovo.bet."""
    if not user_id or user_id <= 0:
        return False
    if not is_logovo_access_allowed(user_id):
        return False
    if is_admin(user_id):
        return True
    try:
        flag = database.get_feature_flag("betting_market", default="public")
        return flag in ("public", "all", "enabled")
    except Exception:
        return True

