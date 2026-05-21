"""Tests for the FastAPI server endpoints.

Covers:
- GET /health returns 200 with banks data.
- POST /sync returns 202 with status=accepted, sync_id UUID, and banks list.
- POST /sync/{session_id} returns 202 for a known session_id.
- POST /sync/{session_id} returns 404 for an unknown session_id.
- POST /sync triggers run_all_banks as a background task.
- POST /sync/{session_id} triggers run_one_bank as a background task.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from notion_finance_sync.server.app import app
from tests.fakes import FakeBankScraper

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_health_file(monkeypatch, tmp_path: Path):
    """Redirect data/health.json to a per-test tmp file."""
    from notion_finance_sync.health import tracker

    monkeypatch.setattr(tracker, "HEALTH_FILE", tmp_path / "health.json")


@pytest.fixture()
def fake_registry(monkeypatch):
    """Monkeypatch the bank registry with a single FakeBank so tests don't
    need real scraper credentials or network access.
    """
    from notion_finance_sync.banks import registry

    fake = FakeBankScraper(records=[])
    fake_reg = {"fake_bank": fake}
    monkeypatch.setattr(registry, "BANK_REGISTRY", fake_reg)
    return fake_reg


@pytest.fixture(autouse=True)
def _stub_orchestrator(monkeypatch):
    """Stub out the orchestrator functions in the server module so background
    tasks don't try to call the real orchestrator (which needs live Notion
    credentials). Individual tests that want to assert on the mock can override
    this by patching again after this autouse fixture runs.
    """
    from notion_finance_sync.server import app as app_module
    from notion_finance_sync.sync.orchestrator import SyncResult

    default_result = SyncResult(session_id="fake_bank", status="success")

    monkeypatch.setattr(
        app_module, "run_all_banks", AsyncMock(return_value={"fake_bank": default_result})
    )
    monkeypatch.setattr(app_module, "run_one_bank", AsyncMock(return_value=default_result))


@pytest.fixture()
def client():
    """Synchronous TestClient — FastAPI runs BackgroundTasks synchronously
    within the request lifecycle when using TestClient, so we can assert on
    side effects immediately after the response.
    """
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_has_banks_key(self, client):
        response = client.get("/health")
        body = response.json()
        assert "banks" in body

    def test_banks_is_dict(self, client):
        response = client.get("/health")
        body = response.json()
        assert isinstance(body["banks"], dict)


# ---------------------------------------------------------------------------
# POST /sync
# ---------------------------------------------------------------------------


class TestSyncAll:
    def test_returns_202(self, client, fake_registry):
        response = client.post("/sync")
        assert response.status_code == 202

    def test_body_has_status_accepted(self, client, fake_registry):
        response = client.post("/sync")
        assert response.json()["status"] == "accepted"

    def test_body_has_sync_id_as_uuid(self, client, fake_registry):
        response = client.post("/sync")
        sync_id = response.json()["sync_id"]
        # Should parse as a valid UUID without raising ValueError
        parsed = uuid.UUID(sync_id)
        assert str(parsed) == sync_id

    def test_body_has_banks_list(self, client, fake_registry):
        response = client.post("/sync")
        body = response.json()
        assert "banks" in body
        assert isinstance(body["banks"], list)
        assert "fake_bank" in body["banks"]

    def test_banks_matches_registry(self, client, fake_registry):
        from notion_finance_sync.banks.registry import all_session_ids

        response = client.post("/sync")
        assert response.json()["banks"] == all_session_ids()

    def test_triggers_run_all_banks_background_task(self, client, fake_registry, monkeypatch):
        """run_all_banks should be invoked as a background task.

        The autouse _stub_orchestrator fixture already patches app_module.run_all_banks
        with an AsyncMock. We just need to post and verify the stub was awaited.
        """
        from notion_finance_sync.server import app as app_module

        client.post("/sync")

        # The autouse stub for run_all_banks should have been called once
        app_module.run_all_banks.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /sync/{session_id}
# ---------------------------------------------------------------------------


class TestSyncOne:
    def test_known_session_returns_202(self, client, fake_registry):
        response = client.post("/sync/fake_bank")
        assert response.status_code == 202

    def test_unknown_session_returns_404(self, client, fake_registry):
        response = client.post("/sync/nonexistent_bank")
        assert response.status_code == 404

    def test_body_has_status_accepted(self, client, fake_registry):
        response = client.post("/sync/fake_bank")
        assert response.json()["status"] == "accepted"

    def test_body_has_sync_id_as_uuid(self, client, fake_registry):
        response = client.post("/sync/fake_bank")
        sync_id = response.json()["sync_id"]
        parsed = uuid.UUID(sync_id)
        assert str(parsed) == sync_id

    def test_body_has_bank_field(self, client, fake_registry):
        response = client.post("/sync/fake_bank")
        assert response.json()["bank"] == "fake_bank"

    def test_triggers_run_one_bank_background_task(self, client, fake_registry, monkeypatch):
        """run_one_bank should be called with the correct session_id as a background task.

        The autouse _stub_orchestrator fixture already patches app_module.run_one_bank
        with an AsyncMock. We just need to post and verify it was awaited with the right arg.
        """
        from notion_finance_sync.server import app as app_module

        client.post("/sync/fake_bank")

        app_module.run_one_bank.assert_awaited_once()
        call_args = app_module.run_one_bank.await_args
        # First positional arg should be the session_id
        assert call_args.args[0] == "fake_bank"

    def test_404_detail_mentions_unknown_session_id(self, client, fake_registry):
        response = client.post("/sync/DOES_NOT_EXIST")
        body = response.json()
        assert "DOES_NOT_EXIST" in body["detail"]
