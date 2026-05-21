"""Tests for sync.orchestrator — the bank/enricher coordinator.

Covers:
- Happy path: scraper returns N records, all get created
- Existing rows: dedup by source_id; updates only differing rows
- Pending -> Released: orphan release flips a row's status
- 3-attempt retry on scrape failure, escalation to Notion task on threshold
- SUPPORTS_LIVE=False scrapers return "skipped" without HTTP calls
- run_all_banks runs every registered bank sequentially
- Enricher failures (NotImplementedError or any Exception) don't crash the sync
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from notion_finance_sync.config.settings import (
    NOTION_TASKS_DATA_SOURCE_ID,
    NOTION_TRANSACTIONS_DATA_SOURCE_ID,
)
from notion_finance_sync.models import (
    AccountType,
    BankName,
    CardNetwork,
    TransactionRecord,
    TransactionStatus,
)
from tests.fakes import FakeBankScraper, FakeEnricher

TEST_API_KEY = "secret_test"

QUERY_URL = f"https://api.notion.com/v1/data_sources/{NOTION_TRANSACTIONS_DATA_SOURCE_ID}/query"
PAGES_URL = "https://api.notion.com/v1/pages"
TASKS_QUERY_URL = f"https://api.notion.com/v1/data_sources/{NOTION_TASKS_DATA_SOURCE_ID}/query"


# ---------------------------------------------------------------------------
# Auto-applied fixtures (env + per-test isolated health.json)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_health_file(monkeypatch, tmp_path: Path):
    """Redirect data/health.json to a per-test tmp file so tests don't share state."""
    from notion_finance_sync.health import tracker

    monkeypatch.setattr(tracker, "HEALTH_FILE", tmp_path / "health.json")


@pytest.fixture(autouse=True)
def _notion_api_key(monkeypatch):
    monkeypatch.setenv("NOTION_API_KEY", TEST_API_KEY)
    # The settings module caches the API key — clear it so the env var wins.
    from notion_finance_sync.config import settings as settings_mod

    settings_mod.get_notion_api_key.cache_clear()
    yield
    settings_mod.get_notion_api_key.cache_clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_query_response() -> dict:
    return {"object": "list", "results": [], "has_more": False}


def _query_response(rows: list[dict]) -> dict:
    return {"object": "list", "results": rows, "has_more": False}


def _make_record(
    source_id: str = "src-fake-1",
    *,
    name: str = "Starbucks",
    amount: float = -5.75,
    status: TransactionStatus = TransactionStatus.POSTED,
    transaction_date: date | None = None,
) -> TransactionRecord:
    """Build a TransactionRecord whose fields match what ``_existing_row``
    produces, so dedup-by-source-id treats them as unchanged when source_id
    matches."""
    return TransactionRecord(
        source_id=source_id,
        source_account_id="acct-fake-1",
        name=name,
        amount=amount,
        transaction_date=transaction_date or date(2026, 5, 1),
        transacted_at=None,  # _existing_row omits "Transacted At" too
        status=status,
        payee=name,
        memo="",
        bank=BankName.BANK_OF_AMERICA,
        credit_card_account="AQHA Customized Cash Rewards",
        card_network=CardNetwork.VISA,
        account_type=AccountType.CREDIT_CARD,
        account_name="AQHA",
    )


def _existing_row(
    *,
    page_id: str,
    source_id: str,
    name: str,
    amount: float,
    status: str = "Posted",
    transaction_date: str = "2026-05-01",
) -> dict:
    """Shape a Notion page-response row matching what NotionClient expects."""
    return {
        "id": page_id,
        "properties": {
            "Name": {"title": [{"plain_text": name}]},
            "Transaction Amount": {"number": amount},
            "Transaction Date": {"date": {"start": transaction_date}},
            "Transaction Status": {"status": {"name": status}},
            "Transaction Source ID": {"rich_text": [{"plain_text": source_id}]},
            "Source Account ID": {"rich_text": [{"plain_text": "acct-fake-1"}]},
            "Payee": {"rich_text": [{"plain_text": name}]},
            "Bank": {"select": {"name": "Bank of America"}},
            "Credit Card / Account": {"select": {"name": "AQHA Customized Cash Rewards"}},
            "Card Network": {"select": {"name": "Visa"}},
            "Account Type": {"select": {"name": "Credit Card"}},
            "Account Name": {"rich_text": [{"plain_text": "AQHA"}]},
        },
    }


