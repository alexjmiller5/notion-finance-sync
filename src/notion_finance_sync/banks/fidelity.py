"""Fidelity 401k scraper.

Captures biweekly payroll contributions + auto-buys of target-date fund
shares + dividends.

NOTE: Old notion-ai-budgeting-app README flagged that "fidelity data sync
seems to be broken for now as of April 18, 2026" via the aggregator. Direct
scraping may also be painful. Probe the UI carefully during initial impl.

Account Type = "401k"
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from notion_finance_sync.models import CategoryMap, TransactionRecord


class FidelityScraper:
    SESSION_ID = "fidelity"
    BANK_DISPLAY_NAME = "Fidelity"
    SUPPORTS_LIVE = True
    CATEGORY_MAP: CategoryMap = {}

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: Fidelity 401k recent scrape")

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: Fidelity 401k historical scrape")

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError("TODO: Fidelity statement archive")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: Fidelity PDF parser")
