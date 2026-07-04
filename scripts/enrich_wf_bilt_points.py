"""Enrich the backfilled Wells Fargo (old Bilt World Elite Mastercard) rows with Bilt points.

The WF statements don't list points, but Bilt's loyalty feed does — the old card earned
Bilt points as the primary card, so those show up as ``category=BILT_MC`` + ``EARNED``
entries in ``GET /loyalty/activity?month=M&year=Y`` (the per-month history behind
bilt.com/rewards/activity). This matches them onto the WF rows by merchant + date (±3d,
mirroring the Bilt scraper's ``match_true_rewards``) and writes ``True Rewards`` — the same
field/convention the Bilt Blue card uses for its own earned points.

Idempotent: re-running only updates rows whose ``True Rewards`` actually changed. Use
``--dry-run`` to preview matches without writing.

    export OP_SERVICE_ACCOUNT_TOKEN=... ; PYTHONPATH=src \
      uv run python scripts/enrich_wf_bilt_points.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import structlog

from notion_finance_sync.banks.wells_fargo.scraper import WellsFargoScraper
from notion_finance_sync.config.settings import (
    NOTION_API_VERSION,
    NOTION_TRANSACTIONS_DATA_SOURCE_ID,
    get_notion_api_key,
)
from notion_finance_sync.models import TransactionRecord
from notion_finance_sync.notion.client import NotionClient

logger = structlog.get_logger()

API_ORIGIN = "https://api.biltrewards.com"
KEYCLOAK_TOKEN_URL = "https://www.bilt.com/realms/BILT/protocol/openid-connect/token"
KEYCLOAK_CLIENT_ID = "identity-svc"
TOKENS_PATH = Path(__file__).resolve().parents[1] / "data" / "sessions" / "bilt" / "tokens.json"
_EASTERN = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Bilt loyalty feed
# ---------------------------------------------------------------------------
def _bilt_access_token() -> str:
    """Refresh a Bilt access token (persists the rotated pair back)."""
    tokens = json.loads(TOKENS_PATH.read_text())
    resp = httpx.post(
        KEYCLOAK_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": KEYCLOAK_CLIENT_ID,
            "refresh_token": tokens["refresh_token"],
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    new = resp.json()
    TOKENS_PATH.write_text(
        json.dumps({"access_token": new["access_token"], "refresh_token": new["refresh_token"]})
    )
    return new["access_token"]


def _months(start: date, end: date) -> list[tuple[int, int]]:
    """(month, year) tuples spanning [start, end], inclusive, +1 trailing month for
    points that post after the transaction cycle."""
    out: list[tuple[int, int]] = []
    y, m = start.year, start.month
    end_y, end_m = (end.year + (end.month // 12), (end.month % 12) + 1)  # one month past end
    while (y, m) <= (end_y, end_m):
        out.append((m, y))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def fetch_bilt_earn_entries(start: date, end: date) -> list[dict]:
    """Fetch ALL EARNED loyalty entries across the months covering [start, end].

    Every category counts (BILT_MC base + DINING/RIDESHARE/PHARMACY Neighborhood bonuses)
    because the OLD Bilt card earned them all on its own purchases. Entries that belong to
    a DIFFERENT card (e.g. a Lyft ride Alex paid with a BofA card) simply won't match any WF
    row — the amount+date+merchant key below is specific enough to exclude them.
    """
    token = _bilt_access_token()
    entries: list[dict] = []
    with httpx.Client(
        base_url=API_ORIGIN, headers={"Authorization": f"Bearer {token}"}, timeout=30.0
    ) as client:
        for month, year in _months(start, end):
            resp = client.get("/loyalty/activity", params={"month": month, "year": year})
            resp.raise_for_status()
            for e in resp.json().get("entries", []):
                if e.get("pointState") == "EARNED":
                    entries.append(e)
    return entries


# ---------------------------------------------------------------------------
# Matching: amount (exact) + date (±3d) + merchant-token overlap, one entry -> one row.
# WF descriptions carry city/state and processor prefixes (TST*, SQ *, UBER *); the loyalty
# rawTitle is the cleaner form. Exact amount + close date is the strong key; a shared
# merchant token guards against a coincidental same-amount purchase on another card.
# ---------------------------------------------------------------------------
_NOISE = {"NEW", "YORK", "THE", "LLC", "INC", "CORP", "CO", "AND", "TST", "SQ"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").upper()).strip()


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[A-Z0-9]{3,}", _norm(s)) if t not in _NOISE}


def _entry_date(e: dict) -> date:
    return datetime.fromisoformat(e["datetime"].replace("Z", "+00:00")).astimezone(_EASTERN).date()


def match_points(records: list[TransactionRecord], entries: list[dict]) -> int:
    """Set ``true_rewards`` on WF spend rows from matched loyalty entries. Returns match count."""
    spends = [r for r in records if r.amount < 0]
    matched: set[str] = set()
    count = 0
    for e in sorted(entries, key=_entry_date):
        e_date = _entry_date(e)
        amount = e.get("transactionAmount")
        e_tokens = _tokens(e.get("rawTitle", "")) | _tokens(e.get("title", ""))
        is_rent = e.get("title") == "Housing Points" or "RENT" in _norm(e.get("rawTitle", ""))

        def candidate(r: TransactionRecord) -> bool:
            if r.source_id in matched or abs((r.transaction_date - e_date).days) > 3:
                return False
            if is_rent:
                return "RENT" in _norm(r.name)
            if amount is None or abs(abs(r.amount) - float(amount)) > 0.01:
                return False
            return bool(_tokens(r.name) & e_tokens)

        pool = [r for r in spends if candidate(r)]
        rec = min(pool, key=lambda r: abs((r.transaction_date - e_date).days), default=None)
        if rec is not None:
            rec.true_rewards = float(e.get("totalPoints") or 0)
            matched.add(rec.source_id)
            count += 1
    return count


# ---------------------------------------------------------------------------
async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    records = WellsFargoScraper().parse_statements(WellsFargoScraper._statement_paths())
    if not records:
        print("No WF statement rows found (data/statements/wf/).")
        return 1
    lo = min(r.transaction_date for r in records)
    hi = max(r.transaction_date for r in records)

    entries = fetch_bilt_earn_entries(lo, hi)
    n = match_points(records, entries)
    enriched = [r for r in records if r.true_rewards is not None]
    print(
        f"WF rows: {len(records)} | loyalty earn entries: {len(entries)} | matched points on: {n}"
    )
    for r in sorted(enriched, key=lambda r: r.transaction_date):
        print(
            f"  {r.transaction_date}  {r.true_rewards:>7.0f} pts  {r.amount:>10.2f}  {r.name[:40]}"
        )
    unmatched_spend = [r for r in records if r.amount < 0 and r.true_rewards is None]
    if unmatched_spend:
        print(f"\n  {len(unmatched_spend)} spend rows got NO points match:")
        for r in unmatched_spend:
            print(f"    {r.transaction_date}  {r.amount:>10.2f}  {r.name[:40]}")

    if args.dry_run:
        print("\n[DRY RUN] no writes.")
        return 0

    # Targeted "True Rewards"-only PATCH per row (idempotent). We deliberately do NOT send the
    # full encoded record: the shared encoder emits "Excluded from Spending" but the live DB
    # names that property "Excluded" (schema drift) — a full update 400s. This only touches the
    # field we're enriching, which is also safer.
    client = NotionClient(
        api_key=get_notion_api_key(), data_source_id=NOTION_TRANSACTIONS_DATA_SOURCE_ID
    )
    existing = await client.get_existing_transactions(since_date=lo.isoformat())
    headers = {
        "Authorization": f"Bearer {get_notion_api_key()}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }
    updated = 0
    async with httpx.AsyncClient(timeout=30.0) as http:
        for r in enriched:
            row = existing.get(r.source_id)
            if row is None:
                logger.warning("wf_enrich_row_missing", source_id=r.source_id)
                continue
            if row.get("true_rewards") == r.true_rewards:
                continue  # already set — idempotent
            resp = await http.patch(
                f"https://api.notion.com/v1/pages/{row['page_id']}",
                headers=headers,
                json={"properties": {"True Rewards": {"number": r.true_rewards}}},
            )
            resp.raise_for_status()
            updated += 1
    print(f"\n[WRITTEN] set True Rewards on {updated} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
