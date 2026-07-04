"""Tests for the BofA Private Bank (U.S. Trust) IRA holdings parser."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from notion_finance_sync.banks.bofa.investments import parse_activity, parse_holdings
from notion_finance_sync.models import AccountType, BankName, CanonicalCategory

FIXTURE = Path(__file__).parent / "fixtures" / "bofa" / "ira_holdings.html"
ACTIVITY_FIXTURE = Path(__file__).parent / "fixtures" / "bofa" / "ira_activity.html"


@pytest.fixture
def html() -> str:
    return FIXTURE.read_text()


@pytest.fixture
def activity_html() -> str:
    return ACTIVITY_FIXTURE.read_text()


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


# --- activity feed ---------------------------------------------------------


def test_activity_parses_dividend(activity_html):
    recs = parse_activity(activity_html, account_name="IRA ROTH", source_account_id="ira-7337")
    divs = [r for r in recs if "DIV" in r.name and "VANGUARD 500" in r.name]
    assert divs, "expected the $15.70 VANGUARD 500 dividend"
    d = divs[0]
    assert d.amount == 15.70  # income
    assert d.transaction_date == date(2026, 6, 30)
    assert d.account_type == AccountType.IRA
    assert d.bank == BankName.BANK_OF_AMERICA
    assert d.category == CanonicalCategory.INCOME
    assert d.bank_category == "Cash Receipts: Dividends-taxable"


def test_activity_parses_fee_as_negative(activity_html):
    recs = parse_activity(activity_html, account_name="IRA ROTH", source_account_id="ira-7337")
    fee = next(r for r in recs if "ASSET FEES" in r.name)
    assert fee.amount == -3.95  # principal debit
    assert fee.category == CanonicalCategory.OTHER


def test_activity_drops_internal_transfers(activity_html):
    recs = parse_activity(activity_html, account_name="IRA ROTH", source_account_id="ira-7337")
    # the "Intra Account Trsf Income To Principal" double-entry rows are dropped
    assert all("intra account trsf" not in (r.raw_data["minor"].lower()) for r in recs)


def test_activity_source_id_stable(activity_html):
    a = parse_activity(activity_html, account_name="IRA ROTH", source_account_id="ira-7337")
    b = parse_activity(activity_html, account_name="IRA ROTH", source_account_id="ira-7337")
    assert [r.source_id for r in a] == [r.source_id for r in b]
    assert all(len(r.source_id) == 64 for r in a)
