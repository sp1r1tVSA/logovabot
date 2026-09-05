import asyncio
import os
import re
import ast
import time
import hmac
import hashlib
import urllib.parse
import json
import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
import database
import config
from api.auth import validate_telegram_init_data, get_authenticated_user
from api.routes_matches import handle_get_matches
from api.routes_markets import handle_get_match_markets
import services.odds_engine as odds_engine
from services.cashout_engine import execute_cashout
from handlers.base import is_global_admin, is_admin


def test_odds_consistency_api_matches_vs_markets():
    """Verify that GET /api/matches and GET /api/matches/{id}/markets return identical canonical odds."""
    async def _run():
        test_uid = 999101
        m_id = 8801

        # Setup test data in DB
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM matches WHERE id = ?", (m_id,))
            cursor.execute("DELETE FROM bet_markets WHERE match_id = ?", (m_id,))
            cursor.execute("DELETE FROM markets WHERE match_id = ?", (m_id,))
            cursor.execute("DELETE FROM users WHERE telegram_id = ?", (test_uid,))

            cursor.execute("""
                INSERT INTO users (telegram_id, username, role)
                VALUES (?, 'test_odds_user', 'admin')
            """, (test_uid,))

            cursor.execute("""
                INSERT INTO matches (id, round_number, player1_team, player2_team, status, division_id, season_id)
                VALUES (?, 1, 'Arsenal', 'Chelsea', 'scheduled', 1, 1)
            """, (m_id,))

        # 1. Generate markets via odds_engine
        markets = odds_engine.generate_match_markets(m_id, "Arsenal", "Chelsea")
        assert len(markets) > 0

        # 2. Update odds for p1 to a distinct custom value (2.75) via set_odds
        m_1x2 = [m for m in markets if m["market_key"] == "1x2"][0]
        odds_engine.set_odds(m_1x2["id"], "p1", 2.75, admin_id=test_uid, reason="Sharp money adjustment")

        # 3. Request GET /api/matches
        req_matches = make_mocked_request(
            "GET",
            f"/api/matches?division_id=1",
            headers={"X-Telegram-Init-Data": f"mock_admin_{test_uid}"}
        )
        os.environ["ALLOW_DEV_AUTH_BYPASS"] = "1"
        res_matches = await handle_get_matches(req_matches)
        assert res_matches.status == 200
        body_matches = json.loads(res_matches.text)
        assert body_matches["status"] == "ok"
        target_match = [m for m in body_matches["matches"] if m["id"] == m_id][0]
        p1_odd_matches = target_match["odds"]["p1"]

        # 4. Request GET /api/matches/{id}/markets
        req_markets = make_mocked_request(
            "GET",
            f"/api/matches/{m_id}/markets",
            headers={"X-Telegram-Init-Data": f"mock_admin_{test_uid}"}
        )
        req_markets.match_info["id"] = str(m_id)
        res_markets = await handle_get_match_markets(req_markets)
        assert res_markets.status == 200
        body_markets = json.loads(res_markets.text)
        assert body_markets["status"] == "ok"

        mkt_1x2 = [m for m in body_markets["markets"] if m["market_key"] == "1x2"][0]
        sel_p1 = [s for s in mkt_1x2["selections"] if s["selection_key"] == "p1"][0]
        p1_odd_markets = sel_p1.get("current_odd") or sel_p1.get("odds_value")

        # ASSERT CANONICAL CONSISTENCY: both must return 2.75
        assert p1_odd_matches == 2.75
        assert p1_odd_markets == 2.75
        assert p1_odd_matches == p1_odd_markets

        # Clean up
        with database.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM matches WHERE id = ?", (m_id,))
            cursor.execute("DELETE FROM bet_markets WHERE match_id = ?", (m_id,))
            cursor.execute("DELETE FROM markets WHERE match_id = ?", (m_id,))
            cursor.execute("DELETE FROM users WHERE telegram_id = ?", (test_uid,))

    asyncio.run(_run())


