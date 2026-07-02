"""Validate the live BofA login + cookie -> httpx fetch pipeline end-to-end.

Run this yourself (it opens a real Chrome window and needs your phone for 2FA):

    uv run python scripts/validate_bofa_login.py

Prerequisites:
  1. 1Password CLI signed in:            op signin
  2. A "BofA" login item in the vault:    op://Notion Finance Sync/BofA/{username,password}
  3. (optional) Full Disk Access for the terminal so the SMS 2FA code auto-reads
     from Messages. If not granted, the script pauses for you to type the code
     into the browser.

What it does: logs in via SeleniumBase, extracts cookies, then does a couple of
authenticated fetches (checking activity + one card statement) and parses them —
proving session -> fetchers -> parsers all work against the live site.
"""

from __future__ import annotations

import argparse
import sys

import structlog

from notion_finance_sync.banks.bofa import card, deposit, fetchers, scraper, session

log = structlog.get_logger()


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate live BofA login + fetch pipeline")
    ap.add_argument(
        "--manual",
        action="store_true",
        help="Prompt for credentials instead of reading them from 1Password. "
        "Default reads from the 'Notion Finance Sync' vault (needs `op signin` or "
        "OP_SERVICE_ACCOUNT_TOKEN).",
    )
    args = ap.parse_args()
    auth = "manual" if args.manual else "service_account"

    print("=" * 70)
    print(f"BofA live login validation ({auth}) — a Chrome window will open. Log in / 2FA.")
    print("=" * 70)

    try:
        cookies = session.login_and_get_cookies("bofa", auth=auth, interactive=True)
    except Exception as exc:  # noqa: BLE001
        print(f"\n[FAIL] login raised: {type(exc).__name__}: {exc}")
        if auth == "service_account":
            print("  Reading creds from 1Password failed. Most likely:")
            print("    1. Not signed in — run:  eval $(op signin)")
            print("       (or export OP_SERVICE_ACCOUNT_TOKEN for unattended use)")
            print("    2. Item/field names differ — expected:")
            print("       op read 'op://Notion Finance Sync/BofA/username'")
            print("       op read 'op://Notion Finance Sync/BofA/password'")
            print("  Or bypass 1Password for this run:  just validate-bofa-login --manual")
        return 1

    print(f"\n[OK] logged in, {len(cookies)} cookies captured.")
    client = session.build_client(cookies)
    try:
        # 1) checking activity (JSON API)
        adx_checking = scraper.DEPOSIT_ACCOUNTS["Adv Plus Banking - 2093"]
        raw = fetchers.fetch_deposit_activity(client, adx_checking, page_size=50, max_pages=1)
        dep_recs = deposit.parse_activity(raw, account_name="Adv Plus Banking - 2093")
        print(f"[OK] checking: fetched + parsed {len(dep_recs)} transactions")
        if dep_recs:
            r = dep_recs[0]
            print(f"     newest: {r.transaction_date} {r.amount:+.2f} cat={r.category}")
            print(f"             {r.name[:50]!r}")

        # 2) one card statement (HTML)
        adx_card = scraper.CARD_ACCOUNTS["Travel Rewards Visa Signature - 9766"]
        html = fetchers.fetch_card_statement(client, adx_card)
        rows = card.parse_statement(html)
        print(f"[OK] Travel Rewards: fetched + parsed {len(rows)} statement rows")
        if rows:
            r = rows[0]
            print(f"     newest: {r.transaction_date} {r.amount:+.2f} {r.name[:40]!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n[FAIL] authenticated fetch raised: {type(exc).__name__}: {exc}")
        return 2
    finally:
        client.close()

    print("\n[SUCCESS] login + cookies + fetch + parse all work against live BofA.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
