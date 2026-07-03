"""Stepwise autonomous recon of the Fidelity NetBenefits 401k flow.

NOT the scraper. Run phases incrementally to discover selectors + endpoints:

    uv run python scripts/recon_fidelity.py login-page   # capture login page only
    uv run python scripts/recon_fidelity.py login        # full login (+2FA) -> capture landing
    uv run python scripts/recon_fidelity.py explore URL  # navigate somewhere logged-in, capture

Artifacts land in data/snapshots/fidelity/recon_<stamp>/ (gitignored).
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from notion_finance_sync.browser.factory import open_session
from notion_finance_sync.config.settings import get_bank_password, get_bank_username

SNAPSHOT_ROOT = Path(__file__).resolve().parents[1] / "data" / "snapshots" / "fidelity"
LOGIN_URL = "https://digital.fidelity.com/prgw/digital/login/full-page"

# Wrap fetch + XHR so every /ftgw/ API call's request AND response body is
# recorded to window.__recon (recon only; never part of the scraper).
RECORDER_JS = """
(() => {
  if (window.__recon) return 'already-hooked';
  window.__recon = [];
  const keep = (url) => url && url.includes('/ftgw/');
  const origFetch = window.fetch;
  window.fetch = async function(input, init) {
    const url = typeof input === 'string' ? input : (input && input.url);
    const resp = await origFetch.apply(this, arguments);
    if (keep(url)) {
      try {
        const body = init && init.body ? String(init.body) : null;
        const clone = resp.clone();
        const text = await clone.text();
        window.__recon.push({kind: 'fetch', url, method: (init && init.method) || 'GET',
                             requestBody: body, status: resp.status,
                             response: text.slice(0, 500000)});
      } catch (e) {}
    }
    return resp;
  };
  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  const origSetHeader = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__reconInfo = {method, url, headers: {}};
    return origOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function(k, v) {
    if (this.__reconInfo) this.__reconInfo.headers[k] = v;
    return origSetHeader.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function(body) {
    const info = this.__reconInfo || {};
    if (keep(info.url)) {
      this.addEventListener('load', () => {
        try {
          window.__recon.push({kind: 'xhr', url: info.url, method: info.method,
                               headers: info.headers,
                               requestBody: body ? String(body) : null, status: this.status,
                               response: String(this.responseText || '').slice(0, 500000)});
        } catch (e) {}
      });
    }
    return origSend.apply(this, arguments);
  };
  return 'hooked';
})()
"""


def _capture(sb, out_dir: Path, label: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        url = sb.cdp.get_current_url()
        (out_dir / f"{label}_url.txt").write_text(url + "\n")
        print(f"  [ok] URL -> {url}")
    except Exception as e:  # noqa: BLE001
        print(f"  [!!] url: {e!r}")
    try:
        html = sb.cdp.get_page_source()
        (out_dir / f"{label}.html").write_text(html)
        print(f"  [ok] HTML -> {label}.html ({len(html):,} bytes)")
    except Exception as e:  # noqa: BLE001
        print(f"  [!!] html: {e!r}")
    try:
        sb.cdp.save_screenshot(f"{label}.png", folder=str(out_dir))
        print(f"  [ok] screenshot -> {label}.png")
    except Exception as e:  # noqa: BLE001
        print(f"  [!!] screenshot: {e!r}")
    try:
        resources = sb.cdp.evaluate(
            "JSON.stringify(performance.getEntriesByType('resource')"
            ".filter(r => ['xmlhttprequest','fetch'].includes(r.initiatorType))"
            ".map(r => r.name))"
        )
        (out_dir / f"{label}_xhrs.json").write_text(
            resources if isinstance(resources, str) else json.dumps(resources)
        )
        print(f"  [ok] xhr list -> {label}_xhrs.json")
    except Exception as e:  # noqa: BLE001
        print(f"  [!!] xhrs: {e!r}")


def main() -> None:
    phase = sys.argv[1] if len(sys.argv) > 1 else "login-page"
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    out_dir = SNAPSHOT_ROOT / f"recon_{stamp}_{phase.replace('/', '_')[:20]}"

    with open_session("fidelity") as sb:
        sb.activate_cdp_mode(LOGIN_URL)
        time.sleep(8)
        if phase == "login-page":
            _capture(sb, out_dir, "login_page")
            print(f"\nSaved to {out_dir}")
            return

        # --- login phase: fill creds, capture whatever comes next ---
        username = get_bank_username("fidelity")
        password = get_bank_password("fidelity")
        try:
            sb.cdp.wait_for_element_visible("#dom-username-input", timeout=30)
            sb.cdp.type("#dom-username-input", username)
            sb.cdp.type("#dom-pswd-input", password)
            sb.cdp.click("#dom-login-button")
            print("  [ok] submitted credentials")
        except Exception as e:  # noqa: BLE001
            print(f"  [!!] login form: {e!r}")
            _capture(sb, out_dir, "login_form_failure")
            return
        time.sleep(12)
        _capture(sb, out_dir, "post_login")

        if phase == "twofa-options":
            try:
                sb.cdp.wait_for_element_visible("#dom-try-another-way-link", timeout=20)
                sb.cdp.click("#dom-try-another-way-link")
                time.sleep(6)
                _capture(sb, out_dir, "twofa_options")
            except Exception as e:  # noqa: BLE001
                print(f"  [!!] try-another-way: {e!r}")
                _capture(sb, out_dir, "twofa_options_failure")

        if phase == "sms-login":
            from notion_finance_sync.twofa.sms import get_sms_code

            sb.cdp.wait_for_element_visible("#dom-try-another-way-link", timeout=20)
            sb.cdp.click("#dom-try-another-way-link")
            sb.cdp.wait_for_element_visible("#dom-channel-list-primary-button", timeout=20)
            code_requested_at = datetime.now(tz=UTC)
            sb.cdp.click("#dom-channel-list-primary-button")  # "Text me the code"
            time.sleep(6)
            _capture(sb, out_dir, "code_entry")
            # Find the code input + trust checkbox + submit dynamically.
            info = sb.cdp.evaluate(
                "JSON.stringify({"
                "inputs: [...document.querySelectorAll('input')]"
                ".map(i => ({id:i.id, type:i.type})),"
                "buttons: [...document.querySelectorAll('button')]"
                ".map(b => ({id:b.id, text:(b.textContent||'').trim().slice(0,50)}))"
                "})"
            )
            print(f"  [info] entry page elements: {info}")
            code = get_sms_code(
                after=code_requested_at,
                sender_pattern="36726",
                code_regex=r"(?i)code\s+is\D{0,3}(\d{6})",
                timeout_s=150,
            )
            print(f"  [info] sms code read: {code!r}")
            if code:
                parsed = json.loads(info) if isinstance(info, str) else info
                inp = next(
                    (
                        i["id"]
                        for i in parsed["inputs"]
                        if i["id"] and i["type"] in ("text", "tel", "number", "password")
                    ),
                    None,
                )
                trust = next(
                    (i["id"] for i in parsed["inputs"] if i["id"] and "trust" in i["id"]), None
                )
                btn = next(
                    (
                        b["id"]
                        for b in parsed["buttons"]
                        if b["id"] and ("submit" in b["id"] or "primary" in b["id"])
                    ),
                    None,
                )
                print(f"  [info] using input={inp} trust={trust} submit={btn}")
                sb.cdp.type(f"#{inp}", code)
                if trust:
                    try:
                        sb.cdp.click(f"#{trust}")
                    except Exception as e:  # noqa: BLE001
                        print(f"  [!!] trust checkbox: {e!r}")
                sb.cdp.click(f"#{btn}")
                time.sleep(15)
                _capture(sb, out_dir, "landing")

        if phase == "activity":
            # Hook fetch/XHR to record API request+response bodies, then click
            # the Activity & Orders tab (in-page SPA nav keeps the hook alive).
            sb.cdp.evaluate(RECORDER_JS)
            clicked = sb.cdp.evaluate(
                "(() => { const el = [...document.querySelectorAll('a,button,[role=tab]')]"
                ".find(e => (e.textContent||'').includes('Activity & Orders'));"
                "if (el) { el.click(); return el.tagName; } return null; })()"
            )
            print(f"  [info] clicked activity tab: {clicked!r}")
            time.sleep(20)
            _capture(sb, out_dir, "activity")
            rec = sb.cdp.evaluate("JSON.stringify(window.__recon || [])")
            (out_dir / "activity_api_recording.json").write_text(
                rec if isinstance(rec, str) else json.dumps(rec)
            )
            print(f"  [ok] api recording -> activity_api_recording.json ({len(rec):,} chars)")

        if phase == "history-wide":
            sb.cdp.open("https://digital.fidelity.com/ftgw/digital/portfolio/activity")
            time.sleep(15)
            # Replay the history POST with a 2-year window straight from the page
            # (carries the session cookies + CSRF automatically).
            sb.cdp.evaluate("""
            window.__hist = 'pending';
            (async () => {
              const now = 1783091773;
              const windows = [300, 330, 350, 360, 364, 365, 366];
              const out = {};
              const call = async (body, full) => {
                const r = await fetch('/ftgw/digital/activityapi/api/v1/transactions/history',
                  {method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json',
                   'appId':'ap182468','appName':'activity-orders-ui'},
                   credentials:'include',body:JSON.stringify(body)});
                const t = await r.text();
                let n = null; try { n = JSON.parse(t).data.transactions.length; } catch(e){}
                return {status:r.status, count:n, body: (r.status===200 && !full) ? undefined : t};
              };
              for (const days of windows) {
                const from = now - days*86400;
                out[days] = await call({filter:{accounts:[
                  {acctNum:"259079998",acctName:"Uk9USCBJUkE=",acctType:"Brokerage"},
                  {acctNum:"30072",acctName:"Q0FQSVRBTCBPTkUgNDAxSyBBU1A=",acctType:"WPS"}],
                  searchCriteriaDetail:{txnFromDate:from,txnToDate:now,
                    includeBasketNames:false,includeCoreFundSettlementTransactions:false}}}, false);
              }
              // full 180-day response saved for fixtures
              out['full180'] = await call({filter:{accounts:[
                {acctNum:"259079998",acctName:"Uk9USCBJUkE=",acctType:"Brokerage"},
                {acctNum:"30072",acctName:"Q0FQSVRBTCBPTkUgNDAxSyBBU1A=",acctType:"WPS"}],
                searchCriteriaDetail:{txnFromDate:now-180*86400,txnToDate:now,
                  includeBasketNames:false,includeCoreFundSettlementTransactions:false}}}, true);
              window.__hist = JSON.stringify(out);
            })();
            """)
            out = "pending"
            for _ in range(40):
                time.sleep(2)
                out = sb.cdp.evaluate("window.__hist")
                if out and out != "pending":
                    break
            (out_dir / "history_2yr.json").write_text(
                out if isinstance(out, str) else json.dumps(out)
            )
            print(f"  [ok] 2yr history -> history_2yr.json ({len(out):,} chars)")

        if phase == "explore" and len(sys.argv) > 2:
            sb.cdp.open(sys.argv[2])
            time.sleep(12)
            _capture(sb, out_dir, "explore")

        print(f"\nSaved to {out_dir}")


if __name__ == "__main__":
    main()
