"""Generate notion/properties.py — pin each field's Notion property ID.

Notion property IDs are STABLE across renames, so the sync references IDs (not
display names) and a rename in the Notion UI never breaks a write/read. Run this
once, and again only if the data source is recreated (recreation mints new IDs).

    uv run scripts/gen_property_ids.py
"""

from __future__ import annotations

import asyncio

import httpx

from notion_finance_sync.config.settings import (
    NOTION_API_VERSION,
    NOTION_TRANSACTIONS_DATA_SOURCE_ID,
    get_notion_api_key,
)

# Semantic constant -> current Notion display name. The NAME can change freely in
# the UI; we resolve it to a stable ID here and pin the ID. Keep this list in sync
# with the fields the code actually reads/writes.
SEMANTIC_TO_NAME: dict[str, str] = {
    "NAME": "Name",
    "AMOUNT": "Txn Amount",
    "DATE": "Transaction Date",
    "STATUS": "Transaction Status",
    "SOURCE_ID": "Transaction Source ID",
    "SOURCE_ACCOUNT_ID": "Source Account ID",
    "PAYEE": "Payee",
    "MEMO": "Memo",
    "BANK_CATEGORY": "Bank Category",
    "CATEGORY": "Category",
    "BANK": "Bank",
    "CREDIT_CARD_ACCOUNT": "Credit Card / Account",
    "CARD_NETWORK": "Card Network",
    "ACCOUNT_TYPE": "Account Type",
    "ACCOUNT_NAME": "Account Name",
    "CALCULATED_REWARDS": "Calculated Rewards",
    "TRUE_REWARDS": "True Rewards",
    "REWARDS_TYPE": "Rewards Type",
    "BILT_POINTS": "Bilt Points",
    "BILT_PARTNER": "Bilt Partner",
    "EXCLUDED": "Excluded",
    "QUANTITY": "Qty",
    "TICKER": "Ticker",
    "PRICE_PER_SHARE": "PPS",
    "REVIEW_STATUS": "Review Status",
    "RELEASE_DATE": "Release Date",
    # Computed / manual fields the encoder must NEVER write (guarded by tests).
    "NET_AMOUNT": "Net Amount",
    "RELATED_TRANSACTIONS": "Related Transactions",
    "RELATED_TRANSACTIONS_AMOUNT": "Related Transactions Amount",
}


async def main() -> None:
    key = get_notion_api_key()
    url = f"https://api.notion.com/v1/data_sources/{NOTION_TRANSACTIONS_DATA_SOURCE_ID}"
    headers = {"Authorization": f"Bearer {key}", "Notion-Version": NOTION_API_VERSION}
    async with httpx.AsyncClient(timeout=30) as c:
        props = (await c.get(url, headers=headers)).json()["properties"]

    name_to_id = {name: p["id"] for name, p in props.items()}
    missing = [n for n in SEMANTIC_TO_NAME.values() if n not in name_to_id]
    if missing:
        raise SystemExit(f"These names are not in the live schema: {missing}")

    # Print the config.toml block to paste under [notion] (keeps the IDs out of
    # source — they live in gitignored config.toml).
    print("# Paste this into config.toml, replacing the existing [notion.property_ids]:\n")
    print("[notion.property_ids]")
    for const, name in SEMANTIC_TO_NAME.items():
        print(f'{const} = "{name_to_id[name]}"')


if __name__ == "__main__":
    asyncio.run(main())
