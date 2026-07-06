"""E*Trade session bootstrap: SeleniumBase login -> cookies + stk1 -> httpx.

The only E*Trade module that drives a real browser. Logs in with SeleniumBase
UC+CDP (persistent profile at data/sessions/etrade/), handles the SMS 2FA, then
captures everything the fetchers need:

- session cookies
- the ``stk1`` API token (embedded in the transactions page HTML as
  ``pageConfig['uaa_vt']`` — the activities API 403s without it)
- ``keyAccountId`` + account display name (from the account dropdown)
- ESPP lot table (best-effort DOM scrape of Stock Plan Benefit History)

Selectors + flow from live recon 2026-07-03 (data/snapshots/etrade/FINDINGS.md):
  login:  #USER / #password / #mfaLogonButton
  2FA:    /login/sendotpcode -> #sendOTPCodeBtn (sends SMS) ->
          /login/verifyotpcode -> #verificationCode + #saveDevice ->
          button:contains("Submit")
  Device trust: selecting #saveDevice earns 2FA-free logins in this profile.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import structlog

from notion_finance_sync.browser.factory import open_session
from notion_finance_sync.config.paths import SNAPSHOTS_DIR
from notion_finance_sync.config.settings import get_bank_password, get_bank_username
from notion_finance_sync.twofa.sms import get_sms_code

logger = structlog.get_logger()

LOGIN_URL = "https://us.etrade.com/etx/pxy/login"
TRANSACTIONS_URL = "https://us.etrade.com/etx/pxy/accounts/transactions"
BENEFIT_HISTORY_URL = "https://us.etrade.com/etx/sp/stockplan#/myAccount/benefitHistory"

# E*Trade sends the one-time code by SMS from short code 95851:
#   "Your E*TRADE verification code is 827713. No one from E*TRADE will ..."
ETRADE_SMS_SENDER = "95851"
ETRADE_SMS_REGEX = r"(?i)verification code is\D{0,6}(\d{6})"

_STK1_RE = re.compile(r"""pageConfig\['uaa_vt'\]\s*=\s*"([^"]+)\"""")
_KEY_ACCOUNT_RE = re.compile(r'data-dropdown-option-value="([\d.\-]+)"')
_ACCOUNT_NAME_RE = re.compile(r">([^<>]*Brokerage[^<>]*-\d{4})<")

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

# JS snippets for the ESPP Benefit History page (React app; hashed CSS classes,
# so target by text + tag instead of selectors).
_ESPP_EXPAND_JS = """
(() => {
  const els = [...document.querySelectorAll('button, [role=button]')];
  const t = els.find(el => el.textContent.includes('Employee Stock Purchase Plan'));
  if (!t) return 'not-found';
  t.click();
  return 'clicked';
})()
"""
_TABLE_DUMP_JS = """
JSON.stringify([...document.querySelectorAll('table')].map(tb => ({
  headers: [...tb.querySelectorAll('th')].map(th => th.textContent.trim()),
  rows: [...tb.querySelectorAll('tbody tr')].map(tr =>
    [...tr.querySelectorAll('td')].map(td => td.textContent.trim()))
})))
"""


@dataclass
class ETradeSession:
    """Everything the API fetchers need, captured from one browser login."""

    cookies: dict[str, str]
    stk1: str
    key_account_id: str
    account_name: str
    espp_lots: dict[str, float] = field(default_factory=dict)


def build_client(session: ETradeSession) -> httpx.Client:
    """Build an httpx client carrying the browser cookies + stk1 API token."""
    return httpx.Client(
        cookies=session.cookies,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "stk1": session.stk1,
            "Referer": TRANSACTIONS_URL,
        },
        follow_redirects=True,
        timeout=30.0,
    )


def _login_failure_screenshot(sb, session_id: str) -> None:
    """Save a screenshot + current URL when login fails, for blind debugging."""

    try:
        folder = SNAPSHOTS_DIR / "etrade"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"login_failure_{session_id}_{datetime.now(tz=UTC).strftime('%H%M%S')}.png"
        sb.cdp.save_screenshot(name, folder=str(folder))
        logger.error(
            "etrade_login_failed",
            session_id=session_id,
            screenshot=str(folder / name),
            url=sb.cdp.get_current_url(),
        )
    except Exception as exc:  # noqa: BLE001 — never mask the original error
        logger.error("etrade_login_screenshot_failed", error=str(exc))


def _submit_credentials(sb, session_id: str) -> datetime:
    """Fill the login form and submit. Returns the pre-submit timestamp."""
    sb.cdp.wait_for_element_visible("#USER", timeout=30)
    for sel in ("#onetrust-accept-btn-handler",):
        try:
            sb.cdp.click_if_visible(sel)
        except Exception:  # noqa: BLE001 — best-effort banner dismissal
            pass
    sb.cdp.type("#USER", get_bank_username(session_id))
    sb.cdp.type("#password", get_bank_password(session_id))
    code_requested_at = datetime.now(tz=UTC)
    sb.cdp.click("#mfaLogonButton")
    return code_requested_at


def _complete_otp(sb, code_requested_at: datetime, *, interactive: bool) -> None:
    """Drive the sendotpcode -> verifyotpcode pages, reading the SMS code."""
    if sb.cdp.is_element_present("#sendOTPCodeBtn"):
        code_requested_at = datetime.now(tz=UTC)
        sb.cdp.click("#sendOTPCodeBtn")
        sb.cdp.wait_for_element_visible("#verificationCode", timeout=45)

    code = get_sms_code(
        after=code_requested_at,
        sender_pattern=ETRADE_SMS_SENDER,
        code_regex=ETRADE_SMS_REGEX,
        timeout_s=150,
    )
    if not code:
        if interactive:
            input("2FA code not auto-read. Enter it in the browser, then press ENTER... ")
            return
        raise RuntimeError("E*Trade 2FA code was not received within timeout")

    sb.cdp.type("#verificationCode", code)
    try:
        sb.cdp.click("#saveDevice")  # earn device trust -> future logins skip 2FA
    except Exception:  # noqa: BLE001 — trust radio is optional
        logger.warning("etrade_save_device_click_failed")
    sb.cdp.click('button:contains("Submit")')


def _wait_until_logged_in(sb, session_id: str, *, interactive: bool, timeout_s: int = 120) -> None:
    """State loop: land on accountshome, doing login/OTP steps as they appear.

    The persistent profile may carry device trust (straight to accountshome) or
    an expired session (login form) — poll for whichever page shows up instead
    of assuming a fixed sequence.
    """
    code_requested_at = datetime.now(tz=UTC)
    did_login = did_otp = False
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        url = sb.cdp.get_current_url()
        if "accountshome" in url:
            return
        if not did_login and sb.cdp.is_element_present("#USER"):
            code_requested_at = _submit_credentials(sb, session_id)
            did_login = True
        elif not did_otp and (
            sb.cdp.is_element_present("#sendOTPCodeBtn")
            or sb.cdp.is_element_present("#verificationCode")
        ):
            _complete_otp(sb, code_requested_at, interactive=interactive)
            did_otp = True
        sb.cdp.sleep(2)
    raise RuntimeError(f"E*Trade login did not reach accountshome (last URL: {url})")


def _scrape_espp_lots(sb) -> dict[str, float]:
    """Best-effort: Stock Plan Benefit History -> {qty: purchase price}."""
    import json

    from notion_finance_sync.banks.etrade.activity import parse_espp_lots

    sb.cdp.open(BENEFIT_HISTORY_URL)
    sb.cdp.sleep(8)
    clicked = sb.cdp.evaluate(_ESPP_EXPAND_JS)
    if clicked != "clicked":
        logger.warning("etrade_espp_accordion_not_found")
        return {}
    sb.cdp.sleep(3)
    dump = sb.cdp.evaluate(_TABLE_DUMP_JS)
    tables = json.loads(dump) if isinstance(dump, str) else dump
    lots = parse_espp_lots(tables or [])
    logger.info("etrade_espp_lots_scraped", count=len(lots))
    return lots


def login_and_capture(
    session_id: str = "etrade",
    *,
    interactive: bool = False,
) -> ETradeSession:
    """Log into E*Trade and capture cookies + stk1 + account id + ESPP lots."""
    with open_session(session_id) as sb:
        try:
            sb.activate_cdp_mode(LOGIN_URL)
            sb.cdp.sleep(3)
            _wait_until_logged_in(sb, session_id, interactive=interactive)

            # Transactions page: source of the stk1 token + keyAccountId.
            sb.cdp.open(TRANSACTIONS_URL)
            html = ""
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                html = sb.cdp.get_page_source()
                if _STK1_RE.search(html) and _KEY_ACCOUNT_RE.search(html):
                    break
                sb.cdp.sleep(2)
            stk1_m = _STK1_RE.search(html)
            key_m = _KEY_ACCOUNT_RE.search(html)
            if not stk1_m or not key_m:
                raise RuntimeError("E*Trade transactions page missing stk1/keyAccountId")
            name_m = _ACCOUNT_NAME_RE.search(html)

            try:
                espp_lots = _scrape_espp_lots(sb)
            except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
                logger.warning("etrade_espp_scrape_failed", error=str(exc))
                espp_lots = {}

            cookies = _cookies_to_dict(sb.cdp.get_all_cookies())
            logger.info(
                "etrade_login_ok",
                session_id=session_id,
                cookie_count=len(cookies),
                key_account_id=key_m.group(1),
            )
            return ETradeSession(
                cookies=cookies,
                stk1=stk1_m.group(1),
                key_account_id=key_m.group(1),
                account_name=name_m.group(1) if name_m else "E*Trade Brokerage",
                espp_lots=espp_lots,
            )
        except Exception:
            _login_failure_screenshot(sb, session_id)
            raise


def _cookies_to_dict(raw_cookies) -> dict[str, str]:
    """Normalize SeleniumBase CDP cookies (objects or dicts) to name->value."""
    out: dict[str, str] = {}
    for c in raw_cookies or []:
        if isinstance(c, dict):
            name, value = c.get("name"), c.get("value")
        else:
            name, value = getattr(c, "name", None), getattr(c, "value", None)
        if name is not None and value is not None:
            out[name] = value
    return out
