"""Test fakes used by orchestrator and other integration-style tests.

`FakeBankScraper` implements the ``BankScraper`` Protocol so it can be dropped
into the bank registry by tests. It returns a pre-supplied list of
``TransactionRecord`` from ``fetch_recent`` (or raises a pre-supplied
exception on every call to simulate scrape failures).

`FakeEnricher` implements ``Enricher`` and lets tests assert that enricher
failures don't crash the sync.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from notion_finance_sync.banks._base import UnsupportedOperation
from notion_finance_sync.enrichers._base import ExternalRewardEntry, NotionUpdate
from notion_finance_sync.models import CategoryMap, TransactionRecord


class FakeBankScraper:
    """Test double implementing the BankScraper Protocol.

    Constructed with either:
    - ``records``: a list of TransactionRecord ``fetch_recent`` should return, or
    - ``should_raise``: an exception to raise from every ``fetch_recent`` call.

    Records the arguments of each ``fetch_recent`` call in ``self.calls`` so
    tests can assert retry behaviour (e.g., that exactly N attempts ran).
    """

    SESSION_ID = "fake_bank"
    BANK_DISPLAY_NAME = "Fake Bank"
    SUPPORTS_LIVE = True
    CATEGORY_MAP: CategoryMap = {}

    def __init__(
        self,
        records: list[TransactionRecord] | None = None,
        *,
        should_raise: Exception | None = None,
    ) -> None:
        self.records: list[TransactionRecord] = list(records or [])
        self.should_raise: Exception | None = should_raise
        self.calls: list[date] = []

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        self.calls.append(since)
        if self.should_raise is not None:
            raise self.should_raise
        return list(self.records)

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        raise UnsupportedOperation("FakeBankScraper.fetch_historical not supported in tests")

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise UnsupportedOperation("FakeBankScraper.download_statements not supported in tests")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        raise UnsupportedOperation("FakeBankScraper.parse_statements not supported in tests")


class FakeEnricher:
    """Test double implementing the Enricher Protocol.

    Optionally raises a pre-supplied exception from ``fetch_external_data``
    (default behaviour mimics the v1 stubs which raise ``NotImplementedError``).
    """

    SOURCE = "fake_enricher"
    UPDATES_FIELDS: list[str] = []

    def __init__(
        self,
        *,
        entries: list[ExternalRewardEntry] | None = None,
        updates: list[NotionUpdate] | None = None,
        should_raise_on_fetch: Exception | None = None,
        should_raise_on_correlate: Exception | None = None,
    ) -> None:
        self.entries = entries or []
        self.updates = updates or []
        self.should_raise_on_fetch = should_raise_on_fetch
        self.should_raise_on_correlate = should_raise_on_correlate
        self.fetch_calls = 0
        self.correlate_calls = 0

    def fetch_external_data(self) -> list[ExternalRewardEntry]:
        self.fetch_calls += 1
        if self.should_raise_on_fetch is not None:
            raise self.should_raise_on_fetch
        return list(self.entries)

    def correlate_to_notion(
        self,
        entries: list[ExternalRewardEntry],
        notion_txns: dict[str, dict],
    ) -> list[NotionUpdate]:
        self.correlate_calls += 1
        if self.should_raise_on_correlate is not None:
            raise self.should_raise_on_correlate
        return list(self.updates)
