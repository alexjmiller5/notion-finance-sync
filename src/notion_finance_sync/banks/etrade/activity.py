"""Parse the E*Trade activities JSON + ESPP benefit-history lot table.

Pure module: JSON/dict in -> ``TransactionRecord`` list out. See
``data/snapshots/etrade/FINDINGS.md`` for the captured response shapes.

Sign conventions (already normalized by the bank):
- ``amount`` is signed: negative = outflow (ACH withdrawal), positive = inflow
  (dividend, sell proceeds). ESPP share allocations are 0.00 (no cash leg).
- ``quantity`` is signed: negative on sells.
"""

from __future__ import annotations

from datetime import datetime

from notion_finance_sync.models import (
    AccountType,
    BankName,
    CanonicalCategory,
    CategoryMap,
    TransactionRecord,
    TransactionStatus,
)

DEFAULT_ACCOUNT_NAME = "Individual Brokerage -5735"

# Curated Notion "Credit Card / Account" select value (Alex approved 2026-07-05).
NOTION_ACCOUNT = "E*Trade Brokerage"

# activityType (raw label, kept as bank_category) -> canonical category.
# Investment events: cash movements are Transfer, payouts are Income; trade
# legs land in Other (the quantity/ticker/price fields carry the substance).
CATEGORY_MAP: CategoryMap = {
    "Online Transfer": CanonicalCategory.TRANSFER,
    "Transfer": CanonicalCategory.TRANSFER,
    "Qualified Dividend": CanonicalCategory.INCOME,
    "Non-Qualified Dividend": CanonicalCategory.INCOME,
    "Dividend": CanonicalCategory.INCOME,
    "Interest": CanonicalCategory.INCOME,
    "Sold": CanonicalCategory.OTHER,
    "Bought": CanonicalCategory.OTHER,
}

_STATUS = {
    "POSTED": TransactionStatus.POSTED,
    "PENDING": TransactionStatus.PENDING,
}


def _num(value: object) -> float:
    """Parse E*Trade's numeric strings ('-8.738', '1,851.32', None)."""
    s = str(value or "").replace(",", "").replace("$", "").strip()
    return float(s) if s else 0.0


def parse_activities(
    raw: dict,
    *,
    account_name: str = DEFAULT_ACCOUNT_NAME,
    source_account_id: str | None = None,
) -> list[TransactionRecord]:
    """Parse an ``activities/v2`` response into ``TransactionRecord`` list.

    Args:
        raw: parsed JSON response body.
        account_name: display label for the account (free-text field).
        source_account_id: override for the bank-native account id (defaults to
            each txn's ``keyAcctNo``).
    """
    txns = raw["activityDetails"]["activities"]
    records: list[TransactionRecord] = []
    for t in txns:
        desc = " ".join(d.strip() for d in (t.get("description") or []) if d).strip()
        activity_type = (t.get("activityType") or "").strip()
        quantity = _num(t.get("quantity"))
        price = _num(t.get("price"))
        status = _STATUS.get((t.get("activityStatus") or "").upper(), TransactionStatus.POSTED)
        records.append(
            TransactionRecord(
                source_id=str(t["activityId"]),
                source_account_id=source_account_id or t.get("keyAcctNo", ""),
                name=desc,
                amount=_num(t.get("amount")),
                transaction_date=datetime.strptime(t["transactionDate"], "%m/%d/%y").date(),
                transacted_at=None,
                status=status,
                memo=desc,
                bank_category=activity_type or None,
                category=CATEGORY_MAP.get(activity_type),
                bank=BankName.ETRADE,
                credit_card_account=NOTION_ACCOUNT,
                account_type=AccountType.BROKERAGE,
                account_name=account_name,
                quantity=quantity or None,
                ticker=t.get("symbol") or None,
                price_per_share=price or None,
                raw_data=t,
            )
        )
    return records


def parse_espp_lots(tables: list[dict]) -> dict[str, float]:
    """Extract ``{quantity(3dp): purchase_price}`` from the Benefit History DOM dump.

    ``tables`` is the JS table dump captured by session.py: a list of
    ``{"headers": [...], "rows": [[cell, ...], ...]}``. The ESPP lot table is
    the one whose headers include "Purchase Date" and "Purchase Price".
    Quantities are normalized to 3 decimals to match the activities API format
    ('15.84' in the DOM vs '15.840' in the API).
    """
    for table in tables:
        headers = " ".join(table.get("headers") or [])
        if "Purchase Date" not in headers or "Purchase Price" not in headers:
            continue
        lots: dict[str, float] = {}
        for row in table.get("rows") or []:
            # row = [expand-icon, date, '$212.58', '8.738', sellable, market value]
            if len(row) < 4:
                continue
            try:
                price = _num(row[2])
                qty = _num(row[3])
            except ValueError:
                continue
            if price and qty:
                lots[f"{qty:.3f}"] = price
        return lots
    return {}


def enrich_espp_prices(records: list[TransactionRecord], lots: dict[str, float]) -> None:
    """Fill ``price_per_share`` on ESPP share allocations by quantity match.

    Allocations ('Allocate shares for N', activityType 'Transfer') come through
    the activities API with price 0.000; the Benefit History lot table has the
    actual purchase price, and lot quantity matches allocation quantity exactly.
    Cash ``amount`` stays 0 — the ESPP purchase has no brokerage cash leg.
    """
    for r in records:
        if r.price_per_share is None and r.quantity and r.quantity > 0:
            price = lots.get(f"{r.quantity:.3f}")
            if price:
                r.price_per_share = price
