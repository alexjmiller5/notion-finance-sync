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
    p.add_argument(
        "--dry-run", action="store_true", help="Compute changes but don't write to Notion"
    )
    return p.parse_args()


async def main() -> int:
    from notion_finance_sync.backfill.runner import run_backfill

    args = parse_args()
    if args.since is None:
        print("--since is required for live backfill (e.g. --since 2025-06-01)")
        return 2

    logger.info(
        "backfill_start", bank=args.bank, since=args.since.isoformat(), dry_run=args.dry_run
    )
    result = await run_backfill(args.bank, since=args.since, dry_run=args.dry_run)

    mode = "DRY RUN" if result.dry_run else "WRITTEN"
    extra = "" if result.dry_run else f" | created {result.created}, updated {result.updated}"
    print(
        f"[{mode}] {result.session_id}: scraped {result.scraped} | "
        f"create {result.to_create}, update {result.to_update}, unchanged {result.unchanged}{extra}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
