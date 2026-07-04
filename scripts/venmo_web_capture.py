"""Capture Venmo WEB-API cookies via a mostly-automated login, then test httpx.

The Venmo web API (account.venmo.com/api/stories) is cookie-authed and returns the
personal transaction feed. The session auth cookie is HttpOnly, so it must be read
with SeleniumBase get_cookies() (the claude-in-chrome tool can't). This script:

1. Opens a stealth Chrome window and AUTO-FILLS email + password (React-safe keys),
   so the human only has to solve the DataDome captcha + 2FA in that window.
2. Waits for the logged-in home page.
3. Reads cookies -> saves them -> hits /api/stories from httpx to learn whether
   DataDome blocks non-browser requests (decides browser-fetch vs httpx-fetch).

    VENMO_LOGIN_EMAIL=... VENMO_PASSWORD=... PYTHONPATH=src \
        uv run python scripts/venmo_web_capture.py

Credentials come from env (VENMO_LOGIN_EMAIL/VENMO_PASSWORD) or the vault getters.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from notion_finance_sync.browser.factory import open_session
from notion_finance_sync.config.settings import get_bank_password, get_gmail_address
from notion_finance_sync.twofa.sms import get_sms_code

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
BLOCK = ("You have been blocked", "couldn't load the security challenge")
# id.venmo.com/PayPal 2FA SMS. Sender/regex confirmed here on the first real code.
_SMS_REGEX = r"(?i)(?:venmo|paypal|verification|security|code)\D{0,80}?(\d{6})"
_OTP_INPUT = (
    "input[autocomplete='one-time-code'], input[inputmode='numeric'], "
    "input[name*='otp' i], input[name='code'], input[type='tel']"
)


def _present(sb, sel):
    try:
        return sb.is_element_present(sel)
    except Exception:  # noqa: BLE001
        return False


def _type_real(sb, sel, val):
    """React-safe input: click + real keys (send_keys), commits onChange."""
    sb.click(sel)
    sb.type(sel, val)


def _auto_login(sb, email, password) -> None:
    # Wait for the id.venmo.com email form (past any DataDome interstitial).
    for _ in range(22):
        if _present(sb, "#email"):
            break
        html = sb.get_page_source()
        if any(m in html for m in BLOCK):
            print("[capture] DataDome hard block on the form — will still let you retry by hand.")
            return
        sb.sleep(5)
    if not _present(sb, "#email"):
        print("[capture] email form didn't appear; log in fully by hand in the window.")
        return
    _type_real(sb, "#email", email)
    if _present(sb, "#btnNext"):
        sb.click("#btnNext")
        sb.sleep(4)
    pass_sel = "input[name='login_password']"
    if _present(sb, pass_sel):
        _type_real(sb, pass_sel, password)
        sb.send_keys(pass_sel, "\n")
        print("[capture] email + password submitted; handling 2FA…")
        _handle_2fa(sb)
    else:
        print("[capture] password field not found; finish the login by hand.")


_CLICK_BY_TEXT = """
const texts = arguments[0];
const els = [...document.querySelectorAll("button,[role=button],input[type=submit],a")];
for (const t of texts) {
  const b = els.find(x => ((x.textContent||x.value||'')+'').trim().toLowerCase().includes(t));
  if (b) { b.click(); return t; }
}
return null;
"""


def _dump(sb, tag):
    try:
        (SNAP / f"webcap_{tag}.html").write_text(sb.get_page_source())
        sb.save_screenshot(str(SNAP / f"webcap_{tag}.png"))
    except Exception:  # noqa: BLE001
        pass


def _handle_2fa(sb) -> None:
    """Automate the id.venmo.com SMS 2FA: remember device, send code, read it, submit."""
    # Wait for either the delivery screen (a Send/Text button) or the OTP input.
    for _ in range(18):
        sb.sleep(3)
        if sb.execute_script(f'return !!document.querySelector("{_OTP_INPUT}")'):
            break
        if sb.execute_script(
            "return /verify it|text you a code|send.{0,6}code|remember this device/i"
            ".test(document.body.innerText)"
        ):
            break
        html = sb.get_page_source()
        if any(m in html for m in BLOCK):
            print("[capture] DataDome block during 2FA.")
            _dump(sb, "2fa_block")
            return
    _dump(sb, "2fa_start")

    # Check "remember this device" so future logins skip 2FA.
    try:
        sb.execute_script(
            "const c=document.querySelector('input[type=checkbox]'); if(c&&!c.checked)c.click();"
        )
    except Exception:  # noqa: BLE001
        pass

    code_requested_at = datetime.now(tz=UTC)
    # If there's no OTP input yet, click the button that sends the SMS.
    if not sb.execute_script(f'return !!document.querySelector("{_OTP_INPUT}")'):
        clicked = sb.execute_script(
            _CLICK_BY_TEXT, ["text me", "text", "send code", "send", "continue", "next"]
        )
        print(f"[capture] send-code button: {clicked!r}")
        code_requested_at = datetime.now(tz=UTC)
        for _ in range(12):
            sb.sleep(3)
            if sb.execute_script(f'return !!document.querySelector("{_OTP_INPUT}")'):
                break

    _dump(sb, "2fa_otp_screen")
    if not sb.execute_script(f'return !!document.querySelector("{_OTP_INPUT}")'):
        print("[capture] OTP input never appeared — see webcap_2fa_*.html for selectors.")
        return

    code = get_sms_code(
        after=code_requested_at, sender_pattern="%", code_regex=_SMS_REGEX, timeout_s=150
    )
    if not code:
        print("[capture] no SMS code matched; see FINDINGS for regex tuning.")
        return
    print("[capture] OTP read from Messages; entering it")
    # Fill the OTP field via native setter (React-safe), then submit.
    sb.execute_script(
        "const el=document.querySelector(arguments[0]);"
        "const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
        "el.focus(); s.call(el, arguments[1]);"
        "el.dispatchEvent(new Event('input',{bubbles:true}));"
        "el.dispatchEvent(new Event('change',{bubbles:true}));",
        _OTP_INPUT,
        code,
    )
    sb.sleep(1)
    sb.execute_script(_CLICK_BY_TEXT, ["verify", "submit", "continue", "next", "confirm"])
    print("[capture] OTP submitted")


def _external_id(sb):
    js = r"""
    const m = document.documentElement.outerHTML.match(/"idToken"\s*:\s*"([^"]+)"/);
    if (!m) return null;
    try {
      const p = m[1].split('.')[1].replace(/-/g,'+').replace(/_/g,'/');
      return JSON.parse(atob(p)).external_id;
    } catch(e){ return null; }
    """
    try:
        return sb.execute_script(js)
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    email = os.environ.get("VENMO_LOGIN_EMAIL") or get_gmail_address()
    password = os.environ.get("VENMO_PASSWORD") or get_bank_password("venmo")

    with open_session("venmo") as sb:
        sb.uc_open_with_reconnect(HOME, reconnect_time=6)
        sb.sleep(3)
        url = sb.get_current_url()
        if "account.venmo.com" in url and "sign" not in url and not _present(sb, "#email"):
            print("[capture] already logged in.")
        else:
            print("=" * 70)
            print("A Chrome window (automated) is open. I'm filling your email+password;")
            print("you just SOLVE THE CAPTCHA + 2FA in THAT window. Waiting up to 10 min…")
            print("=" * 70)
            _auto_login(sb, email, password)

        for i in range(120):  # 10 min
            sb.sleep(5)
            u = sb.get_current_url()
            if "account.venmo.com" in u and "sign" not in u and not _present(sb, "#email"):
                print(f"[capture] logged in after ~{i * 5}s")
                break
            if i % 6 == 0:
                print(f"  [waiting {i * 5}s] {u[:80]}")
        else:
            print("[capture] timed out waiting for login")
            return 1

        sb.sleep(2)
        cookies = {c["name"]: c.get("value") for c in (sb.get_cookies() or []) if c.get("name")}
        COOKIES_FILE.write_text(json.dumps(cookies, indent=2))
        ext_id = _external_id(sb)
        (SESSION_DIR / "external_id.txt").write_text(str(ext_id or ""))
        print(f"[capture] {len(cookies)} cookies saved; external_id={ext_id}")

    if not ext_id:
        print("[capture] no external_id — cannot test the feed endpoint")
        return 2

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
            print("        => httpx works headless with cookies. Scraper is unblocked.")
        else:
            (SNAP / "web_stories_blocked.txt").write_text(r.text[:3000])
            print("[httpx] BLOCKED — DataDome rejects non-browser requests; use browser fetch.")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
