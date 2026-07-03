"""Wiring tests for WellsFargoScraper (no live browser, no Notion writes)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from notion_finance_sync.banks.wells_fargo.scraper import WellsFargoScraper

WF_PDF_DIR = Path(__file__).resolve().parents[1] / "data" / "statements" / "wf"
_HAS_PDFS = WF_PDF_DIR.exists() and any(WF_PDF_DIR.glob("*.pdf"))


def test_fetch_recent_empty_card_returns_nothing_and_does_not_notify(mocker):
    mocker.patch(
        "notion_finance_sync.banks.wells_fargo.scraper.session.has_live_activity",
        return_value=False,
    )
    notify = mocker.patch(
        "notion_finance_sync.banks.wells_fargo.scraper.notify_wells_fargo_activity"
    )
    assert WellsFargoScraper().fetch_recent(date(2025, 8, 1)) == []
    notify.assert_not_called()


def test_fetch_recent_notifies_when_activity_appears(mocker):
    mocker.patch(
        "notion_finance_sync.banks.wells_fargo.scraper.session.has_live_activity",
        return_value=True,
    )
    notify = mocker.patch(
        "notion_finance_sync.banks.wells_fargo.scraper.notify_wells_fargo_activity"
    )
    assert WellsFargoScraper().fetch_recent(date(2025, 8, 1)) == []
    notify.assert_called_once()


def test_notify_failure_does_not_sink_the_sync(mocker):
    mocker.patch(
        "notion_finance_sync.banks.wells_fargo.scraper.session.has_live_activity",
        return_value=True,
    )
    mocker.patch(
        "notion_finance_sync.banks.wells_fargo.scraper.notify_wells_fargo_activity",
        side_effect=RuntimeError("notion down"),
    )
    # must swallow the notification error and still return cleanly
    assert WellsFargoScraper().fetch_recent(date(2025, 8, 1)) == []


@pytest.mark.skipif(not _HAS_PDFS, reason="real WF statement PDFs not present")
def test_fetch_historical_filters_by_date_window():
    recs = WellsFargoScraper().fetch_historical(date(2025, 12, 1), date(2025, 12, 31))
    assert recs, "December 2025 should have Bilt-era transactions"
    assert all(r.transaction_date.year == 2025 and r.transaction_date.month == 12 for r in recs)
