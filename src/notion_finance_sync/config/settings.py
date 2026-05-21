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

# 1Password item path for the service-account token (stored in Personal vault)
OP_SERVICE_ACCOUNT_TOKEN_REF = (
    "op://Personal/Notion Finance Sync Service Account Token/password"
)


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
    return _resolve("NOTION_API_KEY", f"op://{OP_VAULT}/Notion API Key/credential")


@cache
def get_gmail_client_id() -> str:
    return _resolve("GMAIL_CLIENT_ID", f"op://{OP_VAULT}/Gmail OAuth/client_id")


@cache
def get_gmail_client_secret() -> str:
    return _resolve("GMAIL_CLIENT_SECRET", f"op://{OP_VAULT}/Gmail OAuth/client_secret")


@cache
def get_gmail_refresh_token() -> str:
    return _resolve("GMAIL_REFRESH_TOKEN", f"op://{OP_VAULT}/Gmail OAuth/refresh_token")


def get_bank_username(session_display_name: str) -> str:
    """Read a per-bank username. `session_display_name` is the 1Password item name
    (e.g. 'BofA', 'Wells Fargo') in the Notion Finance Sync vault."""
    return _resolve(
        f"BANK_USERNAME_{session_display_name.upper().replace(' ', '_')}",
        f"op://{OP_VAULT}/{session_display_name}/username",
    )


def get_bank_password(session_display_name: str) -> str:
    """Read a per-bank password from the Notion Finance Sync vault."""
    return _resolve(
        f"BANK_PASSWORD_{session_display_name.upper().replace(' ', '_')}",
        f"op://{OP_VAULT}/{session_display_name}/password",
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
