"""Wells Fargo scraper (Autograph card).

Notable: Wells Fargo statements ALSO contain the historical transactions from
Alex's old Bilt card (before it was renamed to Wells Fargo Autograph — same
issuer all along). So this single scraper covers the entire history of that
card, pre- and post-rename. Rewards from the old-Bilt-era are correlated via
the `bilt_portal` enricher.

Manual spot-checking around the conversion date may be needed during backfill.

Lower priority than BofA/US Bank/Bilt per Alex's ranking.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from notion_finance_sync.models import CanonicalCategory, CategoryMap, TransactionRecord


class WellsFargoScraper:
    SESSION_ID = "wells_fargo"
    BANK_DISPLAY_NAME = "Wells Fargo"
    SUPPORTS_LIVE = True

    CATEGORY_MAP: CategoryMap = {
        "Restaurants": CanonicalCategory.DINING,
        "Travel - Airline": CanonicalCategory.AIRFARE,
        "Travel - Hotel": CanonicalCategory.TRAVEL,
        "Grocery Stores": CanonicalCategory.GROCERIES,
        "Gas Stations": CanonicalCategory.GAS,
        # TODO: extend during discovery
    }

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: Wells Fargo recent scrape")

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: Wells Fargo historical scrape (covers old Bilt era)")

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError("TODO: Wells Fargo statement archive")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        raise NotImplementedError("TODO: Wells Fargo PDF parser")
