"""USBankScraper — implements the BankScraper protocol.

ONE U.S. Bank login covers both credit cards (Cash+ Visa Signature + Harris Teeter
Rewards World Elite Mastercard). Transactions come from a single GraphQL endpoint
(``txnsDetails``) fetched in-page from the authenticated browser — see ``session.py``.

By design (SPEC §11), ``True Rewards`` is NOT scraped for U.S. Bank; only
``Calculated Rewards`` (from config/cards.yaml) applies. There is no rewards enricher.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import structlog

from notion_finance_sync.banks.us_bank import parser, session
from notion_finance_sync.models import CardNetwork, TransactionRecord

logger = structlog.get_logger()

# accountNumber (last 4) -> (Notion "Credit Card / Account" select value, CardNetwork).
# Identified during recon (2026-07-03): 2019 = Harris Teeter World Elite Mastercard,
# 3223 = Cash+ Visa Signature. Both Notion options already exist.
CARD_META: dict[str, tuple[str, CardNetwork]] = {
    "3223": ("Cash+ Visa Signature", CardNetwork.VISA),
    "2019": ("Harris Teeter Rewards World Elite", CardNetwork.MASTERCARD),
}


class USBankScraper:
    SESSION_ID = "us_bank"
    BANK_DISPLAY_NAME = "U.S. Bank"
    SUPPORTS_LIVE = True

    # BankScraper protocol's CATEGORY_MAP (U.S. Bank top-level label -> canonical).
    CATEGORY_MAP = parser.CATEGORY_MAP

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        return self._fetch(since, date.today())

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        # The backend caps live history at ~6 months regardless of start (recon); a
        # single call returns the whole window (no cursor pagination). Older history
        # would need statements/PDF — out of scope for this module.
        return self._fetch(start, end)

    def _fetch(self, start: date, end: date) -> list[TransactionRecord]:
        raw = session.fetch_activity(start.isoformat(), end.isoformat())
        records = parser.parse_activity(raw, CARD_META)
        records = [r for r in records if r.transaction_date and start <= r.transaction_date <= end]
        logger.info("us_bank_scraped", count=len(records), start=str(start), end=str(end))
        return records

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError("U.S. Bank statement archive not implemented")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        raise NotImplementedError("U.S. Bank PDF parser not implemented")
