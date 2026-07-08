"""EverBank scraper package.

EverBank runs the FIS Digital One / Temenos consumer platform — an Angular SPA
backed by a JSON service bus. One login covers a single high-yield savings account
(Alex's transfer hub). Layout:

- `parser.py`  — pure: TransactionInqSVC ``result[]`` -> TransactionRecord + categorization
- `session.py` — SeleniumBase login -> cookies -> httpx client + JSON service calls
- `scraper.py` — EverbankScraper, implements the BankScraper protocol

See `data/snapshots/everbank/FINDINGS.md` for the endpoint reference.
"""

from __future__ import annotations

from notion_finance_sync.banks.everbank.scraper import EverbankScraper

__all__ = ["EverbankScraper"]
