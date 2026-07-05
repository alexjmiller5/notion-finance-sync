"""Notion Transactions database schema migration.

This module contains the pure logic for computing and applying schema changes.
It is importable and testable independently of the CLI script.

Entry points:
    compute_migration_plan(schema, data_source_id) -> MigrationPlan
    apply_migration_plan(plan, api_key, data_source_id, dry_run) -> None

Design decisions:
    - All API calls go through httpx directly (not NotionClient, which is
      focused on page-level read/write, not schema mutations).
    - All operations are idempotent: the plan computation checks the current
      schema and only includes changes that are actually needed.
    - select option additions preserve existing options by merging them with
      the new options before sending the PATCH body.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from notion_finance_sync.config.settings import NOTION_API_VERSION

logger = structlog.get_logger()

# Constants — canonical schema definition

# The exact formula expression for Net Amount (must match this string exactly)
NET_AMOUNT_FORMULA = 'prop("Txn Amount") + prop("Related Transactions Amount")'

# Property renames: {current_name: new_name}
RENAMES: dict[str, str] = {
    "SimpleFIN ID": "Transaction Source ID",
    "SimpleFIN Account ID": "Source Account ID",
}

# New select options to add (only the additions — existing options are preserved)
NEW_BANK_OPTIONS: list[str] = ["Venmo", "E*Trade", "Fidelity"]
NEW_ACCOUNT_TYPE_OPTIONS: list[str] = ["P2P", "Brokerage", "401k", "IRA"]

# Canonical 19-category taxonomy (§10 of SPEC)
CATEGORY_OPTIONS: list[str] = [
    "Airfare",
    "Travel",
    "Dining",
    "Groceries",
    "Gas",
    "Streaming",
    "Online Shopping",
    "Convenience",
    "Department Stores",
    "Wholesale Clubs",
    "Transit",
    "Bills & Utilities",
    "Healthcare",
    "Cash & ATM",
    "Transfer",
    "Trip Settlement",
    "Income",
    "Rent",
    "Other",
]

# Review Status options for the new "Review Status" status field
REVIEW_STATUS_OPTIONS: list[str] = ["Needs Review", "Reviewed", "Needs Attention"]


def _new_properties_spec(data_source_id: str) -> dict[str, dict[str, Any]]:
    """Return the full property definitions for all new fields.

    Keyed by property name (for display/lookup). Each value is the Notion API
    property-schema object (suitable for embedding in a PATCH body under
    ``properties``).
    """
    return {
        "Bank Category": {
            "type": "rich_text",
            "rich_text": {},
        },
        "Calculated Rewards": {
            "type": "number",
            "number": {"format": "dollar"},
        },
        "True Rewards": {
            "type": "number",
            "number": {"format": "dollar"},
        },
        "Related Transactions": {
            "type": "relation",
            "relation": {
                "data_source_id": data_source_id,
                "type": "dual_property",
                "dual_property": {},
            },
        },
        "Related Transactions Amount": {
            "type": "rollup",
            "rollup": {
                "relation_property_name": "Related Transactions",
                "rollup_property_name": "Txn Amount",
                "function": "sum",
            },
        },
        "Net Amount": {
            "type": "formula",
            "formula": {"expression": NET_AMOUNT_FORMULA},
        },
        "Qty": {
            "type": "number",
            "number": {"format": "number"},
        },
        "Ticker": {
            "type": "rich_text",
            "rich_text": {},
        },
        "PPS": {
            "type": "number",
            "number": {"format": "dollar"},
        },
        "Bilt Points": {
            "type": "number",
            "number": {"format": "number"},
        },
        "Bilt Partner": {
            "type": "checkbox",
            "checkbox": {},
        },
        "Excluded": {
            "type": "checkbox",
            "checkbox": {},
        },
        "Review Status": {
            "type": "status",
            "status": {
                "options": [{"name": opt} for opt in REVIEW_STATUS_OPTIONS],
            },
        },
    }


@dataclass
class MigrationPlan:
    """Computed set of changes to apply to the data-source schema.

    ``current_select_options`` holds raw Notion option objects (with id/name/color)
    from the current schema; these are merged with the new option names at apply
    time to produce a complete list for the PATCH body.
    """

    renames: dict[str, str] = field(default_factory=dict)
    new_properties: dict[str, dict[str, Any]] = field(default_factory=dict)
    select_options: dict[str, list[str]] = field(default_factory=dict)
    current_select_options: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.renames and not self.new_properties and not self.select_options

    def summary_lines(self) -> list[str]:
        """Human-readable list of pending changes."""
        lines: list[str] = []
        for old, new in self.renames.items():
            lines.append(f"  RENAME  '{old}' → '{new}'")
        for name in self.new_properties:
            lines.append(f"  ADD     property '{name}'")
        for prop, opts in self.select_options.items():
            lines.append(f"  OPTIONS {prop}: add {opts}")
        return lines


def compute_migration_plan(schema: dict[str, Any], *, data_source_id: str) -> MigrationPlan:
    """Inspect *schema* (a GET /v1/data_sources/{id} response body) and return
    the minimal set of changes required to reach the target schema.

    This function is pure (no I/O) and can be called in tests with a fabricated
    schema dict.
    """
    existing_props: dict[str, Any] = schema.get("properties", {})
    existing_names: set[str] = set(existing_props.keys())

    renames: dict[str, str] = {}
    for old, new in RENAMES.items():
        if old in existing_names:
            renames[old] = new
            logger.debug("migration_rename_needed", old=old, new=new)
        else:
            logger.debug(
                "migration_rename_skipped",
                old=old,
                new=new,
                reason="already renamed or missing",
            )

    new_props_spec = _new_properties_spec(data_source_id)
    new_properties: dict[str, dict[str, Any]] = {}
    for name, schema_obj in new_props_spec.items():
        if name not in existing_names:
            new_properties[name] = schema_obj
            logger.debug("migration_add_property_needed", name=name)
        else:
            logger.debug("migration_add_property_skipped", name=name, reason="already exists")

    # Targets: {prop_name: [option_name, ...]} of what we want to add
    targets: dict[str, list[str]] = {
        "Bank": NEW_BANK_OPTIONS,
        "Account Type": NEW_ACCOUNT_TYPE_OPTIONS,
        "Category": CATEGORY_OPTIONS,
    }

    select_options: dict[str, list[str]] = {}
    current_select_options: dict[str, list[dict[str, Any]]] = {}

    for prop_name, desired_additions in targets.items():
        if prop_name not in existing_props:
            logger.debug("migration_select_skip", prop=prop_name, reason="property not in schema")
            continue

        existing_option_objs: list[dict[str, Any]] = (
            existing_props[prop_name].get("select", {}).get("options", [])
        )
        existing_option_names: set[str] = {o["name"] for o in existing_option_objs}

        missing = [o for o in desired_additions if o not in existing_option_names]
        if missing:
            select_options[prop_name] = missing
            current_select_options[prop_name] = existing_option_objs
            logger.debug("migration_options_needed", prop=prop_name, missing=missing)
        else:
            logger.debug("migration_options_skipped", prop=prop_name, reason="all options present")

    return MigrationPlan(
        renames=renames,
        new_properties=new_properties,
        select_options=select_options,
        current_select_options=current_select_options,
    )


_NOTION_BASE = "https://api.notion.com"


async def apply_migration_plan(
    plan: MigrationPlan,
    *,
    api_key: str,
    data_source_id: str,
    dry_run: bool,
) -> None:
    """Apply *plan* to the live Notion data source.

    In dry-run mode: logs what would happen but issues no PATCH calls.
    When the plan is empty: returns immediately without any network calls.
    Otherwise: issues a single PATCH with all pending changes merged.
    """
    if plan.is_empty():
        logger.info("migration_nothing_to_do")
        return

    if dry_run:
        logger.info("migration_dry_run_no_changes_applied")
        return

    properties_patch: dict[str, Any] = {}

    for old, new in plan.renames.items():
        properties_patch[old] = {"name": new}
        logger.info("migration_rename", old=old, new=new)

    for name, schema_obj in plan.new_properties.items():
        properties_patch[name] = schema_obj
        logger.info("migration_add_property", name=name, type=schema_obj.get("type"))

    # Merge existing + new options to produce the complete list for PATCH
    for prop_name, new_option_names in plan.select_options.items():
        existing_objs = plan.current_select_options.get(prop_name, [])
        merged = list(existing_objs) + [{"name": o, "color": "default"} for o in new_option_names]
        properties_patch[prop_name] = {"select": {"options": merged}}
        logger.info(
            "migration_add_select_options",
            prop=prop_name,
            added=new_option_names,
        )

    url = f"{_NOTION_BASE}/v1/data_sources/{data_source_id}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.patch(url, headers=headers, json={"properties": properties_patch})
        response.raise_for_status()

    logger.info("migration_patch_applied", num_changes=len(properties_patch))