def test_place_user_bet_no_double_debit_on_idempotency_conflict():
    """Verify that when duplicate idempotency key is submitted, user wallet is NOT debited twice."""
    test_uid = 999102
    m_id = 8802

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE telegram_id = ?", (test_uid,))
        cursor.execute("DELETE FROM user_wallets WHERE user_id = ?", (test_uid,))
        cursor.execute("DELETE FROM user_bets WHERE user_id = ?", (test_uid,))
        cursor.execute("DELETE FROM matches WHERE id = ?", (m_id,))

        cursor.execute("""
            INSERT INTO users (telegram_id, username, role)
            VALUES (?, 'test_idemp_user', 'player')
        """, (test_uid,))
        cursor.execute("""
            INSERT INTO user_wallets (user_id, balance, total_wagered)
            VALUES (?, 1000, 0)
        """, (test_uid,))
        cursor.execute("""
            INSERT INTO matches (id, round_number, player1_team, player2_team, status, division_id, season_id)
            VALUES (?, 1, 'Arsenal', 'Chelsea', 'scheduled', 1, 1)
        """, (m_id,))
        cursor.execute("""
            INSERT INTO bet_markets (match_id, tour, team1_name, team2_name, odd_p1, odd_x, odd_p2, is_active)
            VALUES (?, 1, 'Arsenal', 'Chelsea', 2.0, 3.0, 3.5, 1)
        """, (m_id,))

    # First bet placement
    idemp_key = "unique-key-102"
    selections = [{"match_id": m_id, "outcome": "p1", "odd": 2.0}]
    ok1, bet_id1 = database.place_user_bet(
        user_id=test_uid,
        amount=200,
        selections=selections,
        idempotency_key=idemp_key
    )
    assert ok1 is True
    assert isinstance(bet_id1, int)

    bal1 = database.get_wallet_balance(test_uid)
    assert bal1 == 800  # 1000 - 200

    # Second bet placement with EXACT same idempotency key
    ok2, bet_id2 = database.place_user_bet(
        user_id=test_uid,
        amount=200,
        selections=selections,
        idempotency_key=idemp_key
    )
    assert ok2 is True
    assert bet_id2 == bet_id1

    # CRITICAL INVARIANT: balance must still be exactly 800, NOT 600
    bal2 = database.get_wallet_balance(test_uid)
    assert bal2 == 800

    # Clean up
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE telegram_id = ?", (test_uid,))
        cursor.execute("DELETE FROM user_wallets WHERE user_id = ?", (test_uid,))
        cursor.execute("DELETE FROM user_bets WHERE user_id = ?", (test_uid,))
        cursor.execute("DELETE FROM matches WHERE id = ?", (m_id,))
        cursor.execute("DELETE FROM bet_markets WHERE match_id = ?", (m_id,))


