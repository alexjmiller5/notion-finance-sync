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
from notion_finance_sync.config.settings import get_bank_password, get_bank_username
from notion_finance_sync.twofa.sms import get_sms_code

logger = structlog.get_logger()

AuthMode = Literal["service_account", "manual"]

LOGIN_URL = "https://www.bankofamerica.com/"
OVERVIEW_MARKER = "Accounts Overview"

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
    from pathlib import Path

    try:
        folder = Path(__file__).resolve().parents[3] / "data" / "snapshots" / "bofa"
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
    - ``service_account``: read from the ``Notion Finance Sync`` vault via the
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
    username, password = _resolve_credentials(session_id, auth)

    with open_session(session_id) as sb:
        try:
            sb.activate_cdp_mode(LOGIN_URL)

            # 1. Wait for the login widget to be interactable, dismiss any cookie
            #    banner (it can overlay the form so type/click silently no-op).
            sb.cdp.wait_for_element_visible("#oid", timeout=30)
            for sel in ("#onetrust-accept-btn-handler", "#engagementBannerCloseBtn"):
                try:
                    sb.cdp.click_if_visible(sel)
                except Exception:  # noqa: BLE001 — best-effort dismissal
                    pass

            # 2. Credentials. Timestamp before submit so we only match a code that
            #    arrives after this login attempt (BofA sends it during/after submit).
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
                sb.cdp.click("#ah-authcode-validate-continue-btn")

            # 4. Logged in.
            sb.cdp.wait_for_text(OVERVIEW_MARKER, timeout=60)
            cookies = _cookies_to_dict(sb.cdp.get_all_cookies())
            logger.info("bofa_login_ok", session_id=session_id, cookie_count=len(cookies))
            return cookies
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