# ---------------------------------------------------------------------------
# Test 1: Happy path — 3 new records, no existing, 3 creates
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_creates_all_records_when_no_existing(self, respx_mock, monkeypatch):
        from notion_finance_sync.banks import registry
        from notion_finance_sync.sync import orchestrator

        records = [
            _make_record(source_id="src-1", name="Starbucks", amount=-5.75),
            _make_record(source_id="src-2", name="Whole Foods", amount=-42.10),
            _make_record(source_id="src-3", name="Amazon", amount=-19.99),
        ]
        fake = FakeBankScraper(records=records)
        monkeypatch.setattr(registry, "BANK_REGISTRY", {"fake_bank": fake})

        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_empty_query_response())
        )
        create_route = respx_mock.post(PAGES_URL).mock(
            return_value=httpx.Response(200, json={"object": "page", "id": "new"})
        )

        result = await orchestrator.run_one_bank(
            "fake_bank",
            since=date(2026, 4, 1),
            retry_pause_seconds=0,
        )

        assert result.status == "success"
        assert result.session_id == "fake_bank"
        assert result.transactions_created == 3
        assert result.transactions_updated == 0
        assert result.transactions_unchanged == 0
        assert result.pending_released == 0
        assert result.error is None
        assert create_route.call_count == 3

    @pytest.mark.asyncio
    async def test_passes_since_to_scraper(self, respx_mock, monkeypatch):
        from notion_finance_sync.banks import registry
        from notion_finance_sync.sync import orchestrator

        fake = FakeBankScraper(records=[])
        monkeypatch.setattr(registry, "BANK_REGISTRY", {"fake_bank": fake})

        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_empty_query_response())
        )

        await orchestrator.run_one_bank(
            "fake_bank",
            since=date(2026, 4, 15),
            retry_pause_seconds=0,
        )

        assert fake.calls == [date(2026, 4, 15)]


# ---------------------------------------------------------------------------
# Test 2: With existing rows — one matches by source_id (unchanged), one is new
# ---------------------------------------------------------------------------


class TestWithExisting:
    @pytest.mark.asyncio
    async def test_dedups_unchanged_and_creates_new(self, respx_mock, monkeypatch):
        from notion_finance_sync.banks import registry
        from notion_finance_sync.sync import orchestrator

        existing_record = _make_record(source_id="src-existing", name="Starbucks", amount=-5.75)
        new_record = _make_record(source_id="src-new", name="Whole Foods", amount=-42.10)
        fake = FakeBankScraper(records=[existing_record, new_record])
        monkeypatch.setattr(registry, "BANK_REGISTRY", {"fake_bank": fake})

        existing_rows = [
            _existing_row(
                page_id="existing-page-1",
                source_id="src-existing",
                name="Starbucks",
                amount=-5.75,
            ),
        ]
        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_query_response(existing_rows))
        )
        create_route = respx_mock.post(PAGES_URL).mock(
            return_value=httpx.Response(200, json={"object": "page", "id": "new"})
        )

        result = await orchestrator.run_one_bank(
            "fake_bank",
            since=date(2026, 4, 1),
            retry_pause_seconds=0,
        )

        assert result.status == "success"
        assert result.transactions_created == 1
        assert result.transactions_unchanged == 1
        assert result.transactions_updated == 0
        assert create_route.call_count == 1

    @pytest.mark.asyncio
    async def test_updates_when_material_field_differs(self, respx_mock, monkeypatch):
        from notion_finance_sync.banks import registry
        from notion_finance_sync.sync import orchestrator

        # Scrape returns an updated amount for an existing row
        changed_record = _make_record(source_id="src-existing", name="Starbucks", amount=-9.99)
        fake = FakeBankScraper(records=[changed_record])
        monkeypatch.setattr(registry, "BANK_REGISTRY", {"fake_bank": fake})

        existing_rows = [
            _existing_row(
                page_id="existing-page-1",
                source_id="src-existing",
                name="Starbucks",
                amount=-5.75,
            ),
        ]
        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_query_response(existing_rows))
        )
        respx_mock.post(PAGES_URL).mock(
            return_value=httpx.Response(200, json={"object": "page", "id": "new"})
        )
        update_route = respx_mock.patch("https://api.notion.com/v1/pages/existing-page-1").mock(
            return_value=httpx.Response(200, json={"object": "page", "id": "existing-page-1"})
        )

        result = await orchestrator.run_one_bank(
            "fake_bank",
            since=date(2026, 4, 1),
            retry_pause_seconds=0,
        )

        assert result.status == "success"
        assert result.transactions_updated == 1
        assert result.transactions_created == 0
        assert update_route.call_count == 1


