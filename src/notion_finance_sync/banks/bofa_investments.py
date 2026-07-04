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


def _set_period(sb, period: str) -> str:
    """Set the Activity date-range <select> via a NATIVE selection.

    The select's ``onchange="ProcessDropdownChange()"`` (defined in external JS)
    does the postback; a JS ``value=...`` shim doesn't trigger it, so use
    SeleniumBase's native select which fires the real change event.
    """
    try:
        sb.cdp.select_option_by_value(_PERIOD_SELECT, _PERIOD_VALUE[period])
    except Exception:  # noqa: BLE001 — fall back to matching by visible text
        sb.cdp.select_option_by_text(_PERIOD_SELECT, _PERIOD_TEXT[period])
    sb.cdp.sleep(14)  # ProcessDropdownChange postback + grid reload
    return _PERIOD_TEXT[period]


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
                    picked = _set_period(sb, period)
                    html = sb.cdp.evaluate("document.documentElement.outerHTML") or ""
                    recs = investments.parse_activity(
                        html,
                        account_name=acct.account_name,
                        source_account_id=acct.source_account_id,
                        account_type=acct.account_type,
                    )
                    for r in recs:
                        r.credit_card_account = acct.notion_label
                    logger.info(
                        "bofa_inv_scraped",
                        account=acct.notion_label,
                        period=picked,
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
