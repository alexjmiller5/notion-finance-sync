"""Everbank checking scraper.

No rewards. No categories on the bank side (it's a checking account). All
`*_rewards` fields stay null.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from notion_finance_sync.models import CategoryMap, TransactionRecord


class EverbankScraper:
    SESSION_ID = "everbank"
    BANK_DISPLAY_NAME = "Everbank"
    SUPPORTS_LIVE = True
    CATEGORY_MAP: CategoryMap = {}

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: Everbank recent scrape")

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: Everbank historical scrape")

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError("TODO: Everbank statement archive")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: Everbank PDF parser")
