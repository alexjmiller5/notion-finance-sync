"""EverBank session bootstrap + authenticated JSON fetchers.

EverBank runs the FIS Digital One / Temenos consumer platform (an Angular SPA at
``/LCDDigitalOneConsumer/`` backed by a JSON service bus at
``/consumer-sb/service/d1/<Service>``). This module drives the SeleniumBase UC+CDP
login + SMS 2FA, captures the session cookies, then talks to the JSON API with an
``httpx.Client``.

Every ``consumer-sb`` call needs, beyond the cookies:
- header ``gax`` = the ``gix`` cookie value (per-session GUID) — missing it → 403.
- header ``rquid`` = a fresh UUID per request.
- body ``_credentials._deviceToken`` = base64 of a tab-delimited array whose only
  load-bearing fields are the epoch-ms, the service name and the rquid. The
  UA|phone|email|SSN field is NOT validated, so we send only the UA (no PII).

Selectors + flow are from live recon (2026-07-03; see
``data/snapshots/everbank/FINDINGS.md``).
"""

from __future__ import annotations

import base64
import getpass
import uuid
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

ORIGIN = "https://secure.everbank.com"
# Land on the app root, NOT #/Login: when the persistent profile still holds a
# valid session the SPA routes straight to the dashboard. Navigating to #/Login
# directly renders the login form *transiently* even when authenticated (it then
# redirects), which races the credential fill — so we never force that route.
LOGIN_URL = ORIGIN + "/LCDDigitalOneConsumer/"
_SB_BASE = ORIGIN + "/consumer-sb/service/d1"
OVERVIEW_MARKER = "Accounts"

# EverBank delivers the OTP by SMS from short code 25412:
#   "EverBank passcode: 123456. DO NOT SHARE. ..."
EVERBANK_SMS_SENDER = "25412"
EVERBANK_SMS_REGEX = r"passcode[:\s]*?(\d{6})"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)


# --------------------------------------------------------------------------
# device token
# --------------------------------------------------------------------------
def _device_token(service: str) -> str:
    """Build the base64 ``_deviceToken`` for a given service call.

    Tab-delimited; only field[9]=epoch-ms, field[11]=service and field[14]=rquid
    matter. field[12] (normally UA|phone|email|SSN) carries just the UA — the
    server does not validate it, so no PII is embedded.
    """
    ts = str(int(datetime.now(tz=UTC).timestamp() * 1000))
    rquid = str(uuid.uuid4())
    parts = [
        "2",
        "",
        "2",
        "Chrome",
        "150.0.0.0",
        "1",
        str(uuid.uuid4()),
        "digitalBANKING-1.0.0.0",
        "",
        ts,
        "",
        service,
        _USER_AGENT,
        "en",
        rquid,
        "",
        "1",
        "undefined",
        "digitalBANKING",
        " \n",
    ]
    return base64.b64encode("\t".join(parts).encode()).decode()


def build_client(cookies: dict[str, str]) -> httpx.Client:
    """Build an httpx client carrying the browser cookies + the ``gax`` header."""
    gax = cookies.get("gix", "")
    return httpx.Client(
        base_url=_SB_BASE,
        cookies=cookies,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": ORIGIN,
            "Referer": ORIGIN + "/LCDDigitalOneConsumer/",
            "gax": gax,
        },
        follow_redirects=True,
        timeout=30.0,
    )


def call_service(
    client: httpx.Client, service: str, params: list, optional: dict | None = None
) -> dict:
    """POST one ``consumer-sb`` service and return the parsed JSON body.

    Sends a fresh ``rquid`` header + matching device token per call.
    """
    body: dict = {
        "_credentials": {"_deviceToken": _device_token(service)},
        "_lang": "en",
        "_params": params,
    }
    if optional is not None:
        body["_optionalParams"] = optional
    resp = client.post(f"/{service}", json=body, headers={"rquid": str(uuid.uuid4())})
    resp.raise_for_status()
    data = resp.json()
    status = data.get("status", {})
    if status.get("code") not in (0, None):
        raise RuntimeError(f"{service} failed: {status.get('msg')!r}")
    return data


# --------------------------------------------------------------------------
# login
# --------------------------------------------------------------------------
def _login_failure_screenshot(sb, session_id: str) -> None:
    try:
        folder = SNAPSHOTS_DIR / "everbank"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"login_failure_{session_id}_{datetime.now(tz=UTC).strftime('%H%M%S')}.png"
        sb.cdp.save_screenshot(name, folder=str(folder))
        logger.error(
            "everbank_login_failed",
            session_id=session_id,
            screenshot=str(folder / name),
            url=sb.cdp.get_current_url(),
        )
    except Exception as exc:  # noqa: BLE001 — never mask the original error
        logger.error("everbank_login_screenshot_failed", error=str(exc))


