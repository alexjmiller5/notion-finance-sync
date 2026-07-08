"""Tests for the EverBank transaction JSON parser (pure, offline).

Fixture ``txn_pages_raw.json`` is a real two-page capture (gitignored). It holds
``{"page1": <TransactionInqSVC resp>, "page2": <NextTransactionInqSVC resp>}``.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from notion_finance_sync.banks.everbank import parser
from notion_finance_sync.models import (
    AccountType,
    BankName,
    CanonicalCategory,
    TransactionStatus,
)

FIXTURE = Path(__file__).parent / "fixtures" / "everbank" / "txn_pages_raw.json"


@pytest.fixture
def pages() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def page1(pages) -> list[dict]:
    return pages["page1"]["result"]


def test_parses_all_rows(page1):
    recs = parser.parse_transactions(page1, account_name="EverBank Performance Savings")
    assert len(recs) == len(page1) == 36


def test_first_row_fields(page1):
    # index 0: Pending VENMO +$13.00 on 2026-07-03
    rec = parser.parse_transactions(page1)[0]
    assert rec.source_id == "091000010354834"  # controlNbr (stable across pending->posted)
    assert rec.amount == 13.0
    assert rec.transaction_date == date(2026, 7, 3)
    assert rec.status == TransactionStatus.PENDING
    assert rec.bank == BankName.EVERBANK
    assert rec.account_type == AccountType.SAVINGS
    assert rec.category == CanonicalCategory.TRANSFER  # Venmo -> §17


def test_signed_amounts_preserved(page1):
    recs = parser.parse_transactions(page1)
    assert recs[1].amount == -1796.67  # BILT CARD (debit/outflow) stays negative
    assert recs[2].amount == 10.0  # VENMO cashout (credit/inflow) stays positive
    assert recs[1].status == TransactionStatus.POSTED  # "Processed"


def test_source_id_falls_back_to_trnid_for_allzero_controlnbr(page1):
    # index 8: INTEREST CREDIT has controlNbr 000000000000000 -> use trnId
    rec = parser.parse_transactions(page1)[8]
    assert rec.source_id == "2026063013440.9141.370-0"
    assert rec.name == "Interest Credit" or rec.name.upper() == "INTEREST CREDIT"


def test_zelle_in_memo_is_transfer(page1):
    # index 7: name "ACCOUNT CREDIT" but memo carries "ZELLE ..." -> §17 Transfer
    rec = parser.parse_transactions(page1)[7]
    assert rec.category == CanonicalCategory.TRANSFER


def test_interest_credit_is_income(page1):
    rec = parser.parse_transactions(page1)[8]
    assert rec.category == CanonicalCategory.INCOME


def test_payroll_is_income(page1):
    # index 12: CAPITAL ONE SERV, memo "... PAYROLL ..."
    rec = parser.parse_transactions(page1)[12]
    assert rec.category == CanonicalCategory.INCOME
    assert rec.amount == 2460.02


def test_card_payment_is_transfer(page1):
    recs = parser.parse_transactions(page1)
    bilt = next(r for r in recs if r.name.upper() == "BILT CARD")
    boa = next(r for r in recs if r.name.upper() == "BANK OF AMERICA")
    assert bilt.category == CanonicalCategory.TRANSFER
    assert boa.category == CanonicalCategory.TRANSFER


def test_memo_and_payee_populated(page1):
    rec = parser.parse_transactions(page1)[2]  # VENMO
    assert "VENMO" in rec.memo.upper()
    assert rec.payee  # non-empty counterparty


def test_account_overrides(page1):
    recs = parser.parse_transactions(page1, account_name="My Savings", source_account_id="ABC")
    assert recs[0].account_name == "My Savings"
    assert recs[0].source_account_id == "ABC"


def test_all_source_ids_unique_across_both_pages(pages):
    rows = pages["page1"]["result"] + pages["page2"]["result"]
    recs = parser.parse_transactions(rows)
    ids = [r.source_id for r in recs]
    assert len(set(ids)) == len(ids)


def test_categorize_unknown_is_none():
    assert parser.categorize("SOME RANDOM MERCHANT", "") is None
