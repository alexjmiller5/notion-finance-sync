"""Gmail 2FA code reader.

Polls Gmail via the official Gmail API for bank-specific 2FA codes.

Auth: OAuth2 with refresh token. Client credentials stored in 1Password:
- op://Personal/Gmail OAuth/client_id
- op://Personal/Gmail OAuth/client_secret
- op://Personal/Gmail OAuth/refresh_token

Reference: Alex's existing TypeScript Raycast extension at
/Users/alexmiller/desktop/coding/reference-repos/mail — port the access pattern,
but make queries bank-specific (precise sender + regex) rather than generic
2FA discovery.
"""

from __future__ import annotations

import base64
import re
import time
from datetime import datetime, timedelta, timezone

import structlog
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = structlog.get_logger()


def _build_gmail_service(client_id: str, client_secret: str, refresh_token: str):
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _query_recent_email_bodies(
    service,
    after: datetime,
    sender_pattern: str,
) -> list[str]:
    """Return decoded email body texts received after `after` from `sender_pattern`."""
    # Gmail query syntax: from:sender after:unix_timestamp
    query = f"from:{sender_pattern} after:{int(after.timestamp())}"
    result = service.users().messages().list(userId="me", q=query, maxResults=20).execute()
    messages = result.get("messages", [])

    bodies = []
    for msg_ref in messages:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_ref["id"], format="full")
            .execute()
        )
        bodies.append(_extract_body(msg))
    return bodies


def _extract_body(message: dict) -> str:
    """Extract a plain-text body string from a Gmail message resource."""
    payload = message.get("payload", {})

    def walk(part: dict) -> str:
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        for sub in part.get("parts", []):
            text = walk(sub)
            if text:
                return text
        return ""

    return walk(payload)


def get_email_code(
    after: datetime,
    sender_pattern: str,
    code_regex: str = r"\b(\d{6})\b",
    timeout_s: int = 90,
    poll_interval_s: int = 5,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> str | None:
    """Poll Gmail for an email 2FA code from a bank.

    Args:
        after: Only consider emails received after this datetime.
        sender_pattern: Gmail `from:` pattern (e.g. 'noreply@bilt.com').
        code_regex: Regex with one capture group for the code.
        timeout_s: Maximum total wait time.
        poll_interval_s: Seconds between Gmail polls (Gmail rate-limit-friendly).
        client_id, client_secret, refresh_token: OAuth credentials.

    Returns:
        Extracted code, or None if timeout reached.
    """
    pattern = re.compile(code_regex)
    service = _build_gmail_service(client_id, client_secret, refresh_token)
    deadline = datetime.now(tz=timezone.utc) + timedelta(seconds=timeout_s)

    while datetime.now(tz=timezone.utc) < deadline:
        bodies = _query_recent_email_bodies(service, after, sender_pattern)
        for body in bodies:
            m = pattern.search(body)
            if m:
                code = m.group(1)
                logger.info("email_code_found", sender_pattern=sender_pattern, length=len(code))
                return code
        time.sleep(poll_interval_s)

    logger.warning("email_code_timeout", sender_pattern=sender_pattern, timeout_s=timeout_s)
    return None
