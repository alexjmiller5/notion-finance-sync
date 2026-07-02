"""Tests for BofA category mapping (code -> BofA label -> canonical category)."""

from __future__ import annotations

import pytest

from notion_finance_sync.banks.bofa import categories as cat
from notion_finance_sync.models import CanonicalCategory


def test_code_to_label_covers_known_codes():
    assert cat.BOFA_CATEGORY_CODE_TO_LABEL["112"] == "Groceries: Groceries"
    assert cat.BOFA_CATEGORY_CODE_TO_LABEL["125"] == "Cash, Checks & Misc: Other Expenses"
    assert cat.BOFA_CATEGORY_CODE_TO_LABEL["129"] == "Restaurants & Dining: Restaurants/Dining"
    assert cat.BOFA_CATEGORY_CODE_TO_LABEL["144"] == "Travel: Travel"


def test_all_codes_have_a_canonical_mapping():
    # Every known BofA label must map to *some* canonical category (no silent gaps).
    for code, label in cat.BOFA_CATEGORY_CODE_TO_LABEL.items():
        assert cat.canonical_for_label(label) in set(CanonicalCategory), (
            f"code {code} label {label!r} has no canonical mapping"
        )


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("112", CanonicalCategory.GROCERIES),
        ("129", CanonicalCategory.DINING),
        ("142", CanonicalCategory.GAS),  # Transportation: Gasoline/Fuel
        ("143", CanonicalCategory.TRANSIT),  # Public Transportation
        ("144", CanonicalCategory.TRAVEL),
        ("121", CanonicalCategory.BILLS_UTILITIES),  # Utilities
        ("119", CanonicalCategory.RENT),
        ("113", CanonicalCategory.HEALTHCARE),
        ("122", CanonicalCategory.CASH_ATM),  # ATM/Cash Withdrawals
        ("107", CanonicalCategory.TRANSFER),  # Credit Card Payments
        ("132", CanonicalCategory.TRANSFER),  # Transfers
        ("153", CanonicalCategory.INCOME),  # Paychecks/Salary
    ],
)
def test_canonical_for_code(code, expected):
    assert cat.canonical_for_code(code) == expected


def test_unknown_code_returns_none():
    assert cat.canonical_for_code("999999") is None
    assert cat.canonical_for_label("Some Bank: Nonexistent Category") is None
