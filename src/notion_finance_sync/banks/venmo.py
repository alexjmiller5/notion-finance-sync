"""Venmo scraper.

Different data shape from a bank (no category, no rewards, no card network).
Field mapping per SPEC §17:

- Name: "Sent to {person}" / "Received from {person}"
- Payee: counterparty's display name
- Memo: Venmo note text (including emojis)
- Transaction Amount: signed (negative = sent, positive = received)
- Bank: "Venmo"
- Credit Card / Account: "Venmo Account"
- Account Type: "P2P"
- All Category and reward fields: null

Reimbursement workflow uses `Related Transactions` field (manual linking by
Alex in Notion after sync — not the scraper's job).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from notion_finance_sync.models import CategoryMap, TransactionRecord


class VenmoScraper:
    SESSION_ID = "venmo"
    BANK_DISPLAY_NAME = "Venmo"
    SUPPORTS_LIVE = True
    CATEGORY_MAP: CategoryMap = {}

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: Venmo recent scrape")

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: Venmo historical scrape (Venmo retains full history)")

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError("TODO: Venmo statement archive (CSV export?)")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: Venmo CSV/statement parser")
