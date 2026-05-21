"""Tests for the Notion schema migration.

Tests use respx to mock the Notion HTTP API.

Coverage:
- Idempotency: running migration twice produces no PATCH calls the second time
- Dry-run: no PATCH calls in dry-run mode
- Select option addition preserves existing options
- Net Amount formula syntax is correct
- Renames are skipped when property already has the new name
- New properties are skipped when they already exist with the right type
"""

from __future__ import annotations

import json

import httpx
import pytest

from notion_finance_sync.notion.migrations import (
    CATEGORY_OPTIONS,
    NET_AMOUNT_FORMULA,
    NEW_ACCOUNT_TYPE_OPTIONS,
    NEW_BANK_OPTIONS,
    MigrationPlan,
    apply_migration_plan,
    compute_migration_plan,
)

# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------

BASE_URL = "https://api.notion.com"
DATA_SOURCE_ID = "REDACTED_NOTION_DATA_SOURCE_ID"
SCHEMA_URL = f"{BASE_URL}/v1/data_sources/{DATA_SOURCE_ID}"

TEST_API_KEY = "secret_test"


def make_schema(
    *,
    has_old_names: bool = True,
    has_new_fields: bool = False,
    bank_options: list[str] | None = None,
    account_type_options: list[str] | None = None,
    category_options: list[str] | None = None,
) -> dict:
    """Build a fake data-source schema GET response."""
    if bank_options is None:
        bank_options = ["Bank of America", "Wells Fargo", "US Bank"]
    if account_type_options is None:
        account_type_options = ["Credit Card", "Debit Card", "Checking", "Savings"]
    if category_options is None:
        category_options = []

    properties: dict = {
        "Name": {"id": "title", "name": "Name", "type": "title"},
        "Transaction Amount": {
            "id": "amt",
            "name": "Transaction Amount",
            "type": "number",
            "number": {"format": "dollar"},
        },
        "Transaction Date": {"id": "txdate", "name": "Transaction Date", "type": "date"},
        "Bank": {
            "id": "bank",
            "name": "Bank",
            "type": "select",
            "select": {
                "options": [{"id": f"opt-{o}", "name": o, "color": "default"} for o in bank_options]
            },
        },
        "Account Type": {
            "id": "acct_type",
            "name": "Account Type",
            "type": "select",
            "select": {
                "options": [
                    {"id": f"opt-{o}", "name": o, "color": "default"} for o in account_type_options
                ]
            },
        },
        "Category": {
            "id": "cat",
            "name": "Category",
            "type": "select",
            "select": {
                "options": [
                    {"id": f"opt-{o}", "name": o, "color": "default"} for o in category_options
                ]
            },
        },
        # Legacy retired fields (must be preserved — not touched by migration)
        "Data Source Leader": {
            "id": "dsl",
            "name": "Data Source Leader",
            "type": "rich_text",
            "rich_text": {},
        },
        "Data Source Log": {
            "id": "dslog",
            "name": "Data Source Log",
            "type": "rich_text",
            "rich_text": {},
        },
        "Descriptions Match": {
            "id": "dm",
            "name": "Descriptions Match",
            "type": "checkbox",
            "checkbox": {},
        },
        "Description Diff": {
            "id": "dd",
            "name": "Description Diff",
            "type": "rich_text",
            "rich_text": {},
        },
    }

    if has_old_names:
        properties["SimpleFIN ID"] = {
            "id": "sfid",
            "name": "SimpleFIN ID",
            "type": "rich_text",
            "rich_text": {},
        }
        properties["SimpleFIN Account ID"] = {
            "id": "sfacct",
            "name": "SimpleFIN Account ID",
            "type": "rich_text",
            "rich_text": {},
        }
    else:
        # Already renamed
        properties["Transaction Source ID"] = {
            "id": "sfid",
            "name": "Transaction Source ID",
            "type": "rich_text",
            "rich_text": {},
        }
        properties["Source Account ID"] = {
            "id": "sfacct",
            "name": "Source Account ID",
            "type": "rich_text",
            "rich_text": {},
        }

    if has_new_fields:
        properties["Bank Category"] = {
            "id": "bc",
            "name": "Bank Category",
            "type": "rich_text",
            "rich_text": {},
        }
        properties["Calculated Rewards"] = {
            "id": "cr",
            "name": "Calculated Rewards",
            "type": "number",
            "number": {"format": "dollar"},
        }
        properties["True Rewards"] = {
            "id": "tr",
            "name": "True Rewards",
            "type": "number",
            "number": {"format": "dollar"},
        }
        properties["Related Transactions"] = {
            "id": "rt",
            "name": "Related Transactions",
            "type": "relation",
            "relation": {
                "data_source_id": DATA_SOURCE_ID,
                "type": "dual_property",
                "dual_property": {},
            },
        }
        properties["Related Transactions Amount"] = {
            "id": "rta",
            "name": "Related Transactions Amount",
            "type": "rollup",
            "rollup": {
                "relation_property_name": "Related Transactions",
                "rollup_property_name": "Transaction Amount",
                "function": "sum",
            },
        }
        properties["Net Amount"] = {
            "id": "na",
            "name": "Net Amount",
            "type": "formula",
            "formula": {"expression": NET_AMOUNT_FORMULA},
        }
        properties["Quantity"] = {
            "id": "qty",
            "name": "Quantity",
            "type": "number",
            "number": {"format": "number"},
        }
        properties["Ticker"] = {
            "id": "tick",
            "name": "Ticker",
            "type": "rich_text",
            "rich_text": {},
        }
        properties["Price Per Share"] = {
            "id": "pps",
            "name": "Price Per Share",
            "type": "number",
            "number": {"format": "dollar"},
        }
        properties["Bilt Points"] = {
            "id": "bp",
            "name": "Bilt Points",
            "type": "number",
            "number": {"format": "number"},
        }
        properties["Bilt Partner"] = {
            "id": "bpar",
            "name": "Bilt Partner",
            "type": "checkbox",
            "checkbox": {},
        }

    return {"object": "data_source", "id": DATA_SOURCE_ID, "properties": properties}


