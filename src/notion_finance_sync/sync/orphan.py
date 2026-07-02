"""Pending -> Released orphan detection.

Simplified vs. the old notion-ai-budgeting-app — no dual-provider check, no
waiting period, no minimum age. With direct scraping, the bank's own UI is the
ground truth (see SPEC §9).

Rule: a Pending Notion row that isn't in this scrape's results → flip to
Released immediately, set Release Date = today.

The ONE guard: orphan detection only runs after a SUCCESSFUL scrape (no
exceptions, expected post-login state reached, returned data for the account).
Failed/partial scrapes do NOT modify Pending statuses — tomorrow's run handles
it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import structlog

from notion_finance_sync.models import TransactionRecord, TransactionStatus

logger = structlog.get_logger()


@dataclass
class OrphanRelease:
    """A Notion row to be flipped from Pending to Released."""

    page_id: str
    source_id: str
    release_date: date


def detect_orphans(
    *,
    pending_notion_rows: dict[str, dict],
    fresh_scrape_records: Iterable[TransactionRecord],
    scrape_was_successful: bool,
    explicit_releases: Iterable[TransactionRecord] = (),
) -> list[OrphanRelease]:
    """Find Pending Notion rows to flip to Released.

    Args:
        pending_notion_rows: dict[source_id -> row dict] from
            NotionClient.get_existing_transactions filtered to Status=Pending.
        fresh_scrape_records: TransactionRecords just returned by the scraper.
        scrape_was_successful: True only if the scraper exited cleanly and got
            data. If False, no orphans are detected (returns []).
        explicit_releases: TransactionRecords the scraper flagged as explicitly
            "Released" in the bank's UI (some banks do this for a day or two
            before removing the entry). These always orphan, regardless of
            absence detection.

    Returns:
        List of OrphanRelease records — one per Pending row missing from the
        scrape (or explicitly released).
    """
    if not scrape_was_successful:
        logger.info("orphan_skipped_scrape_unhealthy", pending_count=len(pending_notion_rows))
        return []

    fresh_ids = {r.source_id for r in fresh_scrape_records}
    explicit_ids = {r.source_id for r in explicit_releases}
    today = date.today()

    orphans: list[OrphanRelease] = []
    for source_id, row in pending_notion_rows.items():
        if source_id in explicit_ids or source_id not in fresh_ids:
            orphans.append(
                OrphanRelease(
                    page_id=row["page_id"],
                    source_id=source_id,
                    release_date=today,
                )
            )

    logger.info(
        "orphan_detected",
        pending_count=len(pending_notion_rows),
        fresh_count=len(fresh_ids),
        explicit_releases=len(explicit_ids),
        orphans=len(orphans),
    )
    return orphans


def filter_pending(rows: dict[str, dict]) -> dict[str, dict]:
    """Filter a dict of all Notion rows to just the Pending ones."""
    return {sid: row for sid, row in rows.items() if row.get("status") == TransactionStatus.PENDING}
