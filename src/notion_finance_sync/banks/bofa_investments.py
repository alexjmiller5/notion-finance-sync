"""BofA Private Bank (U.S. Trust) IRA + Investment Management scraper.

Shares the ``bofa`` browser login. From the Accounts Overview each U.S. Trust
account exposes a gcslsso SSO link (``target=gcslsso&...&target_page=account
summary&common_hash=...``) into ``auth.privatebank.bankofamerica.com``; from
there ``/TFPActivity/Activity.aspx`` is the transaction feed. We set the time
filter (last 30 days for daily syncs, All Available for the seed), then parse the
activity grid into TransactionRecords (Account Type IRA / Brokerage). Holdings are
NOT written — transactions are the source of truth; value is a frontend concern.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import structlog

from notion_finance_sync.banks.bofa import investments, session
from notion_finance_sync.browser.factory import open_session
from notion_finance_sync.models import AccountType, CategoryMap, TransactionRecord

logger = structlog.get_logger(__name__)

_PB = "https://auth.privatebank.bankofamerica.com"
_ACTIVITY_URL = _PB + "/TFPActivity/Activity.aspx?as_cd=1.1.1.1"
_SSO_RE = re.compile(
    r'href="([^"]*target=gcslsso[^"]*target_page=accountsummary[^"]*common_hash=[^"]*)"'
)
# The Activity date-range <select> (option value -> what to pick per period).
_PERIOD_SELECT = "select[id*='dateControlSelectDates']"
_PERIOD_VALUE = {"recent": "last30", "all": "all"}
_PERIOD_TEXT = {"recent": "Last 30 Days", "all": "All Available"}


@dataclass(frozen=True)
class _Account:
    notion_label: str
    account_name: str
    account_type: AccountType
    source_account_id: str


_IRA = _Account("BofA Roth IRA", "IRA ALEXANDER MILLER (ROTH)", AccountType.IRA, "bofa-roth-ira")
_IM = _Account(
    "BofA Investment Management", "IM ALEXANDER MILLER", AccountType.BROKERAGE, "bofa-im"
)
def _account_for(switcher_text: str) -> _Account | None:
    """Map a portal account-switcher link's text to the account it selects."""
    t = switcher_text.upper()
    if "ROTH" in t or "IRA ALEXANDER" in t:
        return _IRA
    if "IM ALEXANDER" in t or t.strip().startswith("IM"):
        return _IM
    return None


def _confirm_retrieval(sb) -> bool:
    """Click 'Yes, continue data retrieval' on the long-retrieval alert if shown.

    A large date range triggers an in-page "Activity Data Retrieval Alert"
    ("Data retrieval may take longer than expected. Would you like to continue?")
    — without confirming, the grid returns only the default window.
    """
    return bool(
        sb.cdp.evaluate(
            "(() => {"
            "  for (const el of document.querySelectorAll('a,button,input')) {"
            "    const t = (el.innerText || el.value || '').toLowerCase();"
            "    if (t.includes('continue data retrieval')"
            "        || (t.includes('yes') && t.includes('continue'))) {"
            "      el.click(); return true; } }"
            "  return false; })()"
        )
    )


def _wait_confirm_retrieval(sb, tries: int = 8) -> bool:
    """Poll for the retrieval-alert (it appears a beat after Apply/paging)."""
    for _ in range(tries):
        if _confirm_retrieval(sb):
            sb.cdp.sleep(12)  # long retrieval after confirming
            return True
        sb.cdp.sleep(3)
    return False


def _set_period(sb, period: str) -> str:
    """Select the date range AND click Apply (staging the option isn't enough).

    Picking an option in the date <select> only stages the date inputs; the grid
    re-queries only when the "Apply" button (``ApplyActivityFilter()``) is
    clicked. Returns the applied ``period start..end`` for validation/logging.
    """
    try:
        sb.cdp.select_option_by_value(_PERIOD_SELECT, _PERIOD_VALUE[period])
    except Exception:  # noqa: BLE001 — fall back to matching by visible text
        try:
            sb.cdp.select_option_by_text(_PERIOD_SELECT, _PERIOD_TEXT[period])
        except Exception:  # noqa: BLE001
            pass
    sb.cdp.sleep(3)
    sb.cdp.evaluate(
        "(() => { if (typeof ApplyActivityFilter === 'function') { ApplyActivityFilter(); return; }"
        "  const b = document.querySelector('#btnASApply'); if (b) b.click(); })()"
    )
    sb.cdp.sleep(4)
    _wait_confirm_retrieval(sb)  # confirm the "may take longer" alert if it appears
    sb.cdp.sleep(14)  # filter apply postback + grid reload
    # Read back the applied range so the log proves whether it actually widened.
    info = sb.cdp.evaluate(
        "(() => {"
        "  const p = document.querySelector(\"select[id*='dateControlSelectDates']\");"
        "  const s = document.querySelector('#dateFilterStartDate');"
        "  const e = document.querySelector('#dateFilterEndDate');"
        "  const period = p && p.selectedIndex >= 0 ? p.options[p.selectedIndex].text : '?';"
        "  return period + ' ' + (s ? s.value : '?') + '..' + (e ? e.value : '?'); })()"
    )
    return info if isinstance(info, str) else str(info)


