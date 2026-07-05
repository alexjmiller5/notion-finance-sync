"""WellsFargoScraper — implements the BankScraper protocol.

Two data paths, reflecting the reality found in recon (see FINDINGS.md):

- ``fetch_recent`` / live: logs into WF, drives the Autograph card's activity search, and
  reads the reported transaction count. The card is currently **unused (0 transactions)**,
  so this returns ``[]`` — but the day a transaction appears it drops a High-priority Notion
  task telling Alex to build out the full online parser (the per-transaction JSON shape can
  only be finalised against real data). This is the "notification" Alex asked for.

- ``fetch_historical`` / ``parse_statements``: the real historical data. Alex's old **Bilt
  World Elite Mastercard** (…6972, since converted to the Autograph …8000) transactions live
  in WF statement PDFs under ``data/statements/wf/``. Parsed and labelled under the curated
  Notion card "Bilt World Elite Mastercard".
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import structlog

from notion_finance_sync.banks.wells_fargo import session, statements
from notion_finance_sync.banks.wells_fargo.notify import notify_wells_fargo_activity
from notion_finance_sync.models import CanonicalCategory, CategoryMap, TransactionRecord

logger = structlog.get_logger()

# WF statement PDFs (gitignored). MMDDYY WellsFargo.pdf, the old-Bilt-era + Autograph months.
STATEMENTS_DIR = Path(__file__).resolve().parents[4] / "data" / "statements" / "wf"


class WellsFargoScraper:
    SESSION_ID = "wells_fargo"
    BANK_DISPLAY_NAME = "Wells Fargo"
    SUPPORTS_LIVE = True

    # WF statements/API expose NO merchant category, so this stays minimal; rows land
    # category-null (Needs Review) and are enriched later by wells_rewards / bilt_portal.
    CATEGORY_MAP: CategoryMap = {
        "Restaurants": CanonicalCategory.DINING,
        "Travel - Airline": CanonicalCategory.AIRFARE,
        "Travel - Hotel": CanonicalCategory.TRAVEL,
        "Grocery Stores": CanonicalCategory.GROCERIES,
        "Gas Stations": CanonicalCategory.GAS,
    }

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        """Check the live Autograph activity. Returns [] while the card is unused; notifies
        Alex (Notion task) the day the account-details page stops saying "no recent activity"."""
        if session.has_live_activity(self.SESSION_ID):
            logger.info("wf_live_activity_detected")
            try:
                notify_wells_fargo_activity(since=since.isoformat())
            except Exception as exc:  # noqa: BLE001 — notification must not sink the sync
                logger.error("wf_activity_notify_failed", error=str(exc))
        return []

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        """Historical transactions from WF statement PDFs, filtered to ``[start, end]``."""
        records = self.parse_statements(self._statement_paths())
        return [r for r in records if r.transaction_date and start <= r.transaction_date <= end]

    def download_statements(self, start: date, end: date) -> list[Path]:
        # Statements are placed in data/statements/wf/ by hand (WF eDocs export).
        return self._statement_paths()

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        return statements.parse(pdf_paths)

    @staticmethod
    def _statement_paths() -> list[Path]:
        if not STATEMENTS_DIR.exists():
            return []
        return sorted(STATEMENTS_DIR.glob("*.pdf"))
