"""Unit tests for sync.diffing::build_transaction_changes."""

from __future__ import annotations

from datetime import date

from notion_finance_sync.models import (
    AccountType,
    BankName,
    CardNetwork,
    TransactionRecord,
    TransactionStatus,
)
from notion_finance_sync.sync.diffing import build_transaction_changes


def _record(
    source_id: str = "src-1",
    *,
    name: str = "Starbucks",
    amount: float = -5.75,
    status: TransactionStatus = TransactionStatus.POSTED,
    transaction_date: date = date(2026, 5, 1),
    category: str | None = None,
    bilt_partner: bool = False,
) -> TransactionRecord:
    return TransactionRecord(
        source_id=source_id,
        source_account_id="acct-1",
        name=name,
        amount=amount,
        transaction_date=transaction_date,
        status=status,
        payee=name,
        memo="",
        bank=BankName.BANK_OF_AMERICA,
        credit_card_account="AQHA",
        card_network=CardNetwork.VISA,
        account_type=AccountType.CREDIT_CARD,
        account_name="AQHA",
        bilt_partner=bilt_partner,
    )


def _row(
    source_id: str = "src-1",
    *,
    page_id: str = "page-1",
    name: str = "Starbucks",
    amount: float = -5.75,
    status: str = "Posted",
    transaction_date: str | None = "2026-05-01",
    category: str = "",
    bilt_partner: bool = False,
) -> dict:
    """Minimal flat row dict matching what NotionClient._row_from_props returns."""
    return {
        "page_id": page_id,
        "source_id": source_id,
        "name": name,
        "amount": amount,
        "transaction_date": transaction_date,
        "status": status,
        "payee": name,
        "memo": "",
        "bank": "Bank of America",
        "credit_card_account": "AQHA",
        "card_network": "Visa",
        "account_type": "Credit Card",
        "account_name": "AQHA",
        "bank_category": "",
        "category": category,
        "source_account_id": "acct-1",
        "calculated_rewards": None,
        "true_rewards": None,
        "bilt_points": None,
        "bilt_partner": bilt_partner,
        "quantity": None,
        "ticker": "",
        "price_per_share": None,
    }


class TestAllNew:
    def test_empty_existing_all_go_to_create(self):
        records = [_record("src-1"), _record("src-2"), _record("src-3")]
        result = build_transaction_changes(scraped=records, existing={})

        assert len(result.to_create) == 3
        assert result.to_update == []
        assert result.unchanged == []
        assert {r.source_id for r in result.to_create} == {"src-1", "src-2", "src-3"}


class TestAllUnchanged:
    def test_matching_rows_all_go_to_unchanged(self):
        records = [_record("src-1"), _record("src-2")]
        existing = {
            "src-1": _row("src-1", page_id="page-1"),
            "src-2": _row("src-2", page_id="page-2"),
        }
        result = build_transaction_changes(scraped=records, existing=existing)

        assert result.to_create == []
        assert result.to_update == []
        assert len(result.unchanged) == 2


class TestMixedPartition:
    def test_new_unchanged_and_changed_are_partitioned(self):
        records = [
            _record("src-new"),
            _record("src-unchanged"),
            _record("src-changed", amount=-99.99),
        ]
        existing = {
            "src-unchanged": _row("src-unchanged", page_id="pg-u"),
            "src-changed": _row("src-changed", page_id="pg-c", amount=-5.75),
        }
        result = build_transaction_changes(scraped=records, existing=existing)

        assert [r.source_id for r in result.to_create] == ["src-new"]
        assert len(result.to_update) == 1
        assert result.to_update[0][0] == "pg-c"
        assert result.to_update[0][1].source_id == "src-changed"
        assert len(result.unchanged) == 1
        assert result.unchanged[0].source_id == "src-unchanged"


class TestMaterialFieldTriggers:
    def test_amount_change_triggers_update(self):
        record = _record("src-1", amount=-99.00)
        existing = {"src-1": _row("src-1", amount=-5.75)}
        result = build_transaction_changes(scraped=[record], existing=existing)
        assert len(result.to_update) == 1
        assert result.unchanged == []

    def test_status_change_triggers_update(self):
        record = _record("src-1", status=TransactionStatus.PENDING)
        existing = {"src-1": _row("src-1", status="Posted")}
        result = build_transaction_changes(scraped=[record], existing=existing)
        assert len(result.to_update) == 1

    def test_category_change_triggers_update(self):
        record = _record("src-1")
        # Force a category string difference by setting existing category
        existing = {"src-1": _row("src-1", category="Dining")}
        result = build_transaction_changes(scraped=[record], existing=existing)
        assert len(result.to_update) == 1

    def test_bilt_partner_change_triggers_update(self):
        record = _record("src-1", bilt_partner=True)
        existing = {"src-1": _row("src-1", bilt_partner=False)}
        result = build_transaction_changes(scraped=[record], existing=existing)
        assert len(result.to_update) == 1


class TestImmaterialFields:
    def test_raw_data_change_does_not_trigger_update(self):
        """raw_data is not in MATERIAL_FIELDS and must not cause an update."""
        record = _record("src-1")
        record.raw_data = {"extra": "noise"}
        existing = {"src-1": _row("src-1")}
        result = build_transaction_changes(scraped=[record], existing=existing)
        assert result.to_update == []
        assert len(result.unchanged) == 1


class TestNoneEmptyStringEquivalence:
    def test_empty_string_vs_none_in_memo_does_not_trigger_update(self):
        """memo="" in record and memo=None in existing row should be treated as equal."""
        record = _record("src-1")  # memo defaults to ""
        existing_row = _row("src-1")
        existing_row["memo"] = None  # simulate Notion returning null
        result = build_transaction_changes(scraped=[record], existing={"src-1": existing_row})
        assert result.to_update == []
        assert len(result.unchanged) == 1


class TestDateEquivalence:
    def test_date_object_vs_iso_string_no_spurious_update(self):
        """record.transaction_date is a date object; existing has the ISO string."""
        record = _record("src-1", transaction_date=date(2026, 5, 1))
        existing = {"src-1": _row("src-1", transaction_date="2026-05-01")}
        result = build_transaction_changes(scraped=[record], existing=existing)
        assert result.to_update == []
        assert len(result.unchanged) == 1

    def test_different_date_triggers_update(self):
        record = _record("src-1", transaction_date=date(2026, 5, 15))
        existing = {"src-1": _row("src-1", transaction_date="2026-05-01")}
        result = build_transaction_changes(scraped=[record], existing=existing)
        assert len(result.to_update) == 1
