"""Recon: capture the Merrill Edge Roth IRA holdings + activity pages.

The IRA lives behind a gcslsso single-sign-on from the BofA overview
(``target=gcslsso&...&target_page=accountsummary&common_hash=<hash>``). This logs
in, follows that SSO into Merrill, hooks the network, and dumps the landing +
holdings + activity pages (HTML + XHR/JSON) so we can build the parser offline.

Exploratory — captures generously (page HTML, every anchor, every network call,
screenshots) into data/snapshots/bofa/merrill_ira_recon/.
"""

from __future__ import annotations

# ruff: noqa: E501 — inline JS/eval strings are intentionally long
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from notion_finance_sync.banks.bofa.session import (
    BOFA_SMS_REGEX,
    BOFA_SMS_SENDER,
    _resolve_credentials,
)
from notion_finance_sync.browser.factory import open_session
from notion_finance_sync.twofa.sms import get_sms_code

OUT = Path("data/snapshots/bofa/merrill_ira_recon")

HOOK_JS = """
window.__recon = [];
const of = window.fetch;
window.fetch = async function(i, init) {
    const url = typeof i === 'string' ? i : i.url;
    const body = init && init.body ? String(init.body) : null;
    const r = await of.apply(this, arguments);
    let t = null; try { t = await r.clone().text(); } catch (e) {}
    window.__recon.push({url, method:(init&&init.method)||'GET', body, status:r.status, response:t});
    return r;
};
const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.open = function(m,u){ this.__m=m; this.__u=u; return oo.apply(this,arguments); };
XMLHttpRequest.prototype.send = function(b){
    this.addEventListener('load', () => window.__recon.push(
        {url:this.__u, method:this.__m, body:b?String(b):null, status:this.status, response:this.responseText}));
    return os.apply(this, arguments);
};
"""


def _login(sb) -> None:
    username, password = _resolve_credentials("bofa", "service_account")
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
    sb.cdp.wait_for_any_of_elements_present(["#authcodeTextReceive", "#ahAuthcodeValidateOTP"], timeout=45)
    if sb.cdp.is_element_present("#authcodeTextReceive"):
        sb.cdp.click("#authcodeTextReceive")
        requested = datetime.now(tz=UTC)
        sb.cdp.click("#ah-authcode-select-continue-btn")
        sb.cdp.wait_for_element_visible("#ahAuthcodeValidateOTP", timeout=45)
    code = get_sms_code(after=requested, sender_pattern=BOFA_SMS_SENDER, code_regex=BOFA_SMS_REGEX, timeout_s=150)
    assert code, "no 2FA code"
    sb.cdp.type("#ahAuthcodeValidateOTP", code)
    sb.cdp.click("#ah-authcode-validate-continue-btn")
    sb.cdp.wait_for_text("Accounts Overview", timeout=60)
    print("[OK] logged in")


def _dump(sb, label: str) -> None:
    d = OUT / label
    d.mkdir(parents=True, exist_ok=True)
    time.sleep(3)
    html = sb.cdp.evaluate("document.documentElement.outerHTML") or ""
    (d / "page.html").write_text(html if isinstance(html, str) else str(html))
    links = sb.cdp.evaluate(
        "JSON.stringify([...document.querySelectorAll('a')].map(a=>({t:(a.innerText||'').trim().slice(0,40),h:a.href})).filter(x=>x.t))"
    )
    (d / "links.json").write_text(links if isinstance(links, str) else json.dumps(links))
    calls = sb.cdp.evaluate("JSON.stringify(window.__recon||[])")
    calls = json.loads(calls) if isinstance(calls, str) else (calls or [])
    (d / "network.json").write_text(json.dumps(calls, indent=2))
    try:
        sb.cdp.save_screenshot("page.png", folder=str(d))
    except Exception:  # noqa: BLE001
        pass
    url = sb.cdp.get_current_url()
    print(f"[dump {label}] url={url} html={len(html)}b links, {len(calls)} network calls")
    for c in calls:
        if any(k in c["url"] for k in ("position", "holding", "activity", "history", "transaction", "balance")):
            print(f"    * {c['method']} {c['status']} {c['url'][:110]}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with open_session("bofa") as sb:
        _login(sb)
        # Grab the live overview and find the IRA's gcslsso SSO href.
        overview = sb.cdp.evaluate("document.documentElement.outerHTML") or ""
        (OUT).mkdir(exist_ok=True)
        (OUT / "overview.html").write_text(overview)
        hrefs = re.findall(
            r'href="([^"]*target=gcslsso[^"]*target_page=accountsummary[^"]*common_hash=[^"]*)"', overview
        )
        hrefs = [h.replace("&amp;", "&") for h in hrefs]
        print(f"[sso] found {len(hrefs)} USTLink accountsummary hrefs")
        for h in hrefs:
            print("   ", h[:140])
        if not hrefs:
            print("[!] no gcslsso accountsummary href found — inspect overview.html")
            return
        # SSO into the first (IRA is typically first; capture content tells us which).
        target = "https://secure.bankofamerica.com" + hrefs[0]
        sb.cdp.open(target)
        time.sleep(10)  # gcslsso -> Merrill redirect chain
        sb.cdp.evaluate(HOOK_JS)
        time.sleep(6)
        _dump(sb, "merrill_landing")

        # From the landing nav, follow Holdings/Positions and Activity/History.
        for label, needles in (("holdings", ("holding", "position")), ("activity", ("activity", "history", "transaction"))):
            link = sb.cdp.evaluate(
                "(() => { for (const a of document.querySelectorAll('a')) {"
                f"  const t=(a.innerText||'').toLowerCase();"
                f"  if ([{','.join(repr(n) for n in needles)}].some(n=>t.includes(n))) return a.href;"
                "} return null; })()"
            )
            if link:
                print(f"[nav] {label} -> {link}")
                sb.cdp.open(link)
                time.sleep(6)
                sb.cdp.evaluate(HOOK_JS)
                time.sleep(6)
                _dump(sb, label)
            else:
                print(f"[nav] no {label} link found on landing page")


if __name__ == "__main__":
    main()
