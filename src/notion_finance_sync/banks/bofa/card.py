"""Parse BofA credit-card HTML.

Two sources (both server-rendered HTML):

- **Statement list** ``account-details.go?&adx=<adx>[&stx=<stmt>]`` -> ``parse_statement``:
  table ``#transactions`` with ``tr.trans-first-row`` rows. The stable transaction
  id lives in an HTML comment inside each row; the amount is UNSIGNED so the sign
  is derived from the transaction-type icon (``rel="P"`` = purchase/debit).
- **Per-txn detail** ``transaction-details.go?...&txn=<hash>`` -> ``parse_detail``:
  the rich fields matching the UI (category label, MCC merchant description,
  merchant name, reference, online-purchase flag, true transaction date).

``parse_statement`` returns ``TransactionRecord``s with the fields available on the
list (id, date, payee, signed amount, running balance) and stashes the detail
``txn`` hash in ``raw_data`` so the fetcher can enrich category/merchant via
``parse_detail`` and points via the rewards parser.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime

from bs4 import BeautifulSoup, Comment

from notion_finance_sync.banks.bofa import categories
from notion_finance_sync.models import (
    AccountType,
    BankName,
    TransactionRecord,
    TransactionStatus,
)

_DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
_REF_RE = re.compile(r"Object Reference Identifier ::::: (\S+)")
_TXN_HASH_RE = re.compile(r"[?&]txn=([0-9a-f]+)")
_CODE_RE = re.compile(r"\b(\d{3})\b")

# The transaction-type icon's class (``icon-type-<kind>``) is authoritative for
# SIGN. In our convention negative = spend. Everything is a spend except money
# coming back to the card: payments (paying off the card), credits/returns/refunds.
# NOTE: the amount cell is sometimes already signed (statement views) and sometimes
# not (current-activity view), so we take the MAGNITUDE and apply the sign here —
# never trust the cell's own sign.
_CREDIT_ICON_TYPES = {"payment", "credit", "return", "refund", "deposit"}
_PENDING_TEMP_IDS = {"", "TEMP"}


def _magnitude(text: str) -> float:
    """Absolute dollar value from an amount cell (handles ``-$1,282.88`` or ``$4.78``)."""
    return abs(float(text.replace("$", "").replace(",", "").replace("−", "-").strip() or 0))


def _icon_type(type_cell) -> str:
    """Return the ``icon-type-<kind>`` suffix (e.g. 'purchase', 'payment', 'bank-charge')."""
    if type_cell is None:
        return ""
    div = type_cell.find("div", attrs={"rel": True}) or type_cell.find(
        "div", class_=re.compile("icon-type-")
    )
    if div is None:
        return ""
    for c in div.get("class") or []:
        if c.startswith("icon-type-") and c != "icon-type-image":
            return c[len("icon-type-") :]
    return ""


def _parse_date(text: str):
    m = _DATE_RE.search(text or "")
    return datetime.strptime(m.group(1), "%m/%d/%Y").date() if m else None


def _stable_source_id(
    account_key: str,
    txn_date: date | None,
    amount: float,
    payee: str,
    running_balance: float | None,
) -> str:
    """Content-derived, session-stable id for a card txn.

    BofA's per-row reference is often absent (→ an unstable per-view txn hash),
    which duplicated rows across statement views. Hash stable content instead;
    ``running_balance`` keeps repeated same-day/same-amount txns distinct, and
    ``account_key`` separates the same purchase appearing on different cards.
    """
    key = "|".join(
        (
            account_key,
            str(txn_date or ""),
            f"{amount:.2f}",
            payee.strip(),
            "" if running_balance is None else f"{running_balance:.2f}",
        )
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def parse_statement(html: str, *, account_key: str = "") -> list[TransactionRecord]:
    """Parse a card statement/activity page into ``TransactionRecord``s.

    Category and rewards are NOT set here (enriched later from the per-txn detail
    and rewards pages); ``raw_data["detail_txn_hash"]`` carries the detail key.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="transactions")
    if table is None:
        return []

    records: list[TransactionRecord] = []
    for row in table.find_all("tr"):
        date_cell = row.find("td", attrs={"headers": "transaction-date"})
        amt_cell = row.find("td", attrs={"headers": "transaction-amount"})
        if date_cell is None or amt_cell is None:
            continue  # header / beginning-balance / spacer rows

        is_pending = "trans-pending-row" in (row.get("class") or [])

        # source_id from the HTML comment inside the row
        source_id = ""
        for c in row.find_all(string=lambda t: isinstance(t, Comment)):
            m = _REF_RE.search(c)
            if m:
                source_id = m.group(1)
                break

        txn_date = _parse_date(date_cell.get_text(" ", strip=True))

        desc_cell = row.find("td", attrs={"headers": "transaction-description"})
        payee = ""
        detail_url = detail_hash = None
        if desc_cell is not None:
            a = desc_cell.find("a")
            if a is not None:
                span = a.find("span", class_="ada-hidden")
                if span is not None:
                    span.extract()
                payee = a.get_text(" ", strip=True)
        arrow = row.find("img", class_="expand-trans")
        if arrow is not None:
            rel = arrow.get("rel")
            rel = " ".join(rel) if isinstance(rel, list) else (rel or "")
            detail_url = rel
            hm = _TXN_HASH_RE.search(rel)
            detail_hash = hm.group(1) if hm else None

        # Pending rows carry a placeholder reference ("TEMP"); use the per-txn hash
        # as a stable-ish id so they don't all collide on "TEMP".
        if source_id.upper() in _PENDING_TEMP_IDS and detail_hash:
            source_id = detail_hash

        type_cell = row.find("td", attrs={"headers": "transaction-type"})
        icon_type = _icon_type(type_cell)  # e.g. purchase | payment | fee | bank-charge
        sign = 1.0 if icon_type in _CREDIT_ICON_TYPES else -1.0
        amount = sign * _magnitude(amt_cell.get_text(strip=True))

        bal_cell = row.find("td", attrs={"headers": "balance-resulting-from-this-transaction"})
        running_balance = None
        if bal_cell is not None and bal_cell.get_text(strip=True):
            try:
                running_balance = _magnitude(bal_cell.get_text(strip=True))
            except ValueError:
                running_balance = None

        records.append(
            TransactionRecord(
                source_id=_stable_source_id(account_key, txn_date, amount, payee, running_balance),
                source_account_id="",
                name=payee,
                amount=amount,
                transaction_date=txn_date,
                status=TransactionStatus.PENDING if is_pending else TransactionStatus.POSTED,
                payee=payee,
                memo=payee,
                bank=BankName.BANK_OF_AMERICA,
                account_type=AccountType.CREDIT_CARD,
                raw_data={
                    "bank_ref": source_id,
                    "detail_txn_hash": detail_hash,
                    "detail_url": detail_url,
                    "txn_type": icon_type,
                    "running_balance": running_balance,
                    "pending": is_pending,
                },
            )
        )
    return records


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    """Return {label -> value} from the expanded-detail table."""
    table = soup.find("table", class_=re.compile("trans-expanded-details")) or soup.find(
        "table", id=re.compile("Transaction_Details")
    )
    pairs: dict[str, str] = {}
    if table is None:
        return pairs
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        label = cells[0].get_text(" ", strip=True)
        value = cells[1].get_text(" ", strip=True)
        # labels carry trailing help text ("get more information about ...")
        label = re.split(
            r"\s+(?:get more information|Merchant name change|Transaction Category)", label
        )[0]
        # values carry trailing edit UI ("Select to Edit ...")
        value = re.split(r"\s+Select to\b", value)[0].strip()
        pairs[label.rstrip(":").strip()] = value
    return pairs


