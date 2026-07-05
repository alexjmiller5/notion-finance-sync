"""E*Trade (Morgan Stanley) brokerage scraper package.

One JSON API covers everything (see data/snapshots/etrade/FINDINGS.md):

- Activities: ``GET /phx/activitychannelapi/activities/v2`` — session cookies +
  the ``stk1`` header (token embedded in the transactions page HTML as
  ``pageConfig['uaa_vt']``). Amounts arrive signed (negative = outflow).
- ESPP purchase prices: the Stock Plan Benefit History table (DOM-scraped
  best-effort during login; backing API hides in a web worker).

Module layout:
- ``activity.py`` — pure parsers: activities JSON -> records; ESPP lot table ->
  qty->price map; enrichment join.
- ``session.py``  — SeleniumBase login (SMS 2FA via Messages, device-trust) ->
  cookies + stk1 + keyAccountId + ESPP lots.
- ``scraper.py``  — ETradeScraper, implements the BankScraper protocol.
"""

from __future__ import annotations

from notion_finance_sync.banks.etrade.scraper import ETradeScraper

__all__ = ["ETradeScraper"]
