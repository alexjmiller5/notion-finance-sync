"""Idempotently add the 'Bilt World Elite Mastercard' option to the Transactions DB's
'Credit Card / Account' select property.

This is the pre-conversion name of Alex's Wells Fargo Autograph card; all WF statement
(old-Bilt-era) transactions are labelled with it. Run once before the WF backfill so the
option exists deliberately rather than being auto-created on first write.

    export OP_SERVICE_ACCOUNT_TOKEN=... ; PYTHONPATH=src uv run python scripts/add_wf_card_option.py
"""

from __future__ import annotations

import sys

import httpx

from notion_finance_sync.config.settings import (
    NOTION_API_VERSION,
    NOTION_TRANSACTIONS_DATA_SOURCE_ID,
    get_notion_api_key,
)

PROPERTY = "Credit Card / Account"
OPTION = "Bilt World Elite Mastercard"


def main() -> int:
    headers = {
        "Authorization": f"Bearer {get_notion_api_key()}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }
    url = f"https://api.notion.com/v1/data_sources/{NOTION_TRANSACTIONS_DATA_SOURCE_ID}"
    with httpx.Client(timeout=30.0) as client:
        schema = client.get(url, headers=headers)
        schema.raise_for_status()
        prop = schema.json()["properties"].get(PROPERTY)
        if not prop or "select" not in prop:
            print(f"ERROR: property {PROPERTY!r} not found or not a select")
            return 1
        options = prop["select"]["options"]
        names = {o["name"] for o in options}
        if OPTION in names:
            print(f"[ok] {OPTION!r} already present ({len(names)} options) — no change")
            return 0
        merged = options + [{"name": OPTION, "color": "default"}]
        patch = client.patch(
            url, headers=headers, json={"properties": {PROPERTY: {"select": {"options": merged}}}}
        )
        patch.raise_for_status()
        print(f"[added] {OPTION!r} -> {PROPERTY!r} (now {len(merged)} options)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
