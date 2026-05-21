"""Diff scraped transactions against existing Notion rows to compute changes.

Pure function. No I/O. The sync orchestrator calls this with:
  - the existing Notion rows (queried once at the start of the sync)
  - the fresh scrape results from one bank

Returns three lists: pages to create, pages to update, pages unchanged.
The orphan module handles Pending->Released separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import structlog

from notion_finance_sync.models import TransactionRecord

logger = structlog.get_logger()


@dataclass
class TransactionChanges:
    to_create: list[TransactionRecord]
    to_update: list[tuple[str, TransactionRecord]]  # (page_id, record)
    unchanged: list[TransactionRecord]


# Fields that, if changed, trigger an update to an existing Notion row.
MATERIAL_FIELDS = (
    "name",
    "amount",
    "transaction_date",
    "transacted_at",
    "status",
    "payee",
    "memo",
    "bank_category",
    "category",
    "calculated_rewards",
    "true_rewards",
    "bilt_points",
    "bilt_partner",
    "quantity",
    "ticker",
    "price_per_share",
)


def build_transaction_changes(
    *,
    scraped: Iterable[TransactionRecord],
    existing: dict[str, dict],
) -> TransactionChanges:
    """Compute the create/update/unchanged partition.

    Args:
        scraped: TransactionRecords just returned by a scraper.
        existing: dict[source_id -> row_dict] from NotionClient.get_existing_transactions.

    Returns:
        TransactionChanges describing what the sync should do.
    """
    to_create: list[TransactionRecord] = []
    to_update: list[tuple[str, TransactionRecord]] = []
    unchanged: list[TransactionRecord] = []

    for record in scraped:
        existing_row = existing.get(record.source_id)
        if existing_row is None:
            to_create.append(record)
            continue

        if _is_materially_different(record, existing_row):
            to_update.append((existing_row["page_id"], record))
        else:
            unchanged.append(record)

    logger.info(
        "diff_computed",
        create=len(to_create),
        update=len(to_update),
        unchanged=len(unchanged),
    )
    return TransactionChanges(
        to_create=to_create,
        to_update=to_update,
        unchanged=unchanged,
    )


def _is_materially_different(record: TransactionRecord, existing_row: dict) -> bool:
    """True if any material field differs between scrape and existing Notion row."""
    for field in MATERIAL_FIELDS:
        new_val = getattr(record, field, None)
        old_val = existing_row.get(field)

        # Normalize: dates from Notion are ISO strings, dates from records are date objects.
        if hasattr(new_val, "isoformat"):
            new_val = new_val.isoformat()

        # Treat None and empty string as equivalent
        if (new_val in (None, "")) and (old_val in (None, "")):
            continue

        if new_val != old_val:
            return True

    return False
