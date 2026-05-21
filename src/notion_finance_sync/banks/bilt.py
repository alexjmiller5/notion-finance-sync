"""Bilt scraper (Bilt Blue card transactions + the Bilt portal session).

This session has dual duty:
1. Scrape Bilt Blue card transactions (Phase 1, like every other bank).
2. Keep the Bilt portal logged in so the `bilt_portal` enricher (Phase 2) can
   pull cross-card Bilt point earnings.

Bilt's UI shows per-txn multipliers directly in the transactions view, so
`True Rewards` is populated inline (no separate enricher needed for Bilt Blue
card txns specifically — only cross-card points go through the enricher).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from notion_finance_sync.models import CanonicalCategory, CategoryMap, TransactionRecord


class BiltScraper:
    SESSION_ID = "bilt"
    BANK_DISPLAY_NAME = "Bilt"
    SUPPORTS_LIVE = True

    CATEGORY_MAP: CategoryMap = {
        "Rent": CanonicalCategory.RENT,
        "Restaurants": CanonicalCategory.DINING,
        "Travel": CanonicalCategory.TRAVEL,
        "Lyft": CanonicalCategory.TRANSIT,
        # TODO: extend during discovery
    }

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: Bilt Blue card recent scrape")

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: Bilt Blue card historical scrape")

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError("TODO: Bilt statement archive (if any)")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: Bilt PDF parser")
