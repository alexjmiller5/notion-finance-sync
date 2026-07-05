"""U.S. Bank session: SeleniumBase login -> 2FA -> in-page GraphQL fetch.

Unlike BofA (cookies -> httpx), U.S. Bank's GraphQL requires an ``Authorization:
Bearer`` header (token in ``sessionStorage.AccessToken``, rotates per session) and
sits behind Akamai bot protection. So we keep the authenticated browser open and run
the transactions query with the page's own ``fetch()`` — same origin, real browser,
fresh token — rather than replaying it from httpx.

Selectors + flow from live recon (2026-07-03; see data/snapshots/us_bank/FINDINGS.md):
  login: #input_aw-personal-id / #input_aw-password / #login-button-continue
  2FA:   Continue (#otp-cont-button) sends SMS -> #input_idshield-input (6 digits)
         -> #otp-cont-button
"""

from __future__ import annotations

import getpass
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import structlog

from notion_finance_sync.browser.factory import open_session
from notion_finance_sync.config.settings import get_bank_password, get_bank_username
from notion_finance_sync.twofa.sms import get_sms_code

logger = structlog.get_logger()

AuthMode = Literal["service_account", "manual"]

SESSION_ID = "us_bank"
LOGIN_URL = "https://onlinebanking.usbank.com/auth/login/"
DASHBOARD_URL = "https://onlinebanking.usbank.com/digital/servicing/shellapp/#/customer-dashboard"
GRAPHQL_PATH = "/digital/api/customer-management/graphql/v2"
DASHBOARD_MARKER = "customer-dashboard"

# U.S. Bank sends the OTP from short code 29946:
#   "U.S. Bank Alerts: 851733 is your code. Do not share this code..."
# The support number in the body (800.872.2657) is dotted, never a bare 6-digit run.
USB_SMS_SENDER = "29946"
USB_SMS_REGEX = r"\b(\d{6})\b"

# The txnsDetails query — full field set the parser consumes. (GraphQL ignores the
# whitespace; fields are wrapped only to keep source lines under the 100-col limit.)
_TXNS_QUERY = """query txnsDetails($input: TxnsAcctSearchRequestInput) {
  txnsDetails(txnsAcctSearchRequestInput:$input) { txnsResponse {
    filteredAcctList {
      accountToken accountNumber nickName accountName productCode subProductCode
      accountType displayName partnerCode
    }
    pendingTransactions {
      transactionUniqueId transactionId transactionType transactionStatus
      transactionDataSource transactionDateTime postedDateTime accountToken productCode
      description accountType accountNumber transactionAmount debitCreditMemo referenceNumber
      cardTransactionDetails { cardUsedLast4 }
      merchantDetails { name categoryCode categoryGuid city state }
      enrichedDetails { description category subCategory }
    }
    postedTransactions {
      transactionUniqueId transactionUId transactionId transactionType transactionTypeDesc
      transactionStatus transactionDataSource transactionAmount debitCreditMemo runningBalance
      postedDateTime transactionDateTime pointOfSaleDate description referenceNumber productCode
      subProductCode accountToken accountNumber accountType nickName accountName zelleMemo
      cardTransactionDetails { cardUsedLast4 }
      enrichedDetails { description category subCategory }
      merchantDetails { name logoURL city state categoryCode categoryGuid }
    }
  } } }"""

_SNAP_DIR = Path(__file__).resolve().parents[4] / "data" / "snapshots" / "us_bank"


def _set_input(sb, selector: str, value: str) -> bool:
    """Set an input via the native value setter + input/change events.

    ``sb.cdp.type`` did not reliably populate the U.S. Bank login fields (the login
    bounced as if empty). Driving the React-controlled inputs through the prototype
    setter is the reliable path (recon 2026-07-03).
    """
    expr = """
((sel, val) => {
  const el = document.querySelector(sel);
  if (!el) return false;
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  el.focus();
  setter.call(el, val);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  el.blur();
  return el.value === val;
})(__SEL__, __VAL__)
""".replace("__SEL__", json.dumps(selector)).replace("__VAL__", json.dumps(value))
    return bool(sb.cdp.evaluate(expr))


def _login_failure_screenshot(sb, session_id: str) -> None:
    try:
        _SNAP_DIR.mkdir(parents=True, exist_ok=True)
        name = f"login_failure_{session_id}_{datetime.now(tz=UTC).strftime('%H%M%S')}.png"
        sb.cdp.save_screenshot(name, folder=str(_SNAP_DIR))
        logger.error(
            "us_bank_login_failed",
            session_id=session_id,
            screenshot=str(_SNAP_DIR / name),
            url=sb.cdp.get_current_url(),
        )
    except Exception as exc:  # noqa: BLE001 — never mask the original error
        logger.error("us_bank_login_screenshot_failed", error=str(exc))


def _resolve_credentials(session_id: str, auth: AuthMode) -> tuple[str, str]:
    if auth == "manual":
        print(f"[U.S. Bank] Enter credentials for {session_id!r} (password hidden):")
        return input("  Username: ").strip(), getpass.getpass("  Password: ")
    return get_bank_username(session_id), get_bank_password(session_id)


