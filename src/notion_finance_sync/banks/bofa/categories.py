"""BofA category mapping.

BofA tags each transaction with a numeric ``spendingCategoryCode`` (deposit JSON)
or a ``code label`` string (card detail HTML), e.g. ``112`` / ``Groceries: Groceries``.

This module holds:
- ``BOFA_CATEGORY_CODE_TO_LABEL`` — the full 61-entry code -> label map, captured
  once from ``POST /myaccounts/omni/spending/v4/category-domain`` (fixture
  ``tests/fixtures/bofa/category_code_map.json``). Embedded as a constant so it's
  committed and independent of the gitignored capture.
- ``BOFA_LABEL_TO_CANONICAL`` — BofA label -> our canonical taxonomy (SPEC §10).
  Best-effort defaults; the raw ``Bank Category`` label is always preserved on the
  record so Alex can re-map later without rescraping.

The raw label is authoritative for auditing; the canonical is a convenience default.
"""

from __future__ import annotations

from notion_finance_sync.models import CanonicalCategory as C

# --------------------------------------------------------------------------
# code -> BofA label (captured 2026-07-02; see module docstring)
# --------------------------------------------------------------------------
BOFA_CATEGORY_CODE_TO_LABEL: dict[str, str] = {
    "100": "Business Expenses: Business Miscellaneous",
    "101": "Business Expenses: Dues & Subscriptions",
    "102": "Business Expenses: Office Maintenance",
    "103": "Business Expenses: Office Supplies",
    "104": "Business Expenses: Postage & Shipping",
    "105": "Business Expenses: Printing",
    "106": "Education: Education",
    "107": "Finance: Credit Card Payments",
    "108": "Finance: Loans",
    "109": "Finance: Service Charges/Fees",
    "110": "Finance: Taxes",
    "111": "Giving: Giving",
    "112": "Groceries: Groceries",
    "113": "Health: Healthcare/Medical",
    "114": "Health: Insurance",
    "115": "Home & Utilities: Cable/Satellite Services",
    "116": "Home & Utilities: Home Improvement",
    "117": "Home & Utilities: Home Maintenance",
    "118": "Home & Utilities: Mortgages",
    "119": "Home & Utilities: Rent",
    "120": "Home & Utilities: Telephone Services",
    "121": "Home & Utilities: Utilities",
    "122": "Cash, Checks & Misc: ATM/Cash Withdrawals",
    "123": "Cash, Checks & Misc: Checks",
    "124": "Cash, Checks & Misc: Other Bills",
    "125": "Cash, Checks & Misc: Other Expenses",
    "126": "Personal & Family Care: Child/Dependent Expenses",
    "127": "Personal & Family Care: Personal Care",
    "128": "Personal & Family Care: Pets/Pet Care",
    "129": "Restaurants & Dining: Restaurants/Dining",
    "130": "Savings & Transfers: Savings",
    "131": "Savings & Transfers: Securities Trades",
    "132": "Savings & Transfers: Transfers",
    "133": "Shopping & Entertainment: Clothing/Shoes",
    "134": "Shopping & Entertainment: Electronics",
    "135": "Shopping & Entertainment: Entertainment",
    "136": "Shopping & Entertainment: General Merchandise",
    "137": "Shopping & Entertainment: Gifts",
    "138": "Shopping & Entertainment: Hobbies",
    "139": "Shopping & Entertainment: Online Services",
    "140": "Transportation: Automotive Expenses",
    "141": "Transportation: Car Payments",
    "142": "Transportation: Gasoline/Fuel",
    "143": "Transportation: Public Transportation",
    "144": "Travel: Travel",
    "147": "Income: Consulting",
    "148": "Income: Deposits",
    "149": "Income: Expense Reimbursement",
    "150": "Income: Interest",
    "151": "Income: Investment Income",
    "152": "Income: Other Income",
    "153": "Income: Paychecks/Salary",
    "154": "Income: Retirement Income",
    "155": "Income: Sales",
    "156": "Income: Services",
    "157": "Income: Wages Paid",
}

