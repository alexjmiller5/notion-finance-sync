"""Tests for twofa.email — Gmail 2FA reader via IMAP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

import notion_finance_sync.twofa.email as email_mod


def _fail_if_called(*_a, **_k):
    raise AssertionError("real IMAP connection attempted in a test")


@pytest.fixture(autouse=True)
def _no_real_imap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent real IMAP connections in any test by default.

    Tests that need IMAP behavior should patch `_query_recent_email_bodies` or
    `_connect` directly.
    """
    monkeypatch.setattr(email_mod, "_connect", _fail_if_called)


class TestGetEmailCode:
    def test_code_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bodies = ["Your verification code is 654321. Do not share."]
        monkeypatch.setattr(
            email_mod,
            "_query_recent_email_bodies",
            lambda *, after, sender_pattern: bodies,
        )

        result = email_mod.get_email_code(
            after=datetime.now(tz=UTC) - timedelta(minutes=1),
            sender_pattern="bank@example.com",
            timeout_s=2,
            poll_interval_s=1,
        )
        assert result == "654321"

    def test_no_matches_times_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            email_mod,
            "_query_recent_email_bodies",
            lambda *, after, sender_pattern: [],
        )

        result = email_mod.get_email_code(
            after=datetime.now(tz=UTC),
            sender_pattern="bank@example.com",
            timeout_s=1,
            poll_interval_s=1,
        )
        assert result is None

    def test_multi_poll_finds_late_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        call_count = {"n": 0}

        def fake_query(*, after, sender_pattern):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return []
            return ["Your code is 111222"]

        monkeypatch.setattr(email_mod, "_query_recent_email_bodies", fake_query)

        result = email_mod.get_email_code(
            after=datetime.now(tz=UTC),
            sender_pattern="bank@example.com",
            timeout_s=5,
            poll_interval_s=1,
        )
        assert result == "111222"
        assert call_count["n"] >= 2

    def test_custom_regex_four_digit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            email_mod,
            "_query_recent_email_bodies",
            lambda *, after, sender_pattern: ["PIN: 9876"],
        )

        result = email_mod.get_email_code(
            after=datetime.now(tz=UTC),
            sender_pattern="bank@example.com",
            code_regex=r"\b(\d{4})\b",
            timeout_s=2,
            poll_interval_s=1,
        )
        assert result == "9876"

    def test_imap_error_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import imaplib

        def raise_imap(*, after, sender_pattern):
            raise imaplib.IMAP4.error("auth failed")

        monkeypatch.setattr(email_mod, "_query_recent_email_bodies", raise_imap)

        result = email_mod.get_email_code(
            after=datetime.now(tz=UTC),
            sender_pattern="bank@example.com",
            timeout_s=2,
            poll_interval_s=1,
        )
        assert result is None


class TestQueryRecentEmailBodies:
    def test_fetches_message_bodies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_conn = MagicMock()
        fake_conn.search.return_value = ("OK", [b"1 2"])
        fake_conn.fetch.side_effect = [
            (
                "OK",
                [
                    (
                        b"1 (RFC822 {123}",
                        b"From: a@b.com\r\nSubject: T\r\nContent-Type: text/plain\r\n\r\n"
                        b"body one 111222\r\n",
                    )
                ],
            ),
            (
                "OK",
                [
                    (
                        b"2 (RFC822 {123}",
                        b"From: a@b.com\r\nSubject: T\r\nContent-Type: text/plain\r\n\r\n"
                        b"body two 333444\r\n",
                    )
                ],
            ),
        ]
        monkeypatch.setattr(email_mod, "_connect", lambda: fake_conn)

        bodies = email_mod._query_recent_email_bodies(
            after=datetime.now(tz=UTC) - timedelta(hours=1),
            sender_pattern="a@b.com",
        )
        assert len(bodies) == 2
        assert "111222" in bodies[0]
        assert "333444" in bodies[1]

    def test_empty_search_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_conn = MagicMock()
        fake_conn.search.return_value = ("OK", [b""])
        monkeypatch.setattr(email_mod, "_connect", lambda: fake_conn)

        bodies = email_mod._query_recent_email_bodies(
            after=datetime.now(tz=UTC),
            sender_pattern="nobody@example.com",
        )
        assert bodies == []

    def test_logout_on_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_conn = MagicMock()
        fake_conn.search.side_effect = RuntimeError("boom")
        monkeypatch.setattr(email_mod, "_connect", lambda: fake_conn)

        with pytest.raises(RuntimeError):
            email_mod._query_recent_email_bodies(
                after=datetime.now(tz=UTC),
                sender_pattern="x@y.com",
            )
        fake_conn.logout.assert_called_once()
