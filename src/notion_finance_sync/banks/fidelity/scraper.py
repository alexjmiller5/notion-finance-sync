"""FidelityScraper — implements the BankScraper protocol.

One Fidelity login covers the Capital One 401k (acct 30072) + a linked (empty)
Roth IRA. Only the 401k has activity. Flow: SeleniumBase login -> cookies ->
httpx client -> POST the activity/history JSON API -> pure parser.

fetch_recent / fetch_historical are SYNCHRONOUS (they open the browser directly);
the orchestrator calls them via asyncio.to_thread.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import structlog

from notion_finance_sync.banks.fidelity import activity, session
from notion_finance_sync.browser.factory import open_session
from notion_finance_sync.models import TransactionRecord

logger = structlog.get_logger()

# This module is scoped to the 401k ONLY (brief: "Fidelity = 401k").
_ACCT_401K = "30072"

# Accounts requested from the API. acctName is base64 (required by the API);
# acctType is the API's own code (WPS = workplace/defined-contributions).
#
# The login also exposes a Fidelity Roth IRA (acct 259079998, acctName base64
# "ROTH IRA"). It's OUT of scope here — it's a different account type with its
# own history (a 2025 $7k contribution, fund buys/dividends, then an ACAT
# transfer-out), and mislabeling it as 401k is wrong. It belongs to a separate
# module (see fidelity_ira_closed.py / SPEC §16). We both omit it from the
# request AND filter defensively in the parser (only_acct=_ACCT_401K).
_ACCOUNTS = [
    {"acctNum": _ACCT_401K, "acctName": "Q0FQSVRBTCBPTkUgNDAxSyBBU1A=", "acctType": "WPS"},
]

# Fidelity's history endpoint caps the window at 365 days.
_MAX_WINDOW_DAYS = 365

# Curated Notion "Credit Card / Account" select value for the 401k. The option
# was created in the live Transactions DB 2026-07-03.
NOTION_ACCOUNT_401K: str | None = "Capital One 401k"

ACCOUNT_NAME = "Capital One 401k ASP"


class FidelityScraper:
    SESSION_ID = "fidelity"
    BANK_DISPLAY_NAME = "Fidelity"
    SUPPORTS_LIVE = True

    CATEGORY_MAP = activity.CATEGORY_MAP

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        return self._fetch(since, date.today())

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        return self._fetch(start, end)

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError("Fidelity 401k history reaches 365d live; no PDF path in v1")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        raise NotImplementedError("Fidelity 401k history reaches 365d live; no PDF path in v1")

    # ------------------------------------------------------------------
    def _fetch(self, start: date, end: date) -> list[TransactionRecord]:
        # Clamp to the API's 365-day reach; older history isn't available live.
        floor = end - timedelta(days=_MAX_WINDOW_DAYS)
        if start < floor:
            logger.warning("fidelity_window_clamped", requested=str(start), clamped=str(floor))
            start = floor

        body = {
            "filter": {
                "accounts": _ACCOUNTS,
                "searchCriteriaDetail": {
                    "txnFromDate": _epoch(start),
                    "txnToDate": _epoch(end, end_of_day=True),
                    "includeBasketNames": False,
                    "includeCoreFundSettlementTransactions": False,
                },
            }
        }
        with open_session(self.SESSION_ID) as sb:
            session.perform_login(sb, session_id=self.SESSION_ID)
            raw = session.fetch_history_in_page(sb, body)

        records = activity.parse_activity(
            raw,
            account_name=ACCOUNT_NAME,
            credit_card_account=NOTION_ACCOUNT_401K,
            only_acct=_ACCT_401K,
        )
        records = [r for r in records if r.transaction_date and start <= r.transaction_date <= end]
        logger.info("fidelity_scraped", count=len(records), start=str(start), end=str(end))
        return records


def _epoch(d: date, *, end_of_day: bool = False) -> int:
    t = datetime(d.year, d.month, d.day, tzinfo=UTC)
    if end_of_day:
        t += timedelta(hours=23, minutes=59, seconds=59)
    return int(t.timestamp())
