"""Tests for the Venmo mobile-API stories parser (pure, offline).

Fixture ``stories_sample.json`` is constructed from the documented Venmo mobile-API
schema (github.com/mmohades/Venmo) with fake counterparties — safe to commit. It
covers the four direction cases (pay/charge × I'm actor/target) plus a non-payment
story that must be skipped, plus a UTC→Eastern date-boundary case.
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

FIXTURE = Path(__file__).parent / "fixtures" / "venmo" / "stories_sample.json"
MY_ID = "1111111111111111111"


@pytest.fixture
def raw() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def records(raw):
    return venmo.parse_stories(raw, my_user_id=MY_ID)


def test_skips_non_payment_stories(records):
    # 5 stories in, one is a "transfer" -> 4 payment records out.
    assert len(records) == 4


def test_pay_i_am_actor_is_outflow(records):
    r = records[0]
    assert r.source_id == "3800000000000000001"
    assert r.source_account_id == MY_ID
    assert r.amount == -30.0  # I paid Jordan -> negative
    assert r.name == "Sent to Jordan Rivera"
    assert r.payee == "Jordan Rivera"
    assert r.memo == "Dinner 🍜"
    assert r.transaction_date == date(2026, 7, 2)
    assert r.status == TransactionStatus.POSTED  # "settled"
    assert r.bank == BankName.VENMO
    assert r.account_type == AccountType.P2P
    assert r.credit_card_account == "Venmo Account"
    assert r.category is None  # Venmo doesn't categorize (SPEC §11 -> Needs Review)


def test_pay_i_am_target_is_inflow_and_eastern_date_boundary(records):
    r = records[1]
    assert r.amount == 85.5  # Sam paid me -> positive
    assert r.name == "Received from Sam Kelly"
    assert r.payee == "Sam Kelly"
    # 2026-07-01T02:30:00 UTC == 2026-06-30 22:30 America/New_York (EDT, -4)
    assert r.transaction_date == date(2026, 6, 30)


def test_charge_i_am_actor_is_inflow(records):
    # I charged Jordan and it settled -> money comes TO me -> positive
    r = records[2]
    assert r.amount == 12.0
    assert r.name == "Received from Jordan Rivera"


def test_charge_i_am_target_is_outflow_and_pending(records):
    # Sam charged me -> I owe/pay -> negative; not settled -> Pending
    r = records[3]
    assert r.amount == -40.0
    assert r.name == "Sent to Sam Kelly"
    assert r.status == TransactionStatus.PENDING


def test_transacted_at_is_utc_timestamp(records):
    r = records[0]
    assert r.transacted_at is not None
    assert r.transacted_at.tzinfo is not None
    assert r.transacted_at.astimezone(UTC).isoformat().startswith("2026-07-02T15:04:18")


def test_all_venmo_records_need_review(records):
    # category is None on every Venmo record -> compute_review_status -> Needs Review
    for r in records:
        assert compute_review_status(r) == ReviewStatus.NEEDS_REVIEW
