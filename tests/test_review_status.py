"""Tests for compute_review_status — the heuristic that defaults Review Status."""

from __future__ import annotations

from datetime import date

from notion_finance_sync.models import (
    AccountType,
    BankName,
    CanonicalCategory,
    ReviewStatus,
    TransactionRecord,
    TransactionStatus,
    compute_review_status,
)


def _record(
    *,
    amount: float,
    account_type: AccountType | None,
    category: CanonicalCategory | None,
) -> TransactionRecord:
    return TransactionRecord(
        source_id="src-1",
        source_account_id="acct-1",
        name="x",
        amount=amount,
        transaction_date=date(2026, 5, 1),
        transacted_at=None,
        status=TransactionStatus.POSTED,
        account_type=account_type,
        category=category,
        bank=BankName.BANK_OF_AMERICA,
    )


class TestCategoryNullForcesNeedsReview:
    def test_venmo_with_null_category(self):
        r = _record(amount=-5.0, account_type=AccountType.P2P, category=None)
        assert compute_review_status(r) == ReviewStatus.NEEDS_REVIEW

    def test_pdf_sourced_card_txn(self):
        r = _record(amount=-10.0, account_type=AccountType.CREDIT_CARD, category=None)
        assert compute_review_status(r) == ReviewStatus.NEEDS_REVIEW

    def test_no_account_type_either(self):
        r = _record(amount=-5.0, account_type=None, category=None)
        assert compute_review_status(r) == ReviewStatus.NEEDS_REVIEW


class TestPositiveAmountOnCardFlagsAsRefund:
    def test_positive_on_credit_card_with_category(self):
        r = _record(
            amount=50.0,
            account_type=AccountType.CREDIT_CARD,
            category=CanonicalCategory.DINING,
        )
        # Likely a refund; user should confirm and link
        assert compute_review_status(r) == ReviewStatus.NEEDS_REVIEW

    def test_positive_on_debit_card_with_category(self):
        r = _record(
            amount=20.0,
            account_type=AccountType.DEBIT_CARD,
            category=CanonicalCategory.GROCERIES,
        )
        assert compute_review_status(r) == ReviewStatus.NEEDS_REVIEW


class TestPositiveAmountOnInvestmentIsNormal:
    def test_positive_on_brokerage_is_reviewed(self):
        # Dividend on E*Trade — normal investment income
        r = _record(
            amount=15.0,
            account_type=AccountType.BROKERAGE,
            category=CanonicalCategory.INCOME,
        )
        assert compute_review_status(r) == ReviewStatus.REVIEWED

    def test_positive_on_401k_is_reviewed(self):
        # Employer match / dividend
        r = _record(
            amount=200.0,
            account_type=AccountType.FOUR_OH_ONE_K,
            category=CanonicalCategory.INCOME,
        )
        assert compute_review_status(r) == ReviewStatus.REVIEWED

    def test_positive_on_ira_is_reviewed(self):
        r = _record(
            amount=100.0,
            account_type=AccountType.IRA,
            category=CanonicalCategory.INCOME,
        )
        assert compute_review_status(r) == ReviewStatus.REVIEWED


class TestHappyPathIsReviewed:
    def test_negative_card_with_category(self):
        # Normal scrape: BofA Dining txn with mapped category — trust it
        r = _record(
            amount=-23.50,
            account_type=AccountType.CREDIT_CARD,
            category=CanonicalCategory.DINING,
        )
        assert compute_review_status(r) == ReviewStatus.REVIEWED

    def test_negative_checking_with_category(self):
        r = _record(
            amount=-1500.0,
            account_type=AccountType.CHECKING,
            category=CanonicalCategory.RENT,
        )
        assert compute_review_status(r) == ReviewStatus.REVIEWED


class TestPositiveOnNonCardWithCategoryIsReviewed:
    def test_positive_on_checking_is_reviewed(self):
        # Direct deposit, paycheck — normal
        r = _record(
            amount=3000.0,
            account_type=AccountType.CHECKING,
            category=CanonicalCategory.INCOME,
        )
        assert compute_review_status(r) == ReviewStatus.REVIEWED

    def test_positive_on_savings_is_reviewed(self):
        # Interest payment
        r = _record(
            amount=5.0,
            account_type=AccountType.SAVINGS,
            category=CanonicalCategory.INCOME,
        )
        assert compute_review_status(r) == ReviewStatus.REVIEWED
