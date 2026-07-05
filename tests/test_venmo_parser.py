"""Tests for the Venmo web-API stories parser (pure, offline).

Fixture ``web_stories_sample.json`` mirrors the real ``account.venmo.com/api/stories``
shape (captured live) with fake counterparties — safe to commit. Covers every story
type Venmo emits: payment (sent/received/settled-charge), authorization (debit-card
merchant purchase), transfer (bank cash-out, standard + instant), refund, and
disbursement.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from notion_finance_sync.banks import venmo
from notion_finance_sync.models import (
    AccountType,
    BankName,
    CanonicalCategory,
    ReviewStatus,
    TransactionStatus,
    compute_review_status,
)

FIXTURE = Path(__file__).parent / "fixtures" / "venmo" / "web_stories_sample.json"
MY_ID = "1111111111111111111"


@pytest.fixture
def raw() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def records(raw):
    return venmo.parse_stories(raw, my_user_id=MY_ID)


@pytest.fixture
def by_id(records):
    return {r.source_id: r for r in records}


def test_parses_every_story_type(records):
    # 8 stories in the fixture; all are real transactions now (none skipped).
    assert len(records) == 8


def test_payment_i_sent_is_outflow(by_id):
    r = by_id["4633391919166609215"]
    assert r.source_account_id == MY_ID
    assert r.amount == -7.61  # "- $7.61"
    assert r.name == "Sent to Jordan Rivera"
    assert r.payee == "Jordan Rivera"
    assert r.memo == "Amazon"
    assert r.status == TransactionStatus.POSTED
    assert r.bank == BankName.VENMO
    assert r.account_type == AccountType.P2P
    assert r.credit_card_account == "Venmo Account"
    assert r.category is None  # SPEC §11 -> Needs Review


def test_payment_i_received_is_inflow_and_eastern_date_boundary(by_id):
    r = by_id["4632115520812142999"]
    assert r.amount == 13.0  # "+ $13.00"
    assert r.name == "Received from Sam Kelly"
    assert r.memo == "Uberrrr 🚗"
    # 2026-07-02T02:18:50 UTC == 2026-07-01 22:18 America/New_York (EDT, -4)
    assert r.transaction_date == date(2026, 7, 1)


def test_settled_charge_received_and_comma_amount(by_id):
    r = by_id["4631000000000000000"]
    assert r.amount == 1240.0  # "+ $1,240.00"
    assert r.name == "Received from Jordan Rivera"


def test_authorization_is_merchant_purchase(by_id):
    # Venmo debit-card purchase: signed amount, merchant is title.receiver.
    r = by_id["4629000000000000000"]
    assert r.amount == -16.68
    assert r.name == "Sent to American Multi Cinema Inc"
    assert r.payee == "American Multi Cinema Inc"
    assert r.category is None  # Venmo doesn't categorize -> Needs Review
    assert r.account_type == AccountType.P2P


def test_transfer_standard_is_bank_cashout(by_id):
    # Cash-out to bank: unsigned amount -> negative; category Transfer.
    r = by_id["4630000000000000000"]
    assert r.amount == -13.0  # "$13.00" unsigned -> outflow
    assert r.name == "Transfer to TIAA BANK ...0172"
    assert r.payee == "TIAA BANK"
    assert r.category == CanonicalCategory.TRANSFER
    assert r.status == TransactionStatus.POSTED


def test_transfer_instant_is_bank_cashout(by_id):
    r = by_id["4626000000000000000"]
    assert r.amount == -459.52
    assert r.name == "Transfer to BANK OF AMERICA N.A. ...9876"
    assert r.category == CanonicalCategory.TRANSFER


def test_refund_is_inflow_with_note_name_counterparty(by_id):
    # Refund has no title.sender/receiver; counterparty is note.name.
    r = by_id["4628000000000000000"]
    assert r.amount == 10.0  # "+ $10.00"
    assert r.name == "Received from a user on iMessage"
    assert r.payee == "a user on iMessage"
    assert r.category is None


def test_disbursement_is_inflow_from_sender(by_id):
    r = by_id["4627000000000000000"]
    assert r.amount == 72.0  # "+ $72.00"
    assert r.name == "Received from Settlement Administrator"
    assert "settlement" in r.memo.lower()
    assert r.category is None


def test_transfers_are_reviewed_others_need_review(records):
    for r in records:
        if r.category == CanonicalCategory.TRANSFER:
            # Transfer has a category -> not forced to Needs Review by rule 1
            assert compute_review_status(r) == ReviewStatus.REVIEWED
        else:
            assert compute_review_status(r) == ReviewStatus.NEEDS_REVIEW


def test_amount_parsing_edge_cases():
    assert venmo._parse_amount("- $7.61") == -7.61
    assert venmo._parse_amount("+ $13.00") == 13.0
    assert venmo._parse_amount("$13.00") == 13.0
    assert venmo._parse_amount("- $1,240.00") == -1240.0
    assert venmo._parse_amount("") == 0.0
