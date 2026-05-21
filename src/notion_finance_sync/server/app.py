"""FastAPI HTTP server for on-demand syncs.

Runs locally on the Mac Mini at http://127.0.0.1:8765 by default.

Endpoints:
    GET  /health             - per-bank health status from data/health.json
    POST /sync               - run a full sync (all active banks + enrichers)
    POST /sync/{session_id}  - run a single bank's sync (1-3 min, fast feedback)

Hit from iOS Shortcut, curl, browser, or any LAN-connected client.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import BackgroundTasks, FastAPI, HTTPException

from notion_finance_sync.banks.registry import all_session_ids, get_scraper
from notion_finance_sync.health import tracker
from notion_finance_sync.sync.orchestrator import run_all_banks, run_one_bank

logger = structlog.get_logger()

app = FastAPI(title="notion-finance-sync", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    """Return per-bank consecutive-failure tracker state."""
    return {"banks": tracker.get_all()}


@app.post("/sync", status_code=202)
async def sync_all(background_tasks: BackgroundTasks) -> dict:
    """Kick off a full sync (all banks + enrichers). Returns immediately;
    sync runs in the background.
    """
    sync_id = str(uuid.uuid4())
    banks = all_session_ids()

    logger.info("sync_all_accepted", sync_id=sync_id, banks=banks)

    async def _run() -> None:
        log = logger.bind(sync_id=sync_id)
        log.info("background_sync_all_started")
        try:
            results = await run_all_banks()
            total_created = sum(r.transactions_created for r in results.values())
            total_updated = sum(r.transactions_updated for r in results.values())
            failed = [sid for sid, r in results.items() if r.status == "failure"]
            log.info(
                "background_sync_all_finished",
                total_created=total_created,
                total_updated=total_updated,
                failed_banks=failed,
            )
        except Exception:
            log.exception("background_sync_all_crashed")

    background_tasks.add_task(_run)

    return {"status": "accepted", "sync_id": sync_id, "banks": banks}


@app.post("/sync/{session_id}", status_code=202)
async def sync_one(session_id: str, background_tasks: BackgroundTasks) -> dict:
    """Kick off a sync for one bank. Returns immediately; sync runs in background."""
    try:
        get_scraper(session_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"unknown bank session_id: {session_id!r}",
        )

    sync_id = str(uuid.uuid4())
    logger.info("sync_one_accepted", sync_id=sync_id, session_id=session_id)

    async def _run() -> None:
        log = logger.bind(sync_id=sync_id, session_id=session_id)
        log.info("background_sync_one_started")
        try:
            result = await run_one_bank(session_id)
            log.info(
                "background_sync_one_finished",
                status=result.status,
                created=result.transactions_created,
                updated=result.transactions_updated,
                error=result.error,
            )
        except Exception:
            log.exception("background_sync_one_crashed")

    background_tasks.add_task(_run)

    return {"status": "accepted", "sync_id": sync_id, "bank": session_id}
