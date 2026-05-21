"""Creates rows in Alex's Notion Tasks DB when a bank fails repeatedly.

Tasks DB data source ID: REDACTED_NOTION_TASKS_ID

The exact property schema of the Tasks DB will be discovered on first run by
fetching the data source — we hardcode the set of properties we know are
present (title, status) and skip the rest until verified.

TODO during implementation:
- Fetch Tasks DB schema once, cache the property names + select options
- Set Priority appropriately for these failures (probably medium/high)
- Add tags like 'connector', 'notion-finance-sync'
- Link back to the bank's last error in the page body via the markdown endpoint
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()

TASKS_DATA_SOURCE_ID = "REDACTED_NOTION_TASKS_ID"


async def create_failure_task(
    *,
    session_id: str,
    bank_display_name: str,
    error_summary: str,
    consecutive_failures: int,
) -> None:
    """Create a Notion task asking Alex to fix a broken bank connector.

    Title: "Fix {bank_display_name} scraper — {n} failures today"
    Body: error summary + suggested action ("Run: uv run python scripts/sync.py
          --bank {session_id} --interactive")
    """
    # TODO: implement Notion API call to TASKS_DATA_SOURCE_ID
    # Should:
    # 1. Check if an unresolved task for this bank already exists (avoid dupes within a day)
    # 2. POST to /v1/pages with parent.data_source_id = TASKS_DATA_SOURCE_ID
    # 3. Set Title + any other required properties
    # 4. Add body content via the markdown endpoint with the error + remediation steps
    raise NotImplementedError(
        "Wire up Notion API call. See SPEC §15 and notion-brain skill for schema details."
    )