# ---------------------------------------------------------------------------
# Test 3: Pending -> Released — a pending Notion row missing from scrape
# ---------------------------------------------------------------------------


class TestOrphanRelease:
    @pytest.mark.asyncio
    async def test_releases_pending_row_not_in_scrape(self, respx_mock, monkeypatch):
        from notion_finance_sync.banks import registry
        from notion_finance_sync.sync import orchestrator

        # Scrape returns nothing — the existing pending row is an orphan
        fake = FakeBankScraper(records=[])
        monkeypatch.setattr(registry, "BANK_REGISTRY", {"fake_bank": fake})

        pending_rows = [
            _existing_row(
                page_id="pending-page-1",
                source_id="src-pending",
                name="Pending Coffee",
                amount=-3.50,
                status="Pending",
            ),
        ]
        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_query_response(pending_rows))
        )
        release_route = respx_mock.patch("https://api.notion.com/v1/pages/pending-page-1").mock(
            return_value=httpx.Response(200, json={"object": "page", "id": "pending-page-1"})
        )

        result = await orchestrator.run_one_bank(
            "fake_bank",
            since=date(2026, 4, 1),
            retry_pause_seconds=0,
        )

        assert result.status == "success"
        assert result.pending_released == 1
        assert release_route.call_count == 1
        body = json.loads(release_route.calls[0].request.content)
        assert body["properties"]["Transaction Status"] == {"status": {"name": "Released"}}
        assert "Release Date" in body["properties"]


# ---------------------------------------------------------------------------
# Test 4: 3 failures -> escalation creates Notion task
# ---------------------------------------------------------------------------


