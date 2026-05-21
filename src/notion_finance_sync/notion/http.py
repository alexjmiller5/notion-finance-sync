"""Shared HTTP helpers for Notion API clients."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

MAX_RETRIES = 3
RETRY_BASE_DELAY = 0.5


async def request_with_retry(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    log_prefix: str = "notion",
    **kwargs: Any,
) -> httpx.Response:
    """HTTP request with retry on 429 and timeout.

    Args:
        method: HTTP method (GET, POST, PATCH, …).
        url: Target URL.
        headers: Request headers (caller supplies their own auth headers).
        log_prefix: Prefix for structlog event keys, e.g. ``"notion"`` →
            ``notion_rate_limited``, or ``"tasks_notion"`` →
            ``tasks_notion_rate_limited``.
        **kwargs: Extra kwargs forwarded to ``httpx.AsyncClient.request``.
    """
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.request(method, url, headers=headers, **kwargs)
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", RETRY_BASE_DELAY))
                delay = max(retry_after, RETRY_BASE_DELAY * (2**attempt))
                logger.warning(f"{log_prefix}_rate_limited", attempt=attempt, delay=delay)
                await asyncio.sleep(delay)
                continue
            response.raise_for_status()
            return response
        except httpx.TimeoutException:
            delay = RETRY_BASE_DELAY * (2**attempt)
            logger.warning(f"{log_prefix}_timeout", attempt=attempt, delay=delay)
            await asyncio.sleep(delay)
    raise RuntimeError(f"Notion API request failed after {MAX_RETRIES} retries")
