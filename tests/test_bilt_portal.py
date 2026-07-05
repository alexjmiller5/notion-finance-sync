"""Tests for the Bilt portal cross-card points enricher (SPEC §12).

Bilt's /loyalty/activity feed contains point earnings on OTHER cards (a BofA
UCR charge at a Neighborhood Dining partner still earns Bilt points). The
enricher correlates those entries to existing Notion rows by (amount, date,
merchant) and sets `Bilt Points` (+ `Bilt Partner` for dining).

Feed fixture is the real captured response (gitignored).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from notion_finance_sync.enrichers.bilt_portal import BiltPortalEnricher, entries_to_external

LOYALTY_FIXTURE = Path(__file__).parent / "fixtures" / "bilt" / "loyalty_activity.json"


@pytest.fixture
def feed() -> list[dict]:
    if not LOYALTY_FIXTURE.exists():
        pytest.skip("bilt real-data fixture not present")
    return json.loads(LOYALTY_FIXTURE.read_text())["entries"]


@pytest.fixture
def external(feed):
    return entries_to_external(feed)


def _row(
    source_id: str,
    *,
    page_id: str,
    amount: float,
    transaction_date: str,
    payee: str = "",
    bank: str = "Bank of America",
    bilt_points: float | None = None,
) -> dict:
    return {
        "page_id": page_id,
        "source_id": source_id,
        "name": payee,
        "payee": payee,
        "amount": amount,
        "transaction_date": transaction_date,
        "bank": bank,
        "bilt_points": bilt_points,
        "status": "Posted",
    }


class TestEntriesToExternal:
    def test_only_cross_card_earned_entries(self, external):
        # BILT_MC entries (Bilt Blue card) are handled inline by the scraper;
        # zero-point / pending / SPENT entries carry nothing to attribute.
        merchants = {e.merchant for e in external}
        assert "Shukette" in merchants
        assert "Lyft" in merchants
        assert "Housing Points" not in merchants  # BILT_MC
        assert "NY Grill & Deli" not in merchants  # BILT_MC
        assert "Neighborhood Dining" not in merchants  # pending, 0 pts

    def test_shukette_entry_shape(self, external):
        e = next(x for x in external if x.merchant == "Shukette")
        assert e.amount == 105.74
        assert e.reward_value == 105
        # feed datetime 2026-05-28T01:55:03Z -> Eastern date 2026-05-27
        assert e.date.isoformat() == "2026-05-27"
        assert e.raw["bilt_partner"] is True  # DINING = Neighborhood Dining

    def test_rideshare_is_not_dining_partner(self, external):
        e = next(x for x in external if x.merchant == "Lyft")
        assert e.raw["bilt_partner"] is False


class TestCorrelate:
    def test_matches_bofa_row_by_amount_and_date(self, external):
        rows = {
            "bofa-1": _row(
                "bofa-1",
                page_id="pg-shukette",
                amount=-105.74,
                transaction_date="2026-05-27",
                payee="TST* SHUKETTE",
            ),
        }
        updates = BiltPortalEnricher().correlate_to_notion(external, rows)
        up = next(u for u in updates if u.page_id == "pg-shukette")
        assert up.field_updates["Bilt Points"] == {"number": 105.0}
        assert up.field_updates["Bilt Partner"] == {"checkbox": True}

    def test_ambiguous_amount_prefers_merchant_name(self, external):
        rows = {
            "a": _row(
                "a",
                page_id="pg-other",
                amount=-105.74,
                transaction_date="2026-05-27",
                payee="SOME OTHER PLACE",
            ),
            "b": _row(
                "b",
                page_id="pg-shukette",
                amount=-105.74,
                transaction_date="2026-05-28",
                payee="SHUKETTE NYC",
            ),
        }
        updates = BiltPortalEnricher().correlate_to_notion(external, rows)
        assert [u.page_id for u in updates if u.field_updates.get("Bilt Points")] == ["pg-shukette"]

    def test_no_row_outside_date_window(self, external):
        rows = {
            "far": _row(
                "far",
                page_id="pg-far",
                amount=-105.74,
                transaction_date="2026-04-01",
                payee="SHUKETTE",
            ),
        }
        assert BiltPortalEnricher().correlate_to_notion(external, rows) == []

    def test_bilt_rows_are_skipped(self, external):
        # Bilt Blue rows get true_rewards inline; never double-attach points.
        rows = {
            "bilt-1": _row(
                "bilt-1",
                page_id="pg-bilt",
                amount=-105.74,
                transaction_date="2026-05-27",
                payee="Shukette",
                bank="Bilt",
            ),
        }
        assert BiltPortalEnricher().correlate_to_notion(external, rows) == []

    def test_already_set_points_not_rewritten(self, external):
        rows = {
            "bofa-1": _row(
                "bofa-1",
                page_id="pg-shukette",
                amount=-105.74,
                transaction_date="2026-05-27",
                payee="Shukette",
                bilt_points=105.0,
            ),
        }
        assert BiltPortalEnricher().correlate_to_notion(external, rows) == []

    def test_positive_rows_never_match(self, external):
        rows = {
            "refund": _row(
                "refund",
                page_id="pg-refund",
                amount=105.74,
                transaction_date="2026-05-27",
                payee="Shukette",
            ),
        }
        assert BiltPortalEnricher().correlate_to_notion(external, rows) == []


class TestTipAdjustedMatch:
    def test_tipped_restaurant_charge_matches_by_name(self, external):
        # Real case: Shukette points accrued on the pre-tip $105.74, but the
        # BofA charge settled at $130.14 (tip added). Name + date + amount
        # range (base..base*1.5) must still match.
        rows = {
            "bofa-1": _row(
                "bofa-1",
                page_id="pg-shukette",
                amount=-130.14,
                transaction_date="2026-05-28",
                payee="BILT - Shukette 844-8222458 NY",
            ),
        }
        updates = BiltPortalEnricher().correlate_to_notion(external, rows)
        up = next(u for u in updates if u.page_id == "pg-shukette")
        assert up.field_updates["Bilt Points"] == {"number": 105.0}

    def test_amount_out_of_tip_range_does_not_match_by_name(self, external):
        # Same name + date but 3x the base amount: not a tip, don't match.
        rows = {
            "bofa-1": _row(
                "bofa-1",
                page_id="pg-wrong",
                amount=-317.22,
                transaction_date="2026-05-28",
                payee="BILT - Shukette 844-8222458 NY",
            ),
        }
        assert BiltPortalEnricher().correlate_to_notion(external, rows) == []
