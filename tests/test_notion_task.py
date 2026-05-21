"""Tests for health/notion_task.py — Notion Tasks DB writer.

Coverage:
- First call (no existing task) creates the task via POST /v1/pages
- Idempotency: existing matching To Do task suppresses creation
- Title format: em-dash, bank name, failure count
- Notes contains error_summary, remediation command, session_id
- Tags exactly ["Finances", "Development"]
- Priority = "High", Status = "To Do"
"""

from __future__ import annotations

import json

import httpx
import pytest

from notion_finance_sync.health.notion_task import (
    TASKS_DATA_SOURCE_ID,
    TasksClient,
    _build_properties,
    create_failure_task,
)

TEST_API_KEY = "secret_test"
BANK = "Bank of America"
SESSION_ID = "bofa"
ERROR_SUMMARY = "Login page returned 503 after 30s"
FAILURES = 3

QUERY_URL = f"https://api.notion.com/v1/data_sources/{TASKS_DATA_SOURCE_ID}/query"
PAGES_URL = "https://api.notion.com/v1/pages"


def _empty_query_response() -> dict:
    return {"object": "list", "results": [], "has_more": False}


def _matching_task_response(bank_display_name: str) -> dict:
    title = f"Fix {bank_display_name} scraper — 3 failures today"
    return {
        "object": "list",
        "results": [
            {
                "id": "existing-page-id-abc",
                "properties": {
                    "Name": {"title": [{"plain_text": title}]},
                    "Status": {"status": {"name": "To Do"}},
                },
            }
        ],
        "has_more": False,
    }


def _create_page_response() -> dict:
    return {"object": "page", "id": "new-page-id-xyz"}


# ---------------------------------------------------------------------------
# Tests: _build_properties — pure property encoding
# ---------------------------------------------------------------------------


class TestBuildProperties:
    def setup_method(self):
        self.props = _build_properties(
            bank_display_name=BANK,
            session_id=SESSION_ID,
            error_summary=ERROR_SUMMARY,
            consecutive_failures=FAILURES,
        )

    def test_title_format_uses_em_dash(self):
        title_parts = self.props["Name"]["title"]
        title = title_parts[0]["text"]["content"]
        # em-dash U+2014, not a hyphen
        assert title == f"Fix {BANK} scraper — {FAILURES} failures today"

    def test_status_is_to_do(self):
        assert self.props["Status"] == {"status": {"name": "To Do"}}

    def test_priority_is_high(self):
        assert self.props["Priority"] == {"select": {"name": "High"}}

    def test_tags_exactly(self):
        tags = [t["name"] for t in self.props["Tags"]["multi_select"]]
        assert tags == ["Finances", "Development"]

    def test_notes_contains_error_summary(self):
        notes = self.props["Notes"]["rich_text"][0]["text"]["content"]
        assert ERROR_SUMMARY in notes

    def test_notes_contains_remediation_command(self):
        notes = self.props["Notes"]["rich_text"][0]["text"]["content"]
        assert f"uv run python scripts/sync.py --bank {SESSION_ID} --interactive" in notes

    def test_notes_contains_session_id(self):
        notes = self.props["Notes"]["rich_text"][0]["text"]["content"]
        assert SESSION_ID in notes


# ---------------------------------------------------------------------------
# Tests: create_failure_task — HTTP interactions
# ---------------------------------------------------------------------------


