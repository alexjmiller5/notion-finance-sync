"""Pure parser for EverBank's ``TransactionInqSVC`` JSON.

Offline, deterministic, TDD-able. Turns a ``result[]`` array (from either
``TransactionInqSVC`` or ``NextTransactionInqSVC`` — same row shape) into
``list[TransactionRecord]``.

EverBank savings transactions carry no category/MCC, so categorization is
keyword-based on the row's ``name`` + ``memo`` (see ``categorize``). Per SPEC §17,
Venmo/Zelle/Cash App/Apple Cash rows are auto-``Transfer`` regardless of anything
else. See ``data/snapshots/everbank/FINDINGS.md`` for field semantics.
"""

from __future__ import annotations

import re
from datetime import date

from notion_finance_sync.models import (
    AccountType,
    BankName,
    CanonicalCategory,
    CategoryMap,
    TransactionRecord,
    TransactionStatus,
)

# §17 — funding-leg / P2P counterparties are always Transfer.
_TRANSFER_P2P_RE = re.compile(r"\b(zelle|venmo|cash\s*app|apple\s*cash)\b", re.IGNORECASE)

# Keyword -> canonical, scanned (in order) against the uppercased "name memo"
# string when the §17 rule and the income rules don't fire. Everything on this
# transfer-hub savings account is a card payment, an inter-account transfer, or
# income; unmatched rows stay None -> Needs Review.
EVERBANK_KEYWORD_CATEGORY: CategoryMap = {
    "PAYROLL": CanonicalCategory.INCOME,
    "INTEREST CREDIT": CanonicalCategory.INCOME,
    "WEB PYMT": CanonicalCategory.TRANSFER,
    "WEB PMT": CanonicalCategory.TRANSFER,
    "ONLINE PMT": CanonicalCategory.TRANSFER,
    "PAYMENT": CanonicalCategory.TRANSFER,
    " PYMT": CanonicalCategory.TRANSFER,
    " PMT": CanonicalCategory.TRANSFER,
    "ACH TRNSFR": CanonicalCategory.TRANSFER,
    "EXTERNAL TRANSFER": CanonicalCategory.TRANSFER,
    "TRANSFER TO": CanonicalCategory.TRANSFER,
    "TRANSFER F": CanonicalCategory.TRANSFER,
    # Bilt card autopay (e.g. "BILT CARD HOUSING") funds a card -> Transfer even
    # when the row carries no explicit payment keyword.
    "BILT CARD": CanonicalCategory.TRANSFER,
}

_STATUS = {
    "processed": TransactionStatus.POSTED,
    "posted": TransactionStatus.POSTED,
    "pending": TransactionStatus.PENDING,
}


def categorize(name: str, memo: str) -> CanonicalCategory | None:
    """Best-effort canonical category from a row's name + memo (no bank category).

    Returns ``None`` for anything unmatched so the record defaults to Needs Review.
    """
    blob = f"{name} {memo}"
    if _TRANSFER_P2P_RE.search(blob):  # §17 hard rule wins
        return CanonicalCategory.TRANSFER
    upper = blob.upper()
    for keyword, category in EVERBANK_KEYWORD_CATEGORY.items():
        if keyword in upper:
            return category
    return None


def _status(value: str | None) -> TransactionStatus:
    return _STATUS.get((value or "").strip().lower(), TransactionStatus.POSTED)


def _source_id(t: dict) -> str:
    """Stable per-transaction id.

    ``trnId`` embeds the running balance, so it changes when a Pending row posts;
    ``controlNbr`` (ACH trace) is stable across that transition. System entries
    (e.g. interest) carry an all-zeros ``controlNbr`` and are always Posted, so we
    fall back to their (already-stable) ``trnId``.
    """
    control = (t.get("controlNbr") or "").strip()
    if control and set(control) != {"0"}:
        return control
    return t["trnId"]


def _clean_name(raw: str) -> str:
    """Title-case the SHOUTING bank label for a readable Notion row title."""
    return raw.strip().title() if raw else ""


def _parse_date(s: str) -> date:
    return date.fromisoformat(s.strip())


def parse_transactions(
    rows: list[dict],
    *,
    account_name: str = "EverBank Performance Savings",
    source_account_id: str = "",
    account_type: AccountType = AccountType.SAVINGS,
) -> list[TransactionRecord]:
    """Parse a ``result[]`` array into ``TransactionRecord``s.

    Enricher-owned fields (rewards) are left null — savings has no rewards.
    """
    records: list[TransactionRecord] = []
    for t in rows:
        raw_name = t.get("name") or t.get("trnName") or t.get("trnOnlineDesc") or ""
        memo = (t.get("memo") or "").strip()
        name = _clean_name(raw_name)
        records.append(
            TransactionRecord(
                source_id=_source_id(t),
                source_account_id=source_account_id,
                name=name,
                amount=float(t["trnSortAmount"]),
                transaction_date=_parse_date(t["postedDt"]),
                status=_status(t.get("trnStatus")),
                payee=name,
                memo=memo,
                bank_category=None,  # EverBank exposes no category on savings txns
                category=categorize(raw_name, memo),
                bank=BankName.EVERBANK,
                account_type=account_type,
                account_name=account_name,
                raw_data=t,
            )
        )
    return records
