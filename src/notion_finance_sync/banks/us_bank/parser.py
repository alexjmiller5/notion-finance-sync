"""Pure parser for the U.S. Bank ``txnsDetails`` GraphQL response.

Offline + deterministic: JSON dict -> ``list[TransactionRecord]``. All the I/O
(login, in-page fetch) lives in ``session.py``.

Sign convention (recon 2026-07-03): ``transactionAmount`` is an UNSIGNED magnitude;
``debitCreditMemo`` gives the direction. DEBIT = spend = negative; CREDIT =
payment/refund back to the card = positive.

Category: U.S. Bank exposes its own MX enrichment as ``enrichedDetails.category`` +
``subCategory`` (NOT MCC). We store ``"<category>: <subCategory>"`` as ``bank_category``
and map to canonical via the granular ``SUBCATEGORY_MAP`` first, then the coarse
``CATEGORY_MAP`` fallback so unseen subcategories still land in the right bucket.
"""

from __future__ import annotations

import re
from datetime import date

from notion_finance_sync.models import (
    AccountType,
    BankName,
    CanonicalCategory,
    CardNetwork,
    TransactionRecord,
    TransactionStatus,
)

# SPEC §17: P2P funding legs are Transfers regardless of the bank's own category.
_TRANSFER_RE = re.compile(r"\b(zelle|venmo|cash\s*app|apple\s*cash)\b", re.IGNORECASE)

# Granular U.S. Bank subCategory -> canonical (checked first; overrides the coarse map).
SUBCATEGORY_MAP: dict[str, CanonicalCategory] = {
    "Groceries": CanonicalCategory.GROCERIES,
    "Gas": CanonicalCategory.GAS,
    "Public Transportation": CanonicalCategory.TRANSIT,
    "Pharmacy": CanonicalCategory.HEALTHCARE,
    "Eyecare": CanonicalCategory.HEALTHCARE,
    "Credit Card Payment": CanonicalCategory.TRANSFER,
}

# Coarse U.S. Bank top-level category -> canonical (this is the BankScraper protocol's
# CATEGORY_MAP). Fallback when the subCategory isn't specifically mapped above.
CATEGORY_MAP: dict[str, CanonicalCategory] = {
    "Food & Dining": CanonicalCategory.DINING,
    "Bills & Utilities": CanonicalCategory.BILLS_UTILITIES,
    "Health & Fitness": CanonicalCategory.HEALTHCARE,
    "Travel": CanonicalCategory.TRAVEL,
    "Shopping": CanonicalCategory.ONLINE_SHOPPING,
    "Transfer": CanonicalCategory.TRANSFER,
    "Income": CanonicalCategory.INCOME,
    # Buckets with no clean canonical home — user reviews these.
    "Entertainment": CanonicalCategory.OTHER,
    "Auto & Transport": CanonicalCategory.OTHER,
    "Business Services": CanonicalCategory.OTHER,
    "Fees & Charges": CanonicalCategory.OTHER,
    "Gifts & Donations": CanonicalCategory.OTHER,
    "Personal Care": CanonicalCategory.OTHER,
}


def _canonical(category: str, subcategory: str, description: str) -> CanonicalCategory | None:
    if _TRANSFER_RE.search(description or ""):
        return CanonicalCategory.TRANSFER
    if subcategory in SUBCATEGORY_MAP:
        return SUBCATEGORY_MAP[subcategory]
    return CATEGORY_MAP.get(category)  # None when the bank left it uncategorized


def _bank_category(category: str, subcategory: str) -> str | None:
    if category and subcategory:
        return f"{category}: {subcategory}"
    return category or subcategory or None


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return date.fromisoformat(s[:10])


def _record(txn: dict, card_meta: dict, *, status: TransactionStatus) -> TransactionRecord:
    enriched = txn.get("enrichedDetails") or {}
    category_raw = enriched.get("category") or ""
    subcategory_raw = enriched.get("subCategory") or ""
    description = txn.get("description") or ""
    clean = (enriched.get("description") or "").strip() or description

    magnitude = abs(float(txn.get("transactionAmount") or 0.0))
    sign = 1.0 if (txn.get("debitCreditMemo") or "").upper() == "CREDIT" else -1.0

    # true purchase date when present, else the posting date
    txn_date = _parse_date(txn.get("pointOfSaleDate")) or _parse_date(txn.get("postedDateTime"))

    notion_select, network = card_meta.get(txn.get("accountNumber"), (None, None))

    return TransactionRecord(
        source_id=txn["transactionUniqueId"],
        source_account_id=txn.get("accountToken", ""),
        name=clean,
        amount=sign * magnitude,
        transaction_date=txn_date,
        transacted_at=None,  # settlement timestamp only; not the true swipe time
        status=status,
        payee=clean,
        memo=description,
        bank_category=_bank_category(category_raw, subcategory_raw),
        category=_canonical(category_raw, subcategory_raw, description),
        bank=BankName.US_BANK,
        credit_card_account=notion_select,
        card_network=network,
        account_type=AccountType.CREDIT_CARD,
        account_name=(txn.get("accountName") or f"Credit Card ...{txn.get('accountNumber', '')}"),
        raw_data=txn,
    )


def parse_activity(
    raw: dict, card_meta: dict[str, tuple[str, CardNetwork]]
) -> list[TransactionRecord]:
    """Parse a ``txnsDetails`` response into ``TransactionRecord``s.

    Args:
        raw: the parsed GraphQL JSON response body.
        card_meta: accountNumber (last 4) -> (Notion "Credit Card / Account" select
            value, CardNetwork).
    """
    resp = raw["data"]["txnsDetails"]["txnsResponse"]
    records: list[TransactionRecord] = []
    for txn in resp.get("postedTransactions") or []:
        records.append(_record(txn, card_meta, status=TransactionStatus.POSTED))
    for txn in resp.get("pendingTransactions") or []:
        records.append(_record(txn, card_meta, status=TransactionStatus.PENDING))
    return records
