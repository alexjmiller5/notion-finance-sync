"""TD Bank (CLOSED ACCOUNT) — PDF-only module.

Alex opened this account ~2018, closed ~2020. No live scraping possible.

Workflow:
1. Alex calls TD customer service and requests digital PDFs of historical
   statements covering the full account lifetime.
2. He drops them into data/statements/td/.
3. Backfill runner invokes parse_statements() to extract transactions.

`Category` will be null for TD transactions (no category data in PDFs and
LLM categorization is deferred per SPEC §10).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from notion_finance_sync.banks._base import UnsupportedOperation
from notion_finance_sync.models import CategoryMap, TransactionRecord


class TDBankScraper:
    SESSION_ID = "td"
    BANK_DISPLAY_NAME = "TD Bank"
    SUPPORTS_LIVE = False
    CATEGORY_MAP: CategoryMap = {}

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        raise UnsupportedOperation("TD Bank account is closed — no live scrape")

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        raise UnsupportedOperation(
            "TD Bank account is closed — use parse_statements() with PDFs in data/statements/td/"
        )

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise UnsupportedOperation(
            "TD Bank account is closed — PDFs must be obtained by calling customer service "
            "and dropped manually in data/statements/td/"
        )

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        from notion_finance_sync.backfill.pdf_parsers import td as td_pdf

        return td_pdf.parse(pdf_paths)
