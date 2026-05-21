"""Registry mapping enricher source name -> ``Enricher`` instance.

The orchestrator iterates this dict in Phase 2. Tests monkeypatch
``ENRICHER_REGISTRY`` to swap in ``FakeEnricher``.

All v1 enrichers currently raise ``NotImplementedError``. The orchestrator
catches this (and any other exception) and logs a warning per enricher —
enricher failures are explicitly non-fatal so the sync still succeeds.
"""

from __future__ import annotations

from notion_finance_sync.enrichers import bilt_portal, bofa_rewards, wells_rewards
from notion_finance_sync.enrichers._base import Enricher

ENRICHER_REGISTRY: dict[str, Enricher] = {
    "bilt_portal": bilt_portal.BiltPortalEnricher(),
    "bofa_rewards": bofa_rewards.BofARewardsEnricher(),
    "wells_rewards": wells_rewards.WellsRewardsEnricher(),
}


def get_enricher(source: str) -> Enricher:
    """Look up an enricher by source name."""
    try:
        return ENRICHER_REGISTRY[source]
    except KeyError as exc:
        raise KeyError(f"unknown enricher source: {source!r}") from exc


def all_sources() -> list[str]:
    return list(ENRICHER_REGISTRY.keys())
