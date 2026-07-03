"""Notion property encoders for TransactionRecord.

Converts a TransactionRecord into the property JSON shape required by the
Notion API for POST /v1/pages (create) and PATCH /v1/pages/{id} (update).

Fields excluded from encoding (manual or formula-computed in Notion):
- Related Transactions  (relation — set manually)
- Related Transactions Amount  (rollup — computed)
- Net Amount  (formula — computed)
- Release Date  (set only by release_transaction in orphan.py)
"""

from __future__ import annotations

from typing import Any

from notion_finance_sync.models.transactions import TransactionRecord


def _title(value: str) -> dict[str, Any]:
    return {"title": [{"text": {"content": value}}]}


def _rich_text(value: str) -> dict[str, Any]:
    return {"rich_text": [{"text": {"content": value}}]}


def _number(value: float) -> dict[str, Any]:
    return {"number": value}


def _select(value: str) -> dict[str, Any]:
    return {"select": {"name": value}}


def _status(value: str) -> dict[str, Any]:
    return {"status": {"name": value}}


def _date(value: str) -> dict[str, Any]:
    return {"date": {"start": value}}


def _checkbox(value: bool) -> dict[str, Any]:
    return {"checkbox": value}


def encode_transaction(record: TransactionRecord) -> dict[str, Any]:
    """Return the ``properties`` dict for both POST /v1/pages and PATCH /v1/pages/{id}.

    Notion treats absent fields as "leave unchanged" so omitting None/empty
    values is correct for both create and update operations.
    """
    props: dict[str, Any] = {}

    props["Name"] = _title(record.name)
    props["Transaction Amount"] = _number(record.amount)
    props["Transaction Date"] = _date(record.transaction_date.isoformat())
    props["Transaction Status"] = _status(record.status.value)
    props["Transaction Source ID"] = _rich_text(record.source_id)
    props["Source Account ID"] = _rich_text(record.source_account_id)

    props["Bilt Partner"] = _checkbox(record.bilt_partner)
    props["Excluded from Spending"] = _checkbox(record.excluded_from_spending)

    if record.payee:
        props["Payee"] = _rich_text(record.payee)

    if record.memo:
        props["Memo"] = _rich_text(record.memo)

    if record.bank_category is not None:
        props["Bank Category"] = _rich_text(record.bank_category)

    if record.category is not None:
        props["Category"] = _select(record.category.value)

    if record.bank is not None:
        props["Bank"] = _select(record.bank.value)

    if record.credit_card_account is not None:
        props["Credit Card / Account"] = _select(record.credit_card_account)

    if record.card_network is not None:
        props["Card Network"] = _select(record.card_network.value)

    if record.account_type is not None:
        props["Account Type"] = _select(record.account_type.value)

    if record.account_name:
        props["Account Name"] = _rich_text(record.account_name)

    if record.calculated_rewards is not None:
        props["Calculated Rewards"] = _number(record.calculated_rewards)

    if record.true_rewards is not None:
        props["True Rewards"] = _number(record.true_rewards)

    if record.bilt_points is not None:
        props["Bilt Points"] = _number(record.bilt_points)

    if record.quantity is not None:
        props["Quantity"] = _number(record.quantity)

    if record.ticker is not None:
        props["Ticker"] = _rich_text(record.ticker)

    if record.price_per_share is not None:
        props["Price Per Share"] = _number(record.price_per_share)

    if record.review_status is not None:
        props["Review Status"] = _status(record.review_status.value)

    return props
