"""FastAPI HTTP server for on-demand syncs.

Runs locally on the Mac Mini at http://127.0.0.1:8765 by default.

Endpoints:
    GET  /health             - per-bank health status from data/health.json
    POST /sync               - run a full sync (all active banks + enrichers)
    POST /sync/{session_id}  - run a single bank's sync (1-3 min, fast feedback)

Hit from iOS Shortcut, curl, browser, or any LAN-connected client.
"""

from __future__ import annotations

import structlog
from fastapi import BackgroundTasks, FastAPI, HTTPException

from notion_finance_sync.health import tracker

logger = structlog.get_logger()

app = FastAPI(title="notion-finance-sync", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    """Return per-bank consecutive-failure tracker state."""
    return {"banks": tracker.get_all()}


@app.post("/sync")
async def sync_all(background_tasks: BackgroundTasks) -> dict:
    """Kick off a full sync (all banks + enrichers). Returns immediately;
    sync runs in the background.
    """
    # TODO: wire up to sync.orchestrator.run_all_banks()
    raise HTTPException(status_code=501, detail="Not yet implemented")


@app.post("/sync/{session_id}")
async def sync_one(session_id: str, background_tasks: BackgroundTasks) -> dict:
    """Kick off a sync for one bank. Returns immediately; sync runs in background."""
    # TODO: wire up to sync.orchestrator.run_one_bank(session_id)
    raise HTTPException(status_code=501, detail="Not yet implemented")
