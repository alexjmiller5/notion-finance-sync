"""Bank of America scraper package.

BofA needs several data sources (all reached with the authenticated session's
cookies via httpx, except the SeleniumBase login that mints those cookies):

- Credit cards: server-rendered HTML `account-details.go` (transaction list) +
  `transaction-details.go` per txn (category label, MCC, merchant) + rewards
  `myrewards/points/landing.go` (per-txn points: base + relationship bonus).
- Deposit (checking/savings): JSON API `addapi/v1/activity` (cursor paginated).

Module layout:
- `categories.py` — BofA category code/label -> canonical category
- `deposit.py`    — parse the deposit JSON activity response
- `card.py`       — parse card statement HTML + per-txn detail HTML
- `rewards.py`    — parse the rewards landing HTML + match points to txns
- `session.py`    — SeleniumBase login -> cookies -> httpx client (integration)
- `scraper.py`    — BofAScraper, implements the BankScraper protocol

See `data/snapshots/bofa/backfill/BACKFILL_STATUS.md` for the endpoint reference
and captured fixtures the parsers are tested against.
"""

from __future__ import annotations

from notion_finance_sync.banks.bofa.scraper import BofAScraper

__all__ = ["BofAScraper"]
