#!/usr/bin/env python3
"""One-shot Notion schema migration.

Run ONCE during initial project setup (per README §7).

What this script does (per SPEC §3):

1. RENAMES properties:
   - 'SimpleFIN ID' -> 'Transaction Source ID'
   - 'SimpleFIN Account ID' -> 'Source Account ID'

2. ADDS properties:
   - Bank Category (text)
   - Calculated Rewards (number, dollar format)
   - True Rewards (number, dollar format)
   - Related Transactions (self-relation, bidirectional)
   - Related Transactions Amount (rollup, sum of Transaction Amount across related)
   - Net Amount (formula: prop("Transaction Amount") + prop("Related Transactions Amount"))
   - Quantity (number)
   - Ticker (text)
   - Price Per Share (number, dollar)
   - Bilt Points (number)
   - Bilt Partner (checkbox)

3. ADDS select options:
   - Bank: Venmo, E*Trade, Fidelity
   - Account Type: P2P, Brokerage, 401k, IRA
   - Category: full 18-category canonical taxonomy

4. (Optional) Retires legacy dual-provider fields by leaving them in schema but
   no longer populating them. Removing them entirely is safe but irreversible —
   keep them for at least a release cycle.

Idempotent — safe to re-run. Each step checks for existing state first.

Uses the Notion API directly (PATCH on /v1/data_sources/{id}) to update the
data source schema.

Usage:
    uv run python scripts/migrate_schema.py --dry-run   # preview changes only
    uv run python scripts/migrate_schema.py             # apply changes (asks for confirmation)
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx
import structlog

from notion_finance_sync.config.settings import (
    NOTION_API_VERSION,
    NOTION_TRANSACTIONS_DATA_SOURCE_ID,
    get_notion_api_key,
)
from notion_finance_sync.notion.migrations import (
    MigrationPlan,
    apply_migration_plan,
    compute_migration_plan,
)

logger = structlog.get_logger()

_NOTION_BASE = "https://api.notion.com"


async def fetch_schema(api_key: str, data_source_id: str) -> dict:
    """GET /v1/data_sources/{id} and return the parsed JSON response."""
    url = f"{_NOTION_BASE}/v1/data_sources/{data_source_id}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
    return response.json()


def _print_plan(plan: MigrationPlan, dry_run: bool) -> None:
    """Print a human-readable summary of pending changes."""
    lines = plan.summary_lines()
    if not lines:
        print("No changes needed — schema is already up to date.")
        return

    mode = "[DRY RUN] " if dry_run else ""
    print(f"\n{mode}Pending schema changes ({len(lines)} operations):")
    for line in lines:
        print(line)
    print()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate the Notion Transactions database schema.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without applying them (no PATCH calls).",
    )
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ]
    )

    logger.info(
        "migration_start",
        dry_run=args.dry_run,
        data_source_id=NOTION_TRANSACTIONS_DATA_SOURCE_ID,
    )

    # Resolve API key
    try:
        api_key = get_notion_api_key()
    except Exception as exc:
        logger.error("migration_api_key_error", error=str(exc))
        return 1

    # Fetch current schema
    logger.info("migration_fetching_schema")
    try:
        schema = await fetch_schema(api_key, NOTION_TRANSACTIONS_DATA_SOURCE_ID)
    except httpx.HTTPError as exc:
        logger.error("migration_fetch_failed", error=str(exc))
        return 1

    # Compute plan
    plan = compute_migration_plan(schema, data_source_id=NOTION_TRANSACTIONS_DATA_SOURCE_ID)

    # Print summary
    _print_plan(plan, dry_run=args.dry_run)

    if plan.is_empty():
        logger.info("migration_nothing_to_do")
        return 0

    if args.dry_run:
        logger.info("migration_dry_run_complete", message="No changes applied (--dry-run).")
        return 0

    # Confirm before applying
    try:
        answer = input("Proceed? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        logger.info("migration_aborted", reason="no input / interrupted")
        return 0

    if answer != "y":
        logger.info("migration_aborted", reason="user declined")
        return 0

    # Apply
    try:
        await apply_migration_plan(
            plan,
            api_key=api_key,
            data_source_id=NOTION_TRANSACTIONS_DATA_SOURCE_ID,
            dry_run=False,
        )
    except httpx.HTTPError as exc:
        logger.error("migration_patch_failed", error=str(exc))
        return 1

    logger.info("migration_complete")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
