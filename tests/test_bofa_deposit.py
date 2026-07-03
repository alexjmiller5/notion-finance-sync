"""Tests for the BofA deposit (checking/savings) JSON activity parser."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from notion_finance_sync.banks.bofa import deposit
from notion_finance_sync.models import (
    AccountType,
    BankName,
    CanonicalCategory,
    TransactionStatus,
)

FIXTURE = Path(__file__).parent / "fixtures" / "bofa" / "deposit_activity_raw.json"


@pytest.fixture
def raw() -> dict:
    return json.loads(FIXTURE.read_text())


def test_parses_all_transactions(raw):
    records = deposit.parse_activity(raw)
    assert len(records) == 8


def test_source_id_is_stable_and_token_independent(raw):
    import copy

    rec = deposit.parse_activity(raw)[0]
    # deterministic: same input -> same id
    assert deposit.parse_activity(copy.deepcopy(raw))[0].source_id == rec.source_id
    # NOT derived from transactionToken (which BofA regenerates every session)
    mutated = copy.deepcopy(raw)
    mutated["payload"]["depositActivity"]["transactionList"]["transactions"][0][
        "transactionToken"
    ] = "totally-different-token"
    assert deposit.parse_activity(mutated)[0].source_id == rec.source_id
    # IS sensitive to real content (running balance changes the id)
    mutated2 = copy.deepcopy(raw)
    mutated2["payload"]["depositActivity"]["transactionList"]["transactions"][0][
        "actualRunningBalanceAmount"
    ] = "999999.99"
    assert deposit.parse_activity(mutated2)[0].source_id != rec.source_id


def test_first_transaction_fields(raw):
    rec = deposit.parse_activity(raw)[0]
    assert rec.amount == -50.0
    assert rec.transaction_date == date(2026, 7, 1)
    assert rec.status == TransactionStatus.POSTED  # "Completed"
    assert rec.bank == BankName.BANK_OF_AMERICA
    assert rec.account_type == AccountType.CHECKING
    assert "Zelle" in rec.name
    # raw BofA label preserved from spendingCategoryCode 125
    assert rec.bank_category == "Cash, Checks & Misc: Other Expenses"


def test_zelle_payment_is_auto_categorized_transfer(raw):
    # SPEC §17: bank scraper auto-sets Category=Transfer for Zelle/Venmo/Cash App.
    rec = deposit.parse_activity(raw)[0]  # Zelle Recurring payment
    assert rec.category == CanonicalCategory.TRANSFER


def test_non_transfer_uses_bank_category_mapping(raw):
    # index 5: "BofA Rewards-Intl ATM ... Waiver", code 109 (Finance: Service Charges/Fees)
    rec = deposit.parse_activity(raw)[5]
    assert rec.bank_category == "Finance: Service Charges/Fees"
    assert rec.category == CanonicalCategory.OTHER
    assert rec.amount == 0.0


def test_signed_amounts_preserved(raw):
    records = deposit.parse_activity(raw)
    assert records[1].amount == -203.33  # Zelle to Trevor (debit)
    assert records[4].amount == 2000.0  # Zelle from Alexander (credit)


def test_account_name_override(raw):
    records = deposit.parse_activity(raw, account_name="My Checking")
    assert records[0].account_name == "My Checking"


def test_clean_description_trims_truncation_tail():
    from notion_finance_sync.banks.bofa.deposit import _clean_description

    truncated = ' Zelle Recurring payment to Alexander Miller - EverBank for "regularly...'
    assert (
        _clean_description(truncated) == "Zelle Recurring payment to Alexander Miller - EverBank"
    )
    # non-truncated descriptions are untouched (aside from strip)
    assert _clean_description(" Zelle payment to Trevor Conf# tw0n5t2yn ") == (
        "Zelle payment to Trevor Conf# tw0n5t2yn"
    )
    # mutation guard: a real trailing word that isn't a connector stays
    assert _clean_description("APPLE CASH DES:BANK XFER...") == "APPLE CASH DES:BANK XFER"
