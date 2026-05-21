"""E*Trade brokerage scraper.

Main event of interest: monthly RSU vest from Alex's employer. Also captures
buys, sells, dividends, fees.

Field semantics for an RSU vest (per SPEC §16):
    Transaction Amount = market value at vest
    Quantity = shares granted
    Ticker = symbol
    Price Per Share = vest price
    Account Type = "Brokerage"
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from notion_finance_sync.models import CategoryMap, TransactionRecord


class ETradeScraper:
    SESSION_ID = "etrade"
    BANK_DISPLAY_NAME = "E*Trade"
    SUPPORTS_LIVE = True
    CATEGORY_MAP: CategoryMap = {}

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: E*Trade recent scrape (RSU vests, buys, sells, dividends)")

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: E*Trade historical scrape")

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError("TODO: E*Trade statement archive")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: E*Trade PDF parser")
