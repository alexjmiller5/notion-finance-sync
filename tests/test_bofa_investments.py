"""Tests for the BofA Private Bank (U.S. Trust) IRA holdings parser."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from notion_finance_sync.banks.bofa.investments import parse_holdings

FIXTURE = Path(__file__).parent / "fixtures" / "bofa" / "ira_holdings.html"


@pytest.fixture
def html() -> str:
    return FIXTURE.read_text()


def test_parses_equity_positions(html):
    hs = parse_holdings(html, account_id="ira-7337", snapshot_date=date(2026, 7, 3))
    by_ticker = {h.ticker: h for h in hs}
    # VUG: 36 shares @ 85.50 (money-market/cash + unsettled rows excluded)
    assert "VUG" in by_ticker
    vug = by_ticker["VUG"]
    assert vug.quantity == 36
    assert vug.price_per_share == 85.50
    assert vug.account_id == "ira-7337"
    assert vug.snapshot_date == date(2026, 7, 3)
    # the Vanguard ETF sleeve is all present
    assert {"VUG", "VTV", "VO", "VB", "VEA", "VWO"} <= set(by_ticker)


def test_skips_unsettled_cash_and_totals(html):
    hs = parse_holdings(html, account_id="ira-7337", snapshot_date=date(2026, 7, 3))
    assert all(h.ticker.upper() != "UNSTLDCSH" for h in hs)  # 0-qty cash row dropped
    assert all(h.quantity and h.quantity != 0 for h in hs)


def test_empty_html_is_empty():
    assert parse_holdings("<html></html>", account_id="x", snapshot_date=date(2026, 7, 3)) == []