class TestRetryAndEscalation:
    @staticmethod
    def _force_escalation_threshold_reached(monkeypatch) -> None:
        """Force ``needs_escalation`` to return True on this orchestrator run.

        Pre-seeding ``data/health.json`` is fragile because ``tracker.record_failure``
        derives ``failure_day`` from UTC time while ``needs_escalation`` uses local
        time (a real bug in tracker.py, out of scope for this task — see report).
        Tests of the orchestrator's *escalation path* sidestep that seam by stubbing
        ``needs_escalation`` directly.
        """
        from notion_finance_sync.sync import orchestrator

        monkeypatch.setattr(orchestrator, "needs_escalation", lambda session_id: True)

    @pytest.mark.asyncio
    async def test_three_failures_escalates_and_records_failure(self, respx_mock, monkeypatch):
        from notion_finance_sync.banks import registry
        from notion_finance_sync.health import tracker
        from notion_finance_sync.sync import orchestrator

        fake = FakeBankScraper(should_raise=RuntimeError("boom"))
        monkeypatch.setattr(registry, "BANK_REGISTRY", {"fake_bank": fake})

        # Spy on record_failure so we can verify it's called exactly once per run
        failure_calls: list[tuple[str, str]] = []
        real_record_failure = tracker.record_failure

        def _spy_record_failure(session_id: str, error: str) -> int:
            failure_calls.append((session_id, error))
            return real_record_failure(session_id, error)

        monkeypatch.setattr(orchestrator, "record_failure", _spy_record_failure)

        # Seed the tracker so this run's failure trips the threshold
        self._force_escalation_threshold_reached(monkeypatch)

        # Task escalation routes — query returns no existing task, then create succeeds
        respx_mock.post(TASKS_QUERY_URL).mock(
            return_value=httpx.Response(200, json=_empty_query_response())
        )
        task_create_route = respx_mock.post(PAGES_URL).mock(
            return_value=httpx.Response(200, json={"object": "page", "id": "task-new"})
        )

        result = await orchestrator.run_one_bank(
            "fake_bank",
            since=date(2026, 4, 1),
            retry_pause_seconds=0,
        )

        assert result.status == "failure"
        assert result.error is not None and "boom" in result.error
        assert result.error_traceback is not None
        assert len(fake.calls) == 3  # 3 attempts per run
        assert len(failure_calls) == 1  # record_failure called once per run, not per attempt
        assert task_create_route.call_count == 1

    @pytest.mark.asyncio
    async def test_failure_task_body_has_correct_title_prefix(self, respx_mock, monkeypatch):
        from notion_finance_sync.banks import registry
        from notion_finance_sync.sync import orchestrator

        fake = FakeBankScraper(should_raise=RuntimeError("boom"))
        monkeypatch.setattr(registry, "BANK_REGISTRY", {"fake_bank": fake})
        self._force_escalation_threshold_reached(monkeypatch)

        respx_mock.post(TASKS_QUERY_URL).mock(
            return_value=httpx.Response(200, json=_empty_query_response())
        )
        task_create_route = respx_mock.post(PAGES_URL).mock(
            return_value=httpx.Response(200, json={"object": "page", "id": "task-new"})
        )

        await orchestrator.run_one_bank(
            "fake_bank",
            since=date(2026, 4, 1),
            retry_pause_seconds=0,
        )

        assert task_create_route.called
        body = json.loads(task_create_route.calls[0].request.content)
        title = body["properties"]["Name"]["title"][0]["text"]["content"]
        assert title.startswith("Fix Fake Bank scraper")

    @pytest.mark.asyncio
    async def test_no_escalation_on_first_failure_of_the_day(self, respx_mock, monkeypatch):
        """A single failed run is not enough to trip the threshold."""
        from notion_finance_sync.banks import registry
        from notion_finance_sync.sync import orchestrator

        fake = FakeBankScraper(should_raise=RuntimeError("boom"))
        monkeypatch.setattr(registry, "BANK_REGISTRY", {"fake_bank": fake})

        # No seeding — health.json starts empty so this run's count == 1
        task_create_route = respx_mock.post(PAGES_URL).mock(
            return_value=httpx.Response(200, json={"object": "page", "id": "task-new"})
        )
        tasks_query_route = respx_mock.post(TASKS_QUERY_URL).mock(
            return_value=httpx.Response(200, json=_empty_query_response())
        )

        result = await orchestrator.run_one_bank(
            "fake_bank",
            since=date(2026, 4, 1),
            retry_pause_seconds=0,
        )

        assert result.status == "failure"
        assert not task_create_route.called
        assert not tasks_query_route.called

    @pytest.mark.asyncio
    async def test_recovery_on_third_attempt(self, respx_mock, monkeypatch):
        """Scraper raises twice then succeeds — sync returns success without escalation."""
        from notion_finance_sync.banks import registry
        from notion_finance_sync.sync import orchestrator

        records = [_make_record(source_id="src-recovered", name="OK", amount=-1.0)]

        class FlakyScraper(FakeBankScraper):
            def __init__(self):
                super().__init__(records=records)
                self.attempts = 0

            def fetch_recent(self, since: date) -> list[TransactionRecord]:
                self.calls.append(since)
                self.attempts += 1
                if self.attempts < 3:
                    raise RuntimeError(f"transient {self.attempts}")
                return list(records)

        flaky = FlakyScraper()
        monkeypatch.setattr(registry, "BANK_REGISTRY", {"fake_bank": flaky})

        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_empty_query_response())
        )
        respx_mock.post(PAGES_URL).mock(
            return_value=httpx.Response(200, json={"object": "page", "id": "new"})
        )

        result = await orchestrator.run_one_bank(
            "fake_bank",
            since=date(2026, 4, 1),
            retry_pause_seconds=0,
        )

        assert result.status == "success"
        assert result.transactions_created == 1
        assert flaky.attempts == 3

    @pytest.mark.asyncio
    async def test_escalation_failure_does_not_mask_original_error(self, respx_mock, monkeypatch):
        """If Notion task creation itself fails, we still return the original sync error."""
        from notion_finance_sync.banks import registry
        from notion_finance_sync.sync import orchestrator

        fake = FakeBankScraper(should_raise=RuntimeError("scrape boom"))
        monkeypatch.setattr(registry, "BANK_REGISTRY", {"fake_bank": fake})
        # Pre-seed so escalation fires (and exposes the broken tasks endpoint)
        self._force_escalation_threshold_reached(monkeypatch)

        # Tasks endpoint blows up — orchestrator should swallow and continue
        respx_mock.post(TASKS_QUERY_URL).mock(
            return_value=httpx.Response(500, json={"error": "notion down"})
        )
        respx_mock.post(PAGES_URL).mock(
            return_value=httpx.Response(500, json={"error": "notion down"})
        )

        result = await orchestrator.run_one_bank(
            "fake_bank",
            since=date(2026, 4, 1),
            retry_pause_seconds=0,
        )

        assert result.status == "failure"
        assert "scrape boom" in result.error


