"""BofAScraper — implements the BankScraper protocol.

ONE BofA login covers many accounts (cards + checking + savings + Roth IRA +
Investment Mgmt). This scraper handles the spending accounts (cards + deposit);
investment accounts are handled by ``bofa_investments.py`` sharing the session.

Flow (``fetch_recent``): SeleniumBase login -> cookies -> httpx client, then per
account:
- **cards**: statement HTML -> rows; per-txn detail -> category/merchant;
  rewards landing -> points; ``assemble.enrich_card_records`` -> complete records.
- **deposit**: activity JSON -> records (category inline).

The heavy lifting lives in the pure, unit-tested parser modules (categories,
deposit, card, rewards, assemble). Only ``session``/``fetchers`` do I/O.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import structlog

from notion_finance_sync.banks._base import UnsupportedOperation  # noqa: F401  (protocol docs)
from notion_finance_sync.banks.bofa import assemble, card, deposit, fetchers, rewards, session
from notion_finance_sync.banks.bofa.categories import BOFA_LABEL_TO_CANONICAL
from notion_finance_sync.models import AccountType, RewardsType, TransactionRecord

logger = structlog.get_logger()

# adx = per-account key (captured 2026-07-02). adx appeared stable across the
# recon session; if BofA rotates them, discover from the overview page instead.
_ADX_TRAVEL = "b806e631f5d9f6f8c3f1219b494f5f618e6d3bfa4b1e08223a93cad31aaabe1f"
_ADX_UNLIMITED = "36669a998734ec16bc9e1f821356a70f053754bb7c5f98113bb867306570a81a"
_ADX_KOMEN = "88d5f9612acc5f66c5b161d35283aa98dbe3e871a235d70ea482fc8b47b244e1"
_ADX_AQHA = "ea9f1a3994275222d040982ef9dd109c9265be8cf05efbe5421911853adbbad0"
_ADX_NEA = "2ccfa14ae4beaf80b3e158016ca8fde363134b379b998e0ebc9d7d89d4e5ae48"
_ADX_CHECKING = "72d830ffef2341a8318fb3a27044db3f150c2f5c80d16c097218f388aedc5f5e"

CARD_ACCOUNTS: dict[str, str] = {
    "Travel Rewards Visa Signature - 9766": _ADX_TRAVEL,
    "Unlimited Cash Rewards Visa Signature - 3510": _ADX_UNLIMITED,
    "Susan G. Komen For the Cure Visa Signature - 1762": _ADX_KOMEN,
    "American Quarter Horse Association Visa Signature - 1656": _ADX_AQHA,
    "National Education Association Visa Signature - 8202": _ADX_NEA,
}
DEPOSIT_ACCOUNTS: dict[str, str] = {
    "Adv Plus Banking - 2093": _ADX_CHECKING,
}

# BofA account display name -> the curated Notion "Credit Card / Account" select
# value (Notion uses short human names, not the raw "... Visa Signature - 9766").
# Writing the raw name would auto-create duplicate options, so map to these.
NOTION_ACCOUNT: dict[str, str] = {
    "Travel Rewards Visa Signature - 9766": "Travel Rewards",
    "Unlimited Cash Rewards Visa Signature - 3510": "Unlimited Cash Rewards",
    "Susan G. Komen For the Cure Visa Signature - 1762": "Susan G. Komen Customized Cash Rewards",
    "American Quarter Horse Association Visa Signature - 1656": "AQHA Customized Cash Rewards",
    "National Education Association Visa Signature - 8202": "NEA Customized Cash Rewards",
    "Adv Plus Banking - 2093": "Advantage Plus",
}

# Each card's rewards currency. Travel Rewards earns points; the Cash Rewards
# cards earn cashback. Tells Notion whether True/Calculated Rewards are $ or pts.
CARD_REWARD_TYPE: dict[str, RewardsType] = {
    "Travel Rewards Visa Signature - 9766": RewardsType.POINTS,
    "Unlimited Cash Rewards Visa Signature - 3510": RewardsType.CASHBACK,
    "Susan G. Komen For the Cure Visa Signature - 1762": RewardsType.CASHBACK,
    "American Quarter Horse Association Visa Signature - 1656": RewardsType.CASHBACK,
    "National Education Association Visa Signature - 8202": RewardsType.CASHBACK,
}


class BofAScraper:
    SESSION_ID = "bofa"
    BANK_DISPLAY_NAME = "BofA"
    SUPPORTS_LIVE = True

    # BankScraper protocol's CATEGORY_MAP (BofA label -> canonical).
    CATEGORY_MAP = BOFA_LABEL_TO_CANONICAL

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        cookies = session.login_and_get_cookies(self.SESSION_ID)
        client = session.build_client(cookies)
        try:
            records: list[TransactionRecord] = []
            for name, adx in CARD_ACCOUNTS.items():
                records.extend(self._fetch_card(client, name, adx, since))
            for name, adx in DEPOSIT_ACCOUNTS.items():
                records.extend(self._fetch_deposit(client, name, adx, since))
            return records
        finally:
            client.close()

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        """Full live history in [start, end]: iterate card statements + deposit cursor.

        Cards reach ~12 months live (statement dropdown); deposit ~13 months
        (cursor). Anything older comes from the PDF backfill.
        """
        cookies = session.login_and_get_cookies(self.SESSION_ID)
        client = session.build_client(cookies)
        try:
            records: list[TransactionRecord] = []
            for name, adx in CARD_ACCOUNTS.items():
                records.extend(self._fetch_card_historical(client, name, adx, start, end))
            for name, adx in DEPOSIT_ACCOUNTS.items():
                recs = self._fetch_deposit(client, name, adx, start)
                records.extend(r for r in recs if r.transaction_date and r.transaction_date <= end)
            return records
        finally:
            client.close()

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError("TODO: download statement PDFs to data/statements/bofa/")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        from notion_finance_sync.backfill.pdf_parsers import bofa as bofa_pdf

        return bofa_pdf.parse(pdf_paths)

    # ------------------------------------------------------------------
    # per-account helpers (I/O + assembly)
    # ------------------------------------------------------------------
    def _rewards_entries(self, client, name: str, adx: str) -> list[dict]:
        try:
            return rewards.parse_rewards(fetchers.fetch_rewards(client, adx))
        except Exception as exc:  # noqa: BLE001 — rewards are best-effort enrichment
            logger.warning("bofa_card_rewards_failed", card=name, error=str(exc))
            return []

    def _scrape_card_html(
        self, client, name: str, adx: str, html: str, reward_entries: list[dict]
    ) -> list[TransactionRecord]:
        """Parse one card statement HTML, enrich each row with detail + rewards."""
        rows = card.parse_statement(html, account_key=name)
        detail_map: dict[str, str] = {}
        for r in rows:
            url = r.raw_data.get("detail_url")
            h = r.raw_data.get("detail_txn_hash")
            if url and h:
                try:
                    detail_map[h] = fetchers.fetch_detail_by_url(client, url)
                except Exception as exc:  # noqa: BLE001 — one bad detail shouldn't sink the run
                    logger.warning("bofa_card_detail_failed", card=name, error=str(exc))
        assemble.enrich_card_records(rows, detail_map, reward_entries)
        reward_type = CARD_REWARD_TYPE.get(name)
        for r in rows:
            r.account_name = name  # full descriptive name (free-text field)
            r.credit_card_account = NOTION_ACCOUNT.get(name)  # curated Notion select value
            r.rewards_type = reward_type
        return rows

    def _fetch_card(self, client, name: str, adx: str, since: date) -> list[TransactionRecord]:
        entries = self._rewards_entries(client, name, adx)
        html = fetchers.fetch_card_statement(client, adx)
        rows = self._scrape_card_html(client, name, adx, html, entries)
        rows = [r for r in rows if r.transaction_date and r.transaction_date >= since]
        logger.info("bofa_card_scraped", card=name, count=len(rows))
        return rows

    def _fetch_card_historical(
        self, client, name: str, adx: str, start: date, end: date
    ) -> list[TransactionRecord]:
        # Rewards landing is current-period only; match what we can, older rows
        # keep true_rewards=None (enrich later if BofA exposes historical rewards).
        entries = self._rewards_entries(client, name, adx)
        current_html = fetchers.fetch_card_statement(client, adx)
        stx_options = fetchers.statement_stx_options(current_html)

        collected = self._scrape_card_html(client, name, adx, current_html, entries)
        for _label, stx in stx_options:
            stmt_html = fetchers.fetch_card_statement(client, adx, stx=stx)
            collected.extend(self._scrape_card_html(client, name, adx, stmt_html, entries))

        collected = assemble.dedupe_by_source_id(collected)
        collected = [
            r for r in collected if r.transaction_date and start <= r.transaction_date <= end
        ]
        logger.info(
            "bofa_card_historical", card=name, count=len(collected), statements=len(stx_options) + 1
        )
        return collected

    def _fetch_deposit(self, client, name: str, adx: str, since: date) -> list[TransactionRecord]:
        raw = fetchers.fetch_deposit_activity(client, adx)
        recs = deposit.parse_activity(raw, account_name=name, account_type=AccountType.CHECKING)
        notion_acct = NOTION_ACCOUNT.get(name)
        for r in recs:
            r.credit_card_account = notion_acct
        recs = [r for r in recs if r.transaction_date and r.transaction_date >= since]
        # The list JSON truncates long descriptions; fetch each in-window txn's
        # detail (the UI 'View/Edit' endpoint) for the full untruncated text.
        for r in recs:
            token = (r.raw_data or {}).get("transactionToken")
            if not token:
                continue
            full = fetchers.fetch_deposit_detail(client, token, adx)
            if full:
                r.name = r.payee = r.memo = full
        logger.info("bofa_deposit_scraped", account=name, count=len(recs))
        return recs
