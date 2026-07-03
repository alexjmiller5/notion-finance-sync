"""Fidelity session: SeleniumBase login -> 2FA -> in-page JSON API fetch.

Logs into digital.fidelity.com with SeleniumBase UC+CDP (real Chrome, persistent
profile), handles the SMS 2FA (Fidelity shows a mobile-push challenge first; we
click "Try another way" -> "Text me the code").

The activity API sits behind Akamai bot protection and **rejects off-browser
requests** (httpx from the cookie jar 500s). So — per the project's "prefer
fetch() from the logged-in page" rule — the history POST is issued *inside* the
browser via ``fetch_history_in_page`` (returns 200, same as the app).

Selectors + flow from live recon 2026-07-03 (see data/snapshots/fidelity/FINDINGS.md).
Device-trust works: after one trusted login the next run usually skips 2FA.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import structlog

from notion_finance_sync.config.settings import get_bank_password, get_bank_username
from notion_finance_sync.twofa.sms import get_sms_code

logger = structlog.get_logger()

LOGIN_URL = "https://digital.fidelity.com/prgw/digital/login/full-page"
PORTFOLIO_URL = "https://digital.fidelity.com/ftgw/digital/portfolio/summary"
ACTIVITY_URL = "https://digital.fidelity.com/ftgw/digital/portfolio/activity"
PORTFOLIO_MARKER_URL = "portfolio/summary"
HISTORY_ENDPOINT = "/ftgw/digital/activityapi/api/v1/transactions/history"

# Fidelity sends the one-time code by SMS from short code 36726 (validated 2026-07-03):
#   "Fidelity Investments: If anyone asks for this code, STOP. ... Code is: 698051"
FIDELITY_SMS_SENDER = "36726"
FIDELITY_SMS_REGEX = r"(?i)code\s+is\D{0,3}(\d{6})"


def _login_failure_screenshot(sb, session_id: str) -> None:
    try:
        folder = Path(__file__).resolve().parents[4] / "data" / "snapshots" / "fidelity"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"login_failure_{session_id}_{datetime.now(tz=UTC).strftime('%H%M%S')}.png"
        sb.cdp.save_screenshot(name, folder=str(folder))
        logger.error(
            "fidelity_login_failed",
            session_id=session_id,
            screenshot=str(folder / name),
            url=sb.cdp.get_current_url(),
        )
    except Exception as exc:  # noqa: BLE001 — never mask the original error
        logger.error("fidelity_login_screenshot_failed", error=str(exc))


def _handle_2fa(sb, interactive: bool) -> None:
    """Drive the SMS 2FA path if challenged. No-op if device trust skipped it."""
    sb.cdp.sleep(3)
    if PORTFOLIO_MARKER_URL in sb.cdp.get_current_url():
        logger.info("fidelity_2fa_skipped_device_trust")
        return

    # Push-notification challenge appears first -> switch to a code method.
    if not sb.cdp.is_element_present("#dom-otp-code-input"):
        try:
            sb.cdp.wait_for_element_visible("#dom-try-another-way-link", timeout=20)
            sb.cdp.click("#dom-try-another-way-link")
        except Exception:  # noqa: BLE001 — maybe already on a code page
            pass

    code_requested_at = datetime.now(tz=UTC)
    if sb.cdp.is_element_present("#dom-channel-list-primary-button"):
        sb.cdp.click("#dom-channel-list-primary-button")  # "Text me the code"

    sb.cdp.wait_for_element_visible("#dom-otp-code-input", timeout=45)
    code = get_sms_code(
        after=code_requested_at,
        sender_pattern=FIDELITY_SMS_SENDER,
        code_regex=FIDELITY_SMS_REGEX,
        timeout_s=150,
    )
    if not code:
        if interactive:
            input("2FA code not auto-read. Enter it in the browser, then press ENTER... ")
        else:
            raise RuntimeError("Fidelity 2FA code was not received within timeout")
    else:
        sb.cdp.type("#dom-otp-code-input", code)
        try:
            sb.cdp.click_if_visible("#dom-trust-device-checkbox")  # skip 2FA next run
        except Exception:  # noqa: BLE001 — best-effort
            pass
        sb.cdp.click("#dom-otp-code-submit-button")


def perform_login(sb, *, session_id: str = "fidelity", interactive: bool = False) -> None:
    """Log into Fidelity on an already-open SeleniumBase session and land logged-in."""
    username = get_bank_username(session_id)
    password = get_bank_password(session_id)
    try:
        sb.activate_cdp_mode(LOGIN_URL)
        sb.cdp.wait_for_element_visible("#dom-username-input", timeout=30)
        sb.cdp.type("#dom-username-input", username)
        sb.cdp.type("#dom-pswd-input", password)
        sb.cdp.click("#dom-login-button")

        _handle_2fa(sb, interactive)

        # Ensure we're on a logged-in page (activity endpoint fetch needs same-origin).
        sb.cdp.sleep(2)
        if PORTFOLIO_MARKER_URL not in sb.cdp.get_current_url():
            sb.cdp.open(PORTFOLIO_URL)
        sb.cdp.wait_for_element_visible("body", timeout=30)
        sb.cdp.sleep(3)
        logger.info("fidelity_login_ok", session_id=session_id)
    except Exception:
        _login_failure_screenshot(sb, session_id)
        raise


def fetch_history_in_page(sb, body: dict, *, timeout_s: int = 60) -> dict:
    """POST the activity/history request from inside the logged-in page.

    Akamai rejects the same request from httpx (500), so we run it in-browser via
    a real ``fetch`` with the required ``appId``/``appName`` headers. Returns the
    parsed JSON response body.
    """
    # Navigate to the activity SPA so the fetch is same-origin with the right referer.
    if "portfolio/activity" not in sb.cdp.get_current_url():
        sb.cdp.open(ACTIVITY_URL)
        sb.cdp.wait_for_element_visible("body", timeout=30)
        sb.cdp.sleep(3)

    body_json = json.dumps(body)
    js = (
        "window.__fidres = 'pending';"
        "(async () => {"
        "  try {"
        f"    const r = await fetch('{HISTORY_ENDPOINT}', {{method:'POST',"
        "      headers:{'Content-Type':'application/json','Accept':'application/json',"
        "               'appId':'ap182468','appName':'activity-orders-ui'},"
        f"      credentials:'include', body: JSON.stringify({body_json})}});"
        "    window.__fidres = JSON.stringify({status: r.status, body: await r.text()});"
        "  } catch (e) { window.__fidres = 'ERROR: ' + e; }"
        "})();"
    )
    sb.cdp.evaluate(js)

    out = "pending"
    for _ in range(timeout_s // 2):
        time.sleep(2)
        out = sb.cdp.evaluate("window.__fidres")
        if out and out != "pending":
            break
    if not out or out == "pending":
        raise RuntimeError("Fidelity history fetch timed out")
    if isinstance(out, str) and out.startswith("ERROR:"):
        raise RuntimeError(f"Fidelity history fetch failed in-page: {out}")

    wrapper = json.loads(out) if isinstance(out, str) else out
    if wrapper.get("status") != 200:
        raise RuntimeError(
            f"Fidelity history HTTP {wrapper.get('status')}: {str(wrapper.get('body'))[:200]}"
        )
    return json.loads(wrapper["body"])
