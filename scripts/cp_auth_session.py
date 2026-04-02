import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open Challenge Place login and save authenticated Playwright session."
    )
    parser.add_argument(
        "--url",
        default="https://challenge.place/login",
        help="Login URL to open (default: %(default)s)",
    )
    parser.add_argument(
        "--state-path",
        default="state/challenge_storage_state.json",
        help="Where to save Playwright storage state (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Max seconds to wait for manual login (default: %(default)s)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between auth checks (default: %(default)s)",
    )
    parser.add_argument(
        "--stable-checks",
        type=int,
        default=3,
        help="Consecutive positive checks before save (default: %(default)s)",
    )
    return parser.parse_args()


async def has_visible_sign_in(page) -> bool:
    locator = page.locator("a[href='/login'], a:has-text('Sign in'), button:has-text('Sign in')")
    count = await locator.count()
    for index in range(min(count, 10)):
        try:
            if await locator.nth(index).is_visible(timeout=250):
                return True
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue
    return False


async def read_local_storage(page) -> dict[str, str]:
    data = await page.evaluate(
        """
        () => {
            const out = {};
            for (let i = 0; i < localStorage.length; i += 1) {
                const key = localStorage.key(i);
                if (!key) continue;
                out[key] = localStorage.getItem(key) || "";
            }
            return out;
        }
        """
    )
    return data if isinstance(data, dict) else {}


def json_has_tokens(raw_value: str) -> bool:
    try:
        payload = json.loads(raw_value)
    except Exception:
        return False

    if not isinstance(payload, dict):
        return False

    candidates = [
        payload.get("accessToken"),
        payload.get("refreshToken"),
        payload.get("idToken"),
    ]
    manager = payload.get("stsTokenManager") or payload.get("tokenManager")
    if isinstance(manager, dict):
        candidates.extend(
            [
                manager.get("accessToken"),
                manager.get("refreshToken"),
                manager.get("idToken"),
            ]
        )

    return any(isinstance(item, str) and item.strip() for item in candidates)


async def has_auth_storage(page) -> bool:
    storage = await read_local_storage(page)
    for key, value in storage.items():
        key_lower = key.lower()
        if key.startswith("firebase:authUser:"):
            return True
        if "authuser" in key_lower and json_has_tokens(value):
            return True
        if "token" in key_lower and isinstance(value, str) and len(value.strip()) > 80:
            return True
    return False


async def has_auth_cookie(context) -> bool:
    try:
        cookies = await context.cookies("https://challenge.place")
    except Exception:
        return False

    for cookie in cookies:
        name = str(cookie.get("name", "")).lower()
        if any(part in name for part in ("session", "auth", "token")):
            value = str(cookie.get("value", "")).strip()
            if value and value.lower() not in {"true", "false", "1", "0"}:
                return True
    return False


async def is_logged_in(page) -> bool:
    current_url = page.url.lower()
    if "challenge.place" not in current_url:
        return False
    if "/login" in current_url:
        return False
    if await has_visible_sign_in(page):
        return False
    if await has_auth_storage(page):
        return True
    if await has_auth_cookie(page.context):
        return True
    return False


async def run() -> int:
    args = parse_args()
    state_path = Path(args.state_path)
    timeout_ms = max(args.timeout, 1) * 1000
    poll_ms = int(max(args.poll_interval, 0.2) * 1000)
    stable_required = max(args.stable_checks, 1)

    print("Opening browser for manual login...")
    print("1) Complete login (including 2FA/captcha) in the opened window.")
    print("2) Keep the browser open. Script will auto-detect authenticated state.")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(args.url, wait_until="domcontentloaded")

        elapsed = 0
        stable_hits = 0
        while elapsed <= timeout_ms:
            try:
                if await is_logged_in(page):
                    stable_hits += 1
                    if stable_hits >= stable_required:
                        state_path.parent.mkdir(parents=True, exist_ok=True)
                        temp_path = state_path.with_suffix(state_path.suffix + ".tmp")
                        await context.storage_state(path=str(temp_path))
                        temp_path.replace(state_path)
                        print(f"Session saved: {state_path}")
                        await browser.close()
                        return 0
                else:
                    stable_hits = 0
            except Exception:
                stable_hits = 0

            await page.wait_for_timeout(poll_ms)
            elapsed += poll_ms

        print("Login was not detected before timeout. Session was not saved.")
        await browser.close()
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
