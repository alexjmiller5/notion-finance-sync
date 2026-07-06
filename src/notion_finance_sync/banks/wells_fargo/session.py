"""Wells Fargo live session: SeleniumBase login -> open Autograph -> is the card used yet?

Only this module drives a real browser. Login is username/password (#userid/#password/
#btnSignon) with **Advanced Access** 2FA (SMS, sender 93557) that is only challenged
intermittently — WF honours device trust on the persistent profile (unlike BofA).

Recon lessons applied (see data/snapshots/wells_fargo/FINDINGS.md):
- Explicit waits, not fixed sleeps.
- Screenshot-on-failure to data/snapshots/wells_fargo/.
- Navigate by clicking in-page only (direct deep-URL nav bounces to /auth/logout).
- The transactions ``_x`` nonce is single-use and session-bound, so we never mint the
  request ourselves.

The card is currently unused (0 transactions), and the per-transaction online JSON shape
was never observed, so this does NOT parse rows. It reads the account-details page's
"no recent activity" marker and returns a simple boolean — the trigger for Alex to build
out the full online parser once the card is actually used.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import structlog

from notion_finance_sync.browser.factory import open_session
from notion_finance_sync.config.paths import SNAPSHOTS_DIR
from notion_finance_sync.config.settings import get_bank_password, get_bank_username
from notion_finance_sync.twofa.sms import get_sms_code

logger = structlog.get_logger()

LOGIN_URL = "https://www.wellsfargo.com/"
SUMMARY_MARKER = "Account Summary"
INTERDICTION_MARKER = "Verify Your Identity"
# The account-details page shows this exact copy when the card has no posted activity.
# Its disappearance is the signal that the card is finally being used.
NO_ACTIVITY_MARKER = "no recent activity"
POSTED_REGION_MARKER = "Posted Transactions"

# 2FA: WF Advanced Access SMS. Sender short code + code format validated live 2026-07-03.
WF_SMS_SENDER = "93557"
WF_SMS_REGEX = r"Advanced Access code\s+(\d{6})"
WF_2FA_PHONE_TAIL = "6345"  # Alex's phone; the delivery-select list item to click

_CLICK_AUTOGRAPH_JS = (
    "(function(){const e=[...document.querySelectorAll('button,a')]"
    ".find(x=>/AUTOGRAPH/i.test(x.textContent||''));if(e){e.click();return 'ok';}return 'nf';})()"
)


def _screenshot(sb, session_id: str, label: str) -> None:
    try:
        folder = SNAPSHOTS_DIR / "wells_fargo"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{label}_{session_id}_{datetime.now(tz=UTC).strftime('%H%M%S')}.png"
        sb.cdp.save_screenshot(name, folder=str(folder))
        logger.error(
            "wf_failure_screenshot",
            label=label,
            path=str(folder / name),
            url=sb.cdp.get_current_url(),
        )
    except Exception as exc:  # noqa: BLE001 — never mask the original error
        logger.error("wf_screenshot_failed", error=str(exc))


def _handle_2fa(sb, session_id: str) -> None:
    """Complete Advanced Access SMS 2FA if challenged (no-op if device-trusted)."""
    if INTERDICTION_MARKER not in sb.cdp.get_page_source():
        return
    logger.info("wf_2fa_challenge")
    code_requested_at = datetime.now(tz=UTC)
    sb.cdp.evaluate(
        "(function(t){const e=[...document.querySelectorAll('div,li')]"
        ".find(x=>(x.textContent||'').includes(t)&&(x.className||'').includes('WFListItem'));"
        "if(e)e.click();})('" + WF_2FA_PHONE_TAIL + "')"
    )
    sb.cdp.sleep(5)
    code = get_sms_code(
        after=code_requested_at,
        sender_pattern=WF_SMS_SENDER,
        code_regex=WF_SMS_REGEX,
        timeout_s=150,
    )
    if not code:
        raise RuntimeError("WF Advanced Access code not received within timeout")
    otp_id = sb.cdp.evaluate(
        "(function(){const i=[...document.querySelectorAll('input')]"
        ".find(x=>x.type==='text'||x.type==='tel'||x.getAttribute('inputmode'));"
        "if(i){i.id=i.id||'wf_otp';return i.id;}return '';})()"
    )
    if not otp_id:
        raise RuntimeError("WF 2FA code-entry input not found")
    sb.cdp.type(f"#{otp_id}", code)
    sb.cdp.evaluate(
        "(function(){const b=[...document.querySelectorAll('button,input[type=submit]')]"
        ".find(x=>/continue|verify|submit|done/i.test(x.textContent||x.value||''));if(b)b.click();})()"
    )
    sb.cdp.sleep(8)


def _wait_for_any_text(sb, markers: tuple[str, ...], timeout: int) -> str | None:
    """Poll the page source until one of ``markers`` appears; return it or None."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        src = sb.cdp.get_page_source()
        for marker in markers:
            if marker in src:
                return marker
        time.sleep(1)
    return None


def has_live_activity(session_id: str) -> bool:
    """Log in, open the Autograph card, and report whether it shows any activity yet.

    Returns ``False`` while the account-details page shows "no recent activity" (the card
    is unused). Returns ``True`` once that marker is gone but the posted-transactions region
    has rendered — the signal that Alex should build out the full online parser.
    Robust by design: reads a DOM marker rather than driving the fragile search panel, and
    never mints the single-use ``_x`` request.
    """
    username = get_bank_username(session_id)
    password = get_bank_password(session_id)
    with open_session(session_id) as sb:
        try:
            sb.activate_cdp_mode(LOGIN_URL)
            sb.cdp.wait_for_element_visible("#userid", timeout=30)
            for sel in ("#onetrust-accept-btn-handler", "button[title='Close']"):
                try:
                    sb.cdp.click_if_visible(sel)
                except Exception:  # noqa: BLE001 — best-effort
                    pass
            sb.cdp.type("#userid", username)
            sb.cdp.type("#password", password)
            sb.cdp.click("#btnSignon")

            # After sign-on WF lands on either the summary or an Advanced Access challenge.
            landed = _wait_for_any_text(sb, (SUMMARY_MARKER, INTERDICTION_MARKER), timeout=60)
            if landed is None:
                raise RuntimeError("WF post-login page did not load (no summary / 2FA marker)")
            _handle_2fa(sb, session_id)
            if not _wait_for_any_text(sb, (SUMMARY_MARKER,), timeout=60):
                raise RuntimeError("WF account summary did not load after login")

            if sb.cdp.evaluate(_CLICK_AUTOGRAPH_JS) != "ok":
                raise RuntimeError("WF Autograph account tile not found on summary")
            # Wait for the account-details activity region to render.
            if not _wait_for_any_text(sb, (POSTED_REGION_MARKER,), timeout=45):
                raise RuntimeError("WF account-details activity region did not render")
            sb.cdp.sleep(2)  # let the posted-transactions list settle

            src = sb.cdp.get_page_source().lower()
            no_activity = NO_ACTIVITY_MARKER in src
            logger.info("wf_activity_checked", has_activity=not no_activity)
            return not no_activity
        except Exception:
            _screenshot(sb, session_id, "login_failure")
            raise
