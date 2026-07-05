"""End-to-end (offline) pipeline: captured fixtures -> parse -> assemble -> Notion props.

Proves the whole BofA path up to the Notion API boundary without a live session:
raw statement/detail/rewards/deposit fixtures become fully-populated
``TransactionRecord``s, which ``encode_transaction`` turns into the exact Notion
property JSON the client will POST.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from notion_finance_sync.banks.bofa import assemble, card, deposit, rewards
from notion_finance_sync.notion.encoders import encode_transaction
from notion_finance_sync.notion.properties import P

FX = Path(__file__).parent / "fixtures" / "bofa"


@pytest.fixture
def card_first_record():
    statement = (FX / "card_statement.html").read_text()
    detail = (FX / "card_txn_detail.html").read_text()
    rewards_html = (FX / "rewards_landing.html").read_text()
    records = card.parse_statement(statement)
    first = records[0]  # PARADISE MARKET MIKONOS
    detail_map = {first.raw_data["detail_txn_hash"]: detail}
    entries = rewards.parse_rewards(rewards_html)
    assemble.enrich_card_records(records, detail_map, entries)
    return first


def test_card_record_encodes_to_expected_notion_properties(card_first_record):
    props = encode_transaction(card_first_record)

    assert props[P.NAME]["title"][0]["text"]["content"].startswith("PARADISE MARKET")
    assert props[P.AMOUNT]["number"] == -4.78
    assert props[P.DATE]["date"]["start"] == "2026-06-24"  # detail's true date
    assert props[P.BANK]["select"]["name"] == "Bank of America"
    assert props[P.ACCOUNT_TYPE]["select"]["name"] == "Credit Card"
    assert props[P.CATEGORY]["select"]["name"] == "Groceries"
    assert props[P.BANK_CATEGORY]["rich_text"][0]["text"]["content"] == "Groceries: Groceries"
    assert props[P.CARD_NETWORK]["select"]["name"] == "Visa"
    assert props[P.TRUE_REWARDS]["number"] == 12.55  # points (7.17 base + 5.38 bonus)
    # source_id is a stable content hash now (BofA's per-row ref is unstable across views)
    assert len(props[P.SOURCE_ID]["rich_text"][0]["text"]["content"]) == 64
    # two description fields: Payee = cleaned merchant, Memo = raw statement line
    assert props[P.PAYEE]["rich_text"][0]["text"]["content"] == "PARADISE MARKET"
    assert props[P.MEMO]["rich_text"][0]["text"]["content"] == "PARADISE MARKET MIKONOS"


def test_deposit_zelle_record_encodes_as_transfer():
    raw = json.loads((FX / "deposit_activity_raw.json").read_text())
    rec = deposit.parse_activity(raw, account_name="Adv Plus Banking - 2093")[0]
    props = encode_transaction(rec)

    assert props[P.AMOUNT]["number"] == -50.0
    assert props[P.BANK]["select"]["name"] == "Bank of America"
    assert props[P.ACCOUNT_TYPE]["select"]["name"] == "Checking"
    assert props[P.CATEGORY]["select"]["name"] == "Transfer"  # Zelle auto-Transfer
    assert (
        props[P.BANK_CATEGORY]["rich_text"][0]["text"]["content"]
        == "Cash, Checks & Misc: Other Expenses"
    )
    assert props[P.ACCOUNT_NAME]["rich_text"][0]["text"]["content"] == "Adv Plus Banking - 2093"
