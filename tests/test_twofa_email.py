"""Unit tests for twofa.email::get_email_code.

The Gmail API calls are mocked via pytest-mock so no real network or OAuth flow
is exercised.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from notion_finance_sync.twofa.email import _extract_body, get_email_code

CUTOFF = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


def _b64(text: str) -> str:
    """Encode a string in the URL-safe base64 format Gmail uses."""
    return base64.urlsafe_b64encode(text.encode()).decode()


def _simple_message(body_text: str) -> dict:
    """Build a minimal Gmail message resource with a flat text/plain payload."""
    return {
        "id": "msg-1",
        "payload": {
            "mimeType": "text/plain",
            "body": {"data": _b64(body_text)},
            "parts": [],
        },
    }


def _multipart_message(body_text: str) -> dict:
    """Build a Gmail message resource with one level of multipart nesting."""
    return {
        "id": "msg-2",
        "payload": {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64("<html>654321</html>")},
                    "parts": [],
                },
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64(body_text)},
                    "parts": [],
                },
            ],
        },
    }


def _build_service_stub(messages: list[dict]) -> MagicMock:
    """Return a mock Gmail service that yields `messages` from list() and get()."""
    service = MagicMock()

    list_resp = {"messages": [{"id": m["id"]} for m in messages]}
    service.users().messages().list().execute.return_value = list_resp

    get_execute = MagicMock(side_effect=messages)
    service.users().messages().get().execute = get_execute

    return service


class TestExtractBody:
    def test_flat_plain_text_payload(self):
        msg = _simple_message("Hello 123456")
        assert "Hello 123456" in _extract_body(msg)

    def test_multipart_walks_to_plain_text(self):
        msg = _multipart_message("Your code is 654321")
        body = _extract_body(msg)
        assert "654321" in body

    def test_empty_payload_returns_empty_string(self):
        assert _extract_body({}) == ""


class TestGetEmailCode:
    def _run(self, service: MagicMock, **kwargs) -> str | None:
        """Call get_email_code with OAuth creds mocked out."""
        with (
            patch("notion_finance_sync.twofa.email._build_gmail_service", return_value=service),
            patch("notion_finance_sync.twofa.email.time.sleep"),
        ):
            return get_email_code(
                CUTOFF,
                sender_pattern="alerts@example.com",
                timeout_s=kwargs.get("timeout_s", 2),
                poll_interval_s=0,
                client_id="cid",
                client_secret="csec",
                refresh_token="rtok",
                **{k: v for k, v in kwargs.items() if k != "timeout_s"},
            )

    def test_code_found_in_plain_message(self):
        msg = _simple_message("Your verification code is 654321")
        service = _build_service_stub([msg])
        assert self._run(service) == "654321"

    def test_no_messages_returns_none_after_timeout(self):
        service = _build_service_stub([])
        result = self._run(service, timeout_s=0)
        assert result is None

    def test_multiple_polls_finds_code_on_second_attempt(self):
        """First poll returns nothing; second poll returns the code."""
        call_count = 0

        def _fake_query(service, after, sender_pattern):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []
            return ["Code: 987654"]

        _bodies_patch = "notion_finance_sync.twofa.email._query_recent_email_bodies"
        with (
            patch("notion_finance_sync.twofa.email._build_gmail_service", return_value=MagicMock()),
            patch(_bodies_patch, side_effect=_fake_query),
            patch("notion_finance_sync.twofa.email.time.sleep"),
        ):
            result = get_email_code(
                CUTOFF,
                sender_pattern="alerts@example.com",
                timeout_s=5,
                poll_interval_s=0,
                client_id="cid",
                client_secret="csec",
                refresh_token="rtok",
            )

        assert result == "987654"
        assert call_count == 2

    def test_multipart_body_code_extracted(self):
        msg = _multipart_message("Your code is 112233")
        service = _build_service_stub([msg])
        assert self._run(service) == "112233"
