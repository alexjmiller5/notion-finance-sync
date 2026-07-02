"""Offline dry-run: process the captured BofA backfill through the parsers.

Runs every captured statement (5 cards) + the checking JSON through the real
parsers/assembler and prints what WOULD be written to Notion — without any live
login or Notion write. Doubles as integration QA: it exercises the parsers on
the full real dataset (payments, refunds, fees, foreign txns, ...), not just the
single unit-test fixtures.

    uv run python scripts/dry_run_bofa_backfill.py

Note: per-transaction card *category* needs the detail endpoint (not captured for
every txn in the backfill), so card categories show mostly blank here; checking
categories are inline and fully populated. This is a parsing/volume check.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from notion_finance_sync.banks.bofa import card, deposit, rewards
from notion_finance_sync.banks.bofa.rewards import match_rewards

BACKFILL = Path("data/snapshots/bofa/backfill")


def _unwrap(d):
    return d["result"] if isinstance(d, dict) and set(d) == {"result"} else d


def _date_range(recs):
    dates = sorted(r.transaction_date for r in recs if r.transaction_date)
    return (dates[0], dates[-1]) if dates else (None, None)


def dry_run_checking() -> int:
    f = BACKFILL / "checking_2093_activity.json"
    if not f.exists():
        print("  (no checking capture)")
        return 0
    data = _unwrap(json.loads(f.read_text()))
    raw = {
        "payload": {"depositActivity": {"transactionList": {"transactions": data["transactions"]}}}
    }
    recs = deposit.parse_activity(raw, account_name="Adv Plus Banking - 2093")
    lo, hi = _date_range(recs)
    cats = Counter((r.category.value if r.category else "—") for r in recs)
    print(f"\n[CHECKING] Adv Plus Banking - 2093: {len(recs)} txns  {lo} → {hi}")
    print(f"  category mix: {dict(cats.most_common())}")
    neg = sum(1 for r in recs if r.amount < 0)
    pos = sum(1 for r in recs if r.amount > 0)
    zero = sum(1 for r in recs if r.amount == 0)
    print(f"  signed-amount sanity: {neg} debits / {pos} credits / {zero} zero")
    for r in recs[:3]:
        print(f"    {r.transaction_date} {r.amount:+9.2f} {r.category!s:10} {r.name[:44]!r}")
    return len(recs)


def dry_run_cards() -> int:
    total = 0
    for f in sorted(BACKFILL.glob("card_*.json")):
        if f.stem.endswith("_rewards_landing") or f.stem.endswith("_details_sample"):
            continue
        data = _unwrap(json.loads(f.read_text()))
        name = data.get("card", f.stem)
        rows_all = []
        for label, html in data.get("htmlByLabel", {}).items():
            rows_all.extend(card.parse_statement(html))
        # rewards (current period only) if we captured the landing for this card
        rfile = BACKFILL / f"{f.stem}_rewards_landing.json"
        matched = 0
        if rfile.exists():
            rhtml = _unwrap(json.loads(rfile.read_text())).get("htmlFull", "")
            matched = match_rewards(rows_all, rewards.parse_rewards(rhtml))
        lo, hi = _date_range(rows_all)
        types = Counter(r.raw_data.get("txn_type", "?") for r in rows_all)
        print(
            f"\n[CARD] {name}: {len(rows_all)} txns across "
            f"{len(data.get('htmlByLabel', {}))} statements  {lo} → {hi}"
        )
        print(f"  type-icon codes: {dict(types)}  | rewards matched: {matched}")
        blank_id = sum(1 for r in rows_all if not r.source_id)
        if blank_id:
            print(f"  ⚠️ {blank_id} rows had no source_id (check parser)")
        for r in rows_all[:2]:
            pts = r.true_rewards
            print(f"    {r.transaction_date} {r.amount:+9.2f} pts={pts} {r.name[:40]!r}")
        total += len(rows_all)
    return total


def main() -> int:
    print("=" * 74)
    print("BofA backfill DRY RUN — parsing all captured data (no login, no writes)")
    print("=" * 74)
    n_check = dry_run_checking()
    n_cards = dry_run_cards()
    print("\n" + "=" * 74)
    print(
        f"TOTAL records that would flow to Notion: {n_check + n_cards} "
        f"(checking {n_check} + cards {n_cards})"
    )
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
