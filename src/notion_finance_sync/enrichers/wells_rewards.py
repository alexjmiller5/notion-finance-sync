"""Wells Fargo Autograph rewards enricher — populates `True Rewards` from
Wells Fargo's rewards center.

Similar pattern to bofa_rewards: per-txn rewards live on a different page
than the main transactions view. Correlate by (date, amount).
"""

from __future__ import annotations

import structlog

from notion_finance_sync.enrichers._base import ExternalRewardEntry, NotionUpdate

logger = structlog.get_logger()


class WellsRewardsEnricher:
    SOURCE = "wells_rewards"
    UPDATES_FIELDS = ["True Rewards"]

    def fetch_external_data(self) -> list[ExternalRewardEntry]:
        raise NotImplementedError("TODO: Wells Fargo rewards center scrape")

    def correlate_to_notion(
        self,
        entries: list[ExternalRewardEntry],
        notion_txns: dict[str, dict],
    ) -> list[NotionUpdate]:
        raise NotImplementedError("TODO: correlate Wells points to Notion rows")