# ---------------------------------------------------------------------------
# Test 5: SUPPORTS_LIVE = False -> skipped without HTTP calls
# ---------------------------------------------------------------------------


class TestSupportsLiveFalse:
    @pytest.mark.asyncio
    async def test_closed_account_returns_skipped(self, respx_mock, monkeypatch):
        from notion_finance_sync.banks import registry, td
        from notion_finance_sync.sync import orchestrator

        monkeypatch.setattr(registry, "BANK_REGISTRY", {"td": td.TDBankScraper()})

        # If the orchestrator made HTTP calls, respx would record them; we want
        # ZERO calls for a closed account.

        result = await orchestrator.run_one_bank(
            "td", since=date(2026, 4, 1), retry_pause_seconds=0
        )

        assert result.status == "skipped"
        assert result.transactions_created == 0
        assert respx_mock.calls.call_count == 0


# ---------------------------------------------------------------------------
# Test 6: run_all_banks runs every registered bank
# ---------------------------------------------------------------------------


class TestRunAllBanks:
    @pytest.mark.asyncio
    async def test_runs_each_registered_bank(self, respx_mock, monkeypatch):
        from notion_finance_sync.banks import registry
        from notion_finance_sync.sync import orchestrator

        class BankA(FakeBankScraper):
            SESSION_ID = "fake_a"
            BANK_DISPLAY_NAME = "Fake A"

        class BankB(FakeBankScraper):
            SESSION_ID = "fake_b"
            BANK_DISPLAY_NAME = "Fake B"

        a = BankA(records=[_make_record(source_id="src-a", name="A", amount=-1.0)])
        b = BankB(records=[_make_record(source_id="src-b", name="B", amount=-2.0)])
        monkeypatch.setattr(registry, "BANK_REGISTRY", {"fake_a": a, "fake_b": b})

        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_empty_query_response())
        )
        respx_mock.post(PAGES_URL).mock(
            return_value=httpx.Response(200, json={"object": "page", "id": "new"})
        )

        results = await orchestrator.run_all_banks(since=date(2026, 4, 1), retry_pause_seconds=0)

        assert set(results.keys()) == {"fake_a", "fake_b"}
        assert all(r.status == "success" for r in results.values())
        assert len(a.calls) == 1
        assert len(b.calls) == 1


