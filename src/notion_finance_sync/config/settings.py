"""Application settings.

Secrets resolution order:
1. Environment variable (for CI / runtime overrides)
2. 1Password CLI (`op read`) for local dev

Notion IDs + 1Password references come from config.toml (gitignored; see
config.example.toml) — not hardcoded here.

1Password layout:

- The project vault (``[onepassword].vault`` — referenced by ID) holds all bank
  credentials and project secrets.
- A separate vault holds the service-account token (a service account can't grant
  access to its own host vault), referenced by ``[onepassword].service_account_token_ref``.

For unattended runs (launchd), export OP_SERVICE_ACCOUNT_TOKEN from the
Personal-vault token before invoking the sync, and the service account will
authenticate without an interactive `op signin`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from functools import cache
from pathlib import Path

import structlog
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Non-secret personal config (config.toml — gitignored; see config.example.toml)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]


class _EmailConfig(BaseModel):
    gmail_address: str


class _BiltConfig(BaseModel):
    phone: str


class _NotionConfig(BaseModel):
    transactions_database_id: str
    transactions_data_source_id: str
    tasks_data_source_id: str
    property_ids: dict[str, str]


class _OnePasswordConfig(BaseModel):
    vault: str
    service_account_token_ref: str
    bank_items: dict[str, str]


class _ProjectConfig(BaseModel):
    """Validated shape of config.toml (personal, non-secret identifiers)."""

    email: _EmailConfig
    bilt: _BiltConfig
    notion: _NotionConfig
    onepassword: _OnePasswordConfig


def _load_config() -> _ProjectConfig:
    """Load config.toml, falling back to config.example.toml (placeholders).

    The fallback lets a fresh clone / CI import + run unit tests; live runs need a
    real config.toml (copy config.example.toml and fill in your values).
    """
    override = os.environ.get("NFS_CONFIG")
    if override:
        path = Path(override).expanduser()
    else:
        path = _REPO_ROOT / "config.toml"
        if not path.exists():
            path = _REPO_ROOT / "config.example.toml"
    if not path.exists():
        raise RuntimeError(
            f"config file not found ({path}). Set NFS_CONFIG, or copy "
            "config.example.toml to config.toml and fill in your identifiers."
        )
    with open(path, "rb") as f:
        return _ProjectConfig.model_validate(tomllib.load(f))


_CONFIG = _load_config()

# ---------------------------------------------------------------------------
# Constants (not secrets)
# ---------------------------------------------------------------------------
NOTION_API_VERSION = "2026-03-11"
NOTION_TRANSACTIONS_DATABASE_ID = _CONFIG.notion.transactions_database_id
NOTION_TRANSACTIONS_DATA_SOURCE_ID = _CONFIG.notion.transactions_data_source_id
NOTION_TASKS_DATA_SOURCE_ID = _CONFIG.notion.tasks_data_source_id

# 1Password vault name (project-scoped)
OP_VAULT = _CONFIG.onepassword.vault

# 1Password item path for the service-account token (stored in a separate vault).
# Used by the unattended (launchd) entry point to export OP_SERVICE_ACCOUNT_TOKEN.
OP_SERVICE_ACCOUNT_TOKEN_REF = _CONFIG.onepassword.service_account_token_ref

# session_id -> 1Password item title/id (username + password fields). Banks that
# auth by phone device-trust (e.g. Bilt) need no entry.
OP_BANK_ITEM_BY_SESSION: dict[str, str] = _CONFIG.onepassword.bank_items


def get_notion_property_ids() -> dict[str, str]:
    """Stable Notion property IDs, keyed by internal name (see notion/properties.py)."""
    return dict(_CONFIG.notion.property_ids)


# ---------------------------------------------------------------------------
# 1Password helper
# ---------------------------------------------------------------------------
def _read_op_secret(reference: str) -> str:
    """Read a secret from 1Password CLI."""
    result = subprocess.run(
        ["op", "read", reference],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        tok = os.environ.get("OP_SERVICE_ACCOUNT_TOKEN", "")
        which = shutil.which("op") or "(op not on PATH)"
        raise RuntimeError(
            f"op read {reference!r} failed (exit {result.returncode}): "
            f"{result.stderr.strip() or '(no stderr)'} "
            f"[op={which}; OP_SERVICE_ACCOUNT_TOKEN "
            f"{'set len=' + str(len(tok)) if tok else 'MISSING'}]"
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

    From ``[email].gmail_address`` in config.toml (gitignored — never committed,
    so the personal email stays out of source control). A ``GMAIL_ADDRESS`` env
    var still overrides, for CI / ad-hoc runs.
    """
    return os.environ.get("GMAIL_ADDRESS") or _CONFIG.email.gmail_address


@cache
def get_bilt_phone() -> str:
    """Bilt SMS-OTP phone number (10 digits). From ``[bilt].phone`` in config.toml;
    ``BILT_PHONE`` env still overrides."""
    return os.environ.get("BILT_PHONE") or _CONFIG.bilt.phone


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
    """Read a per-bank username from the project 1Password vault.

    `session_id` is the internal short identifier (e.g. 'bofa', 'us_bank'). The
    function maps it to the actual 1Password item title.
    """
    item = _bank_item_name(session_id)
    return _resolve(
        f"BANK_USERNAME_{session_id.upper()}",
        f"op://{OP_VAULT}/{item}/username",
    )


def get_bank_password(session_id: str) -> str:
    """Read a per-bank password from the project 1Password vault."""
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
