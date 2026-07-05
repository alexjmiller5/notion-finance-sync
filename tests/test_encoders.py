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
from datetime import date

import httpx
import pytest

from notion_finance_sync.models.transactions import (
    AccountType,
    BankName,
    CanonicalCategory,
    CardNetwork,
    RewardsType,
    TransactionRecord,
    TransactionStatus,
)
from notion_finance_sync.notion.client import NotionClient
from notion_finance_sync.notion.encoders import encode_transaction
from notion_finance_sync.notion.properties import P

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
        rewards_type=RewardsType.CASHBACK,
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
        status=TransactionStatus.PENDING,
    )


def make_investment_record() -> TransactionRecord:
    return TransactionRecord(
        source_id="src-003",
        source_account_id="acct-003",
        name="TSLA Buy",
        amount=-500.00,
        transaction_date=date(2025, 4, 5),
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
        assert self.props[P.NAME] == {"title": [{"text": {"content": "Starbucks"}}]}

    def test_amount_encoded(self):
        assert self.props[P.AMOUNT] == {"number": -5.75}

    def test_transaction_date_encoded(self):
        assert self.props[P.DATE] == {"date": {"start": "2025-03-15"}}

    def test_status_encoded(self):
        assert self.props[P.STATUS] == {"status": {"name": "Posted"}}

    def test_payee_encoded(self):
        assert self.props[P.PAYEE] == {"rich_text": [{"text": {"content": "Starbucks Coffee"}}]}

    def test_memo_encoded(self):
        assert self.props[P.MEMO] == {"rich_text": [{"text": {"content": "Morning coffee"}}]}

    def test_bank_category_encoded(self):
        assert self.props[P.BANK_CATEGORY] == {"rich_text": [{"text": {"content": "Food & Drink"}}]}

    def test_category_encoded(self):
        assert self.props[P.CATEGORY] == {"select": {"name": "Dining"}}

    def test_bank_encoded(self):
        assert self.props[P.BANK] == {"select": {"name": "Bilt"}}

    def test_credit_card_account_encoded(self):
        assert self.props[P.CREDIT_CARD_ACCOUNT] == {"select": {"name": "Bilt Mastercard"}}

    def test_card_network_encoded(self):
        assert self.props[P.CARD_NETWORK] == {"select": {"name": "Mastercard"}}

    def test_account_type_encoded(self):
        assert self.props[P.ACCOUNT_TYPE] == {"select": {"name": "Credit Card"}}

    def test_account_name_encoded(self):
        assert self.props[P.ACCOUNT_NAME] == {
            "rich_text": [{"text": {"content": "Bilt World Elite"}}]
        }

    def test_source_id_encoded(self):
        assert self.props[P.SOURCE_ID] == {
            "rich_text": [{"text": {"content": "src-001"}}]
        }

    def test_source_account_id_encoded(self):
        assert self.props[P.SOURCE_ACCOUNT_ID] == {"rich_text": [{"text": {"content": "acct-001"}}]}

    def test_calculated_rewards_encoded(self):
        assert self.props[P.CALCULATED_REWARDS] == {"number": 0.1725}

    def test_true_rewards_encoded(self):
        assert self.props[P.TRUE_REWARDS] == {"number": 0.115}

    def test_rewards_type_encoded(self):
        assert self.props[P.REWARDS_TYPE] == {"select": {"name": "Cashback"}}

    def test_bilt_points_encoded(self):
        assert self.props[P.BILT_POINTS] == {"number": 5.75}

    def test_bilt_partner_true_encoded(self):
        assert self.props[P.BILT_PARTNER] == {"checkbox": True}

    def test_investment_fields_absent_when_none(self):
        assert P.QUANTITY not in self.props
        assert P.TICKER not in self.props
        assert P.PRICE_PER_SHARE not in self.props

    def test_excluded_computed_fields_not_present(self):
        assert P.RELATED_TRANSACTIONS not in self.props
        assert P.RELATED_TRANSACTIONS_AMOUNT not in self.props
        assert P.NET_AMOUNT not in self.props
        assert P.RELEASE_DATE not in self.props


# ---------------------------------------------------------------------------
# Tests: encode_transaction — sparse record
# ---------------------------------------------------------------------------


class TestEncodeTransactionSparseRecord:
    def setup_method(self):
        self.record = make_sparse_record()
        self.props = encode_transaction(self.record)

    def test_required_fields_present(self):
        assert P.NAME in self.props
        assert P.AMOUNT in self.props
        assert P.DATE in self.props
        assert P.STATUS in self.props
        assert P.SOURCE_ID in self.props
        assert P.SOURCE_ACCOUNT_ID in self.props

    def test_payee_absent_when_empty_string(self):
        assert P.PAYEE not in self.props

    def test_memo_absent_when_empty_string(self):
        assert P.MEMO not in self.props

    def test_bank_category_absent_when_none(self):
        assert P.BANK_CATEGORY not in self.props

    def test_category_absent_when_none(self):
        assert P.CATEGORY not in self.props

    def test_bank_absent_when_none(self):
        assert P.BANK not in self.props

    def test_credit_card_account_absent_when_none(self):
        assert P.CREDIT_CARD_ACCOUNT not in self.props

    def test_card_network_absent_when_none(self):
        assert P.CARD_NETWORK not in self.props

    def test_account_type_absent_when_none(self):
        assert P.ACCOUNT_TYPE not in self.props

    def test_account_name_absent_when_empty_string(self):
        assert P.ACCOUNT_NAME not in self.props

    def test_calculated_rewards_absent_when_none(self):
        assert P.CALCULATED_REWARDS not in self.props

    def test_true_rewards_absent_when_none(self):
        assert P.TRUE_REWARDS not in self.props

    def test_bilt_points_absent_when_none(self):
        assert P.BILT_POINTS not in self.props

    def test_bilt_partner_false_is_present(self):
        assert P.BILT_PARTNER in self.props
        assert self.props[P.BILT_PARTNER] == {"checkbox": False}

    def test_quantity_absent_when_none(self):
        assert P.QUANTITY not in self.props

    def test_ticker_absent_when_none(self):
        assert P.TICKER not in self.props

    def test_price_per_share_absent_when_none(self):
        assert P.PRICE_PER_SHARE not in self.props


# ---------------------------------------------------------------------------
# Tests: StrEnum encoding
# ---------------------------------------------------------------------------


class TestStrEnumEncoding:
    def test_status_pending_encodes_by_value(self):
        record = make_sparse_record()
        record.status = TransactionStatus.PENDING
        props = encode_transaction(record)
        assert props[P.STATUS] == {"status": {"name": "Pending"}}

    def test_status_released_encodes_by_value(self):
        record = make_sparse_record()
        record.status = TransactionStatus.RELEASED
        props = encode_transaction(record)
        assert props[P.STATUS] == {"status": {"name": "Released"}}

    def test_bank_name_encodes_by_value(self):
        record = make_sparse_record()
        record.bank = BankName.WELLS_FARGO
        props = encode_transaction(record)
        assert props[P.BANK] == {"select": {"name": "Wells Fargo"}}

    def test_card_network_visa_encodes_by_value(self):
        record = make_sparse_record()
        record.card_network = CardNetwork.VISA
        props = encode_transaction(record)
        assert props[P.CARD_NETWORK] == {"select": {"name": "Visa"}}

    def test_account_type_brokerage_encodes_by_value(self):
        record = make_sparse_record()
        record.account_type = AccountType.BROKERAGE
        props = encode_transaction(record)
        assert props[P.ACCOUNT_TYPE] == {"select": {"name": "Brokerage"}}

    def test_category_groceries_encodes_by_value(self):
        record = make_sparse_record()
        record.category = CanonicalCategory.GROCERIES
        props = encode_transaction(record)
        assert props[P.CATEGORY] == {"select": {"name": "Groceries"}}


# ---------------------------------------------------------------------------
# Tests: investment record shape
# ---------------------------------------------------------------------------


class TestInvestmentRecord:
    def setup_method(self):
        self.record = make_investment_record()
        self.props = encode_transaction(self.record)

    def test_quantity_encoded(self):
        assert self.props[P.QUANTITY] == {"number": 3.5}

    def test_ticker_encoded(self):
        # Live schema: Ticker is a select (auto-creates the option on write).
        assert self.props[P.TICKER] == {"select": {"name": "TSLA"}}

    def test_price_per_share_encoded(self):
        assert self.props[P.PRICE_PER_SHARE] == {"number": 142.86}

    def test_category_absent_when_none(self):
        assert P.CATEGORY not in self.props

    def test_calculated_rewards_absent_when_none(self):
        assert P.CALCULATED_REWARDS not in self.props

    def test_true_rewards_absent_when_none(self):
        assert P.TRUE_REWARDS not in self.props

    def test_bilt_points_absent_when_none(self):
        assert P.BILT_POINTS not in self.props


# ---------------------------------------------------------------------------
# Tests: checkbox always emitted
# ---------------------------------------------------------------------------


class TestCheckboxAlwaysEmitted:
    def test_bilt_partner_false_emitted(self):
        record = make_sparse_record()
        assert record.bilt_partner is False
        props = encode_transaction(record)
        assert P.BILT_PARTNER in props
        assert props[P.BILT_PARTNER] == {"checkbox": False}

    def test_bilt_partner_true_emitted(self):
        record = make_sparse_record()
        record.bilt_partner = True
        props = encode_transaction(record)
        assert P.BILT_PARTNER in props
        assert props[P.BILT_PARTNER] == {"checkbox": True}

    def test_excluded_from_spending_false_emitted(self):
        record = make_sparse_record()
        assert record.excluded_from_spending is False
        props = encode_transaction(record)
        assert props[P.EXCLUDED] == {"checkbox": False}

    def test_excluded_from_spending_true_emitted(self):
        record = make_sparse_record()
        record.excluded_from_spending = True
        props = encode_transaction(record)
        assert props[P.EXCLUDED] == {"checkbox": True}


class TestReviewStatusEncoding:
    def test_review_status_omitted_when_none(self):
        record = make_sparse_record()
        assert record.review_status is None
        props = encode_transaction(record)
        assert P.REVIEW_STATUS not in props

    def test_review_status_emitted_when_set(self):
        from notion_finance_sync.models import ReviewStatus

        record = make_sparse_record()
        record.review_status = ReviewStatus.NEEDS_REVIEW
        props = encode_transaction(record)
        assert props[P.REVIEW_STATUS] == {"status": {"name": "Needs Review"}}

    def test_review_status_reviewed_value(self):
        from notion_finance_sync.models import ReviewStatus

        record = make_sparse_record()
        record.review_status = ReviewStatus.REVIEWED
        props = encode_transaction(record)
        assert props[P.REVIEW_STATUS] == {"status": {"name": "Reviewed"}}


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
        assert P.CATEGORY not in props
        assert P.BANK not in props

    def test_bilt_partner_always_present(self):
        record = make_sparse_record()
        props = encode_transaction(record)
        assert P.BILT_PARTNER in props


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
        assert body["properties"][P.QUANTITY] == {"number": 3.5}
        assert body["properties"][P.TICKER] == {"select": {"name": "TSLA"}}
        assert body["properties"][P.PRICE_PER_SHARE] == {"number": 142.86}
        assert P.CATEGORY not in body["properties"]