def _parse_all_pages(sb, *, account_name, source_account_id, account_type):
    """Parse the activity grid across all result pages (dedup by source_id)."""
    out: list[TransactionRecord] = []
    seen: set[str] = set()
    for _ in range(40):  # safety cap
        html = sb.cdp.evaluate("document.documentElement.outerHTML") or ""
        for r in investments.parse_activity(
            html,
            account_name=account_name,
            source_account_id=source_account_id,
            account_type=account_type,
        ):
            if r.source_id not in seen:
                seen.add(r.source_id)
                out.append(r)
        advanced = sb.cdp.evaluate(
            "(() => { const n = document.querySelector('#PaginationCtrlBottom_next')"
            " || document.querySelector('#PaginationCtrlTop_next');"
            "  if (n && !n.classList.contains('disabled')) { n.click(); return true; }"
            "  return false; })()"
        )
        if not advanced:
            break
        sb.cdp.sleep(6)
        _confirm_retrieval(sb)  # paging a large range can re-trigger the alert
    return out


class BofAInvestmentsScraper:
    SESSION_ID = "bofa"  # shares the bofa login
    BANK_DISPLAY_NAME = "BofA Investments"
    SUPPORTS_LIVE = True

    # Investment accounts don't expose bank-category labels (not a spending account).
    CATEGORY_MAP: CategoryMap = {}

    def _scrape(self, period: str) -> list[TransactionRecord]:
        with open_session(self.SESSION_ID) as sb:
            try:
                session.perform_login(sb, session_id=self.SESSION_ID)
                # SSO once into the private bank, then switch accounts via the
                # portal's own switcher (as_cd query param) — re-SSO doesn't switch.
                overview = sb.cdp.evaluate("document.documentElement.outerHTML") or ""
                hrefs = [h.replace("&amp;", "&") for h in _SSO_RE.findall(overview)]
                logger.info("bofa_inv_sso_links", count=len(hrefs))
                if not hrefs:
                    return []
                sb.cdp.open("https://secure.bankofamerica.com" + hrefs[0])
                sb.cdp.sleep(10)  # gcslsso -> private-bank redirect chain
                sb.cdp.open(_ACTIVITY_URL)
                sb.cdp.sleep(6)

                switcher = sb.cdp.evaluate(
                    "JSON.stringify([...document.querySelectorAll('a[id^=\"act_act_\"]')]"
                    ".map(a => ({href: a.getAttribute('href'), text: (a.innerText||'').trim()})))"
                )
                links = json.loads(switcher) if isinstance(switcher, str) else (switcher or [])
                logger.info("bofa_inv_accounts", count=len(links))

                records: list[TransactionRecord] = []
                seen: set[str] = set()
                for link in links:
                    acct = _account_for(link.get("text", ""))
                    if acct is None or acct.source_account_id in seen:
                        continue
                    seen.add(acct.source_account_id)
                    sb.cdp.open(_PB + "/TFPActivity/" + link["href"])  # as_cd switches account
                    sb.cdp.sleep(6)
                    applied = _set_period(sb, period)
                    recs = _parse_all_pages(
                        sb,
                        account_name=acct.account_name,
                        source_account_id=acct.source_account_id,
                        account_type=acct.account_type,
                    )
                    for r in recs:
                        r.credit_card_account = acct.notion_label
                    logger.info(
                        "bofa_inv_scraped",
                        account=acct.notion_label,
                        applied=applied,
                        count=len(recs),
                    )
                    records.extend(recs)
                return records
            except Exception:
                session._login_failure_screenshot(sb, self.SESSION_ID)
                raise

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        recs = self._scrape("recent")
        return [r for r in recs if r.transaction_date and r.transaction_date >= since]

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        recs = self._scrape("all")
        return [r for r in recs if r.transaction_date and start <= r.transaction_date <= end]

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError("BofA investment statements are not archived (live feed only)")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        raise NotImplementedError("BofA investment statements are not archived (live feed only)")
