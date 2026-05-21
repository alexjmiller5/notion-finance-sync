from datetime import date
from pathlib import Path
from typing import Protocol, runtime_checkable

from notion_finance_sync.models import CategoryMap, TransactionRecord


class UnsupportedOperation(Exception):
    """Raised when a scraper method is called on an account that doesn't support it.

    Examples:
    - Closed-account modules raise this for `fetch_recent` / `fetch_historical`
    - Live-only modules raise this for `parse_statements`
    """


@runtime_checkable
class BankScraper(Protocol):
    """Interface every bank module implements.

    A 'session' (one BankScraper instance) covers everything under a single bank
    login. The bofa session covers all BofA cards + checking + savings + Roth IRA
    + Investment Mgmt with one login.
    """

    # ------------------------------------------------------------------
    # Identity / declarative config
    # ------------------------------------------------------------------
    SESSION_ID: str
    """Short identifier matching the user_data_dir under data/sessions/."""

    BANK_DISPLAY_NAME: str
    """Human-readable name used in logs and Notion task creation."""

    SUPPORTS_LIVE: bool
    """True for active accounts. False for closed-account / PDF-only modules."""

    CATEGORY_MAP: CategoryMap
    """Raw bank category label -> canonical category. Built up via discovery."""

    # ------------------------------------------------------------------
    # Live scraping methods (active modules only)
    # ------------------------------------------------------------------
    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        """Scrape recent transactions for the daily sync.

        Implementations should:
        1. Open the persistent SeleniumBase profile for this session
        2. Log in if needed (handle 2FA via twofa shared funcs)
        3. Navigate to transactions, scrape rows since `since`
        4. Return TransactionRecord list — bank scraper sets all fields it can,
           leaves enricher-owned fields (True Rewards, Bilt Points) null
        """
        ...

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        """Scrape historical transactions for backfill.

        Same as fetch_recent but with the date filter pushed as far as the UI
        and backend API allow. Probe the backend during initial dev to find the
        true date-range limit.
        """
        ...

    # ------------------------------------------------------------------
    # PDF / manual-input methods (closed accounts use these)
    # ------------------------------------------------------------------
    def download_statements(self, start: date, end: date) -> list[Path]:
        """Download statement PDFs into data/statements/{session_id}/.

        Returns list of newly-downloaded PDF paths. Implementations check
        what's already on disk and only fetch missing months.
        """
        ...

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        """Parse downloaded PDF statements into TransactionRecord list.

        Used by:
        - Backfill (active accounts: gap-fill where live scrape can't reach)
        - Closed-account modules (their ONLY data source)
        """
        ...
