"""SMS 2FA code reader.

Reads ~/Library/Messages/chat.db (macOS Messages.app SQLite database) and
extracts bank-specific 2FA codes by sender + regex.

Requires Full Disk Access granted to the Python interpreter / terminal.
See README.md step 6.

Reference: Alex's existing TypeScript Raycast extension at
/Users/alexmiller/desktop/coding/active-projects/messages — port the access
pattern, but make queries bank-specific (precise sender + regex) rather than
generic 2FA discovery.
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

logger = structlog.get_logger()

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"

# Apple's timestamp format: nanoseconds since 2001-01-01 UTC
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)


def _apple_ts(dt: datetime) -> int:
    """Convert a Python datetime to Apple's nanosecond-since-2001 format."""
    return int((dt.astimezone(UTC) - APPLE_EPOCH).total_seconds() * 1e9)


# Modern macOS stores many message bodies in `attributedBody` (a binary
# streamtyped NSAttributedString) with a NULL `text` column — including BofA's
# 2FA texts. We only need enough to run the code regex, so pull the printable
# runs and drop the streamtyped class-name boilerplate.
_PRINTABLE_RUN = re.compile(rb"[\x20-\x7e]{4,}")
_STREAM_BOILER = frozenset(
    {
        "streamtyped",
        "NSMutableAttributedString",
        "NSAttributedString",
        "NSObject",
        "NSMutableString",
        "NSString",
        "NSDictionary",
        "NSNumber",
        "NSValue",
    }
)


def _decode_attributed_body(blob: bytes | None) -> str:
    """Best-effort plaintext from a Messages ``attributedBody`` blob."""
    if not blob:
        return ""
    runs = [r.decode("utf-8", "replace") for r in _PRINTABLE_RUN.findall(blob)]
    kept = [
        r for r in runs if r not in _STREAM_BOILER and not r.startswith(("__kIM", "NSAttribute"))
    ]
    return " ".join(kept).strip()


def _query_recent_messages(
    after: datetime,
    sender_pattern: str,
    db_path: Path = CHAT_DB,
) -> list[str]:
    """Return message texts received after `after` from senders matching `sender_pattern`.

    `sender_pattern` is a SQL LIKE pattern, e.g. '%bofa%', '73981', or '+1800555%'.
    Reads both the plain `text` column and the binary `attributedBody` fallback.
    """
    cutoff_apple = _apple_ts(after)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = con.execute(
            """
            SELECT message.text, message.attributedBody
            FROM message
            JOIN handle ON message.handle_id = handle.ROWID
            WHERE message.date > ?
              AND (handle.id LIKE ? OR message.text LIKE ?)
            ORDER BY message.date DESC
            LIMIT 50
            """,
            (cutoff_apple, sender_pattern, sender_pattern),
        )
        out: list[str] = []
        for text, attributed_body in cur.fetchall():
            body = text or _decode_attributed_body(attributed_body)
            if body:
                out.append(body)
        return out
    finally:
        con.close()


def get_sms_code(
    after: datetime,
    sender_pattern: str,
    code_regex: str = r"\b(\d{6})\b",
    timeout_s: int = 90,
    poll_interval_s: int = 3,
) -> str | None:
    """Poll Messages.app for an SMS 2FA code from a bank.

    Args:
        after: Only consider messages received after this datetime.
        sender_pattern: SQL LIKE pattern to filter by sender (e.g. '%bofa%').
        code_regex: Regex with one capture group for the code. Default is 6 digits.
        timeout_s: Maximum total wait time before giving up.
        poll_interval_s: Seconds between DB polls.

    Returns:
        The extracted code, or None if timeout reached.
    """
    pattern = re.compile(code_regex)
    deadline = datetime.now(tz=UTC) + timedelta(seconds=timeout_s)

    while datetime.now(tz=UTC) < deadline:
        try:
            messages = _query_recent_messages(after, sender_pattern)
        except sqlite3.OperationalError as e:
            logger.error(
                "sms_db_unreadable",
                error=str(e),
                hint="Grant Full Disk Access in System Settings → Privacy & Security",
            )
            return None

        for text in messages:
            m = pattern.search(text)
            if m:
                code = m.group(1)
                logger.info("sms_code_found", sender_pattern=sender_pattern, length=len(code))
                return code

        time.sleep(poll_interval_s)

    logger.warning("sms_code_timeout", sender_pattern=sender_pattern, timeout_s=timeout_s)
    return None