class TestCreateFailureTaskFirstCall:
    @pytest.mark.asyncio
    async def test_posts_to_pages_when_no_existing_task(self, respx_mock, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", TEST_API_KEY)

        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_empty_query_response())
        )
        respx_mock.post(PAGES_URL).mock(
            return_value=httpx.Response(200, json=_create_page_response())
        )

        await create_failure_task(
            session_id=SESSION_ID,
            bank_display_name=BANK,
            error_summary=ERROR_SUMMARY,
            consecutive_failures=FAILURES,
        )

        # query + create
        assert respx_mock.calls.call_count == 2
        create_call = respx_mock.calls[1]
        body = json.loads(create_call.request.content)
        assert body["parent"] == {"data_source_id": TASKS_DATA_SOURCE_ID}

    @pytest.mark.asyncio
    async def test_posted_body_has_correct_properties(self, respx_mock, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", TEST_API_KEY)

        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_empty_query_response())
        )
        respx_mock.post(PAGES_URL).mock(
            return_value=httpx.Response(200, json=_create_page_response())
        )

        await create_failure_task(
            session_id=SESSION_ID,
            bank_display_name=BANK,
            error_summary=ERROR_SUMMARY,
            consecutive_failures=FAILURES,
        )

        body = json.loads(respx_mock.calls[1].request.content)
        props = body["properties"]

        title = props["Name"]["title"][0]["text"]["content"]
        assert title == f"Fix {BANK} scraper — {FAILURES} failures today"
        assert props["Status"] == {"status": {"name": "To Do"}}
        assert props["Priority"] == {"select": {"name": "High"}}

        tags = [t["name"] for t in props["Tags"]["multi_select"]]
        assert tags == ["Finances", "Development"]

        notes = props["Notes"]["rich_text"][0]["text"]["content"]
        assert ERROR_SUMMARY in notes
        assert SESSION_ID in notes
        assert "uv run python scripts/sync.py" in notes


class TestCreateFailureTaskIdempotency:
    @pytest.mark.asyncio
    async def test_skips_creation_when_matching_task_exists(self, respx_mock, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", TEST_API_KEY)

        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_matching_task_response(BANK))
        )
        create_route = respx_mock.post(PAGES_URL)

        await create_failure_task(
            session_id=SESSION_ID,
            bank_display_name=BANK,
            error_summary=ERROR_SUMMARY,
            consecutive_failures=FAILURES,
        )

        assert not create_route.called

    @pytest.mark.asyncio
    async def test_no_error_when_task_already_exists(self, respx_mock, monkeypatch):
        monkeypatch.setenv("NOTION_API_KEY", TEST_API_KEY)

        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_matching_task_response(BANK))
        )

        # should return None without raising
        result = await create_failure_task(
            session_id=SESSION_ID,
            bank_display_name=BANK,
            error_summary=ERROR_SUMMARY,
            consecutive_failures=FAILURES,
        )
        assert result is None


# ---------------------------------------------------------------------------
# Tests: TasksClient.find_open_task — query filtering
# ---------------------------------------------------------------------------


class TestTasksClientFindOpenTask:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_results(self, respx_mock):
        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_empty_query_response())
        )
        client = TasksClient(api_key=TEST_API_KEY)
        result = await client.find_open_task(BANK)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_page_when_title_matches(self, respx_mock):
        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_matching_task_response(BANK))
        )
        client = TasksClient(api_key=TEST_API_KEY)
        result = await client.find_open_task(BANK)
        assert result is not None
        assert result["id"] == "existing-page-id-abc"

    @pytest.mark.asyncio
    async def test_returns_none_when_title_prefix_differs(self, respx_mock):
        # Task is for a different bank — prefix won't match
        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_matching_task_response("Wells Fargo"))
        )
        client = TasksClient(api_key=TEST_API_KEY)
        result = await client.find_open_task(BANK)
        assert result is None

    @pytest.mark.asyncio
    async def test_query_includes_status_filter(self, respx_mock):
        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_empty_query_response())
        )
        client = TasksClient(api_key=TEST_API_KEY)
        await client.find_open_task(BANK)

        query_body = json.loads(respx_mock.calls[0].request.content)
        filters = query_body["filter"]["and"]
        status_filter = next(f for f in filters if f.get("property") == "Status")
        assert status_filter["status"]["equals"] == "To Do"

    @pytest.mark.asyncio
    async def test_query_includes_created_time_filter(self, respx_mock):
        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_empty_query_response())
        )
        client = TasksClient(api_key=TEST_API_KEY)
        await client.find_open_task(BANK)

        query_body = json.loads(respx_mock.calls[0].request.content)
        filters = query_body["filter"]["and"]
        time_filter = next(f for f in filters if f.get("property") == "Date Created")
        assert "after" in time_filter["created_time"]