# ---------------------------------------------------------------------------
# Unit tests for compute_migration_plan
# ---------------------------------------------------------------------------


class TestComputeMigrationPlanRenames:
    def test_detects_old_names(self):
        schema = make_schema(has_old_names=True)
        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        assert "SimpleFIN ID" in plan.renames
        assert plan.renames["SimpleFIN ID"] == "Transaction Source ID"
        assert "SimpleFIN Account ID" in plan.renames
        assert plan.renames["SimpleFIN Account ID"] == "Source Account ID"

    def test_skips_renames_when_already_done(self):
        schema = make_schema(has_old_names=False)
        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        assert "SimpleFIN ID" not in plan.renames
        assert "SimpleFIN Account ID" not in plan.renames
        assert len(plan.renames) == 0


class TestComputeMigrationPlanNewFields:
    def test_detects_missing_new_fields(self):
        schema = make_schema(has_old_names=True, has_new_fields=False)
        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        assert "Bank Category" in plan.new_properties
        assert "Calculated Rewards" in plan.new_properties
        assert "True Rewards" in plan.new_properties
        assert "Related Transactions" in plan.new_properties
        assert "Related Transactions Amount" in plan.new_properties
        assert "Net Amount" in plan.new_properties
        assert "Quantity" in plan.new_properties
        assert "Ticker" in plan.new_properties
        assert "Price Per Share" in plan.new_properties
        assert "Bilt Points" in plan.new_properties
        assert "Bilt Partner" in plan.new_properties

    def test_skips_new_fields_when_already_present(self):
        schema = make_schema(has_old_names=False, has_new_fields=True)
        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        assert len(plan.new_properties) == 0

    def test_net_amount_formula_syntax(self):
        schema = make_schema(has_old_names=True, has_new_fields=False)
        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        net_amount_def = plan.new_properties["Net Amount"]
        formula_expr = net_amount_def["formula"]["expression"]
        assert formula_expr == 'prop("Transaction Amount") + prop("Related Transactions Amount")'

    def test_related_transactions_is_self_relation(self):
        schema = make_schema(has_old_names=True, has_new_fields=False)
        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        rt_def = plan.new_properties["Related Transactions"]
        assert rt_def["relation"]["data_source_id"] == DATA_SOURCE_ID
        assert rt_def["relation"]["type"] == "dual_property"

    def test_related_transactions_amount_is_sum_rollup(self):
        schema = make_schema(has_old_names=True, has_new_fields=False)
        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        rta_def = plan.new_properties["Related Transactions Amount"]
        assert rta_def["rollup"]["relation_property_name"] == "Related Transactions"
        assert rta_def["rollup"]["rollup_property_name"] == "Transaction Amount"
        assert rta_def["rollup"]["function"] == "sum"


