"""BofA session bootstrap: SeleniumBase login -> cookies -> httpx client.

This is the only BofA module that drives a real browser. It logs in with
SeleniumBase UC+CDP (real Chrome, persistent profile), handles the SMS 2FA, then
hands the resulting session cookies to an ``httpx.Client`` so the (fast, cheap)
fetchers can pull JSON/HTML directly.

Selectors + flow are from live recon (2026-07-02; see BACKFILL_STATUS.md):
  login:  #oid / #pass / #secure-signin-submit
  2FA:    #authcodeTextReceive (pre-checked) -> #ah-authcode-select-continue-btn
          (sends SMS) -> #ahAuthcodeValidateOTP (6 digits) ->
          #ah-authcode-validate-continue-btn

⚠️ The SeleniumBase CDP calls and the BofA SMS sender pattern below need one
at-keyboard validation run (2FA requires Alex's phone). Everything downstream
(fetchers + parsers + assembler) is already unit-tested against captured fixtures.
"""

from __future__ import annotations

import getpass
from datetime import UTC, datetime
from typing import Literal

import httpx
import structlog

from notion_finance_sync.browser.factory import open_session
from notion_finance_sync.config.paths import SNAPSHOTS_DIR
from notion_finance_sync.config.settings import get_bank_password, get_bank_username
from notion_finance_sync.twofa.sms import get_sms_code

logger = structlog.get_logger()

AuthMode = Literal["service_account", "manual"]

LOGIN_URL = "https://www.bankofamerica.com/"
OVERVIEW_MARKER = "Accounts Overview"

# The authcode page now requires selecting a "remember this device" security
# preference before it will submit. Pick "Yes" (also trusts the device, so future
# runs may skip 2FA). Selected by the radio's visible label since the id varies.
_REMEMBER_DEVICE_JS = r"""
(() => {
  const radios = [...document.querySelectorAll('input[type=radio]')];
  const labelText = (r) => {
    let t = '';
    if (r.id) {
      const l = document.querySelector('label[for="' + r.id + '"]');
      if (l) t += ' ' + l.textContent;
    }
    const p = r.closest('label'); if (p) t += ' ' + p.textContent;
    if (r.parentElement) t += ' ' + r.parentElement.textContent;
    return t;
  };
  for (const r of radios) {
    if (/yes.{0,4}remember this device/i.test(labelText(r))) { r.click(); return 'yes'; }
  }
  return 'not-found';
})()
"""

# BofA sends the one-time code by SMS from short code 73981. Real formats (2026):
#   "BofA: DO NOT share this Sign In code... Code 123456."   (dominant)
#   "BofA: Your code is 123456. ... Call 800.933.6262 ..."
# The regex anchors on the word "code" (optionally "code is"), so the leading
# "Sign In code." with no following digits and the 800.933.6262 phone number are
# never mistaken for the 6-digit code.
BOFA_SMS_SENDER = "73981"
BOFA_SMS_REGEX = r"(?i)code(?:\s+is)?\D{0,6}(\d{6})"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)


