"""Closed Fidelity IRA — PDF + manual-input module.

Rolled over into BofA Roth IRA. Last event is a transfer-OUT; first event in
BofA Roth IRA is a transfer-IN. Link them via `Related Transactions` so the
rollover doesn't look like portfolio disappear/reappear.

Manual-input UX TBD — will be figured out when Alex actually starts digging
through old statements. Could be CSV import, direct Notion entry, or hybrid.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from notion_finance_sync.banks._base import UnsupportedOperation
from notion_finance_sync.models import CategoryMap, TransactionRecord


class FidelityIRAClosedScraper:
    SESSION_ID = "fidelity_ira_closed"
    BANK_DISPLAY_NAME = "Fidelity IRA (closed)"
    SUPPORTS_LIVE = False
    CATEGORY_MAP: CategoryMap = {}

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        raise UnsupportedOperation("Closed account — no live scrape")

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        raise UnsupportedOperation("Closed account — use parse_statements()")

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise UnsupportedOperation(
            "Closed account — PDFs must be sourced manually (dig up old statements). "
            "Drop in data/statements/fidelity_ira_closed/"
        )

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        from notion_finance_sync.backfill.pdf_parsers import fidelity_ira_closed as fic_pdf

        return fic_pdf.parse(pdf_paths)
