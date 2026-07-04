"""Tests for the Venmo web-API stories parser (pure, offline).

Fixture ``web_stories_sample.json`` mirrors the real ``account.venmo.com/api/stories``
shape (captured live) with fake counterparties — safe to commit. Covers: I-sent,
I-received, a settled charge (received), a UTC→Eastern date boundary, a comma amount,
and a non-payment ``transfer`` story that must be skipped.
"""

from __future__ import annotations

import json
from datetime import UTC, date
from pathlib import Path

import pytest

from notion_finance_sync.banks import venmo
from notion_finance_sync.models import (
    AccountType,
    BankName,
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


def test_skips_non_payment_stories(records):
    # 4 stories in, one is a "transfer" -> 3 payment records out.
    assert len(records) == 3


def test_i_sent_is_outflow(records):
    r = records[0]
    assert r.source_id == "4633391919166609215"
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


def test_i_received_is_inflow_and_eastern_date_boundary(records):
    r = records[1]
    assert r.amount == 13.0  # "+ $13.00"
    assert r.name == "Received from Sam Kelly"
    assert r.payee == "Sam Kelly"
    assert r.memo == "Uberrrr 🚗"
    # 2026-07-02T02:18:50 UTC == 2026-07-01 22:18 America/New_York (EDT, -4)
    assert r.transaction_date == date(2026, 7, 1)


def test_settled_charge_received_and_comma_amount(records):
    r = records[2]
    assert r.amount == 1240.0  # "+ $1,240.00"
    assert r.name == "Received from Jordan Rivera"


def test_transacted_at_is_utc_timestamp(records):
    r = records[0]
    assert r.transacted_at is not None
    assert r.transacted_at.tzinfo is not None
    assert r.transacted_at.astimezone(UTC).isoformat().startswith("2026-07-03T20:42:38")


def test_all_venmo_records_need_review(records):
    for r in records:
        assert compute_review_status(r) == ReviewStatus.NEEDS_REVIEW


def test_amount_parsing_edge_cases():
    assert venmo._parse_amount("- $7.61") == -7.61
    assert venmo._parse_amount("+ $13.00") == 13.0
    assert venmo._parse_amount("$13.00") == 13.0
    assert venmo._parse_amount("- $1,240.00") == -1240.0
    assert venmo._parse_amount("") == 0.0
