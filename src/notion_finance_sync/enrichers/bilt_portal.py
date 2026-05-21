"""Bilt portal enricher — populates `Bilt Points` (and `Bilt Partner`) across
ALL transactions, not just Bilt Blue card txns.

Bilt's Neighborhood Dining program means a BofA-card transaction at a
Bilt-partnered restaurant earns Bilt points too. The Bilt portal is the only
place where those cross-card points show up.

Runs in Phase 2 after all bank scrapers complete. Reuses the `bilt` session
(same Chrome profile as banks/bilt.py).
"""

from __future__ import annotations

import structlog

from notion_finance_sync.enrichers._base import ExternalRewardEntry, NotionUpdate

logger = structlog.get_logger()


class BiltPortalEnricher:
    SOURCE = "bilt_portal"
    UPDATES_FIELDS = ["Bilt Points", "Bilt Partner"]

    def fetch_external_data(self) -> list[ExternalRewardEntry]:
        raise NotImplementedError(
            "TODO: Open bilt session, navigate to points/rewards activity, scrape "
            "every Bilt point earning across all cards. Set raw['bilt_partner']=True "
            "for Neighborhood Dining entries."
        )

    def correlate_to_notion(
        self,
        entries: list[ExternalRewardEntry],
        notion_txns: dict[str, dict],
    ) -> list[NotionUpdate]:
        raise NotImplementedError(
            "TODO: For each entry, find the Notion row with matching (date, amount, "
            "merchant_normalized). Build NotionUpdate setting Bilt Points + Bilt Partner."
        )
