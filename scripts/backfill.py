#!/usr/bin/env python3
"""CLI for one-time historical backfill.

Usage:
    uv run python scripts/backfill.py --bank bofa --since 2020-01-01
    uv run python scripts/backfill.py --bank td        # closed account, PDF-only

Pipeline (per SPEC §18):
1. Live scrape pushed back as far as the UI/backend API allows
2. Parse any PDFs in data/statements/{bank}/ for the residual gap
3. Dedup at the seam (live-source preferred over PDF-source)
4. Bulk-insert into Notion in batches respecting rate limits
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

import structlog

logger = structlog.get_logger()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="One-time historical backfill")
    p.add_argument("--bank", required=True, help="Session ID to backfill")
    p.add_argument(
        "--since",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        help="Earliest date to include. Required for active banks; "
        "ignored for closed PDF-only banks.",
    )
    p.add_argument("--dry-run", action="store_true", help="Compute changes but don't write to Notion")
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    logger.info(
        "backfill_start", bank=args.bank, since=args.since.isoformat() if args.since else None
    )
    # TODO: wire up to notion_finance_sync.backfill.runner
    logger.warning("backfill_not_yet_wired", message="backfill runner not implemented")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
