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
"""

from __future__ import annotations

import asyncio
import sys

import structlog

logger = structlog.get_logger()


async def main() -> int:
    # TODO: implement using notion_finance_sync.notion.client + raw httpx calls to
    # PATCH /v1/data_sources/{id}. Notion's 2026-03-11 API supports schema updates
    # via this endpoint.
    #
    # Implementation outline:
    # 1. Fetch current schema (GET /v1/data_sources/{id})
    # 2. For each rename: PATCH with the property renamed
    # 3. For each new field: PATCH with the new property added (with appropriate type config)
    # 4. For each select-option addition: read current options, add missing, PATCH
    # 5. Log all changes to stdout
    logger.warning("migrate_not_yet_wired", message="schema migration not implemented")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
