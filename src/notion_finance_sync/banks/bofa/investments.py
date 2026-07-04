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

import hashlib
import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from notion_finance_sync.models import (
    AccountType,
    BankName,
    CanonicalCategory,
    HoldingSnapshot,
    TransactionRecord,
    TransactionStatus,
)

_GRID_ID = "hbsGridCtl_tablei_0"
# The security cell reads "... holding VUG VANGUARD GROWTH ETF"; grab the symbol.
_TICKER_RE = re.compile(r"holding\s+([A-Z]{1,6})\b")

_ACTIVITY_GRID = "allActivityDetail_tablei_0"
_ROW_SUFFIX_RE = re.compile(r"(\d+_\d+)$")  # pairs a data row with its footer detail row
# The portal double-books income cash->principal as internal transfers (they net
# to zero and just clutter the feed) — drop them.
_SKIP_MINOR = ("intra account trsf",)
# Minor category (lowercased) -> our canonical category. Unknown -> None so
# compute_review_status flags it for manual review.
_MINOR_TO_CATEGORY = {
    "dividends-taxable": CanonicalCategory.INCOME,
    "dividends-nontaxable": CanonicalCategory.INCOME,
    "interest-taxable": CanonicalCategory.INCOME,
    "interest-nontaxable": CanonicalCategory.INCOME,
    "administrative expenses": CanonicalCategory.OTHER,
}


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


def _parse_mdy(text: str) -> date | None:
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _activity_source_id(
    account_id: str, d: date | None, minor: str, amount: float, detail: str
) -> str:
    key = "|".join((account_id, str(d or ""), minor.strip(), f"{amount:.2f}", detail.strip()))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def parse_activity(
    html: str, *, account_name: str, source_account_id: str = ""
) -> list[TransactionRecord]:
    """Parse the IRA Activity feed (/TFPActivity/Activity.aspx) into records.

    Each economic event is a data row ``allActivityDetail_tr_<g>_<i>`` (Trade
    Date, Settlement Date, Major/Minor Category, Quantity, Income Amount,
    Principal Amount, Cost, Realized G/L) followed by a detail row
    ``footerTr_<g>_<i>`` holding the full description ("DIV .3772 A SHARE ON
    35.000 VANGUARD FTSE DEVELOPED MARKETS ETF"). Amount = income + principal
    (dividends/interest land in income; fees/buys/sells/contributions in
    principal). Internal income->principal transfers are dropped.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id=_ACTIVITY_GRID)
    if table is None:
        return []

    details: dict[str, str] = {}
    for r in table.find_all("tr"):
        rid = r.get("id") or ""
        if "footerTr" in rid:
            m = _ROW_SUFFIX_RE.search(rid)
            if m:
                details[m.group(1)] = r.get_text(" ", strip=True)

    records: list[TransactionRecord] = []
    for r in table.find_all("tr"):
        rid = r.get("id") or ""
        if "_tr_" not in rid or "footerTr" in rid:
            continue  # header / detail / spacer
        cells = [c.get_text(" ", strip=True) for c in r.find_all("td")]
        if len(cells) < 8:
            continue
        minor = cells[4]
        if any(s in minor.lower() for s in _SKIP_MINOR):
            continue
        major = cells[3]
        trade_date = _parse_mdy(cells[1])
        quantity = _num(cells[5])
        amount = round((_num(cells[6]) or 0.0) + (_num(cells[7]) or 0.0), 2)
        m = _ROW_SUFFIX_RE.search(rid)
        detail = details.get(m.group(1), "") if m else ""
        name = detail or f"{major}: {minor}"
        records.append(
            TransactionRecord(
                source_id=_activity_source_id(source_account_id, trade_date, minor, amount, detail),
                source_account_id=source_account_id,
                name=name,
                amount=amount,
                transaction_date=trade_date,
                status=TransactionStatus.POSTED,
                payee=name,
                memo=name,
                bank_category=f"{major}: {minor}",
                category=_MINOR_TO_CATEGORY.get(minor.strip().lower()),
                bank=BankName.BANK_OF_AMERICA,
                account_type=AccountType.IRA,
                account_name=account_name,
                quantity=quantity if quantity else None,
                raw_data={"major": major, "minor": minor, "detail": detail},
            )
        )
    return records
