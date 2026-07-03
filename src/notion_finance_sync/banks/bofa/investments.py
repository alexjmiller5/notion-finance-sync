"""Parse the BofA Private Bank (U.S. Trust) Roth IRA holdings page.

The IRA SSOs (gcslsso) into ``auth.privatebank.bankofamerica.com``; the full
positions live at ``/Holdings/HoldingsBySecurity.aspx`` in a grid table
(``id="hbsGridCtl_tablei_0"``). Columns: Security ("TICKER NAME"), Quantity,
Price, Price Change, Value, ...

This parser is pure: HTML -> ``list[HoldingSnapshot]`` (current positions). Cash
/ unsettled-cash / total rows are skipped. Per Alex: holdings *value over time*
is a frontend concern (shares x live price) — we only capture the positions.
"""

from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup

from notion_finance_sync.models import HoldingSnapshot

_GRID_ID = "hbsGridCtl_tablei_0"
# The security cell reads "... holding VUG VANGUARD GROWTH ETF"; grab the symbol.
_TICKER_RE = re.compile(r"holding\s+([A-Z]{1,6})\b")


def _num(text: str) -> float | None:
    t = (text or "").replace(",", "").replace("$", "").strip()
    if not t or t in ("--", "N/A"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def parse_holdings(html: str, *, account_id: str, snapshot_date: date) -> list[HoldingSnapshot]:
    """Parse HoldingsBySecurity.aspx into current positions.

    Each data row is ``[icon, "... holding TICKER NAME", quantity, price, ...]``.
    Ticker comes from the ``holding <TICKER>`` phrase in the security cell; rows
    without one (the money-market sweep, unsettled cash, totals/footers) or
    without a nonzero quantity are skipped.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id=_GRID_ID)
    if table is None:
        return []

    holdings: list[HoldingSnapshot] = []
    for row in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
        if len(cells) < 4:
            continue  # header / footer / spacer
        m = _TICKER_RE.search(cells[1])
        if not m:
            continue  # money-market sweep / unsettled cash / total rows
        ticker = m.group(1)
        quantity = _num(cells[2])
        if quantity is None or quantity == 0:
            continue  # total rows / zero positions
        holdings.append(
            HoldingSnapshot(
                account_id=account_id,
                snapshot_date=snapshot_date,
                ticker=ticker,
                quantity=quantity,
                price_per_share=_num(cells[3]),
            )
        )
    return holdings
