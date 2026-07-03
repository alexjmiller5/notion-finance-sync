"""Notify Alex (via a Notion task) when the Wells Fargo card starts showing activity.

The WF Autograph card is currently unused, so the live scraper returns nothing. The day
the online activity API reports a transaction, this drops a High-priority task in Alex's
Tasks DB telling him to build out the full WF online parser (the per-transaction JSON
shape can only be finalised against real data). Synchronous httpx so it can run inside the
SeleniumBase worker thread without an event loop.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import structlog

from notion_finance_sync.config.settings import (
    NOTION_API_VERSION,
    NOTION_TASKS_DATA_SOURCE_ID,
    get_notion_api_key,
)

logger = structlog.get_logger()

_TITLE = "Implement Wells Fargo online scraper — live transactions detected"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_notion_api_key()}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


def _open_task_exists(client: httpx.Client, headers: dict[str, str]) -> bool:
    """True if a matching High-priority To Do task was created in the last 7 days."""
    since = (datetime.now(tz=UTC) - timedelta(days=7)).isoformat()
    body = {
        "filter": {
            "and": [
                {"property": "Status", "status": {"equals": "To Do"}},
                {"property": "Date Created", "created_time": {"after": since}},
            ]
        },
        "page_size": 20,
    }
    resp = client.post(
        f"https://api.notion.com/v1/data_sources/{NOTION_TASKS_DATA_SOURCE_ID}/query",
        headers=headers,
        json=body,
    )
    resp.raise_for_status()
    for page in resp.json().get("results", []):
        parts = page.get("properties", {}).get("Name", {}).get("title", [])
        title = parts[0]["plain_text"] if parts else ""
        if title.startswith("Implement Wells Fargo online scraper"):
            return True
    return False


def notify_wells_fargo_activity(*, since: str) -> None:
    """Create a High-priority Notion task (idempotent within a 7-day window)."""
    headers = _headers()
    notes = (
        "The Wells Fargo Autograph card (…8000) is now showing activity — its account-"
        f"details page no longer says 'no recent activity' (checked since {since}).\n\n"
        "The card was previously unused, so the per-transaction JSON shape was never "
        "captured and the online parser is a stub. Now that there's real data:\n"
        "  1. Capture a populated transactions/fetch response "
        "(banks/wells_fargo/session.py already logs in and opens the card).\n"
        "  2. Flesh out banks/wells_fargo/activity.py to map rows -> TransactionRecord.\n"
        "  3. Wire it into WellsFargoScraper.fetch_recent and add real fixtures + tests.\n\n"
        "Historical (old Bilt-era) transactions already come from statement PDFs via "
        "fetch_historical."
    )
    with httpx.Client(timeout=30.0) as client:
        if _open_task_exists(client, headers):
            logger.info("wf_activity_task_exists")
            return
        resp = client.post(
            "https://api.notion.com/v1/pages",
            headers=headers,
            json={
                "parent": {"data_source_id": NOTION_TASKS_DATA_SOURCE_ID},
                "properties": {
                    "Name": {"title": [{"text": {"content": _TITLE}}]},
                    "Status": {"status": {"name": "To Do"}},
                    "Priority": {"select": {"name": "High"}},
                    "Tags": {"multi_select": [{"name": "Finances"}, {"name": "Development"}]},
                    "Notes": {"rich_text": [{"text": {"content": notes}}]},
                },
            },
        )
        resp.raise_for_status()
        logger.info("wf_activity_task_created", since=since)
