"""One-shot recon capture for the Bank of America transactions flow.

This is NOT the scraper. It's a human-in-the-loop discovery tool: it opens the
persistent-profile Chrome (the same profile the real scraper will reuse, so the
device-trust cookie earned here carries forward), pauses while YOU log in and
navigate to your transactions, then snapshots everything we need to design the
parser offline:

- rendered page HTML          -> so we can build an HTML-table parser
- current URL + cookies       -> for the scraper's navigation + session reuse
- a screenshot                -> quick visual reference

It ALSO walks you through exporting a HAR from DevTools, which captures every
XHR/fetch request *and its response body* — that's how we discover whether BofA
serves transactions from a JSON backend endpoint (SPEC §18: the backend often
exposes a deeper date range than the UI filter and is ~10x easier to parse than
scraping the DOM).

Run it yourself (needs your phone for 2FA):

    uv run python scripts/recon_bofa.py

Nothing here touches BofA until you drive the browser. Credentials are typed by
hand for this first contact — no 1Password automation yet.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from notion_finance_sync.browser.factory import open_session

SNAPSHOT_ROOT = Path(__file__).resolve().parents[1] / "data" / "snapshots" / "bofa"
START_URL = "https://www.bankofamerica.com/"


def _banner(msg: str) -> None:
    line = "=" * 72
    print(f"\n{line}\n{msg}\n{line}\n")


def _capture(sb, out_dir: Path, label: str) -> None:
    """Best-effort snapshot of the current page state into out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        url = sb.cdp.get_current_url()
        (out_dir / "url.txt").write_text(url + "\n")
        print(f"  [ok] URL         -> {url}")
    except Exception as e:  # noqa: BLE001 — recon: log and keep going
        print(f"  [!!] URL capture failed: {e!r}")

    try:
        html = sb.cdp.get_page_source()
        (out_dir / "page.html").write_text(html)
        print(f"  [ok] HTML        -> page.html ({len(html):,} bytes)")
    except Exception as e:  # noqa: BLE001
        print(f"  [!!] HTML capture failed: {e!r}")

    try:
        sb.cdp.save_screenshot(f"{label}.png", folder=str(out_dir))
        print(f"  [ok] screenshot  -> {label}.png")
    except Exception as e:  # noqa: BLE001
        print(f"  [!!] screenshot failed: {e!r}")

    try:
        sb.cdp.save_cookies(f"{label}_cookies.txt", folder=str(out_dir))
        print(f"  [ok] cookies     -> {label}_cookies.txt")
    except Exception as e:  # noqa: BLE001
        print(f"  [!!] cookie save failed: {e!r}")


def main() -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = SNAPSHOT_ROOT / f"recon_{stamp}"

    _banner("BofA recon capture — opening persistent-profile Chrome")
    print("A Chrome window will open. In it:")
    print("  1. Log in to Bank of America (do the SMS/email 2FA on your phone).")
    print("  2. Navigate to the account whose transactions you want to scrape")
    print("     (start with one card — e.g. the Travel Rewards card).")
    print("  3. Get the FULL transaction list on screen (expand any date filter,")
    print("     scroll so more rows load if it's lazy-loaded).")
    print("\nLeave the browser open — come back to THIS terminal when ready.")

    with open_session("bofa") as sb:
        sb.activate_cdp_mode(START_URL)

        input("\n>>> Press ENTER once you're logged in and viewing transactions... ")
        _banner("Capturing transactions page")
        _capture(sb, out_dir, "transactions")

        _banner("Now capture the network traffic (this finds the JSON endpoint)")
        print("In the Chrome window:")
        print("  1. Open DevTools:  View > Developer > Developer Tools  (or Cmd+Opt+I)")
        print("  2. Click the  Network  tab.")
        print("  3. Check 'Preserve log'. Filter by  Fetch/XHR.")
        print("  4. Reload the transactions page (Cmd+R) so requests are recorded.")
        print("  5. Right-click anywhere in the request list ->")
        print("     'Save all as HAR with content'  (or the export/download icon).")
        print(f"  6. Save the .har file into:\n       {out_dir}")
        print("\n  (The HAR captures request URLs AND response bodies — that's what")
        print("   lets us see if transactions come from a parseable JSON endpoint.)")

        input("\n>>> Press ENTER after you've saved the .har (or to skip)... ")

        # Re-capture in case navigation/reload changed the DOM.
        _banner("Final capture")
        _capture(sb, out_dir, "final")

    _banner("Done")
    print(f"Everything saved under:\n  {out_dir}\n")
    print("Contents:")
    for p in sorted(out_dir.glob("*")):
        print(f"  - {p.name}")
    print("\nTell me the folder name and I'll analyze it + build the parser (TDD).")


if __name__ == "__main__":
    main()