# --------------------------------------------------------------------------
# BofA label -> canonical (best-effort; raw label preserved on the record)
# --------------------------------------------------------------------------
BOFA_LABEL_TO_CANONICAL: dict[str, C] = {
    # Groceries / dining
    "Groceries: Groceries": C.GROCERIES,
    "Restaurants & Dining: Restaurants/Dining": C.DINING,
    # Transport
    "Transportation: Gasoline/Fuel": C.GAS,
    "Transportation: Public Transportation": C.TRANSIT,
    "Transportation: Automotive Expenses": C.OTHER,
    "Transportation: Car Payments": C.BILLS_UTILITIES,
    # Travel
    "Travel: Travel": C.TRAVEL,
    # Bills & utilities / home
    "Home & Utilities: Utilities": C.BILLS_UTILITIES,
    "Home & Utilities: Telephone Services": C.BILLS_UTILITIES,
    "Home & Utilities: Cable/Satellite Services": C.BILLS_UTILITIES,
    "Home & Utilities: Home Improvement": C.OTHER,
    "Home & Utilities: Home Maintenance": C.OTHER,
    "Home & Utilities: Mortgages": C.RENT,
    "Home & Utilities: Rent": C.RENT,
    # Health
    "Health: Healthcare/Medical": C.HEALTHCARE,
    "Health: Insurance": C.BILLS_UTILITIES,
    # Cash / checks / misc
    "Cash, Checks & Misc: ATM/Cash Withdrawals": C.CASH_ATM,
    "Cash, Checks & Misc: Checks": C.OTHER,
    "Cash, Checks & Misc: Other Bills": C.BILLS_UTILITIES,
    "Cash, Checks & Misc: Other Expenses": C.OTHER,
    # Finance / transfers
    "Finance: Credit Card Payments": C.TRANSFER,
    "Finance: Loans": C.OTHER,
    "Finance: Service Charges/Fees": C.OTHER,
    "Finance: Taxes": C.OTHER,
    "Savings & Transfers: Savings": C.TRANSFER,
    "Savings & Transfers: Securities Trades": C.TRANSFER,
    "Savings & Transfers: Transfers": C.TRANSFER,
    # Shopping & entertainment
    "Shopping & Entertainment: Clothing/Shoes": C.DEPARTMENT_STORES,
    "Shopping & Entertainment: Electronics": C.ONLINE_SHOPPING,
    "Shopping & Entertainment: Entertainment": C.OTHER,
    "Shopping & Entertainment: General Merchandise": C.DEPARTMENT_STORES,
    "Shopping & Entertainment: Gifts": C.OTHER,
    "Shopping & Entertainment: Hobbies": C.OTHER,
    "Shopping & Entertainment: Online Services": C.ONLINE_SHOPPING,
    # Income (all -> INCOME)
    "Income: Consulting": C.INCOME,
    "Income: Deposits": C.INCOME,
    "Income: Expense Reimbursement": C.INCOME,
    "Income: Interest": C.INCOME,
    "Income: Investment Income": C.INCOME,
    "Income: Other Income": C.INCOME,
    "Income: Paychecks/Salary": C.INCOME,
    "Income: Retirement Income": C.INCOME,
    "Income: Sales": C.INCOME,
    "Income: Services": C.INCOME,
    "Income: Wages Paid": C.INCOME,
    # Personal & family / business / education / giving -> OTHER
    "Personal & Family Care: Child/Dependent Expenses": C.OTHER,
    "Personal & Family Care: Personal Care": C.OTHER,
    "Personal & Family Care: Pets/Pet Care": C.OTHER,
    "Education: Education": C.OTHER,
    "Giving: Giving": C.OTHER,
    "Business Expenses: Business Miscellaneous": C.OTHER,
    "Business Expenses: Dues & Subscriptions": C.OTHER,
    "Business Expenses: Office Maintenance": C.OTHER,
    "Business Expenses: Office Supplies": C.OTHER,
    "Business Expenses: Postage & Shipping": C.OTHER,
    "Business Expenses: Printing": C.OTHER,
}


def canonical_for_label(label: str | None) -> C | None:
    """Map a BofA category label (e.g. ``'Groceries: Groceries'``) to canonical.

    Whitespace-tolerant. Returns ``None`` for unknown/absent labels (caller then
    leaves ``category`` null -> the record defaults to ``Needs Review``).
    """
    if not label:
        return None
    return BOFA_LABEL_TO_CANONICAL.get(label.strip())


def canonical_for_code(code: str | int | None) -> C | None:
    """Map a BofA ``spendingCategoryCode`` (e.g. ``'112'``) to canonical."""
    if code is None:
        return None
    label = BOFA_CATEGORY_CODE_TO_LABEL.get(str(code).strip())
    return canonical_for_label(label)
