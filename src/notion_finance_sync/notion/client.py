"""Notion API client for reading and writing transaction pages.

Cherry-picked from the old notion-ai-budgeting-app and adapted:
- Field renames: SimpleFIN ID -> Transaction Source ID, SimpleFIN Account ID -> Source Account ID
- New fields supported: Bank Category, Calculated Rewards, True Rewards, Bilt Points,
  Bilt Partner, Quantity, Ticker, Price Per Share
- Dual-provider fields removed (Data Source Leader, Data Source Log, Descriptions Match,
  Description Diff)
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from notion_finance_sync.config.settings import NOTION_API_VERSION
from notion_finance_sync.models.transactions import TransactionRecord
from notion_finance_sync.notion.encoders import encode_transaction
from notion_finance_sync.notion.http import request_with_retry
from notion_finance_sync.notion.properties import P

logger = structlog.get_logger()


class NotionClient:
    """Client for reading/writing transaction data in Notion."""

    def __init__(self, api_key: str, data_source_id: str) -> None:
        self._api_key = api_key
        self._data_source_id = data_source_id
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": "application/json",
        }

    async def _request_with_retry(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """HTTP request with retry on 429 and timeout."""
        return await request_with_retry(
            method, url, headers=self._headers, log_prefix="notion", **kwargs
        )

    async def get_existing_transactions(
        self, since_date: str | None = None
    ) -> dict[str, dict[str, Any]]:
        """Query existing transactions for dedup + update.

        Returns dict mapping `Transaction Source ID` -> row data.
        """
        transactions: dict[str, dict[str, Any]] = {}
        cursor: str | None = None
        # Safety bound only — the loop stops on has_more=False. Must exceed the total
        # row count in the query window or a backfill's dedup silently misses existing
        # rows and RE-CREATES them as duplicates (100 rows/page; a full-history
        # backfill sees thousands). 500 pages = 50k rows of headroom.
        max_pages = 500

        filter_body: dict[str, Any] = {}
        if since_date:
            # Filter by property ID (rename-proof); Notion accepts id or name here.
            filter_body["filter"] = {
                "or": [
                    {"property": P.DATE, "date": {"on_or_after": since_date}},
                    {"property": P.DATE, "date": {"is_empty": True}},
                ]
            }

        for _ in range(max_pages):
            body: dict[str, Any] = {"page_size": 100, **filter_body}
            if cursor:
                body["start_cursor"] = cursor

            response = await self._request_with_retry(
                "POST",
                f"https://api.notion.com/v1/data_sources/{self._data_source_id}/query",
                json=body,
            )
            data = response.json()

            for page in data["results"]:
                props = page["properties"]
                # Read the source id by its stable property id (rename-proof).
                src_prop = next(
                    (v for v in props.values() if v.get("id") == P.SOURCE_ID), {}
                )
                rich_text = src_prop.get("rich_text", [])
                if not rich_text:
                    continue

                source_id = rich_text[0]["plain_text"]
                transactions[source_id] = self._row_from_props(page["id"], props)

            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        logger.info("loaded_existing_transactions", count=len(transactions))
        return transactions

    @staticmethod
    def _row_from_props(page_id: str, props: dict[str, Any]) -> dict[str, Any]:
        """Decode a Notion page's properties into a flat row representation.

        Page ``properties`` are keyed by display NAME but each value carries its
        stable ``id``; read by id so renames never break the decode.
        """
        by_id = {v.get("id"): v for v in props.values()}

        def text(pid: str) -> str:
            rt = by_id.get(pid, {}).get("rich_text", [])
            return rt[0]["plain_text"] if rt else ""

        def number(pid: str) -> float | None:
            return by_id.get(pid, {}).get("number")

        def select(pid: str) -> str:
            sel = by_id.get(pid, {}).get("select")
            return sel["name"] if sel else ""

        def date_start(pid: str) -> str | None:
            d = by_id.get(pid, {}).get("date")
            return d["start"] if d else None

        def status(pid: str) -> str:
            s = by_id.get(pid, {}).get("status", {})
            return s.get("name", "")

        def checkbox(pid: str) -> bool:
            return bool(by_id.get(pid, {}).get("checkbox", False))

        title = by_id.get(P.NAME, {}).get("title", [])
        return {
            "page_id": page_id,
            "name": title[0]["plain_text"] if title else "",
            "amount": number(P.AMOUNT),
            # Key matches TransactionRecord.transaction_date so sync.diffing's
            # MATERIAL_FIELDS comparison lines up.
            "transaction_date": date_start(P.DATE),
            "status": status(P.STATUS),
            "payee": text(P.PAYEE),
            "memo": text(P.MEMO),
            "bank": select(P.BANK),
            "credit_card_account": select(P.CREDIT_CARD_ACCOUNT),
            "card_network": select(P.CARD_NETWORK),
            "account_type": select(P.ACCOUNT_TYPE),
            "account_name": text(P.ACCOUNT_NAME),
            "bank_category": text(P.BANK_CATEGORY),
            "category": select(P.CATEGORY),
            "source_id": text(P.SOURCE_ID),
            "source_account_id": text(P.SOURCE_ACCOUNT_ID),
            "calculated_rewards": number(P.CALCULATED_REWARDS),
            "true_rewards": number(P.TRUE_REWARDS),
            "bilt_points": number(P.BILT_POINTS),
            "bilt_partner": checkbox(P.BILT_PARTNER),
            "quantity": number(P.QUANTITY),
            "ticker": select(P.TICKER),
            "price_per_share": number(P.PRICE_PER_SHARE),
        }

    async def create_transaction(self, properties: dict[str, Any]) -> None:
        """Create a new Notion page in the Transactions data source.

        `properties` is the Notion API property-JSON dict (already encoded —
        caller is responsible for building it via the `_encode` helpers in
        notion_finance_sync.notion.encoders).
        """
        body = {
            "parent": {"data_source_id": self._data_source_id},
            "properties": properties,
        }
        await self._request_with_retry("POST", "https://api.notion.com/v1/pages", json=body)

    async def update_transaction(self, page_id: str, properties: dict[str, Any]) -> None:
        """Update an existing Notion page's properties."""
        await self._request_with_retry(
            "PATCH",
            f"https://api.notion.com/v1/pages/{page_id}",
            json={"properties": properties},
        )

    async def create_from_record(self, record: TransactionRecord) -> None:
        """Create a new Notion page from a TransactionRecord."""
        await self.create_transaction(encode_transaction(record))

    async def update_from_record(self, page_id: str, record: TransactionRecord) -> None:
        """Update an existing Notion page from a TransactionRecord."""
        await self.update_transaction(page_id, encode_transaction(record))

    async def release_transaction(
        self,
        *,
        page_id: str,
        release_date: str,
    ) -> None:
        """Flip a Pending page to Released with a release date."""
        properties = {
            P.STATUS: {"status": {"name": "Released"}},
            P.RELEASE_DATE: {"date": {"start": release_date}},
        }
        await self.update_transaction(page_id, properties)
        logger.info("released_page", page_id=page_id, release_date=release_date)
