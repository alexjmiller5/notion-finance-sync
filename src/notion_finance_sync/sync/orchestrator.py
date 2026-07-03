"""Sync orchestrator — ties scrapers, the Notion client, diff/orphan logic,
and the health tracker into a working sync pipeline.

Two entry points:

- ``run_one_bank(session_id, ...)``: run a single bank end-to-end. Used by the
  on-demand FastAPI endpoint and called by ``run_all_banks`` for each entry
  in the bank registry.
- ``run_all_banks(...)``: run every bank in the registry sequentially.

Per the SPEC §2 / §15 / §19 design:

- Banks run serially (anti-bot signal smoothing + simpler 2FA SMS reading).
- Each bank gets up to 3 attempts within one run, with a brief pause between.
- After 3 failed attempts, ``record_failure`` increments the health counter;
  if the threshold has been reached, ``create_failure_task`` opens a Notion
  task asking Alex to debug. Failures in task creation are swallowed so the
  original sync error is never masked.
- After a successful scrape, orphan detection runs (Pending Notion rows
  missing from the scrape are flipped to Released).
- ``SUPPORTS_LIVE = False`` scrapers (closed accounts: TD, old Fidelity IRA)
  return ``status="skipped"`` without making HTTP calls. They remain reachable
  via the backfill flow.
- Enrichers run after all banks succeed (Phase 2). Each enricher is wrapped
  in a try/except so that a failure (including the v1 ``NotImplementedError``
  stubs) is logged and the sync continues.
"""

from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import structlog

from notion_finance_sync.banks import registry as bank_registry
from notion_finance_sync.banks._base import BankScraper
from notion_finance_sync.config.settings import (
    NOTION_TRANSACTIONS_DATA_SOURCE_ID,
    get_notion_api_key,
)
from notion_finance_sync.enrichers import registry as enricher_registry
from notion_finance_sync.health.notion_task import create_failure_task
from notion_finance_sync.health.tracker import (
    needs_escalation,
    record_failure,
    record_success,
)
from notion_finance_sync.models import compute_review_status
from notion_finance_sync.notion.client import NotionClient
from notion_finance_sync.sync.diffing import build_transaction_changes
from notion_finance_sync.sync.orphan import detect_orphans, filter_pending

logger = structlog.get_logger()

DEFAULT_LOOKBACK_DAYS = 14
"""Default ``since`` window when the caller doesn't provide one."""

MAX_ATTEMPTS = 3
"""Per SPEC §15: 3 attempts per bank per run."""

DEFAULT_RETRY_PAUSE_SECONDS = 3
"""Real runs override this to ~300-600s; v1's testable default is short."""


SyncStatus = Literal["success", "failure", "skipped"]


@dataclass
class SyncResult:
    """Per-bank outcome from one orchestrator run."""

    session_id: str
    status: SyncStatus
    transactions_created: int = 0
    transactions_updated: int = 0
    transactions_unchanged: int = 0
    pending_released: int = 0
    error: str | None = None
    error_traceback: str | None = None
    duration_seconds: float = 0.0
    attempts: int = 0
    """How many times the scrape was attempted (1-3)."""


@dataclass
class _AttemptOutcome:
    """Counters from a single successful scrape attempt."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    released: int = 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_one_bank(
    session_id: str,
    *,
    since: date | None = None,
    retry_pause_seconds: float = DEFAULT_RETRY_PAUSE_SECONDS,
) -> SyncResult:
    """Run the sync pipeline for a single bank session.

    Enrichers are NOT run here — Phase 2 (enricher correlation) is global and
    only runs from ``run_all_banks`` after all banks have succeeded.

    Args:
        session_id: Key into ``BANK_REGISTRY``. Raises ``KeyError`` if unknown.
        since: Lower bound for ``fetch_recent``. Defaults to today - 14 days.
        retry_pause_seconds: Pause between retry attempts within this run.
            Defaults to ``DEFAULT_RETRY_PAUSE_SECONDS``; tests pass ``0``.

    Returns:
        ``SyncResult`` describing what happened.
    """
    scraper = bank_registry.get_scraper(session_id)
    if since is None:
        since = date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS)

    return await _sync_one(scraper, since=since, retry_pause_seconds=retry_pause_seconds)


async def run_all_banks(
    *,
    since: date | None = None,
    skip_enrichers: bool = False,
    retry_pause_seconds: float = DEFAULT_RETRY_PAUSE_SECONDS,
) -> dict[str, SyncResult]:
    """Run the sync pipeline for every registered bank, serially.

    Returns:
        Dict mapping each bank's ``session_id`` to its ``SyncResult``.
    """
    if since is None:
        since = date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS)

    results: dict[str, SyncResult] = {}
    for session_id, scraper in bank_registry.BANK_REGISTRY.items():
        results[session_id] = await _sync_one(
            scraper, since=since, retry_pause_seconds=retry_pause_seconds
        )

    if not skip_enrichers:
        await _run_enrichers()

    return results


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _sync_one(
    scraper: BankScraper,
    *,
    since: date,
    retry_pause_seconds: float,
) -> SyncResult:
    """Run one bank's sync, including retries + escalation on failure."""
    session_id = scraper.SESSION_ID
    started_at = datetime.now(tz=UTC)

    if not scraper.SUPPORTS_LIVE:
        logger.info("sync_skipped_closed_account", session_id=session_id)
        return SyncResult(
            session_id=session_id,
            status="skipped",
            error="closed account — no live sync",
            duration_seconds=0.0,
            attempts=0,
        )

    client = _build_notion_client()

    last_error: str | None = None
    last_tb: str | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        logger.info(
            "sync_attempt_started", session_id=session_id, attempt=attempt, since=since.isoformat()
        )
        try:
            outcome = await _run_one_attempt(scraper, client, since=since)
        except Exception as exc:  # noqa: BLE001 — we want to catch everything
            last_error = f"{type(exc).__name__}: {exc}"
            last_tb = traceback.format_exc()
            logger.warning(
                "sync_attempt_failed",
                session_id=session_id,
                attempt=attempt,
                error=last_error,
            )
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(retry_pause_seconds)
            continue

        # Success!
        record_success(session_id)
        duration = (datetime.now(tz=UTC) - started_at).total_seconds()
        logger.info(
            "sync_succeeded",
            session_id=session_id,
            attempt=attempt,
            created=outcome.created,
            updated=outcome.updated,
            unchanged=outcome.unchanged,
            released=outcome.released,
            duration_seconds=duration,
        )
        return SyncResult(
            session_id=session_id,
            status="success",
            transactions_created=outcome.created,
            transactions_updated=outcome.updated,
            transactions_unchanged=outcome.unchanged,
            pending_released=outcome.released,
            duration_seconds=duration,
            attempts=attempt,
        )

    # All attempts exhausted.
    if last_error is None:
        raise RuntimeError("retry loop exited without recording an error — invariant violated")
    count = record_failure(session_id, last_error)
    if needs_escalation(session_id):
        try:
            await create_failure_task(
                session_id=session_id,
                bank_display_name=scraper.BANK_DISPLAY_NAME,
                error_summary=last_error,
                consecutive_failures=count,
            )
        except Exception as task_err:  # noqa: BLE001
            # Don't let task-creation failure mask the original sync error.
            logger.error(
                "failure_task_create_failed",
                session_id=session_id,
                original_error=last_error,
                task_error=str(task_err),
            )

    duration = (datetime.now(tz=UTC) - started_at).total_seconds()
    logger.error(
        "sync_failed",
        session_id=session_id,
        attempts=MAX_ATTEMPTS,
        error=last_error,
        duration_seconds=duration,
    )
    return SyncResult(
        session_id=session_id,
        status="failure",
        error=last_error,
        error_traceback=last_tb,
        duration_seconds=duration,
        attempts=MAX_ATTEMPTS,
    )


