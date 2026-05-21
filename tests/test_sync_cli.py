"""Tests for the sync CLI (notion_finance_sync.cli.sync_cli).

Covers:
- --bank <id> calls run_one_bank with that session_id, exits 0 on success.
- No --bank calls run_all_banks, exits 0 on all success.
- Exit code 1 if any bank fails.
- Summary line format is human-readable.
- --skip-enrichers is forwarded to run_all_banks.
- --interactive logs a warning and does NOT crash.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from notion_finance_sync.sync.orchestrator import SyncResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_health_file(monkeypatch, tmp_path: Path):
    from notion_finance_sync.health import tracker

    monkeypatch.setattr(tracker, "HEALTH_FILE", tmp_path / "health.json")


@pytest.fixture(autouse=True)
def _notion_api_key(monkeypatch):
    monkeypatch.setenv("NOTION_API_KEY", "secret_test")
    from notion_finance_sync.config import settings as settings_mod

    settings_mod.get_notion_api_key.cache_clear()
    yield
    settings_mod.get_notion_api_key.cache_clear()


def _success_result(session_id: str, **kwargs) -> SyncResult:
    return SyncResult(
        session_id=session_id,
        status="success",
        transactions_created=kwargs.get("created", 3),
        transactions_updated=kwargs.get("updated", 0),
        transactions_unchanged=kwargs.get("unchanged", 0),
        pending_released=kwargs.get("released", 0),
        duration_seconds=kwargs.get("duration", 1.42),
        attempts=1,
    )


def _failure_result(session_id: str) -> SyncResult:
    return SyncResult(
        session_id=session_id,
        status="failure",
        error="Login failed",
        duration_seconds=4.5,
        attempts=3,
    )


def _skipped_result(session_id: str) -> SyncResult:
    return SyncResult(
        session_id=session_id,
        status="skipped",
        error="closed account",
        duration_seconds=0.0,
        attempts=0,
    )


# ---------------------------------------------------------------------------
# Helpers to invoke the CLI function under test
# ---------------------------------------------------------------------------


async def _run_main(argv: list[str], monkeypatch, capsys=None) -> int:
    """Import and invoke the CLI ``main()`` with the given argv."""
    import sys

    monkeypatch.setattr(sys, "argv", ["sync"] + argv)
    from notion_finance_sync.cli.sync_cli import main

    return await main()


# ---------------------------------------------------------------------------
# Tests: --bank path
# ---------------------------------------------------------------------------


class TestBankFlag:
    @pytest.mark.asyncio
    async def test_calls_run_one_bank_with_session_id(self, monkeypatch):
        from notion_finance_sync.cli import sync_cli

        mock = AsyncMock(return_value=_success_result("bofa"))
        monkeypatch.setattr(sync_cli, "run_one_bank", mock)

        exit_code = await _run_main(["--bank", "bofa"], monkeypatch)

        mock.assert_awaited_once()
        assert mock.await_args.args[0] == "bofa"
        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_exits_0_on_success(self, monkeypatch):
        from notion_finance_sync.cli import sync_cli

        monkeypatch.setattr(
            sync_cli, "run_one_bank", AsyncMock(return_value=_success_result("bofa"))
        )

        exit_code = await _run_main(["--bank", "bofa"], monkeypatch)
        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_exits_1_on_failure(self, monkeypatch):
        from notion_finance_sync.cli import sync_cli

        monkeypatch.setattr(
            sync_cli, "run_one_bank", AsyncMock(return_value=_failure_result("bofa"))
        )

        exit_code = await _run_main(["--bank", "bofa"], monkeypatch)
        assert exit_code == 1

    @pytest.mark.asyncio
    async def test_since_is_forwarded(self, monkeypatch):
        from notion_finance_sync.cli import sync_cli

        mock = AsyncMock(return_value=_success_result("bofa"))
        monkeypatch.setattr(sync_cli, "run_one_bank", mock)

        await _run_main(["--bank", "bofa", "--since", "2026-01-01"], monkeypatch)

        call_kwargs = mock.await_args.kwargs
        assert call_kwargs.get("since") == date(2026, 1, 1)

    @pytest.mark.asyncio
    async def test_skipped_counts_as_success(self, monkeypatch):
        from notion_finance_sync.cli import sync_cli

        monkeypatch.setattr(sync_cli, "run_one_bank", AsyncMock(return_value=_skipped_result("td")))

        exit_code = await _run_main(["--bank", "td"], monkeypatch)
        assert exit_code == 0


# ---------------------------------------------------------------------------
# Tests: all-banks path (no --bank flag)
# ---------------------------------------------------------------------------


class TestAllBanks:
    @pytest.mark.asyncio
    async def test_calls_run_all_banks(self, monkeypatch):
        from notion_finance_sync.cli import sync_cli

        mock = AsyncMock(return_value={"bofa": _success_result("bofa")})
        monkeypatch.setattr(sync_cli, "run_all_banks", mock)

        await _run_main([], monkeypatch)

        mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exits_0_all_success(self, monkeypatch):
        from notion_finance_sync.cli import sync_cli

        results = {
            "bofa": _success_result("bofa"),
            "us_bank": _success_result("us_bank"),
        }
        monkeypatch.setattr(sync_cli, "run_all_banks", AsyncMock(return_value=results))

        exit_code = await _run_main([], monkeypatch)
        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_exits_1_any_failure(self, monkeypatch):
        from notion_finance_sync.cli import sync_cli

        results = {
            "bofa": _success_result("bofa"),
            "fidelity": _failure_result("fidelity"),
        }
        monkeypatch.setattr(sync_cli, "run_all_banks", AsyncMock(return_value=results))

        exit_code = await _run_main([], monkeypatch)
        assert exit_code == 1

    @pytest.mark.asyncio
    async def test_exits_0_when_some_skipped(self, monkeypatch):
        from notion_finance_sync.cli import sync_cli

        results = {
            "bofa": _success_result("bofa"),
            "td": _skipped_result("td"),
        }
        monkeypatch.setattr(sync_cli, "run_all_banks", AsyncMock(return_value=results))

        exit_code = await _run_main([], monkeypatch)
        assert exit_code == 0

    @pytest.mark.asyncio
    async def test_skip_enrichers_forwarded(self, monkeypatch):
        from notion_finance_sync.cli import sync_cli

        mock = AsyncMock(return_value={"bofa": _success_result("bofa")})
        monkeypatch.setattr(sync_cli, "run_all_banks", mock)

        await _run_main(["--skip-enrichers"], monkeypatch)

        call_kwargs = mock.await_args.kwargs
        assert call_kwargs.get("skip_enrichers") is True


# ---------------------------------------------------------------------------
# Tests: summary output format
# ---------------------------------------------------------------------------


class TestSummaryOutput:
    @pytest.mark.asyncio
    async def test_summary_success_line_format(self, monkeypatch, capsys):
        from notion_finance_sync.cli import sync_cli

        results = {
            "bofa": _success_result(
                "bofa", created=3, updated=0, unchanged=0, released=0, duration=1.42
            ),
        }
        monkeypatch.setattr(sync_cli, "run_all_banks", AsyncMock(return_value=results))

        await _run_main([], monkeypatch)

        captured = capsys.readouterr()
        # Should contain bank name, status, counts, and duration
        assert "bofa" in captured.out
        assert "success" in captured.out
        assert "3 created" in captured.out

    @pytest.mark.asyncio
    async def test_summary_failure_line_format(self, monkeypatch, capsys):
        from notion_finance_sync.cli import sync_cli

        results = {
            "fidelity": _failure_result("fidelity"),
        }
        monkeypatch.setattr(sync_cli, "run_all_banks", AsyncMock(return_value=results))

        await _run_main([], monkeypatch)

        captured = capsys.readouterr()
        assert "fidelity" in captured.out
        assert "failure" in captured.out
        assert "Login failed" in captured.out

    @pytest.mark.asyncio
    async def test_summary_has_duration(self, monkeypatch, capsys):
        from notion_finance_sync.cli import sync_cli

        results = {
            "bofa": _success_result("bofa", duration=1.42),
        }
        monkeypatch.setattr(sync_cli, "run_all_banks", AsyncMock(return_value=results))

        await _run_main([], monkeypatch)

        captured = capsys.readouterr()
        # Should contain duration in seconds with at least one decimal
        assert re.search(r"\d+\.\d+s", captured.out)

    @pytest.mark.asyncio
    async def test_single_bank_also_prints_summary(self, monkeypatch, capsys):
        from notion_finance_sync.cli import sync_cli

        monkeypatch.setattr(
            sync_cli,
            "run_one_bank",
            AsyncMock(return_value=_success_result("bofa")),
        )

        await _run_main(["--bank", "bofa"], monkeypatch)

        captured = capsys.readouterr()
        assert "bofa" in captured.out
        assert "success" in captured.out


# ---------------------------------------------------------------------------
# Tests: --interactive flag
# ---------------------------------------------------------------------------


class TestInteractiveFlag:
    @pytest.mark.asyncio
    async def test_interactive_logs_warning_does_not_crash(self, monkeypatch, capsys):
        """--interactive is not yet implemented; should log a warning and continue."""
        from notion_finance_sync.cli import sync_cli

        monkeypatch.setattr(
            sync_cli,
            "run_one_bank",
            AsyncMock(return_value=_success_result("bofa")),
        )

        # Should NOT raise — just warn
        exit_code = await _run_main(["--bank", "bofa", "--interactive"], monkeypatch)
        assert exit_code == 0
