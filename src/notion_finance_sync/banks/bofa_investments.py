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
# Option-text substrings to try for each period (first match wins; "all" appended
# as fallback so a daily run never accidentally scrapes nothing).
_PERIOD_NEEDLES = {
    "recent": ["last 30", "past 30", "30 day"],
    "all": ["all avail"],
}


@dataclass(frozen=True)
class _Account:
    notion_label: str
    account_name: str
    account_type: AccountType
    source_account_id: str


def _detect_account(html: str) -> _Account | None:
    if "(ROTH)" in html or "IRA ALEXANDER" in html:
        return _Account(
            "BofA Roth IRA", "IRA ALEXANDER MILLER (ROTH)", AccountType.IRA, "bofa-roth-ira"
        )
    if "IM ALEXANDER" in html:
        return _Account(
            "BofA Investment Management", "IM ALEXANDER MILLER", AccountType.BROKERAGE, "bofa-im"
        )
    return None


def _set_period(sb, period: str) -> str:
    """Set the Activity time-period <select>; returns the option text picked."""
    needles = _PERIOD_NEEDLES[period] + _PERIOD_NEEDLES["all"]  # fall back to All Available
    picked = sb.cdp.evaluate(
        "(() => {"
        f"  const needles = {needles!r};"
        "  for (const n of needles) {"
        "    for (const s of document.querySelectorAll('select')) {"
        "      for (const o of s.options) {"
        "        if (o.text.toLowerCase().includes(n)) {"
        "          s.value = o.value; s.dispatchEvent(new Event('change', {bubbles:true}));"
        "          return o.text; } } } }"
        "  return 'NONE'; })()"
    )
    sb.cdp.sleep(10)  # ASP.NET UpdatePanel reload
    return picked if isinstance(picked, str) else str(picked)


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
                overview = sb.cdp.evaluate("document.documentElement.outerHTML") or ""
                hrefs = [h.replace("&amp;", "&") for h in _SSO_RE.findall(overview)]
                logger.info("bofa_inv_sso_links", count=len(hrefs))

                records: list[TransactionRecord] = []
                seen: set[str] = set()
                for href in hrefs:
                    sb.cdp.open("https://secure.bankofamerica.com" + href)
                    sb.cdp.sleep(10)  # gcslsso -> private-bank redirect chain
                    sb.cdp.open(_ACTIVITY_URL)
                    sb.cdp.sleep(6)
                    picked = _set_period(sb, period)
                    html = sb.cdp.evaluate("document.documentElement.outerHTML") or ""
                    acct = _detect_account(html)
                    if acct is None or acct.source_account_id in seen:
                        continue
                    seen.add(acct.source_account_id)
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