def _do_login(sb, username: str, password: str, *, interactive: bool) -> None:
    sb.cdp.wait_for_element_visible("#input_aw-personal-id", timeout=30)
    for sel in ("#safaClose", "#onetrust-accept-btn-handler"):
        try:
            sb.cdp.click_if_visible(sel)
        except Exception:  # noqa: BLE001 — best-effort overlay dismissal
            pass

    if not _set_input(sb, "#input_aw-personal-id", username):
        raise RuntimeError("failed to populate U.S. Bank username field")
    if not _set_input(sb, "#input_aw-password", password):
        raise RuntimeError("failed to populate U.S. Bank password field")

    sb.cdp.click("#login-button-continue")

    # After submit, U.S. Bank either challenges 2FA (fresh/untrusted profile) or, if
    # the persistent profile is device-trusted, goes straight to the dashboard. Poll
    # for whichever happens first.
    branch = _await_post_login(sb, timeout=45)
    if branch == "dashboard":
        logger.info("us_bank_2fa_skipped", reason="device_trusted")
        return

    if branch == "otp_send":
        code_requested_at = datetime.now(tz=UTC)
        sb.cdp.click("#otp-cont-button")  # sends the SMS
        sb.cdp.wait_for_element_visible("#input_idshield-input", timeout=45)
    else:  # "otp_entry" — code input already shown
        code_requested_at = datetime.now(tz=UTC)

    code = get_sms_code(
        after=code_requested_at,
        sender_pattern=USB_SMS_SENDER,
        code_regex=USB_SMS_REGEX,
        timeout_s=150,
    )
    if not code:
        if interactive:
            input("2FA code not auto-read. Enter it in the browser, then press ENTER... ")
        else:
            raise RuntimeError("U.S. Bank 2FA code was not received within timeout")
    else:
        if not _set_input(sb, "#input_idshield-input", code):
            raise RuntimeError("failed to populate U.S. Bank OTP field")
        sb.cdp.click("#otp-cont-button")

    _wait_for_dashboard(sb)


def _await_post_login(sb, timeout: int = 45) -> str:
    """After login submit, return which state came up first.

    One of: ``"dashboard"`` (device-trusted, no 2FA), ``"otp_entry"`` (code input
    already visible), or ``"otp_send"`` (the Continue button to send the SMS).
    """
    deadline = datetime.now(tz=UTC).timestamp() + timeout
    while datetime.now(tz=UTC).timestamp() < deadline:
        if DASHBOARD_MARKER in (sb.cdp.get_current_url() or ""):
            return "dashboard"
        if sb.cdp.is_element_present("#input_idshield-input"):
            return "otp_entry"
        if sb.cdp.is_element_present("#otp-cont-button"):
            return "otp_send"
        sb.cdp.sleep(1)
    raise RuntimeError(
        f"U.S. Bank post-login state not recognized (url={sb.cdp.get_current_url()!r})"
    )


def _wait_for_dashboard(sb, timeout: int = 60) -> None:
    """Poll the URL until the SPA lands on the customer dashboard."""
    deadline = datetime.now(tz=UTC).timestamp() + timeout
    while datetime.now(tz=UTC).timestamp() < deadline:
        if DASHBOARD_MARKER in (sb.cdp.get_current_url() or ""):
            return
        sb.cdp.sleep(1)
    raise RuntimeError(f"U.S. Bank dashboard not reached (url={sb.cdp.get_current_url()!r})")


def _fetch_txns(sb, start: str, end: str) -> dict:
    """Run the txnsDetails query via the page's own fetch(), return the parsed JSON."""
    variables = {
        "input": {
            "productCodes": ["DDA", "CCD", "BCD"],
            "startTime": start,
            "endTime": end,
            "pageName": "UNIVERSAL_ACTIVITY_UBER",
        }
    }
    payload = json.dumps({"query": _TXNS_QUERY, "variables": variables})
    expr = """
(async () => {
  const r = await fetch(__PATH__, {
    method: "POST", credentials: "include",
    headers: {"Content-Type": "application/json",
      "Authorization": "Bearer " + sessionStorage.getItem("AccessToken"),
      "application-id": "UAL_UBER"},
    body: __BODY__});
  const text = await r.text();
  return JSON.stringify({status: r.status, body: text});
})()
""".replace("__PATH__", json.dumps(GRAPHQL_PATH)).replace("__BODY__", json.dumps(payload))
    result = sb.cdp.loop.run_until_complete(sb.cdp.page.evaluate(expr, await_promise=True))
    if isinstance(result, str):
        result = json.loads(result)
    if result.get("status") != 200:
        raise RuntimeError(f"txnsDetails fetch failed: HTTP {result.get('status')}")
    data = json.loads(result["body"])
    if "errors" in data and not data.get("data"):
        raise RuntimeError(f"txnsDetails GraphQL error: {data['errors']}")
    return data


def fetch_activity(
    start: str,
    end: str,
    *,
    session_id: str = SESSION_ID,
    auth: AuthMode = "service_account",
    interactive: bool = False,
) -> dict:
    """Log in and return the raw ``txnsDetails`` JSON for the ``[start, end]`` window.

    ``start``/``end`` are ``YYYY-MM-DD`` strings. Opens a real browser, logs in
    (SMS 2FA auto-read from Messages), then fetches in-page. Screenshots on failure.
    """
    username, password = _resolve_credentials(session_id, auth)
    with open_session(session_id) as sb:
        try:
            sb.activate_cdp_mode(LOGIN_URL)
            _do_login(sb, username, password, interactive=interactive)
            raw = _fetch_txns(sb, start, end)
            accts = (
                raw.get("data", {})
                .get("txnsDetails", {})
                .get("txnsResponse", {})
                .get("filteredAcctList")
            )
            logger.info("us_bank_fetch_ok", session_id=session_id, accounts=len(accts or []))
            return raw
        except Exception:
            _login_failure_screenshot(sb, session_id)
            raise
