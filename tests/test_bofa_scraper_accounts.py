"""Guard: every BofA account maps to a real Notion 'Credit Card / Account' option.

Writing an unknown select value would auto-create a duplicate option and pollute
the DB, so the scraper must map its account names to the curated Notion values.
The option set below is copied from the live Transactions data source (2026-07-02).
"""

from __future__ import annotations

from notion_finance_sync.banks.bofa import scraper
from notion_finance_sync.models import RewardsType

# Live Notion "Credit Card / Account" select options.
NOTION_CREDIT_CARD_ACCOUNT_OPTIONS = {
    "Wells Fargo Autograph",
    "Performance Savings",
    "AQHA Customized Cash Rewards",
    "Susan G. Komen Customized Cash Rewards",
    "Travel Rewards",
    "Unlimited Cash Rewards",
    "NEA Customized Cash Rewards",
    "Advantage Plus",
    "Cash+ Visa Signature",
    "Harris Teeter Rewards World Elite",
    "Bilt Blue Card",
}


def test_every_account_has_a_notion_mapping():
    for name in list(scraper.CARD_ACCOUNTS) + list(scraper.DEPOSIT_ACCOUNTS):
        assert name in scraper.NOTION_ACCOUNT, f"no Notion account mapping for {name!r}"


def test_mapped_values_are_real_notion_options():
    for name, notion_value in scraper.NOTION_ACCOUNT.items():
        assert notion_value in NOTION_CREDIT_CARD_ACCOUNT_OPTIONS, (
            f"{name!r} -> {notion_value!r} is not an existing Notion option"
        )


def test_every_card_has_a_rewards_type():
    # Every credit card earns either cashback or points — Notion's Rewards Type
    # should never be blank for a card. Travel Rewards is points; the rest cashback.
    for name in scraper.CARD_ACCOUNTS:
        assert name in scraper.CARD_REWARD_TYPE, f"no rewards type for {name!r}"
    assert scraper.CARD_REWARD_TYPE["Travel Rewards Visa Signature - 9766"] == RewardsType.POINTS
    assert (
        scraper.CARD_REWARD_TYPE["Unlimited Cash Rewards Visa Signature - 3510"]
        == RewardsType.CASHBACK
    )