# ---------------------------------------------------------------------------
# Test 7: Enricher failures are non-fatal
# ---------------------------------------------------------------------------


class TestEnricherNonFatal:
    @pytest.mark.asyncio
    async def test_notimplementederror_in_enricher_does_not_crash_sync(
        self, respx_mock, monkeypatch
    ):
        from notion_finance_sync.banks import registry as bank_registry
        from notion_finance_sync.enrichers import registry as enricher_registry
        from notion_finance_sync.sync import orchestrator

        fake = FakeBankScraper(records=[_make_record(source_id="src-1", name="OK", amount=-1.0)])
        monkeypatch.setattr(bank_registry, "BANK_REGISTRY", {"fake_bank": fake})

        broken_enricher = FakeEnricher(should_raise_on_fetch=NotImplementedError("v1 stub"))
        monkeypatch.setattr(
            enricher_registry, "ENRICHER_REGISTRY", {"fake_enricher": broken_enricher}
        )

        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_empty_query_response())
        )
        respx_mock.post(PAGES_URL).mock(
            return_value=httpx.Response(200, json={"object": "page", "id": "new"})
        )

        # run_all_banks runs enrichers after every bank succeeds
        results = await orchestrator.run_all_banks(since=date(2026, 4, 1), retry_pause_seconds=0)

        assert results["fake_bank"].status == "success"
        assert broken_enricher.fetch_calls == 1

    @pytest.mark.asyncio
    async def test_skip_enrichers_flag_skips_enrichers(self, respx_mock, monkeypatch):
        from notion_finance_sync.banks import registry as bank_registry
        from notion_finance_sync.enrichers import registry as enricher_registry
        from notion_finance_sync.sync import orchestrator

        fake = FakeBankScraper(records=[_make_record(source_id="src-1", name="OK", amount=-1.0)])
        monkeypatch.setattr(bank_registry, "BANK_REGISTRY", {"fake_bank": fake})

        enricher = FakeEnricher()
        monkeypatch.setattr(enricher_registry, "ENRICHER_REGISTRY", {"fake_enricher": enricher})

        respx_mock.post(QUERY_URL).mock(
            return_value=httpx.Response(200, json=_empty_query_response())
        )
        respx_mock.post(PAGES_URL).mock(
            return_value=httpx.Response(200, json={"object": "page", "id": "new"})
        )

        await orchestrator.run_all_banks(
            since=date(2026, 4, 1),
            skip_enrichers=True,
            retry_pause_seconds=0,
        )

        assert enricher.fetch_calls == 0


# ---------------------------------------------------------------------------
# Test 8: Registry helpers
# ---------------------------------------------------------------------------


class TestRegistryHelpers:
    def test_all_session_ids_includes_known_banks(self):
        from notion_finance_sync.banks.registry import all_session_ids

        ids = all_session_ids()
        # Spot-check a few; the registry's contents are documented in the module
        for required in {"bofa", "us_bank", "wells_fargo", "td", "fidelity_ira_closed"}:
            assert required in ids

    def test_get_scraper_returns_instance(self):
        from notion_finance_sync.banks.registry import get_scraper

        scraper = get_scraper("bofa")
        assert scraper is not None
        assert scraper.SESSION_ID == "bofa"

    def test_get_scraper_unknown_raises(self):
        from notion_finance_sync.banks.registry import get_scraper

        with pytest.raises(KeyError):
            get_scraper("nonexistent_bank")
