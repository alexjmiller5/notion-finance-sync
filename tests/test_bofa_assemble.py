"""Test the card assembler: statement row + per-txn detail + rewards -> complete record."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from notion_finance_sync.banks.bofa import assemble, card, rewards
from notion_finance_sync.models import CanonicalCategory

FX = Path(__file__).parent / "fixtures" / "bofa"


@pytest.fixture
def statement_html() -> str:
    return (FX / "card_statement.html").read_text()


@pytest.fixture
def detail_html() -> str:
    return (FX / "card_txn_detail.html").read_text()


@pytest.fixture
def rewards_html() -> str:
    return (FX / "rewards_landing.html").read_text()


def test_enrich_card_records_adds_category_merchant_and_rewards(
    statement_html, detail_html, rewards_html
):
    records = card.parse_statement(statement_html)
    first = records[0]  # PARADISE MARKET MIKONOS, $4.78
    detail_map = {first.raw_data["detail_txn_hash"]: detail_html}
    entries = rewards.parse_rewards(rewards_html)

    enriched = assemble.enrich_card_records(records, detail_map, entries)
    assert enriched is records  # mutates in place, returns same list

    # category + merchant from the detail fixture
    assert first.bank_category == "Groceries: Groceries"
    assert first.category == CanonicalCategory.GROCERIES
    assert first.raw_data["merchant_name"] == "PARADISE MARKET"
    assert first.raw_data["merchant_description"] == "GROCERY STORES, SUPERMARKETS"
    # detail carries the *true* transaction date (06/24) vs the list's posting date
    assert first.transaction_date == date(2026, 6, 24)
    # rewards matched by merchant+amount -> points on true_rewards
    assert first.true_rewards == 12.55
    assert first.raw_data["base_points"] == 7.17
    assert first.raw_data["bonus_points"] == 5.38


def test_records_without_detail_are_left_categoryless(statement_html, rewards_html):
    records = card.parse_statement(statement_html)
    entries = rewards.parse_rewards(rewards_html)
    assemble.enrich_card_records(records, {}, entries)  # no detail supplied
    # a row with no detail keeps category None (=> Needs Review downstream)
    assert records[0].category is None
