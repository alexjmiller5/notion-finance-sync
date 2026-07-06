"""Notion property IDs for the Transactions data source.

The IDs themselves live in ``config.toml`` (``[notion.property_ids]``, gitignored)
— generate/refresh them with ``scripts/gen_property_ids.py``. Notion property IDs
are stable across renames, so the sync keys writes/reads by ID (not display name);
renaming a property in the Notion UI therefore never breaks anything.

Reference the fields as attributes, e.g. ``P.AMOUNT`` — populated from config at
import.
"""

from __future__ import annotations

from notion_finance_sync.config.settings import get_notion_property_ids


class _Properties:
    """Attribute access (``P.AMOUNT``) over the config-provided property IDs."""

    def __init__(self, ids: dict[str, str]) -> None:
        for name, prop_id in ids.items():
            setattr(self, name, prop_id)

    def __getattr__(self, name: str) -> str:  # only called when the attr is missing
        raise AttributeError(
            f"Notion property {name!r} is not in config.toml [notion.property_ids] "
            f"(regenerate with scripts/gen_property_ids.py)."
        )


P = _Properties(get_notion_property_ids())
