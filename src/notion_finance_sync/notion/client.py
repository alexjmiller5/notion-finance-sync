"""Notion API client for reading and writing transaction pages.

Cherry-picked from the old notion-ai-budgeting-app and adapted:
- Field renames: SimpleFIN ID -> Transaction Source ID, SimpleFIN Account ID -> Source Account ID
- New fields supported: Bank Category, Calculated Rewards, True Rewards, Bilt Points,
  Bilt Partner, Quantity, Ticker, Price Per Share
- Dual-provider fields removed (Data Source Leader, Data Source Log, Descriptions Match,
  Description Diff)
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from notion_finance_sync.config.settings import NOTION_API_VERSION
from notion_finance_sync.models.transactions import TransactionRecord
from notion_finance_sync.notion.encoders import encode_for_create, encode_for_update

logger = structlog.get_logger()

MAX_RETRIES = 3
RETRY_BASE_DELAY = 0.5


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
        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.request(method, url, headers=self._headers, **kwargs)
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", RETRY_BASE_DELAY))
                    delay = max(retry_after, RETRY_BASE_DELAY * (2**attempt))
                    logger.warning("notion_rate_limited", attempt=attempt, delay=delay)
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
                return response
            except httpx.TimeoutException:
                delay = RETRY_BASE_DELAY * (2**attempt)
                logger.warning("notion_timeout", attempt=attempt, delay=delay)
                await asyncio.sleep(delay)
        raise RuntimeError(f"Notion API request failed after {MAX_RETRIES} retries")

    async def get_existing_transactions(
        self, since_date: str | None = None
    ) -> dict[str, dict[str, Any]]:
        """Query existing transactions for dedup + update.

        Returns dict mapping `Transaction Source ID` -> row data.
        """
        transactions: dict[str, dict[str, Any]] = {}
        cursor: str | None = None
        max_pages = 10  # higher than old project — we're scraping more accounts now

        filter_body: dict[str, Any] = {}
        if since_date:
            filter_body["filter"] = {
                "or": [
                    {"property": "Transaction Date", "date": {"on_or_after": since_date}},
                    {"property": "Transaction Date", "date": {"is_empty": True}},
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
                rich_text = props.get("Transaction Source ID", {}).get("rich_text", [])
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
        """Decode Notion property dict into a flat row representation."""

        def text(name: str) -> str:
            rt = props.get(name, {}).get("rich_text", [])
            return rt[0]["plain_text"] if rt else ""

        def number(name: str) -> float | None:
            return props.get(name, {}).get("number")

        def select(name: str) -> str:
            sel = props.get(name, {}).get("select")
            return sel["name"] if sel else ""

        def date_start(name: str) -> str | None:
            d = props.get(name, {}).get("date")
            return d["start"] if d else None

        def status(name: str) -> str:
            s = props.get(name, {}).get("status", {})
            return s.get("name", "")

        def checkbox(name: str) -> bool:
            return bool(props.get(name, {}).get("checkbox", False))

        title = props.get("Name", {}).get("title", [])
        return {
            "page_id": page_id,
            "name": title[0]["plain_text"] if title else "",
            "amount": number("Transaction Amount"),
            "date": date_start("Transaction Date"),
            "transacted_at": date_start("Transacted At"),
            "status": status("Transaction Status"),
            "payee": text("Payee"),
            "memo": text("Memo"),
            "bank": select("Bank"),
            "credit_card_account": select("Credit Card / Account"),
            "card_network": select("Card Network"),
            "account_type": select("Account Type"),
            "account_name": text("Account Name"),
            "bank_category": text("Bank Category"),
            "category": select("Category"),
            "source_id": text("Transaction Source ID"),
            "source_account_id": text("Source Account ID"),
            "calculated_rewards": number("Calculated Rewards"),
            "true_rewards": number("True Rewards"),
            "bilt_points": number("Bilt Points"),
            "bilt_partner": checkbox("Bilt Partner"),
            "quantity": number("Quantity"),
            "ticker": text("Ticker"),
            "price_per_share": number("Price Per Share"),
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
        await self.create_transaction(encode_for_create(record))

    async def update_from_record(self, page_id: str, record: TransactionRecord) -> None:
        """Update an existing Notion page from a TransactionRecord."""
        await self.update_transaction(page_id, encode_for_update(record))

    async def release_transaction(
        self,
        *,
        page_id: str,
        release_date: str,
    ) -> None:
        """Flip a Pending page to Released with a release date."""
        properties = {
            "Transaction Status": {"status": {"name": "Released"}},
            "Release Date": {"date": {"start": release_date}},
        }
        await self.update_transaction(page_id, properties)
        logger.info("released_page", page_id=page_id, release_date=release_date)
