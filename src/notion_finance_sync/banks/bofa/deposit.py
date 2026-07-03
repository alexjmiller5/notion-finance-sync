"""Parse the BofA deposit (checking/savings) activity JSON.

Source: ``POST /ogateway/addapi/v1/activity`` — response shape
``payload.depositActivity.transactionList.transactions[]``. Cursor pagination via
``pagingRules.pagingNextPageItemToken`` (handled by the fetcher, not here).

This parser is pure: JSON dict -> ``list[TransactionRecord]``. The signed
``amount.amount`` and inline ``spendingCategoryCode`` make deposits easier than
cards. Per SPEC §17, Zelle/Venmo/Cash App/Apple Cash rows are auto-categorized
``Transfer`` (funding-leg / P2P) regardless of the bank's own category code.

NOTE: the list JSON truncates long descriptions and omits the cleaned merchant
name (verified 2026-07-02). The live scraper can enrich full description +
merchant name from the rendered UI detail; this parser uses whatever description
the payload carries.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

from notion_finance_sync.banks.bofa import categories
from notion_finance_sync.models import (
    AccountType,
    BankName,
    CanonicalCategory,
    TransactionRecord,
    TransactionStatus,
)

_TRANSFER_RE = re.compile(r"\b(zelle|venmo|cash\s*app|apple\s*cash)\b", re.IGNORECASE)

_STATUS = {
    "completed": TransactionStatus.POSTED,
    "posted": TransactionStatus.POSTED,
    "pending": TransactionStatus.PENDING,
}


def _stable_source_id(t: dict, account_id: str) -> str:
    """Content-derived, session-stable id for a deposit txn.

    BofA's ``transactionToken`` is REGENERATED every session (verified 2026-07-03:
    0/250 overlap between two captures of the same account) — using it as the
    source_id would create duplicate Notion rows on every sync. Hash stable fields
    instead; ``actualRunningBalanceAmount`` disambiguates same-day/same-amount txns.
    """
    key = "|".join(
        (
            account_id,
            str(t.get("formattedPostedDate", "")),
            str(t.get("amount", {}).get("amount", "")),
            str(t.get("actualRunningBalanceAmount", "")),
            (t.get("preferredDescription") or t.get("customizedDescription") or "").strip(),
        )
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _clean_description(desc: str) -> str:
    """Trim BofA's server-side truncation tail.

    The checking activity API truncates long descriptions to ~64 chars ending in
    ``...`` (the full text isn't exposed anywhere — no per-txn detail endpoint,
    verified 2026-07-03). When truncated, drop the dangling cut-off fragment so
    the name reads cleanly instead of ``... - EverBank for "regularly...``.
    """
    desc = desc.strip()
    if not desc.endswith("..."):
        return desc
    desc = desc[:-3].rstrip()
    if desc.count('"') % 2 == 1:  # unterminated quote from the cut → drop it
        desc = desc[: desc.rfind('"')].rstrip()
    # drop a trailing dangling connector word ("... EverBank for")
    desc = re.sub(r"[\s\-]+(for|to|at|on|from|with|the|a|an|of)\s*$", "", desc, flags=re.I)
    return desc.rstrip(" -")


def _parse_date(s: str):
    return datetime.strptime(s.strip(), "%m/%d/%Y").date()


def _status(value: str | None) -> TransactionStatus:
    return _STATUS.get((value or "").strip().lower(), TransactionStatus.POSTED)


def parse_activity(
    raw: dict,
    *,
    account_name: str | None = None,
    source_account_id: str | None = None,
    account_type: AccountType = AccountType.CHECKING,
) -> list[TransactionRecord]:
    """Parse an ``addapi/v1/activity`` response into ``TransactionRecord``s.

    Args:
        raw: the parsed JSON response body.
        account_name: override for the Notion account label (defaults to the
            payload's ``accountIdentifier.nickname``).
        source_account_id: override for the bank-native account id (defaults to
            the payload's ``accountIdentifier.adxid``).
        account_type: Checking (default) or Savings.
    """
    txns = raw["payload"]["depositActivity"]["transactionList"]["transactions"]
    records: list[TransactionRecord] = []
    for t in txns:
        acct = t.get("accountIdentifier", {})
        desc = _clean_description(
            t.get("customizedDescription") or t.get("preferredDescription") or ""
        )

        code = t.get("spendingCategoryCode")
        bank_label = categories.BOFA_CATEGORY_CODE_TO_LABEL.get(str(code)) if code else None
        category = categories.canonical_for_code(code)
        if _TRANSFER_RE.search(desc):
            category = CanonicalCategory.TRANSFER

        records.append(
            TransactionRecord(
                source_id=_stable_source_id(t, source_account_id or acct.get("adxid", "")),
                source_account_id=source_account_id or acct.get("adxid", ""),
                name=desc,
                amount=float(t["amount"]["amount"]),
                transaction_date=_parse_date(t["formattedPostedDate"]),
                transacted_at=None,
                status=_status(t.get("status", {}).get("value")),
                payee=desc,
                memo=desc,
                bank_category=bank_label,
                category=category,
                bank=BankName.BANK_OF_AMERICA,
                account_type=account_type,
                account_name=account_name or acct.get("nickname", ""),
                raw_data=t,
            )
        )
    return records
