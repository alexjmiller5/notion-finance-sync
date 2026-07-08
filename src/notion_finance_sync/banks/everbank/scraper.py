"""EverbankScraper — implements the BankScraper protocol.

One EverBank login currently covers a single high-yield **savings** account
(Alex's transfer hub). Flow: SeleniumBase login -> cookies -> httpx client, then
the JSON discovery chain (see FINDINGS.md):

    RetrieveUserSVC   -> custId (partyCode)
    AccountListInqSVC -> accounts (acctCode, acctType, acctDesc)
    TransactionInqSVC / NextTransactionInqSVC -> transactions (cursor paginated)

The heavy lifting (row -> record, categorization) lives in the pure, unit-tested
``parser`` module. ``session`` does the I/O.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import structlog

from notion_finance_sync.banks.everbank import parser, session
from notion_finance_sync.banks.everbank.parser import EVERBANK_KEYWORD_CATEGORY
from notion_finance_sync.models import AccountType, TransactionRecord

logger = structlog.get_logger()

_PAGE_SIZE = 50
_MAX_PAGES = 60  # safety cap (~5y at this account's volume)

# EverBank acctType -> our AccountType. Only SAV is live today; extend if a
# checking/CD account appears under the same login (discovery is dynamic).
_ACCT_TYPE = {
    "SAV": AccountType.SAVINGS,
    "SDA": AccountType.SAVINGS,
    "DDA": AccountType.CHECKING,
    "CHK": AccountType.CHECKING,
}

# Curated Notion "Credit Card / Account" select value — matches the existing
# "Performance Savings" option (confirmed against the live DB schema).
NOTION_ACCOUNT = "Performance Savings"


class EverbankScraper:
    SESSION_ID = "everbank"
    BANK_DISPLAY_NAME = "Everbank"
    SUPPORTS_LIVE = True

    # No native categories on savings; keyword map drives §17/income/transfer.
    CATEGORY_MAP = EVERBANK_KEYWORD_CATEGORY

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        return self._fetch(since=since, end=None)

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        return self._fetch(since=start, end=end)

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError("EverBank live history reaches full range; no PDF path")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        raise NotImplementedError("EverBank has no PDF parser (live JSON covers history)")

    # ------------------------------------------------------------------
    # I/O + assembly
    # ------------------------------------------------------------------
    def _fetch(self, *, since: date, end: date | None) -> list[TransactionRecord]:
        cookies = session.login_and_get_cookies(self.SESSION_ID)
        client = session.build_client(cookies)
        try:
            username = _resolve_username(self.SESSION_ID)
            cust_id = _discover_cust_id(client, username)
            records: list[TransactionRecord] = []
            for acct in _discover_accounts(client, cust_id):
                records.extend(self._fetch_account(client, acct, cust_id, since, end))
            return records
        finally:
            client.close()

    def _fetch_account(
        self, client, acct: dict, cust_id: str, since: date, end: date | None
    ) -> list[TransactionRecord]:
        acct_code = acct["acctCode"]
        acct_type_raw = acct["acctType"]
        account_type = _ACCT_TYPE.get(acct_type_raw, AccountType.SAVINGS)
        account_name = acct.get("acctDesc") or "EverBank"

        rows = _paginate(client, acct_code, acct_type_raw, cust_id, since)
        recs = parser.parse_transactions(
            rows,
            account_name=account_name,
            source_account_id=acct_code,
            account_type=account_type,
        )
        for r in recs:
            r.credit_card_account = NOTION_ACCOUNT
        recs = [
            r
            for r in recs
            if r.transaction_date
            and r.transaction_date >= since
            and (end is None or r.transaction_date <= end)
        ]
        logger.info("everbank_account_scraped", account=account_name, count=len(recs))
        return recs


def _paginate(client, acct_code: str, acct_type: str, cust_id: str, since: date) -> list[dict]:
    """Follow the transaction cursor until we pass ``since`` or run out of pages."""
    data = session.call_service(
        client, "TransactionInqSVC", [acct_code, acct_type, "TIAA", _PAGE_SIZE, "", True, cust_id]
    )
    rows = list(data.get("result") or [])
    paging = data.get("paging") or {}

    pages = 1
    while paging.get("moreRecordsInd") == "true" and pages < _MAX_PAGES:
        if _oldest_before(rows, since):
            break  # we've paged past the window; stop
        cursor = paging.get("cursor")
        data = session.call_service(
            client,
            "NextTransactionInqSVC",
            [acct_code, acct_type, "TIAA", _PAGE_SIZE, cursor, "", True],
            optional={},
        )
        page_rows = data.get("result") or []
        if not page_rows:
            break
        rows.extend(page_rows)
        paging = data.get("paging") or {}
        pages += 1
    return rows


def _oldest_before(rows: list[dict], since: date) -> bool:
    """True if the oldest row already predates ``since`` (nothing newer to fetch)."""
    if not rows:
        return False
    try:
        return date.fromisoformat(rows[-1]["postedDt"]) < since
    except (KeyError, ValueError):
        return False


def _discover_cust_id(client, username: str) -> str:
    data = session.call_service(client, "RetrieveUserSVC", [f"&loginName={username}"])
    return data["result"][0]["userList"][0]["partyCode"]


def _discover_accounts(client, cust_id: str) -> list[dict]:
    data = session.call_service(
        client,
        "AccountListInqSVC",
        [cust_id, "Person", "TIAA"],
        optional={"tranCode": "CardDetails"},
    )
    return [a["acctRef"] for a in (data.get("result") or []) if a.get("acctRef")]


def _resolve_username(session_id: str) -> str:
    from notion_finance_sync.config.settings import get_bank_username

    return get_bank_username(session_id)
