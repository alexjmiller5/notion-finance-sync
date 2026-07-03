"""Tests for pending->posted reconciliation.

A card txn gets a DIFFERENT bank-native id when it moves from pending to posted
(true for U.S. Bank and BofA). Without reconciliation the posted form is created
as a new row and the stale pending row is Released — a duplicate, and a wrong
"Released" (that status should mean a genuine auth reversal). ``reconcile_pending_to_posted``
matches a newly-scraped record back to the pending row it supersedes so the row is
updated in place instead.
"""

from __future__ import annotations

from datetime import date

from notion_finance_sync.models import TransactionRecord, TransactionStatus
from notion_finance_sync.sync.reconcile import reconcile_pending_to_posted


def _rec(source_id, *, account="acct-1", name="Bar X", amount=-40.0, day=1):
    return TransactionRecord(
        source_id=source_id,
        source_account_id=account,
        name=name,
        amount=amount,
        transaction_date=date(2026, 7, day),
        transacted_at=None,
        status=TransactionStatus.POSTED,
        payee=name,
    )


def _pending_row(source_id, *, page_id="pg-1", account="acct-1", name="Bar X", amount=-40.0, day=1):
    return {
        "page_id": page_id,
        "source_id": source_id,
        "source_account_id": account,
        "name": name,
        "payee": name,
        "amount": amount,
        "transaction_date": date(2026, 7, day).isoformat(),
        "status": "Pending",
    }


def test_exact_match_reconciles_in_place():
    posted = _rec("B", amount=-40.0, day=3)  # same amount, 2 days later
    orphan = {"A": _pending_row("A", page_id="pg-A", amount=-40.0, day=1)}
    result = reconcile_pending_to_posted(to_create=[posted], orphan_pending_rows=orphan)

    assert result.matched == [("pg-A", posted)]
    assert result.remaining_creates == []
    assert result.reconciled_source_ids == {"A"}


def test_tip_drift_still_matches_same_merchant():
    posted = _rec("B", amount=-48.0, day=2)  # +20% tip
    orphan = {"A": _pending_row("A", page_id="pg-A", amount=-40.0, day=1)}
    result = reconcile_pending_to_posted(to_create=[posted], orphan_pending_rows=orphan)
    assert result.matched == [("pg-A", posted)]
    assert result.remaining_creates == []


def test_different_merchant_does_not_match():
    posted = _rec("B", name="Coffee Y", amount=-40.0, day=2)
    orphan = {"A": _pending_row("A", name="Bar X", amount=-40.0, day=1)}
    result = reconcile_pending_to_posted(to_create=[posted], orphan_pending_rows=orphan)
    assert result.matched == []
    assert result.remaining_creates == [posted]
    assert result.reconciled_source_ids == set()


def test_different_account_does_not_match():
    posted = _rec("B", account="acct-2", amount=-40.0, day=2)
    orphan = {"A": _pending_row("A", account="acct-1", amount=-40.0, day=1)}
    result = reconcile_pending_to_posted(to_create=[posted], orphan_pending_rows=orphan)
    assert result.matched == []


def test_far_apart_dates_do_not_match():
    posted = _rec("B", amount=-40.0, day=20)  # 19 days after the pending
    orphan = {"A": _pending_row("A", amount=-40.0, day=1)}
    result = reconcile_pending_to_posted(to_create=[posted], orphan_pending_rows=orphan)
    assert result.matched == []


def test_opposite_sign_does_not_match():
    posted = _rec("B", amount=40.0, day=2)  # a refund/credit, not the spend
    orphan = {"A": _pending_row("A", amount=-40.0, day=1)}
    result = reconcile_pending_to_posted(to_create=[posted], orphan_pending_rows=orphan)
    assert result.matched == []


def test_ambiguous_two_same_merchant_same_amount_left_unmatched():
    # Two identical pending coffees + one posted -> can't tell which -> don't merge.
    posted = _rec("B", name="Coffee", amount=-5.0, day=2)
    orphans = {
        "A1": _pending_row("A1", page_id="pg-A1", name="Coffee", amount=-5.0, day=1),
        "A2": _pending_row("A2", page_id="pg-A2", name="Coffee", amount=-5.0, day=1),
    }
    result = reconcile_pending_to_posted(to_create=[posted], orphan_pending_rows=orphans)
    assert result.matched == []  # ambiguous -> safe: leave a transient dup
    assert result.remaining_creates == [posted]
    assert result.reconciled_source_ids == set()


def test_merchant_prefix_match_handles_appended_location():
    # pending "Dishes At Home" -> posted "Dishes At Home 123 Ny"
    posted = _rec("B", name="Dishes At Home 123 Ny", amount=-15.19, day=2)
    orphan = {"A": _pending_row("A", name="Dishes At Home", amount=-15.19, day=1)}
    result = reconcile_pending_to_posted(to_create=[posted], orphan_pending_rows=orphan)
    assert result.matched == [("pg-1", posted)]


def test_unmatched_create_and_orphan_pass_through():
    posted = _rec("B", name="New Store", amount=-10.0, day=2)
    orphan = {"A": _pending_row("A", name="Old Merchant", amount=-99.0, day=1)}
    result = reconcile_pending_to_posted(to_create=[posted], orphan_pending_rows=orphan)
    assert result.remaining_creates == [posted]
    assert result.reconciled_source_ids == set()
