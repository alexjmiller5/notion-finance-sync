"""Gmail 2FA code reader via IMAP.

Uses Gmail's IMAP gateway with an App Password (not OAuth).

App Password setup (one-time, manual):
1. Enable 2FA on the Google account.
2. Account → Security → App Passwords → create a new one named
   "notion-finance-sync".
3. Store the 16-character output at `op://<vault>/Gmail App Password/credential`.

Why IMAP and not the Gmail API: app passwords authenticate only at the
IMAP/POP/SMTP layer. OAuth (gmail-api-python-client) is the more "modern"
path but adds setup friction (OAuth consent screen, refresh token bootstrap).
App password + IMAP is simpler for a single-user personal project.

Reference pattern from Alex's existing TS Raycast extension:
/Users/alexmiller/desktop/coding/reference-repos/mail
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import re
import time
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import structlog

from notion_finance_sync.config.settings import get_gmail_address, get_gmail_app_password

logger = structlog.get_logger()

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993


def _connect() -> imaplib.IMAP4_SSL:
    """Open an authenticated IMAP connection to Gmail."""
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(get_gmail_address(), get_gmail_app_password())
    return conn


def _search_recent(
    conn: imaplib.IMAP4_SSL,
    *,
    after: datetime,
    sender_pattern: str,
) -> list[bytes]:
    """Return UIDs of messages from `sender_pattern` arrived after `after`.

    `sender_pattern` is a string fragment Gmail's IMAP FROM search will match
    (e.g. 'noreply@bilt.com' or 'bankofamerica.com').
    """
    conn.select("INBOX", readonly=True)
    since = after.strftime("%d-%b-%Y")
    typ, data = conn.search(None, "FROM", f'"{sender_pattern}"', "SINCE", since)
    if typ != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def _fetch_body(conn: imaplib.IMAP4_SSL, uid: bytes) -> str:
    """Fetch one message body as a plain-text string (HTML stripped to text)."""
    typ, data = conn.fetch(uid, "(RFC822)")
    if typ != "OK" or not data:
        return ""
    raw = data[0][1] if isinstance(data[0], tuple) else b""
    if not raw:
        return ""

    msg = email.message_from_bytes(raw, policy=email.policy.default)
    if isinstance(msg, EmailMessage):
        body = msg.get_body(preferencelist=("plain", "html"))
        if body is None:
            return ""
        return body.get_content() or ""
    return ""


def _query_recent_email_bodies(
    *,
    after: datetime,
    sender_pattern: str,
) -> list[str]:
    """Return decoded body strings of messages from `sender_pattern` after `after`."""
    conn = _connect()
    try:
        uids = _search_recent(conn, after=after, sender_pattern=sender_pattern)
        return [_fetch_body(conn, uid) for uid in uids]
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def get_email_code(
    after: datetime,
    sender_pattern: str,
    code_regex: str = r"\b(\d{6})\b",
    timeout_s: int = 90,
    poll_interval_s: int = 5,
) -> str | None:
    """Poll Gmail (via IMAP) for an email 2FA code from a bank.

    Args:
        after: Only consider emails received after this datetime (UTC).
        sender_pattern: IMAP FROM-clause fragment (e.g. 'noreply@bilt.com').
        code_regex: Regex with one capture group for the code.
        timeout_s: Maximum total wait time.
        poll_interval_s: Seconds between IMAP polls (Gmail-rate-limit friendly).

    Returns:
        The extracted code, or None if timeout reached.
    """
    pattern = re.compile(code_regex)
    deadline = datetime.now(tz=UTC) + timedelta(seconds=timeout_s)

    while datetime.now(tz=UTC) < deadline:
        try:
            bodies = _query_recent_email_bodies(after=after, sender_pattern=sender_pattern)
        except imaplib.IMAP4.error as e:
            logger.error("email_imap_error", error=str(e), sender_pattern=sender_pattern)
            return None

        for body in bodies:
            m = pattern.search(body)
            if m:
                code = m.group(1)
                logger.info("email_code_found", sender_pattern=sender_pattern, length=len(code))
                return code

        time.sleep(poll_interval_s)

    logger.warning("email_code_timeout", sender_pattern=sender_pattern, timeout_s=timeout_s)
    return None
