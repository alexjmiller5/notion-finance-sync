"""Tests for the Bilt transactions/v2 JSON parser (pure, fixture-driven).

Fixture: real captured response from
``GET api.biltrewards.com/bilt-card/cards/{cardId}/transactions/v2`` (2026-07-03
recon; see data/snapshots/bilt/recon_20260703/FINDINGS.md), plus one synthetic
pending purchase. Gitignored (real data).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from notion_finance_sync.banks import bilt
from notion_finance_sync.models import (
    AccountType,
    BankName,
    CanonicalCategory,
    CardNetwork,
    TransactionStatus,
)

FIXTURE = Path(__file__).parent / "fixtures" / "bilt" / "transactions_v2.json"


@pytest.fixture
def raw() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def records(raw):
    return bilt.parse_transactions(raw)


def _by_id(records, source_id):
    return next(r for r in records if r.source_id == source_id)


def test_parses_settled_and_pending(raw, records):
    n = len(raw["transactions"]["settled"]) + len(raw["transactions"]["pending"])
    assert len(records) == n == 17


def test_purchase_sign_is_flipped_to_negative(records):
    # Bilt API: purchases POSITIVE; our convention: spend NEGATIVE.
    rec = _by_id(records, "2f0e54f7-addb-55c6-ada6-2b795512984a")  # NY Grill & Deli
    assert rec.amount == -17.68


def test_purchase_fields(records):
    rec = _by_id(records, "2f0e54f7-addb-55c6-ada6-2b795512984a")
    assert rec.name == "NY Grill & Deli"
    assert rec.payee == "NY Grill & Deli"
    assert rec.status == TransactionStatus.POSTED
    assert rec.bank == BankName.BILT
    assert rec.account_type == AccountType.CREDIT_CARD
    assert rec.card_network == CardNetwork.MASTERCARD
    assert rec.credit_card_account == "Bilt Blue"
    assert rec.bank_category == "GROCERIES"
    assert rec.category == CanonicalCategory.GROCERIES
    assert rec.source_account_id == "5b6f3bb6-11dd-43a6-bce7-6252d85cb3f9"
    assert rec.raw_data["merchant"]["mcc"] == "5499"


def test_transacted_at_and_eastern_date(records):
    # createdAt 2026-07-03T01:30:00Z renders as July 2 in the Bilt UI (ET) —
    # transaction_date must use the Eastern date, not the UTC one.
    rec = _by_id(records, "144ff83d-ab60-5230-8df7-0bfa3e0b6490")
    assert rec.transacted_at == datetime(2026, 7, 3, 1, 30, tzinfo=UTC)
    assert rec.transaction_date == date(2026, 7, 2)


def test_payment_is_positive_transfer(records):
    # PAYMENT legs (autopay + Bilt Housing adjustment) are inflows to the card
    # and auto-categorized Transfer (card payments, SPEC §17 spirit).
    rec = _by_id(records, "144ff83d-ab60-5230-8df7-0bfa3e0b6490")  # Payment - Bilt Housing
    assert rec.amount == 1796.67
    assert rec.category == CanonicalCategory.TRANSFER


def test_rent_purchase_categorized_rent(records):
    # The rent charge itself: PURCHASE with displayCategory RENT.
    rent = [r for r in records if r.category == CanonicalCategory.RENT and r.amount < 0]
    assert rent, "expected at least one rent purchase"
    # rent charges appear as "Bilt Housing Payment" (or "Bilt Rewards" pre-Apr 2026)
    assert all(r.name in ("Bilt Housing Payment", "Bilt Rewards") for r in rent)


def test_refund_is_positive_inflow(records):
    rec = next(r for r in records if r.raw_data["type"] == "REFUND")
    assert rec.amount == 0.68
    assert rec.name == "Foreign Currency Refund"


def test_pending_purchase(records):
    rec = _by_id(records, "00000000-0000-5000-8000-000000000001")
    assert rec.status == TransactionStatus.PENDING
    assert rec.amount == -12.5
    assert rec.category == CanonicalCategory.DINING


def test_unmapped_merchant_category_leaves_category_none():
    raw = {
        "transactions": {
            "pending": [],
            "settled": [
                {
                    "accountId": "a",
                    "transactionId": "t1",
                    "status": "SETTLED",
                    "type": "PURCHASE",
                    "subType": "TRANSACTION_SUB_TYPE_UNSPECIFIED",
                    "amount": {"amount": 5.0, "currencyCode": "USD"},
                    "createdAt": "2026-06-15T12:00:00Z",
                    "description": "Mystery Shop",
                    "merchant": {"name": "Mystery Shop", "category": "SOMETHING_NEW"},
                    "displayCategory": "PURCHASE",
                }
            ],
        }
    }
    (rec,) = bilt.parse_transactions(raw)
    assert rec.category is None  # -> Needs Review downstream
    assert rec.bank_category == "SOMETHING_NEW"


def test_fetch_windows_never_exceed_limit():
    # Backend rejects ranges much over 180 days; we chunk into <=90-day windows.
    windows = bilt._date_windows(date(2026, 1, 1), date(2026, 7, 3))
    assert windows[0][0] == date(2026, 1, 1)
    assert windows[-1][1] == date(2026, 7, 3)
    for start, end in windows:
        assert (end - start).days <= 90
    # contiguous, no gaps or overlaps
    for (_, e1), (s2, _) in zip(windows, windows[1:], strict=False):
        assert (s2 - e1).days == 1


# ---------------------------------------------------------------------------
# true_rewards matching from /loyalty/activity (SPEC §11: Bilt Blue per-txn
# points are scraped inline). Fixture is the real captured feed (gitignored).
# ---------------------------------------------------------------------------

LOYALTY_FIXTURE = Path(__file__).parent / "fixtures" / "bilt" / "loyalty_activity.json"


@pytest.fixture
def matched(raw):
    records = bilt.parse_transactions(raw)
    entries = json.loads(LOYALTY_FIXTURE.read_text())["entries"]
    bilt.match_true_rewards(records, entries)
    return records


def _rec(matched, source_id):
    return next(r for r in matched if r.source_id == source_id)


def test_purchase_points_matched_by_merchant_name(matched):
    # NY Grill & Deli $17.68 -> +17 pts (feed title == merchant name)
    assert _rec(matched, "2f0e54f7-addb-55c6-ada6-2b795512984a").true_rewards == 17


def test_points_matched_despite_fx_amount_difference(matched):
    # Mercado Pago: feed amount 341.55 (pre-FX) vs txn 342.23 — name match wins
    rec = next(r for r in matched if r.name == "Mercado Pago Ticketmas")
    assert rec.true_rewards == 341


def test_housing_points_matched_to_rent_purchases_by_date(matched):
    # Housing Points entries carry no amount; matched to the rent purchase
    # nearest in date: Feb -> 3763, Apr -> 48, Jun -> 1.
    rents = sorted(
        (r for r in matched if r.category == CanonicalCategory.RENT and r.amount < 0),
        key=lambda r: r.transaction_date,
    )
    by_month = {r.transaction_date.month: r.true_rewards for r in rents}
    assert by_month[2] == 3763
    assert by_month[4] == 48
    assert by_month[6] == 1


def test_payments_never_get_rewards(matched):
    for r in matched:
        if r.amount > 0:
            assert r.true_rewards is None


def test_unmatched_purchases_stay_none(matched):
    # July rent purchase has no Housing Points feed entry yet
    july_rent = next(
        r
        for r in matched
        if r.category == CanonicalCategory.RENT and r.amount < 0 and r.transaction_date.month == 7
    )
    assert july_rent.true_rewards is None


def test_cross_card_entries_never_match_bilt_records(raw):
    # A DINING (cross-card) feed entry whose name collides with a Bilt txn must
    # NOT set true_rewards — those points belong on another bank's row.
    records = bilt.parse_transactions(raw)
    entries = [
        {
            "title": "NY Grill & Deli",
            "category": "DINING",
            "pointState": "EARNED",
            "totalPoints": 999,
            "datetime": "2026-02-07T12:00:00Z",
        }
    ]
    bilt.match_true_rewards(records, entries)
    rec = next(r for r in records if r.source_id == "2f0e54f7-addb-55c6-ada6-2b795512984a")
    assert rec.true_rewards is None


def test_entry_outside_date_window_does_not_match(raw):
    # Housing Points entry weeks away from any rent purchase -> no match.
    records = bilt.parse_transactions(raw)
    entries = [
        {
            "title": "Housing Points",
            "category": "BILT_MC",
            "pointState": "EARNED",
            "totalPoints": 500,
            "datetime": "2026-03-15T12:00:00Z",
        }
    ]
    bilt.match_true_rewards(records, entries)
    assert all(r.true_rewards is None for r in records)
