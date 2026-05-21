"""BofA Investment Management + BofA Roth IRA scraper.

Shares the `bofa` session (same Chrome user-data dir as banks/bofa.py). Pulls
investment events: contributions, buys, sells, dividends, fees, rollover
transfer-ins (from the closed Fidelity IRA).

Each event becomes a TransactionRecord with quantity/ticker/price_per_share
populated. Account Type = "Brokerage" or "IRA" depending on the sub-account.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from notion_finance_sync.banks._base import UnsupportedOperation
from notion_finance_sync.models import CategoryMap, TransactionRecord


class BofAInvestmentsScraper:
    SESSION_ID = "bofa"  # shares the bofa login
    BANK_DISPLAY_NAME = "BofA Investments"
    SUPPORTS_LIVE = True

    # Investment accounts don't expose bank-category labels (it's not a spending account)
    CATEGORY_MAP: CategoryMap = {}

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: implement BofA investment recent scrape")

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: implement BofA investment historical scrape")

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError("TODO: BofA brokerage / IRA statement archive")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: parse BofA brokerage / IRA statements")
