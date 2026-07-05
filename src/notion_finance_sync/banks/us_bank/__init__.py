"""U.S. Bank scraper package.

Covers Cash+ Visa Signature + Harris Teeter Rewards World Elite Mastercard under one
login. ``True Rewards`` is deliberately NULL for U.S. Bank (SPEC §11) — only
``Calculated Rewards`` applies, and there is no rewards enricher.
"""

from __future__ import annotations

from notion_finance_sync.banks.us_bank.scraper import CARD_META, USBankScraper

__all__ = ["CARD_META", "USBankScraper"]
