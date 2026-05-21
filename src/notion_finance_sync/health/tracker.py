"""Per-bank consecutive-failure counter, persisted to data/health.json.

State shape:
    {
        "bofa": {
            "consecutive_failures_today": 0,
            "last_success": "2026-05-19T03:42:11Z",
            "last_error": null,
            "last_attempt": "2026-05-19T03:42:11Z"
        },
        ...
    }

When a bank fails 3 times within the same calendar day, the orchestrator
calls health.notion_task.create_failure_task() to escalate.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TypedDict

import structlog

logger = structlog.get_logger()

HEALTH_FILE = Path(__file__).resolve().parents[3] / "data" / "health.json"
FAILURE_THRESHOLD = 3


class BankHealth(TypedDict, total=False):
    consecutive_failures_today: int
    last_success: str | None
    last_error: str | None
    last_attempt: str | None
    failure_day: str | None  # ISO date — resets counter on new day


def _load() -> dict[str, BankHealth]:
    if not HEALTH_FILE.exists():
        return {}
    return json.loads(HEALTH_FILE.read_text())


def _save(state: dict[str, BankHealth]) -> None:
    HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_FILE.write_text(json.dumps(state, indent=2))


def record_success(session_id: str) -> None:
    state = _load()
    now = datetime.now(tz=timezone.utc).isoformat()
    state[session_id] = {
        "consecutive_failures_today": 0,
        "last_success": now,
        "last_error": None,
        "last_attempt": now,
        "failure_day": None,
    }
    _save(state)
    logger.info("health_success", session_id=session_id)


def record_failure(session_id: str, error: str) -> int:
    """Record a failure. Returns the new consecutive-failures-today count."""
    state = _load()
    now = datetime.now(tz=timezone.utc)
    today_iso = now.date().isoformat()

    existing = state.get(session_id, {})
    if existing.get("failure_day") != today_iso:
        # New day, reset counter
        count = 1
    else:
        count = existing.get("consecutive_failures_today", 0) + 1

    state[session_id] = {
        "consecutive_failures_today": count,
        "last_success": existing.get("last_success"),
        "last_error": error,
        "last_attempt": now.isoformat(),
        "failure_day": today_iso,
    }
    _save(state)
    logger.warning("health_failure", session_id=session_id, count=count, error=error)
    return count


def needs_escalation(session_id: str) -> bool:
    """True if this bank has hit FAILURE_THRESHOLD failures today."""
    state = _load()
    h = state.get(session_id, {})
    today_iso = date.today().isoformat()
    return (
        h.get("failure_day") == today_iso
        and h.get("consecutive_failures_today", 0) >= FAILURE_THRESHOLD
    )


def get_all() -> dict[str, BankHealth]:
    """Return the full health state (for /health endpoint)."""
    return _load()
