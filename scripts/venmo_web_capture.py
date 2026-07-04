"""Capture Venmo WEB-API cookies via a human login, then test httpx viability.

The Venmo web API (account.venmo.com/api/stories) is cookie-authed and returns the
personal transaction feed. This is the fallback to the mobile API (whose password
grant is 240-locked). The session cookie is HttpOnly, so it must be captured with
SeleniumBase (not the claude-in-chrome tool, which can't read HttpOnly).

Flow: opens a stealth window -> Alex logs in by hand (passes DataDome + 2FA) ->
we read cookies -> save them -> hit /api/stories from httpx to learn whether
DataDome blocks non-browser requests (decides browser-fetch vs httpx-fetch).

    PYTHONPATH=src uv run python scripts/venmo_web_capture.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

from notion_finance_sync.browser.factory import open_session

ROOT = Path(__file__).resolve().parents[1]
SESSION_DIR = ROOT / "data" / "sessions" / "venmo"
SESSION_DIR.mkdir(parents=True, exist_ok=True)
COOKIES_FILE = SESSION_DIR / "cookies.json"
SNAP = ROOT / "data" / "snapshots" / "venmo"
SNAP.mkdir(parents=True, exist_ok=True)

HOME = "https://account.venmo.com/"
ORIGIN = "https://account.venmo.com"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)


def _external_id(sb) -> str | None:
    """Decode the logged-in user's external id from the page's idToken."""
    js = r"""
    const m = document.documentElement.outerHTML.match(/"idToken"\s*:\s*"([^"]+)"/);
    if (!m) return null;
    try { return JSON.parse(atob(m[1].split('.')[1].replace(/-/g,'+').replace(/_/g,'/'))).external_id; }
    catch(e) { return null; }
    """
    try:
        return sb.execute_script(js)
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    with open_session("venmo") as sb:
        sb.uc_open_with_reconnect(HOME, reconnect_time=6)
        sb.sleep(3)
        print("=" * 70)
        print("Log into Venmo by hand in the open window (solve captcha + 2FA).")
        print("Waiting up to 8 min for the logged-in home page…")
        print("=" * 70)

        for i in range(96):  # 8 min
            sb.sleep(5)
            url = sb.get_current_url()
            if "account.venmo.com" in url and "sign" not in url and "signin" not in url:
                if not sb.is_element_present("#email"):
                    print(f"[capture] logged in after ~{i * 5}s")
                    break
            if i % 6 == 0:
                print(f"  [waiting {i * 5}s] {url}")
        else:
            print("[capture] timed out waiting for login")
            return 1

        sb.sleep(2)
        cookies = {c["name"]: c.get("value") for c in (sb.get_cookies() or []) if c.get("name")}
        COOKIES_FILE.write_text(json.dumps(cookies, indent=2))
        ext_id = _external_id(sb)
        print(f"[capture] {len(cookies)} cookies saved; external_id={ext_id}")
        (SESSION_DIR / "external_id.txt").write_text(str(ext_id or ""))

    if not ext_id:
        print("[capture] no external_id — cannot test the feed endpoint")
        return 2

    # Test whether httpx (no browser) can use these cookies against the web API.
    csrf = cookies.get("_csrf", "")
    client = httpx.Client(
        base_url=ORIGIN,
        cookies=cookies,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
            "Referer": HOME,
            "csrf-token": csrf,
            "xsrf-token": csrf,
        },
        timeout=30,
    )
    try:
        r = client.get("/api/stories", params={"feedType": "me", "externalId": ext_id})
        print(f"[httpx] GET /api/stories -> {r.status_code} ({r.headers.get('content-type')})")
        if r.status_code == 200:
            body = r.json()
            stories = body.get("stories") or body.get("data") or []
            (SNAP / "web_stories_me.json").write_text(json.dumps(body, indent=2))
            print(f"[httpx] SUCCESS — {len(stories)} stories; saved web_stories_me.json")
            print("        => httpx works: the scraper can run headless with cookies.")
        else:
            (SNAP / "web_stories_blocked.txt").write_text(r.text[:3000])
            print("[httpx] BLOCKED — DataDome likely rejects non-browser requests.")
            print("        => scraper must fetch via the browser session instead.")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