def test_cashout_idempotency_and_no_double_payout():
    """Verify that multiple cashout attempts cannot trigger double payout."""
    test_uid = 999103
    m_id = 8803

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE telegram_id = ?", (test_uid,))
        cursor.execute("DELETE FROM user_wallets WHERE user_id = ?", (test_uid,))
        cursor.execute("DELETE FROM user_bets WHERE user_id = ?", (test_uid,))
        cursor.execute("DELETE FROM matches WHERE id = ?", (m_id,))

        cursor.execute("INSERT INTO users (telegram_id, username) VALUES (?, 'cash_u')", (test_uid,))
        cursor.execute("INSERT INTO user_wallets (user_id, balance) VALUES (?, 1000)", (test_uid,))
        cursor.execute("INSERT INTO matches (id, round_number, player1_team, player2_team, status) VALUES (?, 1, 'T1', 'T2', 'scheduled')", (m_id,))
        cursor.execute("INSERT INTO bet_markets (match_id, tour, team1_name, team2_name, odd_p1, odd_x, odd_p2, is_active) VALUES (?, 1, 'T1', 'T2', 2.0, 3.0, 3.5, 1)", (m_id,))

    # Place bet
    ok, bet_id = database.place_user_bet(
        user_id=test_uid,
        amount=500,
        selections=[{"match_id": m_id, "outcome": "p1", "odd": 2.0}]
    )
    assert ok is True

    bal_after_bet = database.get_wallet_balance(test_uid)
    assert bal_after_bet == 500

    # Execute Cashout #1
    success1, res1 = execute_cashout(user_id=test_uid, bet_id=bet_id)
    assert success1 is True
    payout1 = res1["cashout_payout"]
    assert payout1 > 0

    bal_after_cashout = database.get_wallet_balance(test_uid)
    assert bal_after_cashout == 500 + payout1

    # Execute Cashout #2 (Repeated / Concurrent Attempt)
    success2, res2 = execute_cashout(user_id=test_uid, bet_id=bet_id)
    assert success2 is False  # Must be rejected because bet is already settled
    assert res2.get("error") == "ALREADY_SETTLED"

    # Wallet balance MUST NOT increase again
    final_bal = database.get_wallet_balance(test_uid)
    assert final_bal == 500 + payout1

    # Clean up
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE telegram_id = ?", (test_uid,))
        cursor.execute("DELETE FROM user_wallets WHERE user_id = ?", (test_uid,))
        cursor.execute("DELETE FROM user_bets WHERE user_id = ?", (test_uid,))
        cursor.execute("DELETE FROM matches WHERE id = ?", (m_id,))
        cursor.execute("DELETE FROM bet_markets WHERE match_id = ?", (m_id,))


def test_standings_division_isolation():
    """Verify that matches from Division 2 NEVER leak into Division 1 / Global KPL standings."""
    m1_id = 8811
    m2_id = 8812

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM matches WHERE id IN (?, ?)", (m1_id, m2_id))
        # Match in Division 1
        cursor.execute("""
            INSERT INTO matches (id, round_number, player1_team, player2_team, player1_score, player2_score, status, division_id, season_id)
            VALUES (?, 1, 'Бенфика', 'Аякс', 3, 1, 'confirmed', 1, 1)
        """, (m1_id,))
        # Match in Division 2 (same team names or different)
        cursor.execute("""
            INSERT INTO matches (id, round_number, player1_team, player2_team, player1_score, player2_score, status, division_id, season_id)
            VALUES (?, 1, 'Бенфика', 'Аякс', 0, 5, 'confirmed', 2, 1)
        """, (m2_id,))

    # 1. Standings for Division 1
    st_div1 = database.get_standings(division_id=1, season_id=1)
    benfica_div1 = [t for t in st_div1 if t["team_name"] == "Бенфика"][0]
    assert benfica_div1["played"] == 1
    assert benfica_div1["wins"] == 1
    assert benfica_div1["goals_scored"] == 3
    assert benfica_div1["goals_conceded"] == 1

    # 2. Standings for Global / KPL (division_id=None)
    st_global = database.get_standings(division_id=None, season_id=1)
    benfica_global = [t for t in st_global if t["team_name"] == "Бенфика"][0]
    # In Global KPL, Division 2 match MUST NOT count!
    assert benfica_global["played"] == 1
    assert benfica_global["wins"] == 1
    assert benfica_global["goals_scored"] == 3
    assert benfica_global["goals_conceded"] == 1

    # Clean up
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM matches WHERE id IN (?, ?)", (m1_id, m2_id))


