"""Unit tests for health.tracker."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from notion_finance_sync.health import tracker


@pytest.fixture(autouse=True)
def _isolate_health_file(monkeypatch, tmp_path: Path):
    """Redirect HEALTH_FILE to a per-test tmp path so tests don't share state."""
    monkeypatch.setattr(tracker, "HEALTH_FILE", tmp_path / "health.json")


class TestRecordSuccess:
    def test_resets_failures_and_sets_last_success(self):
        tracker.record_failure("bofa", "err1")
        tracker.record_failure("bofa", "err2")

        tracker.record_success("bofa")

        state = tracker.get_all()["bofa"]
        assert state["consecutive_failures_today"] == 0
        assert state["last_success"] is not None
        assert state["last_error"] is None
        assert state["failure_day"] is None

    def test_last_success_is_utc_timestamp(self):
        tracker.record_success("bofa")
        state = tracker.get_all()["bofa"]
        # UTC ISO strings contain "+" or "Z" offset
        assert "+" in state["last_success"] or state["last_success"].endswith("Z")


class TestRecordFailure:
    def test_first_failure_starts_at_one(self):
        count = tracker.record_failure("bofa", "boom")
        assert count == 1
        state = tracker.get_all()["bofa"]
        assert state["consecutive_failures_today"] == 1
        assert state["failure_day"] == date.today().isoformat()
        assert state["last_error"] == "boom"

    def test_same_day_failure_increments(self):
        tracker.record_failure("bofa", "err1")
        count = tracker.record_failure("bofa", "err2")
        assert count == 2
        count = tracker.record_failure("bofa", "err3")
        assert count == 3

    def test_new_day_resets_counter(self, tmp_path: Path):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        state = {
            "bofa": {
                "consecutive_failures_today": 5,
                "last_success": None,
                "last_error": "old",
                "last_attempt": "2026-05-18T00:00:00+00:00",
                "failure_day": yesterday,
            }
        }
        tracker.HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        tracker.HEALTH_FILE.write_text(json.dumps(state))

        count = tracker.record_failure("bofa", "new err")
        assert count == 1
        assert tracker.get_all()["bofa"]["failure_day"] == date.today().isoformat()


class TestRecordSuccessAfterFailures:
    def test_success_clears_failure_state(self):
        tracker.record_failure("bofa", "err")
        tracker.record_failure("bofa", "err")
        tracker.record_success("bofa")

        assert not tracker.needs_escalation("bofa")
        state = tracker.get_all()["bofa"]
        assert state["consecutive_failures_today"] == 0
        assert state["failure_day"] is None


class TestNeedsEscalation:
    def test_true_when_threshold_reached_today(self):
        for _ in range(tracker.FAILURE_THRESHOLD):
            tracker.record_failure("bofa", "err")
        assert tracker.needs_escalation("bofa") is True

    def test_false_below_threshold(self):
        for _ in range(tracker.FAILURE_THRESHOLD - 1):
            tracker.record_failure("bofa", "err")
        assert tracker.needs_escalation("bofa") is False

    def test_false_for_unknown_session(self):
        assert tracker.needs_escalation("nonexistent") is False

    def test_false_for_old_failure_day(self, tmp_path: Path):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        state = {
            "bofa": {
                "consecutive_failures_today": 10,
                "last_success": None,
                "last_error": "old",
                "last_attempt": "2026-05-18T00:00:00+00:00",
                "failure_day": yesterday,
            }
        }
        tracker.HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        tracker.HEALTH_FILE.write_text(json.dumps(state))

        assert tracker.needs_escalation("bofa") is False


class TestGetAll:
    def test_returns_full_state(self):
        tracker.record_success("bofa")
        tracker.record_failure("chase", "oops")

        state = tracker.get_all()
        assert "bofa" in state
        assert "chase" in state
        assert state["bofa"]["consecutive_failures_today"] == 0
        assert state["chase"]["consecutive_failures_today"] == 1


class TestTimezoneConsistency:
    def test_failure_day_uses_local_date(self):
        known_date = date(2026, 6, 1)
        with patch("notion_finance_sync.health.tracker.date") as mock_date:
            mock_date.today.return_value = known_date
            tracker.record_failure("bofa", "err")

        state = tracker.get_all()["bofa"]
        assert state["failure_day"] == "2026-06-01"

    def test_needs_escalation_uses_local_date_for_comparison(self):
        known_date = date(2026, 6, 1)
        with patch("notion_finance_sync.health.tracker.date") as mock_date:
            mock_date.today.return_value = known_date
            for _ in range(tracker.FAILURE_THRESHOLD):
                tracker.record_failure("bofa", "err")

        with patch("notion_finance_sync.health.tracker.date") as mock_date:
            mock_date.today.return_value = known_date
            assert tracker.needs_escalation("bofa") is True

        # A different "today" should not escalate
        with patch("notion_finance_sync.health.tracker.date") as mock_date:
            mock_date.today.return_value = date(2026, 6, 2)
            assert tracker.needs_escalation("bofa") is False