def _login_form_appears(sb, user_field: str, settle_s: int = 20) -> bool:
    """True if the login form shows within ``settle_s`` (=> we must log in).

    Returns False if it never appears — the persistent session routed us straight
    to the dashboard, so no login (and no SMS) is needed.
    """
    try:
        sb.cdp.wait_for_element_visible(user_field, timeout=settle_s)
        return True
    except Exception:  # noqa: BLE001 — absence means already authenticated
        return False


def _resolve_credentials(session_id: str, auth: AuthMode) -> tuple[str, str]:
    if auth == "manual":
        print(f"[EverBank] Enter credentials for {session_id!r} (password hidden):")
        return input("  User ID: ").strip(), getpass.getpass("  Password: ")
    return get_bank_username(session_id), get_bank_password(session_id)


def login_and_get_cookies(
    session_id: str = "everbank",
    *,
    auth: AuthMode = "service_account",
    interactive: bool = False,
) -> dict[str, str]:
    """Log into EverBank via SeleniumBase and return the session cookies."""
    username, password = _resolve_credentials(session_id, auth)

    _USER_FIELD = "input[name='j_username_formWidget']"

    with open_session(session_id) as sb:
        try:
            sb.activate_cdp_mode(LOGIN_URL)

            # The persistent profile may still hold a valid session -> the app
            # routes to the dashboard and the login form never shows. Probe for
            # the username field: if it appears we must log in; if it never does
            # (within the settle window) we're already authenticated.
            if _login_form_appears(sb, _USER_FIELD):
                for sel in ("#onetrust-accept-btn-handler", "button[aria-label='Close banner']"):
                    try:
                        sb.cdp.click_if_visible(sel)
                    except Exception:  # noqa: BLE001 — best-effort dismissal
                        pass

                sb.cdp.type(_USER_FIELD, username)
                sb.cdp.type("input[name='j_password_formWidget']", password)
                sb.cdp.click("button[type='submit']")

                # 2FA — pick "Text me" (an Angular list item, not a real
                # <button>), then enter the 6-digit code.
                sb.cdp.wait_for_text("Text me", timeout=45)
                code_requested_at = datetime.now(tz=UTC)
                _click_by_text(sb, "Text me")

                sb.cdp.wait_for_element_visible("input[aria-label='first digit pin']", timeout=45)
                code = get_sms_code(
                    after=code_requested_at,
                    sender_pattern=EVERBANK_SMS_SENDER,
                    code_regex=EVERBANK_SMS_REGEX,
                    timeout_s=150,
                )
                if not code:
                    if interactive:
                        input("2FA not auto-read. Enter it in the browser, then press ENTER... ")
                    else:
                        raise RuntimeError("EverBank 2FA code was not received within timeout")
                else:
                    _type_otp(sb, code)
                    _click_by_text(sb, "Continue")

            sb.cdp.wait_for_text(OVERVIEW_MARKER, timeout=60)
            cookies = _cookies_to_dict(sb.cdp.get_all_cookies())
            logger.info("everbank_login_ok", session_id=session_id, cookie_count=len(cookies))
            return cookies
        except Exception:
            _login_failure_screenshot(sb, session_id)
            raise


_ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth"]


def _click_by_text(sb, text: str) -> None:
    """Click the visible element whose exact text is ``text`` (or its clickable
    ancestor). The Temenos SPA renders "Text me" / "Continue" as list items /
    styled elements, not always real ``<button>``s, so a plain selector misses.
    A synthetic ``.click()`` still fires the Angular ``(click)`` handler.
    """
    js = (
        "(() => {const els=[...document.querySelectorAll('button,a,li,div,span,[role=button]')];"
        f"const t=els.reverse().find(e=>e.textContent.trim()==={text!r} && e.offsetParent!==null);"
        "if(t){(t.closest('button,a,li,[role=button]')||t).click();return true;}return false;})()"
    )
    if not sb.cdp.evaluate(js):
        raise RuntimeError(f"EverBank login: could not find clickable {text!r}")


def _type_otp(sb, code: str) -> None:
    """Fill the 6 single-digit OTP boxes (one input per digit)."""
    for i, digit in enumerate(code[:6]):
        sb.cdp.type(f"input[aria-label='{_ORDINALS[i]} digit pin']", digit)


def _cookies_to_dict(raw_cookies) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in raw_cookies or []:
        if isinstance(c, dict):
            name, value = c.get("name"), c.get("value")
        else:
            name, value = getattr(c, "name", None), getattr(c, "value", None)
        if name is not None and value is not None:
            out[name] = value
    return out
