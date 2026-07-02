"""Tests for the pure pieces of historical scraping: dedupe + statement stx parsing."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from notion_finance_sync.banks.bofa import assemble, fetchers
from notion_finance_sync.models import (
    AccountType,
    BankName,
    TransactionRecord,
    TransactionStatus,
)

FX = Path(__file__).parent / "fixtures" / "bofa"


def _rec(source_id: str, amount: float = -1.0):
    return TransactionRecord(
        source_id=source_id,
        source_account_id="",
        name="x",
        amount=amount,
        transaction_date=date(2026, 6, 1),
        transacted_at=None,
        status=TransactionStatus.POSTED,
        bank=BankName.BANK_OF_AMERICA,
        account_type=AccountType.CREDIT_CARD,
    )


def test_dedupe_keeps_first_occurrence():
    recs = [_rec("A", -1), _rec("B", -2), _rec("A", -1), _rec("C", -3), _rec("B", -2)]
    out = assemble.dedupe_by_source_id(recs)
    assert [r.source_id for r in out] == ["A", "B", "C"]


def test_dedupe_keeps_rows_without_source_id():
    recs = [_rec("", -1), _rec("", -2), _rec("A", -3), _rec("A", -3)]
    out = assemble.dedupe_by_source_id(recs)
    # both blank-id rows kept; duplicate "A" collapsed to one
    assert len(out) == 3
    assert sum(1 for r in out if r.source_id == "A") == 1


def test_statement_stx_options_parses_dropdown():
    html = (FX / "card_statement.html").read_text()
    opts = fetchers.statement_stx_options(html)
    # the Travel Rewards statement dropdown lists ~11 prior statement periods
    assert len(opts) >= 5
    for label, stx in opts:
        assert label and "Current" not in label
        assert re.fullmatch(r"[0-9a-f]{16,}", stx), f"stx not a hex token: {stx!r}"
