#!/usr/bin/env python3
"""End-to-end demo: register a FakeBank, run the orchestrator against the live
Notion Transactions DB, verify rows appear, then clean them up.

This is a one-shot smoke test that proves the full pipeline works:
    FakeBank.fetch_recent() -> orchestrator -> diff -> Notion API -> verified -> archived

Run with:
    just demo
    # or
    PYTHONPATH=src uv run python scripts/demo_fake_bank.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# Allow running from project root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from fakes import FakeBankScraper

from notion_finance_sync.banks import registry as banks_registry
from notion_finance_sync.config.settings import (
    NOTION_TRANSACTIONS_DATA_SOURCE_ID,
    get_notion_api_key,
)
from notion_finance_sync.models import (
    AccountType,
    BankName,
    CanonicalCategory,
    TransactionRecord,
    TransactionStatus,
)
from notion_finance_sync.notion.client import NotionClient
from notion_finance_sync.sync.orchestrator import run_one_bank

DEMO_PREFIX = "demo-fake-"


def make_demo_records() -> list[TransactionRecord]:
    today = date.today()
    return [
        TransactionRecord(
            source_id=f"{DEMO_PREFIX}001",
            source_account_id="fake-acct-1",
            name="DEMO Starbucks",
            amount=-5.75,
            transaction_date=today,
            transacted_at=datetime.now(tz=UTC),
            status=TransactionStatus.POSTED,
            payee="Starbucks",
            memo="demo run — should be deleted afterward",
            bank_category="Dining",
            category=CanonicalCategory.DINING,
            bank=BankName.BANK_OF_AMERICA,
            account_type=AccountType.CREDIT_CARD,
            account_name="DEMO Account",
        ),
        TransactionRecord(
            source_id=f"{DEMO_PREFIX}002",
            source_account_id="fake-acct-1",
            name="DEMO Whole Foods",
            amount=-42.30,
            transaction_date=today,
            transacted_at=datetime.now(tz=UTC),
            status=TransactionStatus.POSTED,
            payee="Whole Foods",
            memo="demo run — should be deleted afterward",
            bank_category="Groceries",
            category=CanonicalCategory.GROCERIES,
            bank=BankName.BANK_OF_AMERICA,
            account_type=AccountType.CREDIT_CARD,
            account_name="DEMO Account",
        ),
        TransactionRecord(
            source_id=f"{DEMO_PREFIX}003",
            source_account_id="fake-acct-1",
            name="DEMO Pending Auth",
            amount=-12.00,
            transaction_date=today,
            transacted_at=datetime.now(tz=UTC),
            status=TransactionStatus.PENDING,
            payee="Test Merchant",
            memo="demo run — pending status",
            bank_category="Other",
            category=CanonicalCategory.OTHER,
            bank=BankName.BANK_OF_AMERICA,
            account_type=AccountType.CREDIT_CARD,
            account_name="DEMO Account",
        ),
    ]


async def find_demo_pages(client: NotionClient) -> list[dict]:
    """Return all current Notion pages whose Transaction Source ID starts with DEMO_PREFIX."""
    since = (date.today() - timedelta(days=2)).isoformat()
    existing = await client.get_existing_transactions(since_date=since)
    return [row for sid, row in existing.items() if sid.startswith(DEMO_PREFIX)]


async def archive_pages(client: NotionClient, pages: list[dict]) -> None:
    """Archive (soft-delete) the given pages."""
    import httpx

    headers = {
        "Authorization": f"Bearer {get_notion_api_key()}",
        "Notion-Version": "2026-03-11",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        for page in pages:
            response = await http_client.patch(
                f"https://api.notion.com/v1/pages/{page['page_id']}",
                headers=headers,
                json={"in_trash": True},
            )
            response.raise_for_status()
            print(f"  archived: {page['name']!r} ({page['page_id']})")


async def main() -> int:
    print("=" * 70)
    print("  notion-finance-sync — End-to-End Demo (FakeBank)")
    print("=" * 70)

    fake_records = make_demo_records()
    fake_bank = FakeBankScraper(records=fake_records)

    print(f"\nStep 1: Register FakeBank with {len(fake_records)} demo records")
    banks_registry.BANK_REGISTRY["fake_bank"] = fake_bank

    print("\nStep 2: Pre-clean any leftover demo rows from a prior run")
    client = NotionClient(
        api_key=get_notion_api_key(),
        data_source_id=NOTION_TRANSACTIONS_DATA_SOURCE_ID,
    )
    leftover = await find_demo_pages(client)
    if leftover:
        print(f"  found {len(leftover)} leftover demo rows — archiving first")
        await archive_pages(client, leftover)
    else:
        print("  no leftover demo rows")

    print("\nStep 3: Run orchestrator (FakeBank → Notion)")
    result = await run_one_bank("fake_bank", retry_pause_seconds=0)
    print(f"  status:                {result.status}")
    print(f"  attempts:              {result.attempts}")
    print(f"  transactions_created:  {result.transactions_created}")
    print(f"  transactions_updated:  {result.transactions_updated}")
    print(f"  transactions_unchanged:{result.transactions_unchanged}")
    print(f"  pending_released:      {result.pending_released}")
    print(f"  duration:              {result.duration_seconds:.2f}s")
    if result.error:
        print(f"  error:                 {result.error}")

    if result.status != "success":
        print(f"\nFAIL: demo did not succeed (status={result.status})")
        return 1

    print("\nStep 4: Verify rows appear in Notion")
    written = await find_demo_pages(client)
    print(f"  found {len(written)} demo rows in Notion")
    for row in written:
        print(
            f"    - {row['name']:<25} ${row['amount']:>8.2f}  "
            f"status={row['status']:<10} category={row['category']}"
        )

    if len(written) != len(fake_records):
        print(f"\nFAIL: expected {len(fake_records)} rows, found {len(written)}")
        return 1

    print("\nStep 5: Clean up — archive the demo rows")
    await archive_pages(client, written)

    print("\nStep 6: Confirm cleanup")
    remaining = await find_demo_pages(client)
    if remaining:
        print(f"  WARNING: {len(remaining)} demo rows still present after archive")
        return 1
    print("  zero demo rows remaining ✓")

    print("\n" + "=" * 70)
    print("  SUCCESS — full pipeline works end-to-end")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
