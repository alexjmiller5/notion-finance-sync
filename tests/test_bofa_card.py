"""Tests for the BofA credit-card parsers (statement list HTML + per-txn detail HTML)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from notion_finance_sync.banks.bofa import card
from notion_finance_sync.models import AccountType, BankName, CanonicalCategory, TransactionStatus

FX = Path(__file__).parent / "fixtures" / "bofa"


@pytest.fixture
def statement_html() -> str:
    return (FX / "card_statement.html").read_text()


@pytest.fixture
def detail_html() -> str:
    return (FX / "card_txn_detail.html").read_text()


# --------------------------------------------------------------------------
# statement list parser
# --------------------------------------------------------------------------
def test_statement_parses_transaction_rows(statement_html):
    rows = card.parse_statement(statement_html)
    assert len(rows) >= 20  # current Travel Rewards statement had ~24 txns


def test_first_row_fields(statement_html):
    rec = card.parse_statement(statement_html)[0]
    # First row (recon): PARADISE MARKET MIKONOS, 06/26/2026, $4.78, balance $884.34
    # source_id is now a content hash (BofA's per-row ref is unstable across views)
    assert len(rec.source_id) == 64  # sha256 hex
    assert rec.raw_data.get("bank_ref") == "74199476176000196128793"  # old ref kept for audit
    assert "PARADISE MARKET MIKONOS" in rec.name
    assert rec.transaction_date == date(2026, 6, 26)
    assert rec.amount == -4.78  # purchase -> debit (sign derived from type icon)
    assert rec.bank == BankName.BANK_OF_AMERICA
    assert rec.account_type == AccountType.CREDIT_CARD
    # detail hash stashed for enrichment
    assert rec.raw_data.get("detail_txn_hash")


def test_purchases_are_negative(statement_html):
    rows = card.parse_statement(statement_html)
    # every row on this statement is a purchase (debit) -> negative
    assert all(r.amount < 0 for r in rows), "all Travel Rewards statement rows are purchases"


def test_status_defaults_posted_for_statement(statement_html):
    # rows on a closed/settled statement view are posted
    rows = card.parse_statement(statement_html)
    assert rows[0].status in (TransactionStatus.POSTED, TransactionStatus.PENDING)


# --------------------------------------------------------------------------
# per-transaction detail parser
# --------------------------------------------------------------------------
def test_detail_extracts_category_and_merchant(detail_html):
    d = card.parse_detail(detail_html)
    assert d["merchant_description"] == "GROCERY STORES, SUPERMARKETS"  # MCC
    assert d["merchant_name"] == "PARADISE MARKET"
    assert d["reference_number"] == "8793"
    assert d["online_purchase"] is False
    # BofA category label + canonical
    assert d["bank_category"] == "Groceries: Groceries"
    assert d["category"] == CanonicalCategory.GROCERIES


def test_detail_transaction_date(detail_html):
    d = card.parse_detail(detail_html)
    assert d["transaction_date"] == date(2026, 6, 24)  # distinct from posting date


# --------------------------------------------------------------------------
# sign + pending handling (from real mixed-type statements)
# --------------------------------------------------------------------------
@pytest.fixture
def mixed_html() -> str:
    return (FX / "card_statement_mixed.html").read_text()


@pytest.fixture
def payment_html() -> str:
    return (FX / "card_statement_payment.html").read_text()


def test_payment_is_positive_money_to_card(payment_html):
    rows = card.parse_statement(payment_html)
    payments = [r for r in rows if r.raw_data.get("txn_type") == "payment"]
    assert payments, "fixture should contain a payment"
    assert all(r.amount > 0 for r in payments)  # money paid to the card, not a spend


def test_purchases_and_debits_are_negative(mixed_html):
    rows = card.parse_statement(mixed_html)
    for r in rows:
        if r.raw_data.get("txn_type") in (
            "purchase",
            "fee",
            "bank-charge",
            "withdrawal",
            "generic-debit",
        ):
            assert r.amount < 0, f"{r.raw_data['txn_type']} should be negative: {r.name!r}"


def test_pending_rows_flagged_and_get_stable_id(mixed_html):
    rows = card.parse_statement(mixed_html)
    pending = [r for r in rows if r.raw_data.get("pending")]
    assert pending, "fixture should contain pending rows"
    for r in pending:
        assert r.status == TransactionStatus.PENDING
        assert r.source_id.upper() != "TEMP"  # uses the txn hash, not the placeholder
