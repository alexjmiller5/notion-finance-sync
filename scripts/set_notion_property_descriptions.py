"""Set concise descriptions on every Transactions-DB property.

Notion's data_sources PATCH requires the property's TYPE key alongside
``description`` (a plain string), and an empty type config WIPES select options
— so we round-trip each property's existing config. One PATCH per property so a
single failure isolates instead of sinking the batch.
"""
# ruff: noqa: E501 — description strings are intentionally single-line

from __future__ import annotations

import asyncio

import httpx

from notion_finance_sync.config.settings import (
    NOTION_API_VERSION,
    NOTION_TRANSACTIONS_DATA_SOURCE_ID,
    get_notion_api_key,
)

DESCRIPTIONS: dict[str, str] = {
    # --- core ---
    "Name": "Transaction title — merchant/payee or the description from the bank feed.",
    "Transaction Amount": "Signed dollars: negative = money out (spend/debit), positive = money in (payment/credit/income).",
    "Transaction Date": "Date the transaction occurred/posted (the bank's true date when the detail provides it).",
    "Transaction Status": "Pending, Posted, or Released (Released = a pending row that vanished before posting).",
    "Release Date": "Date a pending transaction was released — set only when it disappeared before posting.",
    # --- source / account ---
    "Bank": "Source bank/provider the transaction came from.",
    "Credit Card / Account": "The specific card or account this transaction belongs to.",
    "Card Network": "Visa or Mastercard — credit-card transactions only.",
    "Account Type": "Account kind: Credit Card, Debit Card, Checking, Savings, P2P, Brokerage, 401k, or IRA.",
    "Account Name": "Full descriptive account name from the bank, e.g. 'Adv Plus Banking - 2093'.",
    "Source Account ID": "Bank-native account id (e.g. BofA adx token) for the source account — do not edit.",
    "Transaction Source ID": "Stable content-hash key used to dedupe/match rows across syncs — do not edit.",
    "Review Status": "Reconciliation triage: Needs Review / Reviewed / Needs Attention (auto-set on write).",
    # --- description / category ---
    "Payee": "Cleaned merchant name (e.g. 'PARADISE MARKET' from a noisy statement line).",
    "Memo": "Raw statement description exactly as the bank reported it (uncleaned).",
    "Category": "Normalized spending category, mapped from the bank's own category into a consistent set.",
    "Bank Category": "The bank's own raw category label, kept verbatim (audit trail for the Category mapping).",
    # --- rewards ---
    "Calculated Rewards": "Rewards this transaction should earn, estimated from the card's reward rules.",
    "True Rewards": "Actual rewards/points the bank credited, scraped from the rewards page.",
    "Bilt Partner": "Checked when the merchant is a Bilt rewards partner (bonus-earning).",
    "Bilt Points": "Bilt points earned on this transaction.",
    # --- investments ---
    "Ticker": "Security ticker symbol — investment/brokerage transactions only.",
    "Quantity": "Number of shares/units — investment transactions only.",
    "Price Per Share": "Execution price per share/unit — investment transactions only.",
    # --- manual / computed ---
    "Excluded from Spending": "Check to keep this row out of spending totals (transfers, card payments, reimbursements).",
    "Tags": "Freeform manual tags for ad-hoc grouping/filtering.",
    "Net Amount": "Formula: Transaction Amount net of linked Related Transactions (e.g. after a reimbursement/settlement).",
    "Rewards (if any)": "Formula: shows earned rewards (cashback or points) for quick scanning.",
    "Related Transactions": "Manual links to related rows — e.g. the two legs of a transfer or a trip settlement.",
    "Related to Transactions (Related Transactions)": "Auto back-reference: rows that link to this one via Related Transactions.",
    "Related Transactions Amount": "Rollup: sum of the amounts of the linked Related Transactions.",
    # --- delete candidates (flagged in the description) ---
    "Transacted At": "(Unused — always empty; DELETE CANDIDATE, superseded by Transaction Date.)",
    "Cashback Percentage": "(Not written by the scraper — DELETE CANDIDATE.)",
    "Rewards Type": "(Not written by the scraper — DELETE CANDIDATE.)",
    "Data Source Leader": "(Legacy SimpleFIN/LunchFlow merge field — unused by current scraper; DELETE CANDIDATE.)",
    "Data Source Log": "(Legacy dual-provider merge log — unused; DELETE CANDIDATE.)",
    "Descriptions Match": "(Legacy: whether SimpleFIN & LunchFlow descriptions agreed — unused; DELETE CANDIDATE.)",
    "Description Diff": "(Legacy: diff between provider descriptions — unused; DELETE CANDIDATE.)",
}

# Restore options we wiped while probing the API (this field is a delete candidate).
_RESTORE_OPTIONS = {"Data Source Leader": ["SimpleFIN", "LunchFlow", "Equal"]}


def _type_config(name: str, prop: dict) -> dict:
    """Existing type config to round-trip so a description update preserves it."""
    t = prop["type"]
    if t in ("select", "multi_select"):
        opts = prop[t].get("options", [])
        if name in _RESTORE_OPTIONS and not opts:
            opts = [{"name": o} for o in _RESTORE_OPTIONS[name]]
        return {t: {"options": opts}}
    if t == "status":
        return {"status": {}}  # status options are managed by Notion; {} is a no-op here
    if t == "number":
        return {"number": prop["number"]}
    if t == "formula":
        return {"formula": prop["formula"]}
    if t == "rollup":
        return {"rollup": prop["rollup"]}
    if t == "relation":
        return {"relation": prop["relation"]}
    return {t: {}}  # rich_text, date, checkbox, title — no config to preserve


async def main() -> None:
    key = get_notion_api_key()
    url = f"https://api.notion.com/v1/data_sources/{NOTION_TRANSACTIONS_DATA_SOURCE_ID}"
    headers = {
        "Authorization": f"Bearer {key}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as cli:
        schema = (await cli.get(url, headers=headers)).json()["properties"]
        ok, failed = 0, []
        for name, desc in DESCRIPTIONS.items():
            prop = schema.get(name)
            if prop is None:
                failed.append((name, "not in schema"))
                continue
            body = {"properties": {name: {**_type_config(name, prop), "description": desc}}}
            r = await cli.patch(url, headers=headers, json=body)
            if r.status_code == 200:
                ok += 1
            else:
                failed.append((name, f"{r.status_code} {r.text[:120]}"))
        print(f"described: {ok}/{len(DESCRIPTIONS)}")
        for n, e in failed:
            print(f"  FAILED {n!r}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
