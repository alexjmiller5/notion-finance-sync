"""Bilt portal enricher — populates `Bilt Points` (and `Bilt Partner`) across
ALL transactions, not just Bilt Blue card txns (SPEC §12).

Bilt's Neighborhood Dining program means a BofA-card transaction at a
Bilt-partnered restaurant earns Bilt points too. Bilt's ``/loyalty/activity``
feed (recon 2026-07-03, data/snapshots/bilt/recon_20260703/FINDINGS.md) is the
only place those cross-card points show up: entries with ``category`` other
than ``BILT_MC`` (DINING = Neighborhood Dining, RIDESHARE = Lyft, ...).

Bilt Blue card entries (``BILT_MC``) are deliberately excluded — the bilt
scraper already writes those inline as `True Rewards`.

Runs in Phase 2 after all bank scrapers. Reuses the bilt scraper's Keycloak
token (no browser needed).
"""

from __future__ import annotations

from datetime import date, datetime

import structlog

from notion_finance_sync.banks import bilt
from notion_finance_sync.enrichers._base import ExternalRewardEntry, NotionUpdate

logger = structlog.get_logger()

_MATCH_WINDOW_DAYS = 5


def entries_to_external(entries: list[dict]) -> list[ExternalRewardEntry]:
    """Convert /loyalty/activity entries to cross-card ``ExternalRewardEntry``s.

    Keeps only earnings on OTHER cards: EARNED, positive points, non-``BILT_MC``
    category, with a transaction amount to correlate on. ``raw['bilt_partner']``
    flags Neighborhood Dining entries (DINING category).
    """
    out: list[ExternalRewardEntry] = []
    for e in entries:
        if (
            e.get("category") == "BILT_MC"
            or e.get("pointState") != "EARNED"
            or not e.get("totalPoints")
            or not e.get("transactionAmount")
        ):
            continue
        dt = datetime.fromisoformat(e["datetime"].replace("Z", "+00:00"))
        out.append(
            ExternalRewardEntry(
                date=dt.astimezone(bilt._EASTERN).date(),
                amount=float(e["transactionAmount"]),
                merchant=(e.get("title") or e.get("rawTitle") or "").strip(),
                reward_value=float(e["totalPoints"]),
                raw={**e, "bilt_partner": e.get("category") == "DINING"},
            )
        )
    return out


class BiltPortalEnricher:
    SOURCE = "bilt_portal"
    UPDATES_FIELDS = ["Bilt Points", "Bilt Partner"]

    def fetch_external_data(self) -> list[ExternalRewardEntry]:
        token = bilt.get_access_token()
        with bilt._api_client(token) as client:
            entries = bilt.fetch_loyalty_activity(client)
        external = entries_to_external(entries)
        logger.info("bilt_portal_fetched", total=len(entries), cross_card=len(external))
        return external

    def correlate_to_notion(
        self,
        entries: list[ExternalRewardEntry],
        notion_txns: dict[str, dict],
    ) -> list[NotionUpdate]:
        """Match each cross-card entry to a non-Bilt spend row.

        Tier 0 = exact amount + date within ±5 days (points post a few days
        after the charge); when several rows share the amount, a merchant-name
        substring match wins, then nearest date. Tier 1 = name match with the
        settled amount in [base, base*1.5] — restaurant tips settle above the
        pre-tip amount the points accrued on (real case: Shukette $105.74 base,
        $130.14 settled). Rows already carrying the same `Bilt Points` value
        are skipped (idempotent).
        """
        updates: list[NotionUpdate] = []
        claimed: set[str] = set()

        for entry in entries:
            candidates = []
            for sid, row in notion_txns.items():
                if sid in claimed or row.get("bank") == "Bilt":
                    continue
                amount = row.get("amount")
                if amount is None or amount >= 0:
                    continue
                row_date = date.fromisoformat(row["transaction_date"][:10])
                day_diff = abs((row_date - entry.date).days)
                if day_diff > _MATCH_WINDOW_DAYS:
                    continue
                hay = f"{row.get('payee', '')} {row.get('name', '')}".casefold()
                name_hit = bool(entry.merchant) and entry.merchant.casefold() in hay
                if abs(-amount - entry.amount) <= 0.005:
                    tier = 0
                elif name_hit and entry.amount <= -amount <= entry.amount * 1.5:
                    tier = 1  # tip-adjusted settle
                else:
                    continue
                candidates.append((tier, not name_hit, day_diff, sid, row))

            if not candidates:
                continue
            _, _, _, sid, row = min(candidates, key=lambda c: (c[0], c[1], c[2]))
            claimed.add(sid)

            if row.get("bilt_points") == entry.reward_value:
                continue  # already enriched
            field_updates: dict[str, object] = {"Bilt Points": {"number": entry.reward_value}}
            if entry.raw.get("bilt_partner"):
                field_updates["Bilt Partner"] = {"checkbox": True}
            updates.append(NotionUpdate(page_id=row["page_id"], field_updates=field_updates))

        logger.info("bilt_portal_correlated", entries=len(entries), updates=len(updates))
        return updates
