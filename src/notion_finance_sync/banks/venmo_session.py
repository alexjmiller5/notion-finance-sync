"""Capture a Venmo web-session (cookies + external_id) via an automated browser login.

The Venmo web API (``account.venmo.com/api/stories``) is cookie-authed. The auth
cookie is HttpOnly, so it must be read with SeleniumBase ``get_cookies()``. This module
opens a stealth Chrome window, auto-fills email + password, auto-reads the SMS 2FA from
Messages, ticks "remember this device", waits for the logged-in home, then saves the
cookies + external_id to the SAME session files the scraper replays
(``venmo._COOKIES_FILE`` / ``venmo._EXTID_FILE``, both under ``config.paths.SESSIONS_DIR``).

``capture_session()`` is called automatically by the scraper when cookies are missing or
expired, so the daily sync logs itself in with no manual step. Credentials come from env
(``VENMO_LOGIN_EMAIL`` / ``VENMO_PASSWORD``) or the vault getters — never hardcoded.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import structlog

from notion_finance_sync.browser.factory import open_session
from notion_finance_sync.config.paths import SNAPSHOTS_DIR
from notion_finance_sync.config.settings import get_bank_password, get_gmail_address
from notion_finance_sync.twofa.sms import get_sms_code

logger = structlog.get_logger()

HOME = "https://account.venmo.com/"
_SNAP = SNAPSHOTS_DIR / "venmo"
BLOCK = ("You have been blocked", "couldn't load the security challenge")
# id.venmo.com/PayPal 2FA SMS. Sender/regex confirmed on the first real code.
_SMS_REGEX = r"(?i)(?:venmo|paypal|verification|security|code)\D{0,80}?(\d{6})"
_OTP_INPUT = (
    "input[autocomplete='one-time-code'], input[inputmode='numeric'], "
    "input[name*='otp' i], input[name='code'], input[type='tel']"
)
_CLICK_BY_TEXT = """
const texts = arguments[0];
const els = [...document.querySelectorAll("button,[role=button],input[type=submit],a")];
for (const t of texts) {
  const b = els.find(x => ((x.textContent||x.value||'')+'').trim().toLowerCase().includes(t));
  if (b) { b.click(); return t; }
}
return null;
"""


def _present(sb, sel) -> bool:
    try:
        return sb.is_element_present(sel)
    except Exception:  # noqa: BLE001
        return False


def _type_real(sb, sel, val) -> None:
    """React-safe input: click + real keys (send_keys), commits onChange."""
    sb.click(sel)
    sb.type(sel, val)


def _dump(sb, tag) -> None:
    try:
        _SNAP.mkdir(parents=True, exist_ok=True)
        (_SNAP / f"webcap_{tag}.html").write_text(sb.get_page_source())
        sb.save_screenshot(str(_SNAP / f"webcap_{tag}.png"))
    except Exception:  # noqa: BLE001
        pass


def _auto_login(sb, email, password) -> None:
    # Wait for the id.venmo.com email form (past any DataDome interstitial).
    for _ in range(22):
        if _present(sb, "#email"):
            break
        html = sb.get_page_source()
        if any(m in html for m in BLOCK):
            logger.warning("venmo_capture_datadome_block", stage="form")
            return
        sb.sleep(5)
    if not _present(sb, "#email"):
        logger.warning("venmo_capture_no_email_form")
        return
    _type_real(sb, "#email", email)
    if _present(sb, "#btnNext"):
        sb.click("#btnNext")
        sb.sleep(4)
    pass_sel = "input[name='login_password']"
    if _present(sb, pass_sel):
        _type_real(sb, pass_sel, password)
        sb.send_keys(pass_sel, "\n")
        logger.info("venmo_capture_credentials_submitted")
        _handle_2fa(sb)
    else:
        logger.warning("venmo_capture_no_password_field")


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
            logger.warning("venmo_capture_datadome_block", stage="2fa")
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
        logger.info("venmo_capture_send_code_clicked", button=clicked)
        code_requested_at = datetime.now(tz=UTC)
        for _ in range(12):
            sb.sleep(3)
            if sb.execute_script(f'return !!document.querySelector("{_OTP_INPUT}")'):
                break

    _dump(sb, "2fa_otp_screen")
    if not sb.execute_script(f'return !!document.querySelector("{_OTP_INPUT}")'):
        logger.warning("venmo_capture_no_otp_input")
        return

    code = get_sms_code(
        after=code_requested_at, sender_pattern="%", code_regex=_SMS_REGEX, timeout_s=150
    )
    if not code:
        logger.warning("venmo_capture_no_sms_code")
        return
    logger.info("venmo_capture_otp_read")
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
    logger.info("venmo_capture_otp_submitted")


def _read_external_id(sb) -> str | None:
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


def capture_session() -> None:
    """Log in to Venmo (auto creds + auto SMS-2FA) and save cookies + external_id.

    Writes to ``venmo._COOKIES_FILE`` / ``venmo._EXTID_FILE`` (under ``SESSIONS_DIR``),
    the exact files the scraper replays. Raises ``RuntimeError`` if login never lands on
    the logged-in home. Imported lazily inside ``venmo`` to keep the browser/SMS deps out
    of the fast httpx replay path.
    """
    # Local import avoids a circular import (venmo imports capture_session from here).
    from notion_finance_sync.banks.venmo import _COOKIES_FILE, _EXTID_FILE, _SESSION_DIR

    email = os.environ.get("VENMO_LOGIN_EMAIL") or get_gmail_address()
    password = os.environ.get("VENMO_PASSWORD") or get_bank_password("venmo")

    with open_session("venmo") as sb:
        sb.uc_open_with_reconnect(HOME, reconnect_time=6)
        sb.sleep(3)
        url = sb.get_current_url()
        if "account.venmo.com" in url and "sign" not in url and not _present(sb, "#email"):
            logger.info("venmo_capture_already_logged_in")
        else:
            _auto_login(sb, email, password)

        for i in range(120):  # up to 10 min
            sb.sleep(5)
            u = sb.get_current_url()
            if "account.venmo.com" in u and "sign" not in u and not _present(sb, "#email"):
                logger.info("venmo_capture_logged_in", elapsed_s=i * 5)
                break
        else:
            raise RuntimeError("Venmo capture timed out waiting for login")

        sb.sleep(2)
        cookies = {c["name"]: c.get("value") for c in (sb.get_cookies() or []) if c.get("name")}
        ext_id = _read_external_id(sb)

    _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    _COOKIES_FILE.write_text(json.dumps(cookies, indent=2))
    _EXTID_FILE.write_text(str(ext_id or ""))
    logger.info("venmo_capture_saved", cookies=len(cookies), has_external_id=bool(ext_id))
    if not ext_id:
        raise RuntimeError("Venmo capture got cookies but no external_id")