def build_client(cookies: dict[str, str]) -> httpx.Client:
    """Build an httpx client carrying the browser session cookies + real UA."""
    return httpx.Client(
        cookies=cookies,
        headers={"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        follow_redirects=True,
        timeout=30.0,
    )


def _login_failure_screenshot(sb, session_id: str) -> None:
    """Save a screenshot + current URL when login fails, for blind debugging."""

    try:
        folder = SNAPSHOTS_DIR / "bofa"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"login_failure_{session_id}_{datetime.now(tz=UTC).strftime('%H%M%S')}.png"
        sb.cdp.save_screenshot(name, folder=str(folder))
        logger.error(
            "bofa_login_failed",
            session_id=session_id,
            screenshot=str(folder / name),
            url=sb.cdp.get_current_url(),
        )
    except Exception as exc:  # noqa: BLE001 — never mask the original error
        logger.error("bofa_login_screenshot_failed", error=str(exc))


def _resolve_credentials(session_id: str, auth: AuthMode) -> tuple[str, str]:
    """Get (username, password) either by prompting (manual) or from 1Password.

    - ``manual``: prompt in the terminal (password is not echoed). No 1Password
      needed — handy for a first validation run or when the vault isn't wired up.
    - ``service_account``: read from the project 1Password vault via the
      ``op`` CLI. For unattended runs, export ``OP_SERVICE_ACCOUNT_TOKEN`` first
      and the CLI authenticates with it (no interactive ``op signin`` needed).
    """
    if auth == "manual":
        print(f"[BofA] Enter credentials for session {session_id!r} (input hidden for password):")
        username = input("  User ID: ").strip()
        password = getpass.getpass("  Password: ")
        return username, password
    return get_bank_username(session_id), get_bank_password(session_id)


def login_and_get_cookies(
    session_id: str = "bofa",
    *,
    auth: AuthMode = "service_account",
    interactive: bool = False,
) -> dict[str, str]:
    """Log into BofA via SeleniumBase and return the session cookies.

    Args:
        session_id: 1Password/profile key (``"bofa"``).
        auth: ``"service_account"`` (default) reads creds from 1Password;
            ``"manual"`` prompts for them in the terminal.
        interactive: pause for manual intervention on unexpected challenges
            (e.g. a 2FA code that couldn't be auto-read from Messages).
    """
    with open_session(session_id) as sb:
        try:
            perform_login(sb, session_id=session_id, auth=auth, interactive=interactive)
            cookies = _cookies_to_dict(sb.cdp.get_all_cookies())
            logger.info("bofa_login_ok", session_id=session_id, cookie_count=len(cookies))
            return cookies
        except Exception:
            _login_failure_screenshot(sb, session_id)
            raise


def perform_login(
    sb,
    *,
    session_id: str = "bofa",
    auth: AuthMode = "service_account",
    interactive: bool = False,
) -> None:
    """Drive an already-open SeleniumBase session through BofA login + 2FA.

    Leaves the browser on the Accounts Overview page (session live) so callers
    that need more than cookies — e.g. the investments scraper's gcslsso SSO —
    can keep navigating. ``login_and_get_cookies`` wraps this and returns cookies.
    """
    username, password = _resolve_credentials(session_id, auth)
    sb.activate_cdp_mode(LOGIN_URL)

    # BofA has two login forms: the homepage widget (#oid/#pass, what we use) and
    # a standalone sign-in page (signOnV2Screen.go, no #oid). If we get redirected
    # to the latter, re-assert the homepage form once.
    sb.cdp.sleep(3)
    if not sb.cdp.is_element_present("#oid"):
        sb.cdp.open(LOGIN_URL)

    # 1. Wait for the login widget, dismiss any cookie banner (it can overlay the
    #    form so type/click silently no-op).
    sb.cdp.wait_for_element_visible("#oid", timeout=30)
    for sel in ("#onetrust-accept-btn-handler", "#engagementBannerCloseBtn"):
        try:
            sb.cdp.click_if_visible(sel)
        except Exception:  # noqa: BLE001 — best-effort dismissal
            pass

    # 2. Credentials. Timestamp before submit so we only match a code that arrives
    #    after this login attempt (BofA sends it during/after submit).
    sb.cdp.type("#oid", username)
    sb.cdp.type("#pass", password)
    code_requested_at = datetime.now(tz=UTC)
    sb.cdp.click("#secure-signin-submit")

    # 3. 2FA — BofA ALWAYS challenges (no device trust). Wait for either the
    #    delivery-select page or (rarely) the code-entry page directly.
    sb.cdp.wait_for_any_of_elements_present(
        ["#authcodeTextReceive", "#ahAuthcodeValidateOTP"], timeout=45
    )
    if sb.cdp.is_element_present("#authcodeTextReceive"):
        sb.cdp.click("#authcodeTextReceive")  # text-message delivery
        code_requested_at = datetime.now(tz=UTC)
        sb.cdp.click("#ah-authcode-select-continue-btn")  # sends the SMS
        sb.cdp.wait_for_element_visible("#ahAuthcodeValidateOTP", timeout=45)

    code = get_sms_code(
        after=code_requested_at,
        sender_pattern=BOFA_SMS_SENDER,
        code_regex=BOFA_SMS_REGEX,
        timeout_s=150,
    )
    if not code:
        if interactive:
            input("2FA code not auto-read. Enter it in the browser, then press ENTER... ")
        else:
            raise RuntimeError("BofA 2FA code was not received within timeout")
    else:
        sb.cdp.type("#ahAuthcodeValidateOTP", code)
        # BofA now requires a "remember this device" security preference on this page
        # before it will submit; select "Yes" (also trusts the device).
        result = sb.cdp.evaluate(_REMEMBER_DEVICE_JS)
        logger.info("bofa_remember_device", result=result)
        sb.cdp.click("#ah-authcode-validate-continue-btn")

    # 4. Logged in.
    sb.cdp.wait_for_text(OVERVIEW_MARKER, timeout=60)


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