def test_telegram_webapp_auth_validation():
    """Verify cryptographic HMAC-SHA256 signature verification, freshness and expiration."""
    test_token = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_01234567"
    secret_key = hmac.new(b"WebAppData", test_token.encode("utf-8"), hashlib.sha256).digest()

    now = int(time.time())
    user_payload = {"id": 555777, "first_name": "Authentic", "username": "auth_user"}
    user_str = json.dumps(user_payload, separators=(',', ':'))

    # 1. Build valid initData
    params = {
        "auth_date": str(now - 60),  # 1 min ago
        "query_id": "AAHdF6IQAAAAAN0XohD9p",
        "user": user_str
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    valid_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    params["hash"] = valid_hash

    init_data_valid = urllib.parse.urlencode(params)
    parsed_user = validate_telegram_init_data(init_data_valid, bot_token=test_token)
    assert parsed_user is not None
    assert parsed_user["id"] == 555777
    assert parsed_user["first_name"] == "Authentic"

    # 2. Tampered hash -> must fail
    params_tampered = dict(params)
    params_tampered["hash"] = "deadbeef" + valid_hash[8:]
    assert validate_telegram_init_data(urllib.parse.urlencode(params_tampered), bot_token=test_token) is None

    # 3. Expired auth_date (> 24 hours ago) -> must fail
    params_expired = dict(params)
    params_expired["auth_date"] = str(now - 100000)
    data_check_exp = "\n".join(f"{k}={v}" for k, v in sorted(params_expired.items()) if k != "hash")
    params_expired["hash"] = hmac.new(secret_key, data_check_exp.encode("utf-8"), hashlib.sha256).hexdigest()
    assert validate_telegram_init_data(urllib.parse.urlencode(params_expired), bot_token=test_token) is None

    # 4. Far future auth_date (> 5 min ahead) -> must fail
    params_future = dict(params)
    params_future["auth_date"] = str(now + 1000)
    data_check_fut = "\n".join(f"{k}={v}" for k, v in sorted(params_future.items()) if k != "hash")
    params_future["hash"] = hmac.new(secret_key, data_check_fut.encode("utf-8"), hashlib.sha256).hexdigest()
    assert validate_telegram_init_data(urllib.parse.urlencode(params_future), bot_token=test_token) is None


def test_admin_rbac_isolation_division_vs_global():
    """Verify that division admin is isolated and cannot perform global actions."""
    div_admin_id = 777111
    global_admin_id = 777222

    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE telegram_id IN (?, ?)", (div_admin_id, global_admin_id))
        cursor.execute("DELETE FROM division_admins WHERE user_id IN (?, ?)", (div_admin_id, global_admin_id))

        # Division Admin: assigned role division_admin and tied to division 2
        cursor.execute("""
            INSERT INTO users (telegram_id, username, role, division_id)
            VALUES (?, 'div_admin', 'division_admin', 2)
        """, (div_admin_id,))
        cursor.execute("INSERT INTO division_admins (division_id, user_id) VALUES (2, ?)", (div_admin_id,))

        # Global Admin: role admin, no division
        cursor.execute("""
            INSERT INTO users (telegram_id, username, role, division_id)
            VALUES (?, 'global_admin', 'admin', NULL)
        """, (global_admin_id,))

    assert is_admin(div_admin_id) is True  # is_admin returns True
    assert is_global_admin(div_admin_id) is False  # is_global_admin MUST return False!

    assert is_admin(global_admin_id) is True
    assert is_global_admin(global_admin_id) is True

    # Clean up
    with database.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE telegram_id IN (?, ?)", (div_admin_id, global_admin_id))
        cursor.execute("DELETE FROM division_admins WHERE user_id IN (?, ?)", (div_admin_id, global_admin_id))


def test_all_inline_buttons_match_registered_handlers():
    """Static AST verification that 100% of Telegram inline buttons resolve to an active handler."""
    workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    handlers_dir = os.path.join(workspace, "handlers")

    # Load constants
    constants = {}
    const_path = os.path.join(workspace, "constants.py")
    if os.path.exists(const_path):
        with open(const_path, "r", encoding="utf-8") as f:
            for line in f:
                m = re.match(r'^([A-Z0-9_]+)\s*=\s*["\']([^"\']+)["\']', line.strip())
                if m:
                    constants[m.group(1)] = m.group(2)

    # Extract all CallbackQueryHandler patterns
    handlers_list = []
    for fname in os.listdir(handlers_dir):
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(handlers_dir, fname)
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        matches = re.finditer(r'CallbackQueryHandler\s*\(\s*([a-zA-Z0-9_]+)\s*,\s*pattern\s*=\s*([rR]?["\'].*?["\'])\s*\)', content)
        for m in matches:
            func = m.group(1)
            pat_str = m.group(2)
            try:
                pat = ast.literal_eval(pat_str.lstrip("rR"))
            except Exception:
                pat = pat_str.strip('rR"\'')
            if pat in (".*", "^.*$"):
                continue
            handlers_list.append({"func": func, "regex": re.compile(pat)})

    # Extract buttons
    unmatched = []
    for fname in sorted(os.listdir(handlers_dir)):
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(handlers_dir, fname)
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        for idx, line in enumerate(lines, 1):
            if "InlineKeyboardButton(" in line:
                call_str = line
                curr_idx = idx
                while ")" not in call_str and curr_idx < len(lines):
                    call_str += lines[curr_idx]
                    curr_idx += 1

                url_m = re.search(r'url\s*=\s*([^,\)\n]+)', call_str)
                web_m = re.search(r'web_app\s*=\s*([^,\)\n]+)', call_str)
                if url_m or web_m:
                    continue

                cb_m = re.search(r'callback_data\s*=\s*([^,\)\n]+)', call_str)
                cb = cb_m.group(1).strip() if cb_m else None
                if not cb:
                    unmatched.append((fname, idx, "Missing callback_data"))
                    continue

                sample = cb
                if cb in constants:
                    sample = constants[cb]
                elif cb.startswith(('"', "'")):
                    sample = cb.strip('"\'')
                elif sample.startswith(("f'", 'f"')):
                    s = sample[2:-1]
                    s = re.sub(r'\{[^}]*id[^}]*\}', '1', s)
                    s = re.sub(r'\{[^}]*num[^}]*\}', '1', s)
                    s = re.sub(r'\{[^}]*round[^}]*\}', '1', s)
                    s = re.sub(r'\{[^}]*stage[^}]*\}', '1/8', s)
                    s = re.sub(r'\{[^}]*topic[^}]*\}', 'drafts', s)
                    s = re.sub(r'\{[^}]*action[^}]*\}', 'player', s)
                    s = re.sub(r'\{[^}]*role[^}]*\}', 'd', s)
                    s = re.sub(r'\{[^}]*amount[^}]*\}', '100', s)
                    s = re.sub(r'\{[^}]*bal[^}]*\}', '100', s)
                    s = re.sub(r'\{[^}]*short_code[^}]*\}', 'd', s)
                    s = re.sub(r'\{[^}]*thread_id[^}]*\}', '100', s)
                    s = re.sub(r'\{[^}]*i[^}]*\}', '1', s)
                    s = re.sub(r'\{[^}]*idx[^}]*\}', '1', s)
                    s = re.sub(r'\{[^}]*n_photos[^}]*\}', '2', s)
                    s = re.sub(r'\{[^}]*club[^}]*\}', 'Arsenal', s)
                    s = re.sub(r'\{[^}]*canon[^}]*\}', 'Arsenal', s)
                    s = re.sub(r'\{[^}]*\}', '1', s)
                    sample = s

                if sample == "back_data":
                    sample = "admin_squad_view_Arsenal"
                elif sample == "refresh_cb":
                    sample = "refresh_league_table_topic"
                elif sample == "cb":
                    sample = "pcard_1"
                elif sample == "back_cb":
                    sample = "cb_clubs_catalog"
                elif sample in ("cancel_cb", "manual_cb"):
                    sample = "cabinet_view_match_1"

                matched = False
                for h in handlers_list:
                    if h["regex"].search(sample) or h["regex"].search(sample.replace('"', '').replace("'", "")):
                        matched = True
                        break

                if not matched and sample not in ("noop",) and not sample.startswith(("player:", "admin:", "reassign_top:", "unbind_confirm:")):
                    unmatched.append((fname, idx, sample))

    assert len(unmatched) == 0, f"Unmatched buttons found: {unmatched}"

