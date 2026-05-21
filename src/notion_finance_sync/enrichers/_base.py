from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable


@dataclass
class ExternalRewardEntry:
    """A reward earning pulled from an external source (Bilt portal, BofA rewards page).

    Used by enrichers to correlate to existing Notion transaction rows.
    """

    date: date
    amount: float
    """The original transaction amount (used for matching)."""

    merchant: str
    """The merchant name as the portal/page shows it."""

    reward_value: float
    """The reward earned in this entry's native unit (dollars for cashback, points for points)."""

    raw: dict
    """Original scraped row, retained for debugging."""


@dataclass
class NotionUpdate:
    """A single field update to apply to an existing Notion page."""

    page_id: str
    field_updates: dict[str, object]
    """Field name -> new value, in scraper-side representation (not Notion API JSON)."""


@runtime_checkable
class Enricher(Protocol):
    """Phase 2 of a sync run: pulls data from an external source and updates
    existing Notion transaction rows by correlation.

    Examples:
    - bilt_portal: pulls Bilt points across ALL cards, updates `Bilt Points`
    - bofa_rewards: pulls per-txn cashback from BofA monthly summary, updates `True Rewards`
    - wells_rewards: pulls Autograph points from rewards center, updates `True Rewards`
    """

    SOURCE: str
    """Identifier for logs and the optional Data Source Log field."""

    UPDATES_FIELDS: list[str]
    """Notion field names this enricher writes to."""

    def fetch_external_data(self) -> list[ExternalRewardEntry]:
        """Pull entries from the external source (portal page, rewards center)."""
        ...

    def correlate_to_notion(
        self,
        entries: list[ExternalRewardEntry],
        notion_txns: dict[str, dict],
    ) -> list[NotionUpdate]:
        """Match external entries to existing Notion rows by (date, amount, merchant).

        Returns one NotionUpdate per matched row.
        """
        ...
