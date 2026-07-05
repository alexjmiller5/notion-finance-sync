"""Tests for the Fidelity 401k activity JSON parser.

Uses a sanitized fixture (`history_activity_sample.json`) with the exact shape
captured from the live `transactions/history` endpoint (see
data/snapshots/fidelity/FINDINGS.md) but fake amounts — safe to commit.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from notion_finance_sync.banks.fidelity import activity
from notion_finance_sync.models import (
    AccountType,
    BankName,
    CanonicalCategory,
    TransactionStatus,
)

FIXTURE = Path(__file__).parent / "fixtures" / "fidelity" / "history_activity_sample.json"


@pytest.fixture
def raw() -> dict:
    return json.loads(FIXTURE.read_text())


def test_skips_change_in_market_value(raw):
    # 8 rows in fixture, 1 is realizedGainLoss "Change in Market Value" -> dropped.
    records = activity.parse_activity(raw)
    assert len(records) == 7
    assert all(r.bank_category != "Change in Market Value" for r in records)


def test_only_acct_filters_foreign_account(raw):
    # The fixture includes a Roth IRA (acct 259079998) row; the 401k module must
    # drop it when only_acct is set to the 401k account.
    all_recs = activity.parse_activity(raw)
    assert any(r.source_account_id == "259079998" for r in all_recs)  # present unfiltered

    only_401k = activity.parse_activity(raw, only_acct="30072")
    assert len(only_401k) == 6
    assert all(r.source_account_id == "30072" for r in only_401k)


def test_contribution_fields(raw):
    rec = activity.parse_activity(raw)[0]
    assert rec.name == "Contributions: SP GLB EXUS IDX CL D"
    assert rec.amount == 116.49  # inflow, positive
    assert rec.quantity == 0.546
    assert rec.price_per_share == 213.47
    assert rec.ticker == "3957"
    assert rec.transaction_date == date(2026, 6, 30)
    assert rec.status == TransactionStatus.POSTED
    assert rec.bank == BankName.FIDELITY
    assert rec.account_type == AccountType.FOUR_OH_ONE_K
    assert rec.source_account_id == "30072"
    assert rec.category == CanonicalCategory.TRANSFER
    assert rec.bank_category == "Contributions"


def test_contribution_memo_has_source_breakdown(raw):
    rec = activity.parse_activity(raw)[0]
    # Employee ROTH + Employer Match + 3% Basic
    assert "ROTH" in rec.memo
    assert "EMPLOYER MATCH" in rec.memo
    assert "$51.77" in rec.memo


def test_fee_is_negative_and_other(raw):
    fee = next(r for r in activity.parse_activity(raw) if r.bank_category == "RECORDKEEPING FEE")
    assert fee.amount == -0.84
    assert fee.quantity == -0.003
    assert fee.category == CanonicalCategory.OTHER


def test_exchange_signs_and_transfer(raw):
    recs = activity.parse_activity(raw)
    ein = next(r for r in recs if "Exchanges" in r.name and r.amount > 0)
    eout = next(r for r in recs if "Exchanges" in r.name and r.amount < 0)
    assert ein.amount == 5826.15
    assert eout.amount == -5826.15
    assert ein.quantity == 237.0
    assert eout.quantity == -237.0
    assert ein.category == CanonicalCategory.TRANSFER
    assert eout.category == CanonicalCategory.TRANSFER


def test_dividend_is_income(raw):
    div = next(r for r in activity.parse_activity(raw) if r.bank_category == "Dividend")
    assert div.category == CanonicalCategory.INCOME
    assert div.amount == 12.44


def test_source_id_stable_and_unique(raw):
    records = activity.parse_activity(raw)
    ids = [r.source_id for r in records]
    assert len(ids) == len(set(ids))  # unique
    # deterministic: re-parsing gives identical ids
    again = [r.source_id for r in activity.parse_activity(raw)]
    assert ids == again


def test_credit_card_account_override(raw):
    recs = activity.parse_activity(raw, credit_card_account="Capital One 401k")
    assert all(r.credit_card_account == "Capital One 401k" for r in recs)
    # default is None (Notion option not yet created)
    assert activity.parse_activity(raw)[0].credit_card_account is None