class TestComputeMigrationPlanSelectOptions:
    def test_detects_missing_bank_options(self):
        schema = make_schema(bank_options=["Bank of America"])
        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        assert set(plan.select_options["Bank"]) == set(NEW_BANK_OPTIONS)

    def test_skips_existing_bank_options(self):
        schema = make_schema(bank_options=["Bank of America", "Venmo", "E*Trade", "Fidelity"])
        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        # All options already exist — nothing to add
        assert "Bank" not in plan.select_options

    def test_partial_bank_options(self):
        schema = make_schema(bank_options=["Bank of America", "Venmo"])
        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        # Only the missing ones
        assert set(plan.select_options["Bank"]) == {"E*Trade", "Fidelity"}

    def test_detects_missing_account_type_options(self):
        schema = make_schema(account_type_options=["Credit Card"])
        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        assert set(plan.select_options["Account Type"]) == set(NEW_ACCOUNT_TYPE_OPTIONS)

    def test_detects_missing_category_options(self):
        schema = make_schema(category_options=[])
        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        assert set(plan.select_options["Category"]) == set(CATEGORY_OPTIONS)

    def test_preserves_existing_category_options(self):
        # Start with some existing options
        existing = ["Dining", "Groceries"]
        schema = make_schema(category_options=existing)
        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        # Should only add missing ones
        added = plan.select_options.get("Category", [])
        for o in existing:
            assert o not in added
        # All canonical options not already present should be added
        expected_additions = [o for o in CATEGORY_OPTIONS if o not in existing]
        assert set(added) == set(expected_additions)

    def test_skips_select_options_when_all_present(self):
        schema = make_schema(
            bank_options=[
                "Bank of America",
                "Venmo",
                "E*Trade",
                "Fidelity",
                "Wells Fargo",
                "US Bank",
            ],
            account_type_options=[
                "Credit Card",
                "Debit Card",
                "Checking",
                "Savings",
                "P2P",
                "Brokerage",
                "401k",
                "IRA",
            ],
            category_options=CATEGORY_OPTIONS,
        )
        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        assert len(plan.select_options) == 0


# ---------------------------------------------------------------------------
# Integration tests for apply_migration_plan (respx HTTP mocking)
# ---------------------------------------------------------------------------


class TestApplyMigrationPlanDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_makes_no_patch_calls(self, respx_mock):
        """In dry-run mode, no PATCH requests should be made."""
        schema = make_schema(has_old_names=True, has_new_fields=False)
        respx_mock.get(SCHEMA_URL).mock(return_value=httpx.Response(200, json=schema))
        # PATCH should never be called — set up a route that would fail if called
        patch_route = respx_mock.patch(SCHEMA_URL)

        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        await apply_migration_plan(
            plan,
            api_key=TEST_API_KEY,
            data_source_id=DATA_SOURCE_ID,
            dry_run=True,
        )

        assert not patch_route.called

    @pytest.mark.asyncio
    async def test_dry_run_with_nothing_to_do(self, respx_mock):
        """Dry-run on an already-migrated schema still makes no PATCH calls."""
        schema = make_schema(
            has_old_names=False,
            has_new_fields=True,
            bank_options=["Bank of America", "Venmo", "E*Trade", "Fidelity"],
            account_type_options=[
                "Credit Card",
                "Debit Card",
                "Checking",
                "Savings",
                "P2P",
                "Brokerage",
                "401k",
                "IRA",
            ],
            category_options=CATEGORY_OPTIONS,
        )
        patch_route = respx_mock.patch(SCHEMA_URL)

        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        await apply_migration_plan(
            plan,
            api_key=TEST_API_KEY,
            data_source_id=DATA_SOURCE_ID,
            dry_run=True,
        )

        assert not patch_route.called


class TestApplyMigrationPlanIdempotency:
    @pytest.mark.asyncio
    async def test_second_run_produces_no_patch_calls(self, respx_mock):
        """After the first run, calling with the already-migrated schema produces no PATCH calls."""
        # This simulates the schema AFTER migration has been applied
        fully_migrated_schema = make_schema(
            has_old_names=False,
            has_new_fields=True,
            bank_options=[
                "Bank of America",
                "Wells Fargo",
                "US Bank",
                "Venmo",
                "E*Trade",
                "Fidelity",
            ],
            account_type_options=[
                "Credit Card",
                "Debit Card",
                "Checking",
                "Savings",
                "P2P",
                "Brokerage",
                "401k",
                "IRA",
            ],
            category_options=CATEGORY_OPTIONS,
        )
        respx_mock.get(SCHEMA_URL).mock(
            return_value=httpx.Response(200, json=fully_migrated_schema)
        )
        patch_route = respx_mock.patch(SCHEMA_URL)

        # Compute plan against already-migrated schema
        plan = compute_migration_plan(fully_migrated_schema, data_source_id=DATA_SOURCE_ID)

        # Plan should be empty
        assert len(plan.renames) == 0
        assert len(plan.new_properties) == 0
        assert len(plan.select_options) == 0
        assert plan.is_empty()

        # Apply should be a no-op (no PATCH calls)
        await apply_migration_plan(
            plan,
            api_key=TEST_API_KEY,
            data_source_id=DATA_SOURCE_ID,
            dry_run=False,
        )

        assert not patch_route.called


