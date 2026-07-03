"""Tests for the Wells Fargo statement PDF parser.

Two layers:
- Unit tests drive ``parse_statement_text`` with FORMAT-ACCURATE SYNTHETIC lines
  (made-up merchants/amounts in the real WF layout) so no real financial data is
  committed to source.
- An integration test runs ``parse`` over the real gitignored PDFs in
  data/statements/wf/ when present (skipped in CI where they're absent).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from notion_finance_sync.banks.wells_fargo import statements
from notion_finance_sync.models import (
    AccountType,
    BankName,
    CardNetwork,
    TransactionStatus,
)

# A synthetic old-format ("Bilt World Elite Mastercard", acct 6972) statement.
# Layout copied exactly from a real WF statement; merchants/amounts are invented.
BILT_TEXT = """\
Account Number Ending in 6972 24-Hour Customer Service: 1-833-404-2272
Billing Cycle 12/15/2025
Statement Closing Date 01/14/2026
Transaction Summary
Trans Date Post Date Reference Number Description of Transaction or Credit Amount
12/18 12/18 180001300 5550629B0H26KMZJT SAMPLE DELI NEW YORK NY $9.05
12/25 12/25 210001500 1230202B700SWH9E2 BPS*SAMPLE RENT NEW YORK NY $1,796.67
01/05 01/05 490001200 7271042QNS66F5ZTQ SAMPLE CAFE NEW YORK NY $10.13
01/08 01/08 F229000CR00CHGDDA AUTOMATIC PAYMENT - THANK YOU $1,850.50-
01/14 01/14 Interest Charge on Purchases $0.00
"""

# A synthetic new-format (Wells Fargo Autograph, acct 8000) empty statement.
AUTOGRAPH_EMPTY_TEXT = """\
WELLS FARGO AUTOGRAPH VISA SIGNATURE® CARD
Account ending in 8000
Statement Period to 06/12/2026
Transactions
Card Trans Post Reference Number Description Credits Charges
Ending Date Date in
"""


def _by_desc(records, needle):
    return next(r for r in records if needle in r.name)


def test_parses_all_real_transactions():
    recs = statements.parse_statement_text(BILT_TEXT)
    # 4 real txns (3 purchases + 1 payment); the $0.00 interest line is skipped.
    assert len(recs) == 4
    assert all(r.bank == BankName.WELLS_FARGO for r in recs)
    assert all(r.account_type == AccountType.CREDIT_CARD for r in recs)
    assert all(r.status == TransactionStatus.POSTED for r in recs)


def test_purchase_is_negative():
    rec = _by_desc(statements.parse_statement_text(BILT_TEXT), "SAMPLE DELI")
    assert rec.amount == -9.05  # purchase -> spend -> negative


def test_payment_is_positive():
    rec = _by_desc(statements.parse_statement_text(BILT_TEXT), "AUTOMATIC PAYMENT")
    assert rec.amount == 1850.50  # trailing '-' = money to card -> positive


def test_thousands_amount_parsed():
    rec = _by_desc(statements.parse_statement_text(BILT_TEXT), "SAMPLE RENT")
    assert rec.amount == -1796.67


def test_source_id_is_concatenated_reference():
    rec = _by_desc(statements.parse_statement_text(BILT_TEXT), "SAMPLE DELI")
    assert rec.source_id == "1800013005550629B0H26KMZJT"


def test_payment_single_token_reference():
    rec = _by_desc(statements.parse_statement_text(BILT_TEXT), "AUTOMATIC PAYMENT")
    assert rec.source_id == "F229000CR00CHGDDA"


def test_year_inference_rolls_over_december():
    recs = statements.parse_statement_text(BILT_TEXT)
    deli = _by_desc(recs, "SAMPLE DELI")  # 12/18 on a 01/14/2026 statement
    cafe = _by_desc(recs, "SAMPLE CAFE")  # 01/05 on the same statement
    assert deli.transaction_date == date(2025, 12, 18)  # prior year
    assert cafe.transaction_date == date(2026, 1, 5)  # statement year


def test_interest_line_skipped():
    recs = statements.parse_statement_text(BILT_TEXT)
    assert all("Interest Charge" not in r.name for r in recs)


def test_bilt_card_mapping():
    rec = statements.parse_statement_text(BILT_TEXT)[0]
    assert rec.credit_card_account == "Bilt World Elite Mastercard"
    assert rec.card_network == CardNetwork.MASTERCARD


def test_category_left_null_for_review():
    # WF statements expose no category -> stays null so the orchestrator flags Needs Review.
    recs = statements.parse_statement_text(BILT_TEXT)
    assert all(r.category is None and r.bank_category is None for r in recs)


def test_autograph_empty_statement_yields_nothing():
    assert statements.parse_statement_text(AUTOGRAPH_EMPTY_TEXT) == []


# --------------------------------------------------------------------------
# Integration: real gitignored PDFs (skipped when absent, e.g. in CI)
# --------------------------------------------------------------------------
WF_PDF_DIR = Path(__file__).resolve().parents[1] / "data" / "statements" / "wf"
_PDFS = sorted(WF_PDF_DIR.glob("*.pdf")) if WF_PDF_DIR.exists() else []


@pytest.mark.skipif(not _PDFS, reason="real WF statement PDFs not present")
def test_real_pdfs_parse_cleanly():
    recs = statements.parse(_PDFS)
    assert len(recs) > 0
    # every row has a stable, unique id
    ids = [r.source_id for r in recs]
    assert all(ids)
    assert len(ids) == len(set(ids)), "source_ids must be unique (dedupe key)"
    # Bilt-era rows are the only populated ones and map to the Bilt card
    assert all(r.credit_card_account == "Bilt World Elite Mastercard" for r in recs)
    # signs are sane: at least one spend and at least one payment/credit
    assert any(r.amount < 0 for r in recs)
    assert any(r.amount > 0 for r in recs)
