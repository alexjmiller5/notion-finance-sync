"""Unit tests for twofa.sms::get_sms_code.

Uses a temporary SQLite database that mirrors the chat.db schema so no real
Messages.app database is needed.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

import notion_finance_sync.twofa.sms as sms_mod
from notion_finance_sync.banks.bofa.session import BOFA_SMS_REGEX, BOFA_SMS_SENDER
from notion_finance_sync.twofa.sms import (
    _apple_ts,
    _decode_attributed_body,
    _query_recent_messages,
    get_sms_code,
)


def _make_chat_db(path: Path) -> None:
    """Create a minimal chat.db schema (incl. the attributedBody blob column)."""
    con = sqlite3.connect(str(path))
    con.executescript("""
        CREATE TABLE handle (
            ROWID INTEGER PRIMARY KEY,
            id TEXT NOT NULL
        );
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY,
            handle_id INTEGER,
            text TEXT,
            attributedBody BLOB,
            date INTEGER,
            is_from_me INTEGER DEFAULT 0,
            FOREIGN KEY(handle_id) REFERENCES handle(ROWID)
        );
    """)
    con.commit()
    con.close()


def _insert_message(
    db: Path,
    *,
    handle_id: int,
    text: str | None,
    ts: datetime,
    attributed_body: bytes | None = None,
) -> None:
    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO message (handle_id, text, attributedBody, date) VALUES (?, ?, ?, ?)",
        (handle_id, text, attributed_body, _apple_ts(ts)),
    )
    con.commit()
    con.close()


def _fake_attributed_body(msg: str) -> bytes:
    """A minimal streamtyped-style blob (text in attributedBody, NULL `text`)."""
    return b"streamtyped\x00NSString\x00\x19" + msg.encode("utf-8") + b"\x00iI"


def _insert_handle(db: Path, *, rowid: int, sender: str) -> None:
    con = sqlite3.connect(str(db))
    con.execute("INSERT INTO handle (ROWID, id) VALUES (?, ?)", (rowid, sender))
    con.commit()
    con.close()


CUTOFF = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
AFTER = datetime(2026, 5, 1, 13, 0, 0, tzinfo=UTC)  # after the cutoff


@pytest.fixture()
def chat_db(tmp_path: Path) -> Path:
    db = tmp_path / "chat.db"
    _make_chat_db(db)
    _insert_handle(db, rowid=1, sender="+18005551234")
    _insert_handle(db, rowid=2, sender="+19995559999")
    return db


class TestQueryRecentMessages:
    def test_code_found_returns_matching_text(self, chat_db: Path):
        _insert_message(chat_db, handle_id=1, text="Your BofA code is 123456", ts=AFTER)
        results = _query_recent_messages(CUTOFF, "%bofa%", db_path=chat_db)
        assert any("123456" in r for r in results)

    def test_messages_before_cutoff_excluded(self, chat_db: Path):
        before = datetime(2026, 5, 1, 11, 0, 0, tzinfo=UTC)
        _insert_message(chat_db, handle_id=1, text="Your BofA code is 000000", ts=before)
        results = _query_recent_messages(CUTOFF, "%bofa%", db_path=chat_db)
        assert results == []

    def test_non_matching_sender_excluded(self, chat_db: Path):
        _insert_message(chat_db, handle_id=2, text="Random message 999999", ts=AFTER)
        results = _query_recent_messages(CUTOFF, "%bofa%", db_path=chat_db)
        assert results == []

    def test_most_recent_message_returned_first(self, chat_db: Path):
        t1 = datetime(2026, 5, 1, 13, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 5, 1, 14, 0, 0, tzinfo=UTC)
        _insert_message(chat_db, handle_id=1, text="First bofa code is 111111", ts=t1)
        _insert_message(chat_db, handle_id=1, text="Second bofa code is 222222", ts=t2)
        results = _query_recent_messages(CUTOFF, "%bofa%", db_path=chat_db)
        assert results[0] == "Second bofa code is 222222"


class TestGetSmsCode:
    def test_code_found_via_patched_query(self, monkeypatch, chat_db: Path):
        _insert_message(chat_db, handle_id=1, text="Your BofA code is 123456", ts=AFTER)

        original_query = _query_recent_messages
        monkeypatch.setattr(
            sms_mod,
            "_query_recent_messages",
            lambda after, sp, db_path=None: original_query(after, sp, db_path=chat_db),
        )

        code = get_sms_code(
            CUTOFF,
            "%bofa%",
            timeout_s=2,
            poll_interval_s=0,
        )
        assert code == "123456"

    def test_no_match_returns_none_after_timeout(self, monkeypatch, chat_db: Path):
        def _patched_query(after, sender_pattern, db_path=None):
            return []

        monkeypatch.setattr(sms_mod, "_query_recent_messages", _patched_query)

        code = get_sms_code(
            CUTOFF,
            "%bofa%",
            timeout_s=0,
            poll_interval_s=0,
        )
        assert code is None

    def test_custom_regex_matches_four_digit_code(self, monkeypatch, chat_db: Path):
        def _patched_query(after, sender_pattern, db_path=None):
            return ["Your OTP is 8472"]

        monkeypatch.setattr(sms_mod, "_query_recent_messages", _patched_query)

        code = get_sms_code(
            CUTOFF,
            "%bank%",
            code_regex=r"\b(\d{4})\b",
            timeout_s=2,
            poll_interval_s=0,
        )
        assert code == "8472"

    def test_db_unreadable_returns_none(self, monkeypatch, tmp_path: Path):
        # Point at a nonexistent file — SQLite will raise OperationalError
        missing = tmp_path / "nonexistent.db"

        def _patched_query(after, sender_pattern, db_path=None):
            return _query_recent_messages(after, sender_pattern, db_path=missing)

        monkeypatch.setattr(sms_mod, "_query_recent_messages", _patched_query)

        code = get_sms_code(
            CUTOFF,
            "%bofa%",
            timeout_s=2,
            poll_interval_s=0,
        )
        assert code is None


# BofA's REAL message formats (verified against chat.db 2026-07-02); code faked.
# "Code 123456." is the dominant format; "Your code is 123456" also occurs.
BOFA_MSG_CODE = (
    "Bank of America: DO NOT share this Sign In code. Code 481920. "
    "We NEVER call or text you for it."
)
BOFA_MSG_IS = (
    "BofA: Your code is 481920. Don't share it; we won't call to ask for it. "
    "Call 800.933.6262 if you didn't request it."
)


class TestAttributedBody:
    def test_decode_drops_boilerplate_keeps_message(self):
        decoded = _decode_attributed_body(_fake_attributed_body(BOFA_MSG_CODE))
        assert "Code 481920" in decoded
        assert "NSString" not in decoded and "streamtyped" not in decoded

    def test_decode_handles_empty(self):
        assert _decode_attributed_body(None) == ""
        assert _decode_attributed_body(b"") == ""

    def test_bofa_message_read_from_attributed_body(self, chat_db: Path):
        # sender 73981, NULL text, body only in attributedBody (the real situation)
        _insert_handle(chat_db, rowid=3, sender="73981")
        _insert_message(
            chat_db,
            handle_id=3,
            text=None,
            ts=AFTER,
            attributed_body=_fake_attributed_body(BOFA_MSG_CODE),
        )
        results = _query_recent_messages(CUTOFF, "73981", db_path=chat_db)
        assert any("481920" in r for r in results)


class TestBofaProductionRegex:
    @pytest.mark.parametrize("msg", [BOFA_MSG_CODE, BOFA_MSG_IS])
    def test_extracts_code_from_both_real_formats(self, monkeypatch, msg):
        monkeypatch.setattr(
            sms_mod, "_query_recent_messages", lambda after, sp, db_path=None: [msg]
        )
        code = get_sms_code(
            CUTOFF, BOFA_SMS_SENDER, code_regex=BOFA_SMS_REGEX, timeout_s=2, poll_interval_s=0
        )
        # not the decoy "Sign In code", not 933626 inside 800.933.6262
        assert code == "481920"