async def _run_one_attempt(
    scraper: BankScraper,
    client: NotionClient,
    *,
    since: date,
) -> _AttemptOutcome:
    """Execute a single scrape -> diff -> apply -> orphan cycle.

    Any exception propagates to the retry loop in ``_sync_one``.
    """
    # 1. Scrape (in a worker thread — the browser login drives SeleniumBase's own
    #    asyncio loop, which can't start inside our running event loop)
    scraped = await asyncio.to_thread(scraper.fetch_recent, since)

    # 2. Read existing Notion rows
    existing = await client.get_existing_transactions(since_date=since.isoformat())

    # 3. Diff
    changes = build_transaction_changes(scraped=scraped, existing=existing)

    # 4. Apply creates + updates. Default Review Status per the heuristic
    #    (Venmo / PDF / refund-shaped txns -> Needs Review; rest -> Reviewed).
    #    The scraper may set review_status explicitly for special cases; only
    #    fill in when None.
    for record in changes.to_create:
        if record.review_status is None:
            record.review_status = compute_review_status(record)
        await client.create_from_record(record)
    for page_id, record in changes.to_update:
        if record.review_status is None:
            record.review_status = compute_review_status(record)
        await client.update_from_record(page_id, record)

    # 5. Orphan release (only after a clean scrape — see SPEC §9)
    pending = filter_pending(existing)
    orphans = detect_orphans(
        pending_notion_rows=pending,
        fresh_scrape_records=scraped,
        scrape_was_successful=True,
    )
    for orphan in orphans:
        await client.release_transaction(
            page_id=orphan.page_id,
            release_date=orphan.release_date.isoformat(),
        )

    return _AttemptOutcome(
        created=len(changes.to_create),
        updated=len(changes.to_update),
        unchanged=len(changes.unchanged),
        released=len(orphans),
    )


async def _run_enrichers() -> None:
    """Phase 2: invoke every registered enricher, catching all failures.

    Per SPEC §13 / Task 11 brief, enricher implementations are still
    ``NotImplementedError`` stubs in v1. We do NOT want a stub to crash the
    sync, so each enricher is wrapped in try/except.

    Even when a real enricher returns updates, we only need a thin glue here:
    call ``fetch_external_data`` then ``correlate_to_notion``, then push each
    ``NotionUpdate`` via ``client.update_transaction``.
    """
    client = _build_notion_client()
    # Enrichers need the current Notion rows to correlate against. Reuse the
    # default lookback window so we cover the same horizon as the scrapers.
    since_iso = (date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS)).isoformat()

    for source, enricher in enricher_registry.ENRICHER_REGISTRY.items():
        try:
            entries = enricher.fetch_external_data()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "enricher_fetch_failed", source=source, error=f"{type(exc).__name__}: {exc}"
            )
            continue

        try:
            notion_txns = await client.get_existing_transactions(since_date=since_iso)
            updates = enricher.correlate_to_notion(entries, notion_txns)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "enricher_correlate_failed",
                source=source,
                error=f"{type(exc).__name__}: {exc}",
            )
            continue

        for update in updates:
            try:
                await client.update_transaction(update.page_id, update.field_updates)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "enricher_update_failed",
                    source=source,
                    page_id=update.page_id,
                    error=f"{type(exc).__name__}: {exc}",
                )


def _build_notion_client() -> NotionClient:
    return NotionClient(
        api_key=get_notion_api_key(),
        data_source_id=NOTION_TRANSACTIONS_DATA_SOURCE_ID,
    )