class TestApplyMigrationPlanActualPatch:
    @pytest.mark.asyncio
    async def test_renames_sent_in_patch(self, respx_mock):
        """Verify the PATCH body includes rename operations."""
        schema = make_schema(has_old_names=True, has_new_fields=False)
        patch_route = respx_mock.patch(SCHEMA_URL).mock(
            return_value=httpx.Response(200, json=schema)
        )

        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        # Remove new_properties and select_options to isolate renames test
        plan = MigrationPlan(
            renames=plan.renames,
            new_properties={},
            select_options={},
            current_select_options={},
        )
        await apply_migration_plan(
            plan,
            api_key=TEST_API_KEY,
            data_source_id=DATA_SOURCE_ID,
            dry_run=False,
        )

        assert patch_route.called
        request_body = patch_route.calls[0].request
        body = json.loads(request_body.content)
        props = body["properties"]
        assert "SimpleFIN ID" in props
        assert props["SimpleFIN ID"]["name"] == "Transaction Source ID"
        assert "SimpleFIN Account ID" in props
        assert props["SimpleFIN Account ID"]["name"] == "Source Account ID"

    @pytest.mark.asyncio
    async def test_select_option_addition_preserves_existing(self, respx_mock):
        """PATCH body for select options must include ALL options (existing + new)."""
        schema = make_schema(
            has_old_names=False,
            has_new_fields=True,
            bank_options=["Bank of America", "Wells Fargo"],
        )
        patch_route = respx_mock.patch(SCHEMA_URL).mock(
            return_value=httpx.Response(200, json=schema)
        )

        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        # Isolate to just select options
        plan = MigrationPlan(
            renames={},
            new_properties={},
            select_options=plan.select_options,
            current_select_options=plan.current_select_options,
        )
        await apply_migration_plan(
            plan,
            api_key=TEST_API_KEY,
            data_source_id=DATA_SOURCE_ID,
            dry_run=False,
        )

        assert patch_route.called
        body = json.loads(patch_route.calls[0].request.content)
        props = body["properties"]
        bank_options_sent = [o["name"] for o in props["Bank"]["select"]["options"]]

        # Must include BOTH existing and new options
        assert "Bank of America" in bank_options_sent
        assert "Wells Fargo" in bank_options_sent
        assert "Venmo" in bank_options_sent
        assert "E*Trade" in bank_options_sent
        assert "Fidelity" in bank_options_sent

    @pytest.mark.asyncio
    async def test_new_properties_sent_in_patch(self, respx_mock):
        """PATCH body includes all new properties when adding them."""
        schema = make_schema(has_old_names=False, has_new_fields=False)
        patch_route = respx_mock.patch(SCHEMA_URL).mock(
            return_value=httpx.Response(200, json=schema)
        )

        plan = compute_migration_plan(schema, data_source_id=DATA_SOURCE_ID)
        # Isolate to just new_properties
        plan = MigrationPlan(
            renames={},
            new_properties=plan.new_properties,
            select_options={},
            current_select_options={},
        )
        await apply_migration_plan(
            plan,
            api_key=TEST_API_KEY,
            data_source_id=DATA_SOURCE_ID,
            dry_run=False,
        )

        assert patch_route.called
        body = json.loads(patch_route.calls[0].request.content)
        props = body["properties"]
        assert "Bank Category" in props
        assert props["Bank Category"]["type"] == "rich_text"
        assert "Calculated Rewards" in props
        assert props["Calculated Rewards"]["number"]["format"] == "dollar"
        assert "Net Amount" in props
        assert "formula" in props["Net Amount"]
        assert "Related Transactions" in props
        assert props["Related Transactions"]["relation"]["data_source_id"] == DATA_SOURCE_ID


class TestNetAmountFormula:
    def test_formula_is_exactly_correct(self):
        expected = 'prop("Transaction Amount") + prop("Related Transactions Amount")'
        assert NET_AMOUNT_FORMULA == expected


class TestMigrationPlanIsEmpty:
    def test_empty_plan(self):
        plan = MigrationPlan(
            renames={}, new_properties={}, select_options={}, current_select_options={}
        )
        assert plan.is_empty()

    def test_non_empty_plan_with_renames(self):
        plan = MigrationPlan(
            renames={"Old": "New"},
            new_properties={},
            select_options={},
            current_select_options={},
        )
        assert not plan.is_empty()

    def test_non_empty_plan_with_new_properties(self):
        plan = MigrationPlan(
            renames={},
            new_properties={"Foo": {"type": "rich_text"}},
            select_options={},
            current_select_options={},
        )
        assert not plan.is_empty()

    def test_non_empty_plan_with_select_options(self):
        plan = MigrationPlan(
            renames={},
            new_properties={},
            select_options={"Bank": ["Venmo"]},
            current_select_options={},
        )
        assert not plan.is_empty()
