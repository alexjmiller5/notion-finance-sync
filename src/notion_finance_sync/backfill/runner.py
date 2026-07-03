"""One-time historical backfill: fetch_historical -> diff -> Notion.

Unlike the daily sync (``fetch_recent`` + orphan release), backfill:
- uses ``fetch_historical(since, end)`` so it iterates ALL live statement periods
  (cards) + the full deposit cursor (checking),
- does NOT run orphan release (backfilling old data must not flip old pendings),
- is idempotent via the source-id diff, so it's safe to re-run / resume.

The heavy live scrape happens in the scraper; this module just diffs against the
existing Notion rows and writes the creates/updates (respecting the client's
rate limiting). ``dry_run`` computes the plan without writing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date

import structlog

from notion_finance_sync.banks import registry as bank_registry
from notion_finance_sync.config.settings import (
    NOTION_TRANSACTIONS_DATA_SOURCE_ID,
    get_notion_api_key,
)
from notion_finance_sync.models import compute_review_status
from notion_finance_sync.notion.client import NotionClient
from notion_finance_sync.sync.diffing import build_transaction_changes

logger = structlog.get_logger()


@dataclass
class BackfillResult:
    session_id: str
    scraped: int
    to_create: int
    to_update: int
    unchanged: int
    created: int = 0
    updated: int = 0
    dry_run: bool = False


def _make_client() -> NotionClient:
    return NotionClient(
        api_key=get_notion_api_key(),
        data_source_id=NOTION_TRANSACTIONS_DATA_SOURCE_ID,
    )


async def run_backfill(
    session_id: str,
    *,
    since: date,
    end: date | None = None,
    dry_run: bool = False,
    client: NotionClient | None = None,
) -> BackfillResult:
    """Backfill one bank's live history in ``[since, end]`` into Notion."""
    end = end or date.today()
    scraper = bank_registry.get_scraper(session_id)

    logger.info(
        "backfill_scrape_start", bank=session_id, since=since.isoformat(), end=end.isoformat()
    )
    # The scrape drives SeleniumBase (its own asyncio loop) — run it in a worker
    # thread so it doesn't collide with our running event loop.
    records = await asyncio.to_thread(scraper.fetch_historical, since, end)
    logger.info("backfill_scraped", bank=session_id, count=len(records))

    client = client or _make_client()
    existing = await client.get_existing_transactions(since_date=since.isoformat())
    changes = build_transaction_changes(scraped=records, existing=existing)

    # default Review Status for anything we'll write
    for record in changes.to_create:
        if record.review_status is None:
            record.review_status = compute_review_status(record)
    for _page_id, record in changes.to_update:
        if record.review_status is None:
            record.review_status = compute_review_status(record)

    result = BackfillResult(
        session_id=session_id,
        scraped=len(records),
        to_create=len(changes.to_create),
        to_update=len(changes.to_update),
        unchanged=len(changes.unchanged),
        dry_run=dry_run,
    )

    if dry_run:
        logger.info(
            "backfill_dry_run",
            bank=session_id,
            to_create=result.to_create,
            to_update=result.to_update,
            unchanged=result.unchanged,
        )
        return result

    for record in changes.to_create:
        await client.create_from_record(record)
        result.created += 1
    for page_id, record in changes.to_update:
        await client.update_from_record(page_id, record)
        result.updated += 1

    logger.info("backfill_written", bank=session_id, created=result.created, updated=result.updated)
    return result
