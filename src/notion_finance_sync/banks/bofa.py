"""Bank of America scraper.

ONE BofA login covers many accounts:
- Credit cards: AQHA Customized, Komen Customized, Travel Rewards, Unlimited
  Cash Rewards, NEA Customized, Advantage Plus
- Banking: Checking, Savings
- Investments: Roth IRA, Investment Management (handled by bofa_investments.py
  which shares this session)

Highest-priority bank in v1. Most cards. Backend API may expose deeper date
range than the UI filter — PROBE during initial implementation.

True Rewards: scraped from the monthly rewards summary page by the
`bofa_rewards` enricher (Phase 2), correlated to txns by (date, amount).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from notion_finance_sync.banks._base import UnsupportedOperation
from notion_finance_sync.models import CanonicalCategory, CategoryMap, TransactionRecord


class BofAScraper:
    SESSION_ID = "bofa"
    BANK_DISPLAY_NAME = "BofA"
    SUPPORTS_LIVE = True

    # Built up via discovery — populate from observed raw labels during initial syncs.
    # Examples to seed from old project knowledge:
    CATEGORY_MAP: CategoryMap = {
        "Dining": CanonicalCategory.DINING,
        "Groceries": CanonicalCategory.GROCERIES,
        "Travel": CanonicalCategory.TRAVEL,
        "Gas": CanonicalCategory.GAS,
        "Online Shopping": CanonicalCategory.ONLINE_SHOPPING,
        "Health & Beauty": CanonicalCategory.HEALTHCARE,
        "Cash": CanonicalCategory.CASH_ATM,
        "Bills": CanonicalCategory.BILLS_UTILITIES,
        # TODO: extend during discovery phase
    }

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: implement BofA recent scrape")

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        raise NotImplementedError(
            "TODO: implement BofA historical scrape. PROBE the backend API's true "
            "date-range limit — UI filter caps at ~24mo but JSON endpoint may go deeper."
        )

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError(
            "TODO: navigate to BofA statements archive, download missing PDFs to "
            "data/statements/bofa/"
        )

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        from notion_finance_sync.backfill.pdf_parsers import bofa as bofa_pdf

        return bofa_pdf.parse(pdf_paths)
