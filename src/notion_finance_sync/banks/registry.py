"""Registry mapping ``session_id`` -> ``BankScraper`` instance.

The orchestrator looks scrapers up here. Tests monkeypatch ``BANK_REGISTRY``
to swap in ``FakeBankScraper``.

Active scrapers (``SUPPORTS_LIVE = True``) participate in the daily flow.
Closed-account scrapers (``SUPPORTS_LIVE = False``) sit in the registry so the
backfill flow can still reach them — the orchestrator marks them ``skipped``
on a live sync without making any HTTP calls.
"""

from __future__ import annotations

from notion_finance_sync.banks import (
    bilt,
    bofa,
    bofa_investments,
    etrade,
    everbank,
    fidelity,
    fidelity_ira_closed,
    td,
    us_bank,
    venmo,
    wells_fargo,
)
from notion_finance_sync.banks._base import BankScraper

BANK_REGISTRY: dict[str, BankScraper] = {
    "bofa": bofa.BofAScraper(),
    "us_bank": us_bank.USBankScraper(),
    "wells_fargo": wells_fargo.WellsFargoScraper(),
    "bilt": bilt.BiltScraper(),
    "everbank": everbank.EverbankScraper(),
    "venmo": venmo.VenmoScraper(),
    "etrade": etrade.ETradeScraper(),
    "fidelity": fidelity.FidelityScraper(),
    "bofa_investments": bofa_investments.BofAInvestmentsScraper(),
    "td": td.TDBankScraper(),
    "fidelity_ira_closed": fidelity_ira_closed.FidelityIRAClosedScraper(),
}


def get_scraper(session_id: str) -> BankScraper:
    """Look up a scraper by session id. Raises ``KeyError`` for unknown ids."""
    try:
        return BANK_REGISTRY[session_id]
    except KeyError as exc:
        raise KeyError(f"unknown bank session_id: {session_id!r}") from exc


def all_session_ids() -> list[str]:
    """Return all registered session ids in registration order."""
    return list(BANK_REGISTRY.keys())
