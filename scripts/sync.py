#!/usr/bin/env python3
"""CLI entry point for the daily / on-demand sync.

Usage:
    uv run python scripts/sync.py                    # all banks
    uv run python scripts/sync.py --bank bofa        # one bank
    uv run python scripts/sync.py --bank bofa --interactive
        # one bank, manual escape hatch — pause for human at unsolvable challenges
    uv run python scripts/sync.py --since 2024-01-01 --bank bofa
        # backfill-style date range via the live scraper
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta

import structlog

logger = structlog.get_logger()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="notion-finance-sync CLI")
    p.add_argument(
        "--bank",
        help="Session ID to sync (bofa, wells_fargo, us_bank, bilt, everbank, "
        "venmo, etrade, fidelity). Omit to sync all active banks.",
    )
    p.add_argument(
        "--interactive",
        action="store_true",
        help="Manual escape hatch — pause at unsolvable challenges (CAPTCHA, "
        "unknown 2FA flow) for human intervention, then resume.",
    )
    p.add_argument(
        "--since",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=date.today() - timedelta(days=14),
        help="Earliest transaction date to fetch (default: today - 14 days).",
    )
    p.add_argument(
        "--skip-enrichers",
        action="store_true",
        help="Skip Phase 2 enrichers (Bilt portal, BofA rewards, Wells rewards).",
    )
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    logger.info(
        "sync_start",
        bank=args.bank or "all",
        interactive=args.interactive,
        since=args.since.isoformat(),
    )
    # TODO: wire up to notion_finance_sync.sync.orchestrator.run_*()
    logger.warning("sync_not_yet_wired", message="orchestrator not implemented")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
