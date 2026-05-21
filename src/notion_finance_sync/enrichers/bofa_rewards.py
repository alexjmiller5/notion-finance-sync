"""BofA rewards enricher — populates `True Rewards` from BofA's monthly
rewards summary page.

The main BofA transactions view doesn't show per-txn cashback. The monthly
rewards summary does, but as a separate scrape. This enricher does the
correlation by (date, amount) → updates True Rewards on the matching row.
"""

from __future__ import annotations

import structlog

from notion_finance_sync.enrichers._base import ExternalRewardEntry, NotionUpdate

logger = structlog.get_logger()


class BofARewardsEnricher:
    SOURCE = "bofa_rewards"
    UPDATES_FIELDS = ["True Rewards"]

    def fetch_external_data(self) -> list[ExternalRewardEntry]:
        raise NotImplementedError(
            "TODO: Reuse the bofa session to navigate to the monthly rewards summary "
            "page. Scrape per-txn cashback values for the current statement period."
        )

    def correlate_to_notion(
        self,
        entries: list[ExternalRewardEntry],
        notion_txns: dict[str, dict],
    ) -> list[NotionUpdate]:
        raise NotImplementedError(
            "TODO: Match entries to Notion rows by (date, amount, payee). Update "
            "True Rewards with the bank-applied cashback (handles cap-exhaustion correctly)."
        )
