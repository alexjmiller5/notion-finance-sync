"""Tests for the WF online activity JSON envelope parser.

Uses the REAL captured empty response (0 transactions — no PII) plus a synthetic
populated envelope to prove the notification trigger fires when count > 0.
"""

from __future__ import annotations

from notion_finance_sync.banks.wells_fargo import activity

# Real response captured live 2026-07-03 (Autograph …8000, full-history search, empty).
REAL_EMPTY = (
    '/*WellFargoProprietary%{"transactions":{"transactionStatus":"AVAILABLE",'
    '"transactionData":{"onlineClaimsForNonDelegateCustomer":true,'
    '"requestedCriteria":{"requestedPageNumber":1,"totalPages":0,'
    '"transactionCount":0,"transactionType":"MEMO_POSTED"},"showRunningBalance":true},'
    '"status":{"status":true}}}%WellFargoProprietary*/'
)

# Synthetic: same envelope shape but reporting 3 transactions (field-level txn shape
# is unknown until the card is used; only the count matters for the trigger).
SYNTHETIC_POPULATED = (
    '/*WellFargoProprietary%{"transactions":{"transactionStatus":"AVAILABLE",'
    '"transactionData":{"requestedCriteria":{"transactionCount":3}}}}%WellFargoProprietary*/'
)


def test_strip_envelope_parses_json():
    data = activity.strip_envelope(REAL_EMPTY)
    assert data["transactions"]["transactionStatus"] == "AVAILABLE"


def test_empty_response_counts_zero():
    assert activity.transaction_count(activity.strip_envelope(REAL_EMPTY)) == 0
    assert activity.has_transactions(REAL_EMPTY) is False


def test_populated_response_triggers():
    assert activity.transaction_count(activity.strip_envelope(SYNTHETIC_POPULATED)) == 3
    assert activity.has_transactions(SYNTHETIC_POPULATED) is True
