"""U.S. Bank scraper.

Covers Cash+ Visa Signature + Harris Teeter Rewards World Elite.

By design (per SPEC §11), `True Rewards` is NOT scraped for US Bank — their
rewards data isn't exposed cleanly enough. `Calculated Rewards` (from
config/cards.yaml) is the only source for US Bank reward values. There is NO
`us_bank_rewards` enricher.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from notion_finance_sync.banks._base import UnsupportedOperation
from notion_finance_sync.models import CanonicalCategory, CategoryMap, TransactionRecord


class USBankScraper:
    SESSION_ID = "us_bank"
    BANK_DISPLAY_NAME = "U.S. Bank"
    SUPPORTS_LIVE = True

    # Populate during discovery — US Bank uses labels like "Dining & Entertainment"
    CATEGORY_MAP: CategoryMap = {
        "Dining & Entertainment": CanonicalCategory.DINING,
        "Grocery Stores": CanonicalCategory.GROCERIES,
        "Gas Station": CanonicalCategory.GAS,
        "Travel": CanonicalCategory.TRAVEL,
        # TODO: extend during discovery
    }

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: implement US Bank recent scrape")

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: implement US Bank historical scrape")

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError("TODO: US Bank statement archive")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: US Bank PDF parser")
