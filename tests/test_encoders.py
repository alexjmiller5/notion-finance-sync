"""Tests for Notion property encoders.

Coverage:
- Fully-populated TransactionRecord encodes every property correctly
- Sparse record (most fields None) emits only the required fields
- StrEnum values encode by their .value (e.g., TransactionStatus.PENDING -> "Pending")
- Investment record (quantity/ticker/price_per_share, no category/rewards)
- bilt_partner=False IS emitted (checkbox always present)
- create_from_record / update_from_record send correct HTTP bodies (via respx)
- Both create_from_record and update_from_record delegate to encode_transaction
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import httpx
import pytest

from notion_finance_sync.models.transactions import (
    AccountType,
    BankName,
    CanonicalCategory,
    CardNetwork,
    TransactionRecord,
    TransactionStatus,
)
from notion_finance_sync.notion.client import NotionClient
from notion_finance_sync.notion.encoders import encode_transaction

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DATA_SOURCE_ID = "test-ds-id-1234"
TEST_API_KEY = "secret_test"
PAGE_ID = "page-abc-123"


def make_full_record() -> TransactionRecord:
    return TransactionRecord(
        source_id="src-001",
        source_account_id="acct-001",
        name="Starbucks",
        amount=-5.75,
        transaction_date=date(2025, 3, 15),
        transacted_at=datetime(2025, 3, 15, 14, 30, 0, tzinfo=UTC),
        status=TransactionStatus.POSTED,
        payee="Starbucks Coffee",
        memo="Morning coffee",
        bank_category="Food & Drink",
        category=CanonicalCategory.DINING,
        bank=BankName.BILT,
        credit_card_account="Bilt Mastercard",
        card_network=CardNetwork.MASTERCARD,
        account_type=AccountType.CREDIT_CARD,
        account_name="Bilt World Elite",
        calculated_rewards=0.1725,
        true_rewards=0.115,
        bilt_points=5.75,
        bilt_partner=True,
        quantity=None,
        ticker=None,
        price_per_share=None,
    )


def make_sparse_record() -> TransactionRecord:
    return TransactionRecord(
        source_id="src-002",
        source_account_id="acct-002",
        name="Amazon",
        amount=-99.99,
        transaction_date=date(2025, 4, 1),
        transacted_at=None,
        status=TransactionStatus.PENDING,
    )


def make_investment_record() -> TransactionRecord:
    return TransactionRecord(
        source_id="src-003",
        source_account_id="acct-003",
        name="TSLA Buy",
        amount=-500.00,
        transaction_date=date(2025, 4, 5),
        transacted_at=None,
        status=TransactionStatus.POSTED,
        bank=BankName.ETRADE,
        account_type=AccountType.BROKERAGE,
        quantity=3.5,
        ticker="TSLA",
        price_per_share=142.86,
    )


# ---------------------------------------------------------------------------
# Tests: encode_transaction — fully populated record
# ---------------------------------------------------------------------------


class TestEncodeTransactionFullRecord:
    def setup_method(self):
        self.record = make_full_record()
        self.props = encode_transaction(self.record)

    def test_title_encoded(self):
        assert self.props["Name"] == {"title": [{"text": {"content": "Starbucks"}}]}

    def test_amount_encoded(self):
        assert self.props["Txn Amount"] == {"number": -5.75}

    def test_transaction_date_encoded(self):
        assert self.props["Transaction Date"] == {"date": {"start": "2025-03-15"}}

    def test_transacted_at_never_written(self):
        # Schema migrated away from `Transacted At` (2026-07-03): the record
        # field is model-only and must not reach Notion even when set.
        assert "Transacted At" not in self.props

    def test_status_encoded(self):
        assert self.props["Transaction Status"] == {"status": {"name": "Posted"}}

    def test_payee_encoded(self):
        assert self.props["Payee"] == {"rich_text": [{"text": {"content": "Starbucks Coffee"}}]}

    def test_memo_encoded(self):
        assert self.props["Memo"] == {"rich_text": [{"text": {"content": "Morning coffee"}}]}

    def test_bank_category_encoded(self):
        assert self.props["Bank Category"] == {"rich_text": [{"text": {"content": "Food & Drink"}}]}

    def test_category_encoded(self):
        assert self.props["Category"] == {"select": {"name": "Dining"}}

    def test_bank_encoded(self):
        assert self.props["Bank"] == {"select": {"name": "Bilt"}}

    def test_credit_card_account_encoded(self):
        assert self.props["Credit Card / Account"] == {"select": {"name": "Bilt Mastercard"}}

    def test_card_network_encoded(self):
        assert self.props["Card Network"] == {"select": {"name": "Mastercard"}}

    def test_account_type_encoded(self):
        assert self.props["Account Type"] == {"select": {"name": "Credit Card"}}

    def test_account_name_encoded(self):
        assert self.props["Account Name"] == {
            "rich_text": [{"text": {"content": "Bilt World Elite"}}]
        }

    def test_source_id_encoded(self):
        assert self.props["Transaction Source ID"] == {
            "rich_text": [{"text": {"content": "src-001"}}]
        }

    def test_source_account_id_encoded(self):
        assert self.props["Source Account ID"] == {"rich_text": [{"text": {"content": "acct-001"}}]}

    def test_calculated_rewards_encoded(self):
        assert self.props["Calculated Rewards"] == {"number": 0.1725}

    def test_true_rewards_encoded(self):
        assert self.props["True Rewards"] == {"number": 0.115}

    def test_bilt_points_encoded(self):
        assert self.props["Bilt Points"] == {"number": 5.75}

    def test_bilt_partner_true_encoded(self):
        assert self.props["Bilt Partner"] == {"checkbox": True}

    def test_investment_fields_absent_when_none(self):
        assert "Qty" not in self.props
        assert "Ticker" not in self.props
        assert "PPS" not in self.props

    def test_excluded_computed_fields_not_present(self):
        assert "Related Transactions" not in self.props
        assert "Related Transactions Amount" not in self.props
        assert "Net Amount" not in self.props
        assert "Release Date" not in self.props


# ---------------------------------------------------------------------------
# Tests: encode_transaction — sparse record
# ---------------------------------------------------------------------------


class TestEncodeTransactionSparseRecord:
    def setup_method(self):
        self.record = make_sparse_record()
        self.props = encode_transaction(self.record)

    def test_required_fields_present(self):
        assert "Name" in self.props
        assert "Txn Amount" in self.props
        assert "Transaction Date" in self.props
        assert "Transaction Status" in self.props
        assert "Transaction Source ID" in self.props
        assert "Source Account ID" in self.props

    def test_transacted_at_absent_when_none(self):
        assert "Transacted At" not in self.props

    def test_payee_absent_when_empty_string(self):
        assert "Payee" not in self.props

    def test_memo_absent_when_empty_string(self):
        assert "Memo" not in self.props

    def test_bank_category_absent_when_none(self):
        assert "Bank Category" not in self.props

    def test_category_absent_when_none(self):
        assert "Category" not in self.props

    def test_bank_absent_when_none(self):
        assert "Bank" not in self.props

    def test_credit_card_account_absent_when_none(self):
        assert "Credit Card / Account" not in self.props

    def test_card_network_absent_when_none(self):
        assert "Card Network" not in self.props

    def test_account_type_absent_when_none(self):
        assert "Account Type" not in self.props

    def test_account_name_absent_when_empty_string(self):
        assert "Account Name" not in self.props

    def test_calculated_rewards_absent_when_none(self):
        assert "Calculated Rewards" not in self.props

    def test_true_rewards_absent_when_none(self):
        assert "True Rewards" not in self.props

    def test_bilt_points_absent_when_none(self):
        assert "Bilt Points" not in self.props

    def test_bilt_partner_false_is_present(self):
        assert "Bilt Partner" in self.props
        assert self.props["Bilt Partner"] == {"checkbox": False}

    def test_quantity_absent_when_none(self):
        assert "Qty" not in self.props

    def test_ticker_absent_when_none(self):
        assert "Ticker" not in self.props

    def test_price_per_share_absent_when_none(self):
        assert "PPS" not in self.props


# ---------------------------------------------------------------------------
# Tests: StrEnum encoding
# ---------------------------------------------------------------------------


class TestStrEnumEncoding:
    def test_status_pending_encodes_by_value(self):
        record = make_sparse_record()
        record.status = TransactionStatus.PENDING
        props = encode_transaction(record)
        assert props["Transaction Status"] == {"status": {"name": "Pending"}}

    def test_status_released_encodes_by_value(self):
        record = make_sparse_record()
        record.status = TransactionStatus.RELEASED
        props = encode_transaction(record)
        assert props["Transaction Status"] == {"status": {"name": "Released"}}

    def test_bank_name_encodes_by_value(self):
        record = make_sparse_record()
        record.bank = BankName.WELLS_FARGO
        props = encode_transaction(record)
        assert props["Bank"] == {"select": {"name": "Wells Fargo"}}

    def test_card_network_visa_encodes_by_value(self):
        record = make_sparse_record()
        record.card_network = CardNetwork.VISA
        props = encode_transaction(record)
        assert props["Card Network"] == {"select": {"name": "Visa"}}

    def test_account_type_brokerage_encodes_by_value(self):
        record = make_sparse_record()
        record.account_type = AccountType.BROKERAGE
        props = encode_transaction(record)
        assert props["Account Type"] == {"select": {"name": "Brokerage"}}

    def test_category_groceries_encodes_by_value(self):
        record = make_sparse_record()
        record.category = CanonicalCategory.GROCERIES
        props = encode_transaction(record)
        assert props["Category"] == {"select": {"name": "Groceries"}}


# ---------------------------------------------------------------------------
# Tests: investment record shape
# ---------------------------------------------------------------------------


class TestInvestmentRecord:
    def setup_method(self):
        self.record = make_investment_record()
        self.props = encode_transaction(self.record)

    def test_quantity_encoded(self):
        assert self.props["Qty"] == {"number": 3.5}

    def test_ticker_encoded(self):
        assert self.props["Ticker"] == {"rich_text": [{"text": {"content": "TSLA"}}]}

    def test_price_per_share_encoded(self):
        assert self.props["PPS"] == {"number": 142.86}

    def test_category_absent_when_none(self):
        assert "Category" not in self.props

    def test_calculated_rewards_absent_when_none(self):
        assert "Calculated Rewards" not in self.props

    def test_true_rewards_absent_when_none(self):
        assert "True Rewards" not in self.props

    def test_bilt_points_absent_when_none(self):
        assert "Bilt Points" not in self.props


# ---------------------------------------------------------------------------
# Tests: checkbox always emitted
# ---------------------------------------------------------------------------


class TestCheckboxAlwaysEmitted:
    def test_bilt_partner_false_emitted(self):
        record = make_sparse_record()
        assert record.bilt_partner is False
        props = encode_transaction(record)
        assert "Bilt Partner" in props
        assert props["Bilt Partner"] == {"checkbox": False}

    def test_bilt_partner_true_emitted(self):
        record = make_sparse_record()
        record.bilt_partner = True
        props = encode_transaction(record)
        assert "Bilt Partner" in props
        assert props["Bilt Partner"] == {"checkbox": True}

    def test_excluded_from_spending_false_emitted(self):
        record = make_sparse_record()
        assert record.excluded_from_spending is False
        props = encode_transaction(record)
        assert props["Excluded"] == {"checkbox": False}

    def test_excluded_from_spending_true_emitted(self):
        record = make_sparse_record()
        record.excluded_from_spending = True
        props = encode_transaction(record)
        assert props["Excluded"] == {"checkbox": True}


class TestReviewStatusEncoding:
    def test_review_status_omitted_when_none(self):
        record = make_sparse_record()
        assert record.review_status is None
        props = encode_transaction(record)
        assert "Review Status" not in props

    def test_review_status_emitted_when_set(self):
        from notion_finance_sync.models import ReviewStatus

        record = make_sparse_record()
        record.review_status = ReviewStatus.NEEDS_REVIEW
        props = encode_transaction(record)
        assert props["Review Status"] == {"status": {"name": "Needs Review"}}

    def test_review_status_reviewed_value(self):
        from notion_finance_sync.models import ReviewStatus

        record = make_sparse_record()
        record.review_status = ReviewStatus.REVIEWED
        props = encode_transaction(record)
        assert props["Review Status"] == {"status": {"name": "Reviewed"}}


# ---------------------------------------------------------------------------
# Tests: encode_transaction
# ---------------------------------------------------------------------------


class TestEncodeTransactionConsistency:
    def test_full_record_field_set_is_stable(self):
        record = make_full_record()
        props_a = encode_transaction(record)
        props_b = encode_transaction(record)
        assert set(props_a.keys()) == set(props_b.keys())

    def test_sparse_record_absent_fields_still_absent(self):
        record = make_sparse_record()
        props = encode_transaction(record)
        assert "Transacted At" not in props
        assert "Category" not in props
        assert "Bank" not in props

    def test_bilt_partner_always_present(self):
        record = make_sparse_record()
        props = encode_transaction(record)
        assert "Bilt Partner" in props


# ---------------------------------------------------------------------------
# Integration tests: NotionClient.create_from_record / update_from_record
# ---------------------------------------------------------------------------


class TestNotionClientCreateFromRecord:
    @pytest.mark.asyncio
    async def test_create_from_record_posts_correct_body(self, respx_mock):
        record = make_full_record()
        expected_props = encode_transaction(record)

        respx_mock.post("https://api.notion.com/v1/pages").mock(
            return_value=httpx.Response(200, json={"object": "page", "id": "new-page-id"})
        )

        client = NotionClient(api_key=TEST_API_KEY, data_source_id=DATA_SOURCE_ID)
        await client.create_from_record(record)

        assert respx_mock.calls.call_count == 1
        request = respx_mock.calls[0].request
        body = json.loads(request.content)

        assert body["parent"] == {"data_source_id": DATA_SOURCE_ID}
        assert body["properties"] == expected_props

    @pytest.mark.asyncio
    async def test_create_from_record_sparse(self, respx_mock):
        record = make_sparse_record()
        expected_props = encode_transaction(record)

        respx_mock.post("https://api.notion.com/v1/pages").mock(
            return_value=httpx.Response(200, json={"object": "page", "id": "new-page-id"})
        )

        client = NotionClient(api_key=TEST_API_KEY, data_source_id=DATA_SOURCE_ID)
        await client.create_from_record(record)

        body = json.loads(respx_mock.calls[0].request.content)
        assert body["properties"] == expected_props
        assert "Transacted At" not in body["properties"]


class TestNotionClientUpdateFromRecord:
    @pytest.mark.asyncio
    async def test_update_from_record_patches_correct_body(self, respx_mock):
        record = make_full_record()
        expected_props = encode_transaction(record)

        respx_mock.patch(f"https://api.notion.com/v1/pages/{PAGE_ID}").mock(
            return_value=httpx.Response(200, json={"object": "page", "id": PAGE_ID})
        )

        client = NotionClient(api_key=TEST_API_KEY, data_source_id=DATA_SOURCE_ID)
        await client.update_from_record(PAGE_ID, record)

        assert respx_mock.calls.call_count == 1
        request = respx_mock.calls[0].request
        body = json.loads(request.content)

        assert "parent" not in body
        assert body["properties"] == expected_props

    @pytest.mark.asyncio
    async def test_update_from_record_investment(self, respx_mock):
        record = make_investment_record()

        respx_mock.patch(f"https://api.notion.com/v1/pages/{PAGE_ID}").mock(
            return_value=httpx.Response(200, json={"object": "page", "id": PAGE_ID})
        )

        client = NotionClient(api_key=TEST_API_KEY, data_source_id=DATA_SOURCE_ID)
        await client.update_from_record(PAGE_ID, record)

        body = json.loads(respx_mock.calls[0].request.content)
        assert body["properties"]["Qty"] == {"number": 3.5}
        assert body["properties"]["Ticker"] == {"rich_text": [{"text": {"content": "TSLA"}}]}
        assert body["properties"]["PPS"] == {"number": 142.86}
        assert "Category" not in body["properties"]
