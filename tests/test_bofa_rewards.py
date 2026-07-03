"""Tests for the BofA rewards landing parser + reward->transaction matcher."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from notion_finance_sync.banks.bofa import rewards
from notion_finance_sync.models import (
    AccountType,
    BankName,
    TransactionRecord,
    TransactionStatus,
)

FX = Path(__file__).parent / "fixtures" / "bofa"


@pytest.fixture
def rewards_html() -> str:
    return (FX / "rewards_landing.html").read_text()


def test_parses_reward_rows(rewards_html):
    entries = rewards.parse_rewards(rewards_html)
    assert len(entries) >= 20


def test_shell_reward_breakdown(rewards_html):
    entries = rewards.parse_rewards(rewards_html)
    shell = next(e for e in entries if "SHELL" in e["merchant"])
    assert shell["amount"] == 79.66
    assert shell["total_points"] == 209.11
    assert shell["base_points"] == 119.49
    assert shell["bonus_points"] == 89.62
    assert shell["status"] == "Pending"
    assert shell["transaction_date"] == date(2026, 6, 16)


def _rec(amount, payee, txn_date):
    return TransactionRecord(
        source_id="x",
        source_account_id="",
        name=payee,
        amount=amount,
        transaction_date=txn_date,
        status=TransactionStatus.POSTED,
        payee=payee,
        bank=BankName.BANK_OF_AMERICA,
        account_type=AccountType.CREDIT_CARD,
    )


def test_match_sets_true_rewards(rewards_html):
    entries = rewards.parse_rewards(rewards_html)
    rec = _rec(-79.66, "SHELL NAXOS", date(2026, 6, 16))
    matched = rewards.match_rewards([rec], entries)
    assert matched == 1
    assert rec.true_rewards == 209.11
    assert rec.raw_data.get("base_points") == 119.49
    assert rec.raw_data.get("bonus_points") == 89.62


def test_no_match_leaves_true_rewards_none(rewards_html):
    entries = rewards.parse_rewards(rewards_html)
    rec = _rec(-9999.99, "NONEXISTENT MERCHANT", date(2020, 1, 1))
    matched = rewards.match_rewards([rec], entries)
    assert matched == 0
    assert rec.true_rewards is None
