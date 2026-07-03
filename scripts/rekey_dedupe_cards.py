"""Re-key BofA card rows to the new stable content-hash source_id + remove dupes.

Card source_id used to come from BofA's per-row reference, which is unstable
across statement views — so re-scraping (fetch_recent vs fetch_historical)
produced different ids for the same txn and duplicated rows. card.py now derives
a stable content hash. This one-shot reconciles Notion to that scheme:

  fresh historical scrape (stable ids) -> group by (card, date, amount)
  Notion card rows                     -> group by (card, date, amount)
  per group: re-key the first N Notion rows to the N fresh ids; ARCHIVE extras.

Idempotent: re-run leaves an already-clean DB untouched (0 updated, 0 archived).
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import date

from notion_finance_sync.backfill.runner import _make_client
from notion_finance_sync.banks import registry as bank_registry
from notion_finance_sync.models import AccountType


def _key(card: str, txn_date, amount) -> tuple[str, str, float] | None:
    if not card or txn_date is None or amount is None:
        return None
    d = txn_date.isoformat() if isinstance(txn_date, date) else str(txn_date)
    return (card, d, round(float(amount), 2))


async def main(since: date, *, dry_run: bool) -> None:
    scraper = bank_registry.get_scraper("bofa")
    records = await asyncio.to_thread(scraper.fetch_historical, since, date.today())
    cards = [r for r in records if r.account_type == AccountType.CREDIT_CARD]
    print(f"[scrape] {len(records)} records, {len(cards)} card records")

    fresh: dict[tuple, list[str]] = defaultdict(list)
    for r in cards:
        k = _key(r.credit_card_account, r.transaction_date, r.amount)
        if k:
            fresh[k].append(r.source_id)

    client = _make_client()
    existing = await client.get_existing_transactions(since_date=since.isoformat())
    notion: dict[tuple, list[tuple[str, str]]] = defaultdict(list)
    for sid, m in existing.items():
        if m.get("account_type") != "Credit Card":
            continue
        k = _key(m.get("credit_card_account"), m.get("transaction_date"), m.get("amount"))
        if k:
            notion[k].append((m["page_id"], sid))
    print(f"[notion] {sum(len(v) for v in notion.values())} card rows in {len(notion)} groups")

    rekey = 0
    archive = 0
    unmatched = 0
    for k, pages in notion.items():
        fresh_ids = fresh.get(k, [])
        if not fresh_ids:
            unmatched += len(pages)  # in Notion but not in this scrape window — leave it
            continue
        for i, (page_id, cur_sid) in enumerate(pages):
            if i < len(fresh_ids):
                if cur_sid != fresh_ids[i]:
                    rekey += 1
                    if not dry_run:
                        await client.update_transaction(
                            page_id,
                            {
                                "Transaction Source ID": {
                                    "rich_text": [{"text": {"content": fresh_ids[i]}}]
                                }
                            },
                        )
            else:
                archive += 1  # duplicate: no fresh id left for this group
                if not dry_run:
                    await client._request_with_retry(
                        "PATCH",
                        f"https://api.notion.com/v1/pages/{page_id}",
                        json={"archived": True},
                    )

    print(
        f"[done] {'DRY-RUN ' if dry_run else ''}rekeyed={rekey} archived={archive} "
        f"unmatched={unmatched}"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-06-01")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(date.fromisoformat(args.since), dry_run=args.dry_run))
