"""Tests for the backfill runner (fetch_historical -> diff -> Notion)."""

from __future__ import annotations

from datetime import date

import pytest

import notion_finance_sync.backfill.runner as runner_mod
from notion_finance_sync.backfill.runner import run_backfill
from notion_finance_sync.models import (
    AccountType,
    BankName,
    TransactionRecord,
    TransactionStatus,
)


def _rec(source_id: str, amount: float):
    return TransactionRecord(
        source_id=source_id,
        source_account_id="acct",
        name="x",
        amount=amount,
        transaction_date=date(2025, 8, 1),
        transacted_at=None,
        status=TransactionStatus.POSTED,
        bank=BankName.BANK_OF_AMERICA,
        account_type=AccountType.CREDIT_CARD,
    )


class _FakeScraper:
    SESSION_ID = "bofa"
    SUPPORTS_LIVE = True

    def __init__(self, records):
        self._records = records
        self.hist_calls = []

    def fetch_historical(self, start, end):
        self.hist_calls.append((start, end))
        return list(self._records)


class _FakeClient:
    def __init__(self):
        self.created = []
        self.updated = []

    async def get_existing_transactions(self, since_date=None):
        return {}  # nothing exists -> everything is a create

    async def create_from_record(self, record):
        self.created.append(record)

    async def update_from_record(self, page_id, record):
        self.updated.append((page_id, record))


@pytest.fixture
def fake_scraper(monkeypatch):
    scraper = _FakeScraper([_rec("A", -10.0), _rec("B", -20.0)])
    monkeypatch.setattr(runner_mod.bank_registry, "get_scraper", lambda sid: scraper)
    return scraper


async def test_dry_run_computes_plan_but_writes_nothing(fake_scraper):
    client = _FakeClient()
    result = await run_backfill("bofa", since=date(2025, 6, 1), dry_run=True, client=client)
    assert result.scraped == 2
    assert result.to_create == 2
    assert result.created == 0
    assert client.created == []  # nothing written
    assert fake_scraper.hist_calls  # fetch_historical was called


async def test_real_run_creates_new_records(fake_scraper):
    client = _FakeClient()
    result = await run_backfill("bofa", since=date(2025, 6, 1), dry_run=False, client=client)
    assert result.to_create == 2
    assert result.created == 2
    assert {r.source_id for r in client.created} == {"A", "B"}


async def test_backfill_passes_since_and_end(fake_scraper):
    client = _FakeClient()
    await run_backfill("bofa", since=date(2025, 6, 1), end=date(2026, 7, 1), client=client)
    (start, end) = fake_scraper.hist_calls[0]
    assert start == date(2025, 6, 1)
    assert end == date(2026, 7, 1)
