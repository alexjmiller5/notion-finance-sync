"""Tests for the E*Trade activity JSON parser + ESPP price enrichment.

Fixtures are real captured API responses (gitignored — see .gitignore):
- activities_v2.json: GET /phx/activitychannelapi/activities/v2 (LAST_12_MONTHS, 16 txns)
- espp_tables.json: DOM dump of the Stock Plan Benefit History ESPP lot table
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from notion_finance_sync.banks.etrade import activity
from notion_finance_sync.models import (
    AccountType,
    BankName,
    CanonicalCategory,
    TransactionStatus,
)

FIXTURES = Path(__file__).parent / "fixtures" / "etrade"


@pytest.fixture
def raw() -> dict:
    path = FIXTURES / "activities_v2.json"
    if not path.exists():
        pytest.skip("etrade real-data fixture not present")
    return json.loads(path.read_text())


@pytest.fixture
def espp_tables() -> list:
    path = FIXTURES / "espp_tables.json"
    if not path.exists():
        pytest.skip("etrade real-data fixture not present")
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# parse_activities
# ---------------------------------------------------------------------------
def test_parses_all_activities(raw):
    records = activity.parse_activities(raw)
    assert len(records) == 16


def test_ach_withdrawal_fields(raw):
    rec = activity.parse_activities(raw)[0]
    assert rec.source_id == "26160501261337"
    assert rec.source_account_id == "2011-11-24-02.00.09.777001"
    assert rec.name == "ACH WITHDRAWL  REFID:23639823395;"
    assert rec.amount == -54.69  # signed by the bank; negative = outflow
    assert rec.transaction_date == date(2026, 6, 9)
    assert rec.status == TransactionStatus.POSTED
    assert rec.bank == BankName.ETRADE
    assert rec.account_type == AccountType.BROKERAGE
    assert rec.credit_card_account == "E*Trade Brokerage"  # curated Notion select value
    assert rec.bank_category == "Online Transfer"
    assert rec.category == CanonicalCategory.TRANSFER
    assert rec.quantity is None
    assert rec.ticker is None
    assert rec.price_per_share is None


def test_espp_share_allocation_fields(raw):
    rec = activity.parse_activities(raw)[1]
    assert rec.source_id == "26154501961393"
    assert rec.name == "Allocate shares for 170442324"
    assert rec.amount == 0.0
    assert rec.quantity == 15.840
    assert rec.ticker == "COF"
    assert rec.price_per_share is None  # 0.000 in the API -> None (ESPP enrichment fills it)
    assert rec.bank_category == "Transfer"
    assert rec.category == CanonicalCategory.TRANSFER


def test_dividend_is_income(raw):
    rec = activity.parse_activities(raw)[2]
    assert rec.bank_category == "Qualified Dividend"
    assert rec.category == CanonicalCategory.INCOME
    assert rec.amount == 54.69
    assert rec.ticker == "COF"
    assert rec.quantity is None


def test_sell_has_signed_quantity_and_price(raw):
    rec = activity.parse_activities(raw)[-1]
    assert rec.bank_category == "Sold"
    assert rec.amount == 1851.32  # +cash received
    assert rec.quantity == -8.738  # -shares
    assert rec.price_per_share == 211.870
    assert rec.transaction_date == date(2025, 10, 2)


def test_unknown_activity_type_leaves_category_null(raw):
    txn = raw["activityDetails"]["activities"][0].copy()
    txn["activityType"] = "Corporate Action"
    records = activity.parse_activities(
        {"activityDetails": {"activities": [txn]}},
    )
    assert records[0].bank_category == "Corporate Action"
    assert records[0].category is None  # -> Needs Review downstream


def test_account_name_override(raw):
    rec = activity.parse_activities(raw, account_name="My Brokerage")[0]
    assert rec.account_name == "My Brokerage"


# ---------------------------------------------------------------------------
# ESPP lot parsing + enrichment
# ---------------------------------------------------------------------------
def test_parse_espp_lots(espp_tables):
    lots = activity.parse_espp_lots(espp_tables)
    assert lots["15.840"] == 187.93  # 05/31/2026 lot @ $187.93
    assert lots["8.738"] == 212.58  # 09/30/2025 lot
    assert len(lots) == 10


def test_enrich_espp_prices_fills_allocation_price(raw, espp_tables):
    records = activity.parse_activities(raw)
    lots = activity.parse_espp_lots(espp_tables)
    activity.enrich_espp_prices(records, lots)
    alloc = records[1]  # 15.840 shares allocated 06/03/26
    assert alloc.price_per_share == 187.93
    assert alloc.amount == 0.0  # cash amount untouched (no brokerage cash flow)


def test_enrich_espp_prices_leaves_non_allocations_alone(raw, espp_tables):
    records = activity.parse_activities(raw)
    lots = activity.parse_espp_lots(espp_tables)
    sell_price_before = records[-1].price_per_share
    activity.enrich_espp_prices(records, lots)
    assert records[-1].price_per_share == sell_price_before  # the Sold row
    assert records[0].price_per_share is None  # the ACH withdrawal


def test_enrich_espp_prices_missing_lot_is_noop(raw):
    records = activity.parse_activities(raw)
    activity.enrich_espp_prices(records, {})
    assert records[1].price_per_share is None