def parse_detail(html: str) -> dict:
    """Parse a ``transaction-details.go`` fragment into a detail dict.

    Keys: transaction_date, card_type, transaction_type, merchant_description
    (MCC), reference_number, merchant_name, bank_category, category (canonical),
    online_purchase (bool).
    """
    soup = BeautifulSoup(html, "html.parser")
    pairs = _detail_pairs(soup)

    cat_raw = pairs.get("Transaction Category", "")
    code_m = _CODE_RE.search(cat_raw)
    code = code_m.group(1) if code_m else None
    bank_category = categories.BOFA_CATEGORY_CODE_TO_LABEL.get(code) if code else None
    # fall back to the label text if the code wasn't present
    if bank_category is None and cat_raw:
        label = re.sub(r"^\d+\s*", "", cat_raw).strip()
        bank_category = label or None
    category = (
        categories.canonical_for_code(code)
        if code
        else categories.canonical_for_label(bank_category)
    )

    online = pairs.get("Online Purchase", "").strip().upper()

    return {
        "transaction_date": _parse_date(pairs.get("Transaction date", "")),
        "card_type": pairs.get("Card type") or None,
        "transaction_type": pairs.get("Transaction type") or None,
        "merchant_description": pairs.get("Merchant description") or None,
        "reference_number": pairs.get("Reference number") or None,
        "merchant_name": pairs.get("Merchant Name") or None,
        "bank_category": bank_category,
        "category": category,
        "online_purchase": online == "Y" if online in ("Y", "N") else None,
    }
