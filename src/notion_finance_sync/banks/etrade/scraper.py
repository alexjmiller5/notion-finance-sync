"""ETradeScraper — implements the BankScraper protocol.

One E*Trade login covers the single Individual Brokerage account (which is
also the Capital One stock-plan account). Flow: SeleniumBase login ->
``ETradeSession`` (cookies + stk1 + keyAccountId + ESPP lots) -> httpx pulls
the activities JSON -> pure parser -> ESPP price enrichment.

Live history depth is 12 months (``periodRange=LAST_12_MONTHS`` — the API's
deepest working preset; see FINDINGS.md). Anything older needs statement PDFs.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import structlog

from notion_finance_sync.banks.etrade import activity, session
from notion_finance_sync.models import TransactionRecord

logger = structlog.get_logger()

ACTIVITIES_URL = "https://us.etrade.com/phx/activitychannelapi/activities/v2"
_PAGE_SIZE = 100
_MAX_PAGES = 20


def fetch_activities(client: httpx.Client, key_account_id: str) -> dict:
    """Fetch 12 months of activity, following pageNumber pagination.

    Returns a synthesized response dict of the shape the parser expects
    (``activityDetails.activities`` holding every page concatenated).
    """
    all_txns: list[dict] = []
    for page in range(1, _MAX_PAGES + 1):
        resp = client.get(
            ACTIVITIES_URL,
            params={
                "accountGroupingType": "SINGLE",
                "dateType": "TRANSACTION_DATE",
                "filterType": "ActivityType",
                "filterValue": "All",
                "institutionId": "ET",
                "keyAccountId": key_account_id,
                "orderBy": "DESCENDING",
                "orderByField": "TransactionDate",
                "pageNumber": page,
                "pageSize": _PAGE_SIZE,
                "periodRange": "LAST_12_MONTHS",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("hasError"):
            raise RuntimeError(f"E*Trade activities API error: {data.get('errorDetailsList')}")
        details = data.get("activityDetails") or {}
        all_txns.extend(details.get("activities") or [])
        if page >= (details.get("pageCount") or 1):
            break
    return {"activityDetails": {"activities": all_txns}}


class ETradeScraper:
    SESSION_ID = "etrade"
    BANK_DISPLAY_NAME = "E*Trade"
    SUPPORTS_LIVE = True
    CATEGORY_MAP = activity.CATEGORY_MAP

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        return self._fetch(since, date.max)

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        return self._fetch(start, end)

    def _fetch(self, start: date, end: date) -> list[TransactionRecord]:
        ses = session.login_and_capture(self.SESSION_ID)
        client = session.build_client(ses)
        try:
            raw = fetch_activities(client, ses.key_account_id)
        finally:
            client.close()
        records = activity.parse_activities(
            raw,
            account_name=ses.account_name,
            source_account_id=ses.key_account_id,
        )
        activity.enrich_espp_prices(records, ses.espp_lots)
        records = [r for r in records if start <= r.transaction_date <= end]
        logger.info("etrade_scraped", count=len(records), start=str(start), end=str(end))
        return records

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError("TODO: E*Trade statement archive (pre-12-months backfill)")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: E*Trade PDF parser")
