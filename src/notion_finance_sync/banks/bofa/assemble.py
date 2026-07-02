"""Assemble complete card transactions from the three BofA sources.

A card ``TransactionRecord`` is only *complete* once it carries:
- the base fields from the statement list (``card.parse_statement``),
- the category/merchant/MCC from the per-txn detail (``card.parse_detail``),
- the points (base + bonus) from the rewards page (``rewards.parse_rewards``).

``enrich_card_records`` mutates the statement records in place with the detail
and rewards data. It's a pure function (no I/O) so it's unit-tested against the
captured fixtures; the fetchers supply the detail-HTML-by-hash map and reward
entries at runtime.
"""

from __future__ import annotations

from notion_finance_sync.banks.bofa import card as card_parser
from notion_finance_sync.banks.bofa import rewards as rewards_parser
from notion_finance_sync.models import CardNetwork, TransactionRecord

_CARD_NETWORKS = {
    "visa": CardNetwork.VISA,
    "mastercard": CardNetwork.MASTERCARD,
}


def enrich_card_records(
    records: list[TransactionRecord],
    detail_html_by_hash: dict[str, str],
    reward_entries: list[dict],
) -> list[TransactionRecord]:
    """Enrich statement records with per-txn detail + rewards (in place)."""
    # 1. Detail enrichment (category label + canonical, merchant, MCC, true date)
    for rec in records:
        txn_hash = rec.raw_data.get("detail_txn_hash")
        html = detail_html_by_hash.get(txn_hash) if txn_hash else None
        if not html:
            continue
        detail = card_parser.parse_detail(html)
        if detail.get("bank_category"):
            rec.bank_category = detail["bank_category"]
        if detail.get("category"):
            rec.category = detail["category"]
        if detail.get("transaction_date"):
            # keep the list's posting date, promote the detail's true txn date
            rec.raw_data["posting_date"] = (
                rec.transaction_date.isoformat() if rec.transaction_date else None
            )
            rec.transaction_date = detail["transaction_date"]
        if detail.get("card_type"):
            rec.card_network = _CARD_NETWORKS.get(detail["card_type"].strip().lower())
        for k in (
            "merchant_name",
            "merchant_description",
            "reference_number",
            "online_purchase",
            "card_type",
        ):
            if detail.get(k) is not None:
                rec.raw_data[k] = detail[k]
        # Two description fields (Alex's ask): Payee = cleaned merchant name,
        # Memo/Name keep the raw statement description.
        if detail.get("merchant_name"):
            rec.payee = detail["merchant_name"]

    # 2. Rewards enrichment (points -> true_rewards, base/bonus in raw_data).
    #    Run after detail so records carry the true transaction date for matching.
    rewards_parser.match_rewards(records, reward_entries)
    return records


def dedupe_by_source_id(records: list[TransactionRecord]) -> list[TransactionRecord]:
    """Drop duplicate records sharing a ``source_id`` (keep first seen).

    Historical scraping fetches overlapping statement periods; the bank-native
    ``source_id`` (from each row's HTML comment) is the stable dedupe key. Rows
    with an empty source_id are kept as-is (can't be deduped).
    """
    seen: set[str] = set()
    out: list[TransactionRecord] = []
    for r in records:
        sid = r.source_id
        if sid and sid in seen:
            continue
        if sid:
            seen.add(sid)
        out.append(r)
    return out
