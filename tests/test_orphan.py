"""Unit tests for sync.orphan::detect_orphans and filter_pending."""

from __future__ import annotations

from datetime import date

from notion_finance_sync.models import (
    AccountType,
    BankName,
    CardNetwork,
    TransactionRecord,
    TransactionStatus,
)
from notion_finance_sync.sync.orphan import OrphanRelease, detect_orphans, filter_pending


def _record(
    source_id: str,
    *,
    status: TransactionStatus = TransactionStatus.POSTED,
) -> TransactionRecord:
    return TransactionRecord(
        source_id=source_id,
        source_account_id="acct-1",
        name="Test",
        amount=-1.0,
        transaction_date=date(2026, 5, 1),
        transacted_at=None,
        status=status,
        bank=BankName.BANK_OF_AMERICA,
        account_type=AccountType.CREDIT_CARD,
        card_network=CardNetwork.VISA,
    )


def _pending_row(source_id: str, page_id: str) -> dict:
    return {
        "page_id": page_id,
        "source_id": source_id,
        "status": "Pending",
    }


class TestDetectOrphansNoPending:
    def test_no_pending_rows_returns_empty(self):
        result = detect_orphans(
            pending_notion_rows={},
            fresh_scrape_records=[_record("src-1")],
            scrape_was_successful=True,
        )
        assert result == []


class TestDetectOrphansPresentInScrape:
    def test_pending_rows_present_in_scrape_returns_empty(self):
        pending = {"src-1": _pending_row("src-1", "pg-1")}
        fresh = [_record("src-1")]
        result = detect_orphans(
            pending_notion_rows=pending,
            fresh_scrape_records=fresh,
            scrape_was_successful=True,
        )
        assert result == []


class TestDetectOrphansMissingFromScrape:
    def test_pending_row_absent_from_scrape_becomes_orphan(self):
        today = date.today()
        pending = {"src-missing": _pending_row("src-missing", "pg-missing")}
        result = detect_orphans(
            pending_notion_rows=pending,
            fresh_scrape_records=[],
            scrape_was_successful=True,
        )
        assert len(result) == 1
        orphan = result[0]
        assert isinstance(orphan, OrphanRelease)
        assert orphan.page_id == "pg-missing"
        assert orphan.source_id == "src-missing"
        assert orphan.release_date == today


class TestDetectOrphansUnsuccessfulScrape:
    def test_unsuccessful_scrape_returns_empty_regardless_of_missing(self):
        pending = {"src-missing": _pending_row("src-missing", "pg-1")}
        result = detect_orphans(
            pending_notion_rows=pending,
            fresh_scrape_records=[],
            scrape_was_successful=False,
        )
        assert result == []


class TestDetectOrphansExplicitRelease:
    def test_explicit_release_flagged_even_if_present_in_scrape(self):
        today = date.today()
        pending = {"src-released": _pending_row("src-released", "pg-released")}
        fresh = [_record("src-released")]  # still in scrape, but explicitly released
        explicit = [_record("src-released", status=TransactionStatus.RELEASED)]

        result = detect_orphans(
            pending_notion_rows=pending,
            fresh_scrape_records=fresh,
            scrape_was_successful=True,
            explicit_releases=explicit,
        )
        assert len(result) == 1
        assert result[0].source_id == "src-released"
        assert result[0].release_date == today


class TestFilterPending:
    def test_returns_only_pending_rows(self):
        rows = {
            "src-pending": {"page_id": "pg-1", "status": "Pending"},
            "src-posted": {"page_id": "pg-2", "status": "Posted"},
            "src-released": {"page_id": "pg-3", "status": "Released"},
        }
        result = filter_pending(rows)
        assert set(result.keys()) == {"src-pending"}
        assert result["src-pending"]["page_id"] == "pg-1"

    def test_all_non_pending_returns_empty(self):
        rows = {
            "src-1": {"page_id": "pg-1", "status": "Posted"},
            "src-2": {"page_id": "pg-2", "status": "Released"},
        }
        assert filter_pending(rows) == {}

    def test_empty_input_returns_empty(self):
        assert filter_pending({}) == {}
