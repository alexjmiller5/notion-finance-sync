"""Application settings.

Secrets resolution order:
1. Environment variable (for CI / runtime overrides)
2. 1Password CLI (`op read`) for local dev

All Notion IDs are constants (not secrets).

1Password layout (per Alex's CLAUDE.md preference for service-account vaults
on large/important projects):

- Vault: "Notion Finance Sync" — holds all bank credentials and project secrets
- Vault: "Personal" — holds the service account token for this project (token
  itself is in Personal because a service account can't grant access to its
  own host vault chicken-and-egg style)

For unattended runs (launchd), export OP_SERVICE_ACCOUNT_TOKEN from the
Personal-vault token before invoking the sync, and the service account will
authenticate without an interactive `op signin`.
"""

from __future__ import annotations

import os
import subprocess
from functools import cache

import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Constants (not secrets)
# ---------------------------------------------------------------------------
NOTION_API_VERSION = "2026-03-11"
NOTION_TRANSACTIONS_DATABASE_ID = "REDACTED_NOTION_DB_ID"
NOTION_TRANSACTIONS_DATA_SOURCE_ID = "REDACTED_NOTION_DATA_SOURCE_ID"
NOTION_TASKS_DATA_SOURCE_ID = "REDACTED_NOTION_TASKS_ID"

# 1Password vault name (project-scoped)
OP_VAULT = "Notion Finance Sync"

# 1Password item path for the service-account token (stored in Personal vault).
# Used by the unattended (launchd) entry point to export OP_SERVICE_ACCOUNT_TOKEN
# before invoking sync.
OP_SERVICE_ACCOUNT_TOKEN_REF = "op://Personal/Notion Finance Sync Service Account Token/password"

# Canonical 1Password item names per session_id.
# Wells Fargo, Notion API secret, and Gmail App Password names match exactly.
# The bank logins each have username + password fields.
OP_BANK_ITEM_BY_SESSION: dict[str, str] = {
    "bofa": "BofA",
    "bofa_investments": "BofA",  # shares the BofA login
    "wells_fargo": "Wells Fargo",
    "us_bank": "U.S. Bank",
    "everbank": "Everbank",
    "venmo": "Venmo",
    # The item is titled "E*Trade" but `*` is illegal in op:// secret references,
    # so we reference it by item ID instead.
    "etrade": "REDACTED_OP_ITEM_ID",
    "fidelity": "Fidelity",
    # NB: Bilt is NOT in the vault. Bilt sessions are long-lived (auth by SMS to
    # Alex's phone, persistent device-trust on personal devices). When/if a fresh
    # login is needed, the scraper hits the phone-verification flow rather than
    # username+password from 1Password.
}


# ---------------------------------------------------------------------------
# 1Password helper
# ---------------------------------------------------------------------------
def _read_op_secret(reference: str) -> str:
    """Read a secret from 1Password CLI."""
    result = subprocess.run(
        ["op", "read", reference],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _resolve(env_var: str, op_reference: str) -> str:
    """Env var takes precedence; fall back to 1Password CLI."""
    val = os.environ.get(env_var)
    if val:
        return val
    return _read_op_secret(op_reference)


# ---------------------------------------------------------------------------
# Lazy secret getters (cached per-process)
# ---------------------------------------------------------------------------
@cache
def get_notion_api_key() -> str:
    return _resolve(
        "NOTION_API_KEY",
        f"op://{OP_VAULT}/Notion Finance Sync Notion Internal Integration Secret/credential",
    )


@cache
def get_gmail_app_password() -> str:
    """Gmail app password (16-character) for IMAP access.

    Alex's Gmail uses 2FA so plain login won't work; app passwords are issued
    via Google account security settings and grant IMAP/POP/SMTP access while
    bypassing 2FA.
    """
    return _resolve("GMAIL_APP_PASSWORD", f"op://{OP_VAULT}/Gmail App Password/credential")


@cache
def get_gmail_address() -> str:
    """Email address (the IMAP username) for the Gmail 2FA reader.

    Read from the ``GMAIL_ADDRESS`` env var (set it in ``.env`` — gitignored — or
    the deploy environment). Deliberately NOT hardcoded, to keep the personal
    email out of source control.
    """
    val = os.environ.get("GMAIL_ADDRESS")
    if not val:
        raise RuntimeError(
            "GMAIL_ADDRESS is not set. Add it to .env (gitignored) or the deploy "
            "environment (e.g. the launchd wrapper)."
        )
    return val


def _bank_item_name(session_id: str) -> str:
    """Resolve session_id to the 1Password item title in the project vault."""
    try:
        return OP_BANK_ITEM_BY_SESSION[session_id]
    except KeyError as exc:
        raise KeyError(
            f"No 1Password item mapping for session_id {session_id!r}. "
            f"Add it to settings.OP_BANK_ITEM_BY_SESSION."
        ) from exc


def get_bank_username(session_id: str) -> str:
    """Read a per-bank username from the Notion Finance Sync vault.

    `session_id` is the internal short identifier (e.g. 'bofa', 'us_bank'). The
    function maps it to the actual 1Password item title.
    """
    item = _bank_item_name(session_id)
    return _resolve(
        f"BANK_USERNAME_{session_id.upper()}",
        f"op://{OP_VAULT}/{item}/username",
    )


def get_bank_password(session_id: str) -> str:
    """Read a per-bank password from the Notion Finance Sync vault."""
    item = _bank_item_name(session_id)
    return _resolve(
        f"BANK_PASSWORD_{session_id.upper()}",
        f"op://{OP_VAULT}/{item}/password",
    )


# ---------------------------------------------------------------------------
# Non-secret settings (pydantic-settings driven)
# ---------------------------------------------------------------------------
class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    server_host: str = "127.0.0.1"
    server_port: int = 8765
    log_level: str = "INFO"


@cache
def settings() -> AppSettings:
    return AppSettings()
