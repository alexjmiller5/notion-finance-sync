"""Tests for the U.S. Bank txnsDetails GraphQL parser (pure, offline).

Runs against a real captured fixture (tests/fixtures/us_bank/txns_details.json,
gitignored). Skips cleanly if the fixture isn't present so CI without the local
capture still passes the rest of the suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from notion_finance_sync.banks.us_bank import parser
from notion_finance_sync.banks.us_bank.scraper import CARD_META
from notion_finance_sync.models import (
    AccountType,
    BankName,
    CanonicalCategory,
    CardNetwork,
    TransactionStatus,
)

FX = Path(__file__).parent / "fixtures" / "us_bank" / "txns_details.json"

pytestmark = pytest.mark.skipif(not FX.exists(), reason="us_bank real-data fixture not present")


@pytest.fixture
def raw() -> dict:
    return json.loads(FX.read_text())


@pytest.fixture
def records(raw):
    return parser.parse_activity(raw, CARD_META)


def _find(records, description_substr):
    for r in records:
        if description_substr.lower() in (r.memo or "").lower():
            return r
    raise AssertionError(f"no record matching {description_substr!r}")


# --------------------------------------------------------------------------
# shape
# --------------------------------------------------------------------------
def test_parses_posted_and_pending(records, raw):
    tr = raw["data"]["txnsDetails"]["txnsResponse"]
    expected = len(tr["postedTransactions"]) + len(tr["pendingTransactions"])
    assert len(records) == expected
    assert expected > 100


def test_all_us_bank_credit_cards(records):
    assert all(r.bank == BankName.US_BANK for r in records)
    assert all(r.account_type == AccountType.CREDIT_CARD for r in records)
    assert all(r.source_id for r in records)
    assert all(r.source_account_id for r in records)


# --------------------------------------------------------------------------
# sign convention (DEBIT negative, CREDIT positive)
# --------------------------------------------------------------------------
def test_debit_is_negative(records):
    # DEBIT = spend/outflow -> never positive. (A $0 waived annual fee is a legit DEBIT
    # with zero magnitude, so assert non-positive; real spends are strictly negative.)
    debits = [r for r in records if r.raw_data.get("debitCreditMemo") == "DEBIT"]
    assert debits
    assert all(r.amount <= 0 for r in debits)
    assert all(r.amount < 0 for r in debits if abs(float(r.raw_data["transactionAmount"])) > 0)


def test_credit_is_positive(records):
    credits = [r for r in records if r.raw_data.get("debitCreditMemo") == "CREDIT"]
    assert credits
    assert all(r.amount > 0 for r in credits)


def test_payment_row_is_positive(records):
    pay = _find(records, "Payment Thank You")
    assert pay.amount > 0  # money paid to the card, not a spend
    assert pay.category == CanonicalCategory.TRANSFER


# --------------------------------------------------------------------------
# field mapping
# --------------------------------------------------------------------------
def test_clean_merchant_used_as_name(records):
    uber = _find(records, "Uber *eats")
    assert uber.name == "Uber Eats"  # enrichedDetails.description, not raw "Uber *eats..."
    assert uber.payee == "Uber Eats"
    assert uber.memo == "Uber *eats 8005928996 Ca"  # raw description preserved in memo


def test_source_id_is_transaction_unique_id(records, raw):
    posted = raw["data"]["txnsDetails"]["txnsResponse"]["postedTransactions"][0]
    match = [r for r in records if r.source_id == posted["transactionUniqueId"]]
    assert len(match) == 1


def test_card_metadata_mapped(records):
    by_card = {}
    for r in records:
        by_card.setdefault(r.raw_data["accountNumber"], r)
    ht = by_card["2019"]
    assert ht.credit_card_account == "Harris Teeter Rewards World Elite"
    assert ht.card_network == CardNetwork.MASTERCARD
    cashplus = by_card["3223"]
    assert cashplus.credit_card_account == "Cash+ Visa Signature"
    assert cashplus.card_network == CardNetwork.VISA


def test_status_mapping(records):
    pending = [r for r in records if r.status == TransactionStatus.PENDING]
    posted = [r for r in records if r.status == TransactionStatus.POSTED]
    assert pending and posted


# --------------------------------------------------------------------------
# category mapping
# --------------------------------------------------------------------------
def test_groceries_maps_to_groceries(records):
    # Food & Dining / Groceries -> subcategory override -> Groceries (not Dining)
    grocery = [r for r in records if r.raw_data["enrichedDetails"]["subCategory"] == "Groceries"]
    assert grocery
    assert all(r.category == CanonicalCategory.GROCERIES for r in grocery)


def test_dining_falls_back_to_category(records):
    resto = [r for r in records if r.raw_data["enrichedDetails"]["subCategory"] == "Restaurants"]
    assert resto
    assert all(r.category == CanonicalCategory.DINING for r in resto)


def test_gas_and_transit_split_under_auto_transport(records):
    gas = [r for r in records if r.raw_data["enrichedDetails"]["subCategory"] == "Gas"]
    transit = [
        r
        for r in records
        if r.raw_data["enrichedDetails"]["subCategory"] == "Public Transportation"
    ]
    assert gas and transit
    assert all(r.category == CanonicalCategory.GAS for r in gas)
    assert all(r.category == CanonicalCategory.TRANSIT for r in transit)


def test_bank_category_is_group_colon_sub(records):
    con_ed = _find(records, "coned")
    assert con_ed.bank_category == "Bills & Utilities: Utilities"
    assert con_ed.category == CanonicalCategory.BILLS_UTILITIES


def test_empty_enriched_category_is_none(records):
    # pending "Dishes At Home" had empty enriched category -> Needs Review (None)
    blanks = [r for r in records if not r.raw_data["enrichedDetails"].get("category")]
    assert all(r.category is None for r in blanks)
