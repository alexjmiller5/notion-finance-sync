"""Filesystem locations for mutable app state.

The package itself is immutable (it may live in a read-only /nix/store); all
writable state — per-bank Chrome profiles, snapshots, statements, health, tokens —
lives under a configurable data dir. Set ``NFS_STATE_DIR`` to relocate it (the Nix
deploy points it at ``~/Library/Application Support/notion-finance-sync``).
Defaults to ``<repo>/data`` for local development from a checkout.
"""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("NFS_STATE_DIR")
    if override:
        return Path(override).expanduser()
    # dev default: the repo's data/ (this file is src/notion_finance_sync/config/paths.py)
    return Path(__file__).resolve().parents[3] / "data"


DATA_DIR = data_dir()
SESSIONS_DIR = DATA_DIR / "sessions"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
STATEMENTS_DIR = DATA_DIR / "statements"
# SeleniumBase downloads chromedriver/uc_driver here (its own package dir is
# read-only in the /nix/store); passed to seleniumbase via override_driver_dir.
DRIVERS_DIR = DATA_DIR / "drivers"
HEALTH_FILE = DATA_DIR / "health.json"
