"""SeleniumBase factory.

Centralizes the UC + CDP mode setup so every bank scraper opens its browser
the same way. Headed, real Chrome, persistent profile per session.

Usage:
    from notion_finance_sync.browser.factory import open_session

    with open_session("bofa") as sb:
        sb.activate_cdp_mode("https://www.bankofamerica.com/")
        ...
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import structlog
from seleniumbase import SB

logger = structlog.get_logger()

SESSIONS_DIR = Path(__file__).resolve().parents[3] / "data" / "sessions"


@contextmanager
def open_session(session_id: str, *, headless: bool = False):
    """Open (or create) a SeleniumBase UC+CDP browser bound to a per-session
    Chrome user-data directory.

    Args:
        session_id: short identifier for the bank login (e.g. 'bofa').
        headless: should always be False for real bank scraping. UC mode is
            detectable in headless and adds zero benefit on a Mac Mini.
    """
    user_data_dir = SESSIONS_DIR / session_id
    user_data_dir.mkdir(parents=True, exist_ok=True)

    logger.info("opening_session", session_id=session_id, profile=str(user_data_dir))

    with SB(
        uc=True,
        headless=headless,
        locale="en-US",
        user_data_dir=str(user_data_dir),
        # `channel="chrome"` uses real Chrome instead of bundled Chromium.
        # See SPEC §6 for the rationale.
    ) as sb:
        yield sb
