"""Pending -> posted reconciliation.

A card transaction is assigned a DIFFERENT bank-native id when it moves from
pending to posted (verified for U.S. Bank and BofA). Because the whole sync
dedupes on ``source_id``, the posted form looks brand-new: it gets created as a
second row while the stale pending row falls out of the scrape and would be
Released. That's a duplicate, and a semantically wrong "Released" — that status
should mean a genuine authorization reversal, not a dedup artifact.

This module matches a freshly-scraped record back to the pending Notion row it
supersedes, so the orchestrator can update that row IN PLACE (pending -> posted,
amount adjusted for a tip, new id) instead of create-new + release-old.

The matcher is deliberately conservative: it only merges an unambiguous 1:1 pair
(same account, close date, same sign, matching merchant, compatible amount). When
anything is ambiguous it declines — leaving a transient duplicate that self-heals
next sync is always safer than silently dropping or mis-merging a real transaction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import structlog

from notion_finance_sync.models import TransactionRecord

logger = structlog.get_logger()

# Auth -> post usually settles within a few days; cap the window so we never pair a
# pending row with an unrelated later purchase at the same merchant.
_MAX_DAY_GAP = 6


@dataclass
class Reconciliation:
    """Result of matching new records to the pending rows they supersede."""

    matched: list[tuple[str, TransactionRecord]]  # (pending page_id, superseding record)
    remaining_creates: list[TransactionRecord]  # records with no pending predecessor
    reconciled_source_ids: set[str]  # pending rows that were matched (do NOT release)


def _norm_merchant(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _merchant_match(a: str | None, b: str | None) -> bool:
    """True if the shorter normalized merchant is a prefix of the longer (>=4 chars).

    Handles pending "Dishes At Home" vs posted "Dishes At Home 123 Ny".
    """
    na, nb = _norm_merchant(a), _norm_merchant(b)
    if not na or not nb:
        return False
    lo, hi = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(lo) >= 4 and hi.startswith(lo)


def _amount_compatible(new_amt: float | None, old_amt: float | None) -> bool:
    """Same sign and magnitude within a tip/hold tolerance (or exactly equal)."""
    if new_amt is None or old_amt is None:
        return False
    if (new_amt < 0) != (old_amt < 0):
        return False
    dn, do = abs(new_amt), abs(old_amt)
    if abs(dn - do) < 0.005:
        return True
    return abs(dn - do) <= max(0.30 * do, 2.0)


def _parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _is_match(record: TransactionRecord, pending_row: dict) -> bool:
    if record.source_account_id != pending_row.get("source_account_id"):
        return False
    rec_date = _parse_date(record.transaction_date)
    row_date = _parse_date(pending_row.get("transaction_date"))
    if rec_date is None or row_date is None:
        return False
    if abs((rec_date - row_date).days) > _MAX_DAY_GAP:
        return False
    if not _amount_compatible(record.amount, pending_row.get("amount")):
        return False
    return _merchant_match(
        record.payee or record.name, pending_row.get("payee") or pending_row.get("name")
    )


def reconcile_pending_to_posted(
    *,
    to_create: list[TransactionRecord],
    orphan_pending_rows: dict[str, dict],
) -> Reconciliation:
    """Pair new records with the pending rows they supersede.

    Args:
        to_create: records the diff flagged as new (no existing row for their id).
        orphan_pending_rows: source_id -> full pending row dict for pending rows that
            are NOT in this scrape (i.e. the rows that would otherwise be released).

    Only unambiguous 1:1 matches are reconciled; ambiguous ones are left alone.
    """
    # Build the bipartite match set, then keep only mutually-unique pairs so we never
    # guess between two same-merchant/same-amount candidates.
    creates_for_row: dict[str, list[int]] = {sid: [] for sid in orphan_pending_rows}
    rows_for_create: dict[int, list[str]] = {i: [] for i in range(len(to_create))}
    for sid, row in orphan_pending_rows.items():
        for i, rec in enumerate(to_create):
            if _is_match(rec, row):
                creates_for_row[sid].append(i)
                rows_for_create[i].append(sid)

    matched: list[tuple[str, TransactionRecord]] = []
    reconciled_ids: set[str] = set()
    matched_create_idxs: set[int] = set()
    for sid, idxs in creates_for_row.items():
        if len(idxs) != 1:
            continue
        i = idxs[0]
        if len(rows_for_create[i]) != 1:
            continue  # this record also matches another pending row -> ambiguous
        matched.append((orphan_pending_rows[sid]["page_id"], to_create[i]))
        reconciled_ids.add(sid)
        matched_create_idxs.add(i)

    remaining = [rec for i, rec in enumerate(to_create) if i not in matched_create_idxs]

    if matched:
        logger.info("pending_reconciled", count=len(matched))
    return Reconciliation(
        matched=matched,
        remaining_creates=remaining,
        reconciled_source_ids=reconciled_ids,
    )
