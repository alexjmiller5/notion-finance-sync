"""Recon: discover the checking per-transaction DETAIL endpoint.

Logs in, opens the deposit SPA, hooks window.fetch + XHR to record every call,
expands the first few transaction rows, and dumps captured request/response
pairs to data/snapshots/bofa/checking_detail_recon/.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from notion_finance_sync.banks.bofa.scraper import DEPOSIT_ACCOUNTS
from notion_finance_sync.banks.bofa.session import (
    BOFA_SMS_REGEX,
    BOFA_SMS_SENDER,
    _resolve_credentials,
)
from notion_finance_sync.browser.factory import open_session
from notion_finance_sync.twofa.sms import get_sms_code

OUT = Path("data/snapshots/bofa/checking_detail_recon")

HOOK_JS = """
window.__recon = [];
const origFetch = window.fetch;
window.fetch = async function(input, init) {
    const url = typeof input === 'string' ? input : input.url;
    const body = init && init.body ? String(init.body) : null;
    const resp = await origFetch.apply(this, arguments);
    const clone = resp.clone();
    let text = null;
    try { text = await clone.text(); } catch (e) {}
    window.__recon.push({url: url, method: (init && init.method) || 'GET',
                         body: body, status: resp.status, response: text});
    return resp;
};
const origOpen = XMLHttpRequest.prototype.open;
const origSend = XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.open = function(m, u) { this.__m = m; this.__u = u; return origOpen.apply(this, arguments); };
XMLHttpRequest.prototype.send = function(b) {
    this.addEventListener('load', () => {
        window.__recon.push({url: this.__u, method: this.__m, body: b ? String(b) : null,
                             status: this.status, response: this.responseText});
    });
    return origSend.apply(this, arguments);
};
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    username, password = _resolve_credentials("bofa", "service_account")
    adx = DEPOSIT_ACCOUNTS["Adv Plus Banking - 2093"]

    with open_session("bofa") as sb:
        sb.activate_cdp_mode("https://www.bankofamerica.com/")
        sb.cdp.wait_for_element_visible("#oid", timeout=30)
        for sel in ("#onetrust-accept-btn-handler", "#engagementBannerCloseBtn"):
            try:
                sb.cdp.click_if_visible(sel)
            except Exception:  # noqa: BLE001
                pass
        sb.cdp.type("#oid", username)
        sb.cdp.type("#pass", password)
        requested = datetime.now(tz=UTC)
        sb.cdp.click("#secure-signin-submit")
        sb.cdp.wait_for_any_of_elements_present(
            ["#authcodeTextReceive", "#ahAuthcodeValidateOTP"], timeout=45
        )
        if sb.cdp.is_element_present("#authcodeTextReceive"):
            sb.cdp.click("#authcodeTextReceive")
            requested = datetime.now(tz=UTC)
            sb.cdp.click("#ah-authcode-select-continue-btn")
            sb.cdp.wait_for_element_visible("#ahAuthcodeValidateOTP", timeout=45)
        code = get_sms_code(
            after=requested, sender_pattern=BOFA_SMS_SENDER, code_regex=BOFA_SMS_REGEX, timeout_s=150
        )
        assert code, "no 2FA code"
        sb.cdp.type("#ahAuthcodeValidateOTP", code)
        sb.cdp.click("#ah-authcode-validate-continue-btn")
        sb.cdp.wait_for_text("Accounts Overview", timeout=60)
        print("[OK] logged in")
        _recon(sb, adx)


def _recon(sb, adx: str) -> None:
    # Deposit SPA. Hook BEFORE the app loads its data so we catch everything.
    sb.cdp.open(f"https://secure.bankofamerica.com/deposit-details/activity/?adx={adx}")
    sb.cdp.evaluate(HOOK_JS)
    time.sleep(8)

    # Click the "View/Edit" links — THIS fires the per-txn detail AJAX.
    clicked = sb.cdp.evaluate("""
        (() => {
            const links = document.querySelectorAll('a.view-transaction-details');
            let n = 0;
            for (const a of links) {
                if (n >= 3) break;
                try { a.click(); n++; } catch (e) {}
            }
            return n + ' View/Edit clicked of ' + links.length + ' links';
        })()
    """)
    print("[..] row clicks:", clicked)
    time.sleep(6)

    captured = sb.cdp.evaluate("JSON.stringify(window.__recon || [])")
    calls = json.loads(captured) if isinstance(captured, str) else (captured or [])
    print(f"[OK] captured {len(calls)} network calls")
    (OUT / "captured_calls.json").write_text(json.dumps(calls, indent=2))
    for c in calls:
        print(f"  {c['method']:4} {c['status']} {c['url'][:110]}")
    sb.cdp.save_screenshot("after_clicks.png", folder=str(OUT))


if __name__ == "__main__":
    main()
