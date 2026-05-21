"""Creates rows in Alex's Notion Tasks DB when a bank fails repeatedly.

Tasks DB data source ID: REDACTED_NOTION_TASKS_ID
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog

from notion_finance_sync.config.settings import (
    NOTION_API_VERSION,
    NOTION_TASKS_DATA_SOURCE_ID,
    get_notion_api_key,
)
from notion_finance_sync.notion.http import request_with_retry

logger = structlog.get_logger()


class TasksClient:
    """Thin Notion API client for reading/writing Tasks DB rows."""

    def __init__(self, api_key: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": "application/json",
        }

    async def _request_with_retry(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        return await request_with_retry(
            method, url, headers=self._headers, log_prefix="tasks_notion", **kwargs
        )

    async def find_open_task(self, bank_display_name: str) -> dict[str, Any] | None:
        """Return the first To Do task matching the bank created in the last 24h, or None."""
        since = (datetime.now(tz=UTC) - timedelta(hours=24)).isoformat()
        body: dict[str, Any] = {
            "filter": {
                "and": [
                    {"property": "Status", "status": {"equals": "To Do"}},
                    {"property": "Date Created", "created_time": {"after": since}},
                ]
            },
            # 10 is plenty — more than 10 open To Do tasks/day would signal a bigger problem
            "page_size": 10,
        }
        response = await self._request_with_retry(
            "POST",
            f"https://api.notion.com/v1/data_sources/{NOTION_TASKS_DATA_SOURCE_ID}/query",
            json=body,
        )
        data = response.json()
        prefix = f"Fix {bank_display_name} scraper"
        for page in data.get("results", []):
            title_parts = page.get("properties", {}).get("Name", {}).get("title", [])
            title = title_parts[0]["plain_text"] if title_parts else ""
            if title.startswith(prefix):
                return page
        return None

    async def create_task(self, properties: dict[str, Any]) -> None:
        body = {
            "parent": {"data_source_id": NOTION_TASKS_DATA_SOURCE_ID},
            "properties": properties,
        }
        await self._request_with_retry("POST", "https://api.notion.com/v1/pages", json=body)


def _build_properties(
    *,
    bank_display_name: str,
    session_id: str,
    error_summary: str,
    consecutive_failures: int,
) -> dict[str, Any]:
    title = f"Fix {bank_display_name} scraper — {consecutive_failures} failures today"
    remediation = f"Run: uv run python scripts/sync.py --bank {session_id} --interactive"
    notes_body = "\n".join(
        [
            error_summary,
            "",
            remediation,
            "",
            f"Session ID: {session_id}",
        ]
    )
    return {
        "Name": {"title": [{"text": {"content": title}}]},
        "Status": {"status": {"name": "To Do"}},
        "Priority": {"select": {"name": "High"}},
        "Tags": {
            "multi_select": [
                {"name": "Finances"},
                {"name": "Development"},
            ]
        },
        "Notes": {"rich_text": [{"text": {"content": notes_body}}]},
    }


async def create_failure_task(
    *,
    session_id: str,
    bank_display_name: str,
    error_summary: str,
    consecutive_failures: int,
) -> None:
    """Create a Notion task asking Alex to fix a broken bank connector.

    Skips creation if a matching open To Do task was created in the last 24 hours.
    Title: "Fix {bank_display_name} scraper — {n} failures today"
    Notes: error summary + suggested remediation + session_id
    """
    client = TasksClient(api_key=get_notion_api_key())

    existing = await client.find_open_task(bank_display_name)
    if existing is not None:
        logger.info(
            "failure_task_already_exists",
            bank=bank_display_name,
            page_id=existing.get("id"),
        )
        return

    properties = _build_properties(
        bank_display_name=bank_display_name,
        session_id=session_id,
        error_summary=error_summary,
        consecutive_failures=consecutive_failures,
    )
    await client.create_task(properties)
    logger.info(
        "failure_task_created",
        bank=bank_display_name,
        consecutive_failures=consecutive_failures,
    )
