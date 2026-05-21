#!/usr/bin/env python3
"""CLI entry point for the daily / on-demand sync.

This is a thin shim that delegates all logic to
``notion_finance_sync.cli.sync_cli``, which is importable and testable.

Usage:
    uv run python scripts/sync.py                    # all banks
    uv run python scripts/sync.py --bank bofa        # one bank
    uv run python scripts/sync.py --bank bofa --interactive
        # one bank, manual escape hatch — pause for human at unsolvable challenges
    uv run python scripts/sync.py --since 2024-01-01 --bank bofa
        # backfill-style date range via the live scraper
"""

from __future__ import annotations

import asyncio
import sys

from notion_finance_sync.cli.sync_cli import main

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
