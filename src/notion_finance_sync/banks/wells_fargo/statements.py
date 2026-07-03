"""Wells Fargo statement PDF parser.

This is the real historical data path for the Wells Fargo login (SPEC §4: "Old Bilt
card history lives in Wells Fargo statements"). Alex's card was the **Bilt World Elite
Mastercard** (account ending 6972) before it was converted to the **Wells Fargo
Autograph Visa** (ending 8000) — same issuer, a product rename. The pre-conversion
statements carry all the real transactions; the post-conversion Autograph statements
are (so far) empty.

Two statement layouts:
- **Old Bilt "Transaction Summary" table** (acct 6972):
  ``Trans Date  Post Date  <ref> <ref>  <description>  $Amount[-]``
  A trailing ``-`` on the amount marks money TO the card (payment/credit). The
  reference number is 1-2 alphanumeric tokens; concatenated it is the stable id.
- **New Autograph "Credits/Charges" table** (acct 8000): different columns, and empty
  in practice. Parsed by the same line matcher (it just yields nothing while unused).

Categories are NOT in WF statements, so ``category``/``bank_category`` stay null and the
orchestrator flags these rows Needs Review. Rewards are correlated later by the
``wells_rewards`` / ``bilt_portal`` enrichers.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pdfplumber

from notion_finance_sync.models import (
    AccountType,
    BankName,
    CardNetwork,
    TransactionRecord,
    TransactionStatus,
)

# account last-4 -> (Notion "Credit Card / Account" select value, network)
BILT_LAST4 = "6972"
AUTOGRAPH_LAST4 = "8000"
_CARD_BY_LAST4: dict[str, tuple[str, CardNetwork]] = {
    BILT_LAST4: ("Bilt World Elite Mastercard", CardNetwork.MASTERCARD),
    AUTOGRAPH_LAST4: ("Wells Fargo Autograph", CardNetwork.VISA),
}
_DEFAULT_CARD = ("Bilt World Elite Mastercard", CardNetwork.MASTERCARD)

_CLOSE_DATE_RE = re.compile(
    r"(?:Statement Closing Date|Statement Period\s+to)\s+(\d{2})/(\d{2})/(\d{4})"
)
_ACCT_RE = re.compile(r"(?:Account Number Ending in|Account ending in)\s+(\d{4})")
# a transaction line: two MM/DD dates, a middle (ref + description), then the amount
_TXN_RE = re.compile(r"^(\d{2})/(\d{2})\s+(\d{2})/(\d{2})\s+(.*?)\s+\$([\d,]+\.\d{2})(-?)\s*$")
# a reference token: >=8 chars, uppercase letters/digits only, containing at least one digit
_REF_TOKEN_RE = re.compile(r"^(?=[0-9A-Z]*\d)[0-9A-Z]{8,}$")


def _close_month_year(text: str) -> tuple[int, int] | None:
    m = _CLOSE_DATE_RE.search(text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(3))


def _account_last4(text: str) -> str | None:
    m = _ACCT_RE.search(text)
    return m.group(1) if m else None


def _infer_year(txn_month: int, close_month: int, close_year: int) -> int:
    """A statement spans one cycle; a txn month after the closing month is prior-year."""
    return close_year if txn_month <= close_month else close_year - 1


def _split_reference(middle: str) -> tuple[str, str]:
    """Split the middle field into (concatenated reference, description).

    Reference tokens are the leading run of alphanumeric-with-digit tokens; the rest is
    the merchant description. Returns ("", middle) when no reference is present (e.g. the
    "Interest Charge on Purchases" summary line), which the caller skips.
    """
    tokens = middle.split()
    ref_parts: list[str] = []
    i = 0
    while i < len(tokens) and _REF_TOKEN_RE.match(tokens[i]):
        ref_parts.append(tokens[i])
        i += 1
    return "".join(ref_parts), " ".join(tokens[i:]).strip()


def parse_statement_text(text: str, *, source_name: str = "") -> list[TransactionRecord]:
    """Parse one statement's extracted text into ``TransactionRecord``s."""
    close = _close_month_year(text)
    last4 = _account_last4(text)
    card_name, network = _CARD_BY_LAST4.get(last4 or "", _DEFAULT_CARD)

    records: list[TransactionRecord] = []
    for line in text.splitlines():
        m = _TXN_RE.match(line.strip())
        if not m:
            continue
        trans_month, trans_day = int(m.group(1)), int(m.group(2))
        middle, amount_str, credit_flag = m.group(5), m.group(6), m.group(7)

        source_id, description = _split_reference(middle)
        if not source_id:
            continue  # interest/fee summary line — no reference number

        magnitude = float(amount_str.replace(",", ""))
        amount = magnitude if credit_flag == "-" else -magnitude  # '-' = money to card

        year = _infer_year(trans_month, close[0], close[1]) if close else date.today().year
        txn_date = date(year, trans_month, trans_day)

        records.append(
            TransactionRecord(
                source_id=source_id,
                source_account_id=last4 or "",
                name=description,
                amount=amount,
                transaction_date=txn_date,
                transacted_at=None,
                status=TransactionStatus.POSTED,
                payee=description,
                memo=description,
                bank=BankName.WELLS_FARGO,
                credit_card_account=card_name,
                card_network=network,
                account_type=AccountType.CREDIT_CARD,
                account_name=card_name,
                raw_data={"source_statement": source_name, "account_last4": last4},
            )
        )
    return records


def _extract_text(pdf_path: Path) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def parse(pdf_paths: list[Path]) -> list[TransactionRecord]:
    """Parse WF statement PDFs into ``TransactionRecord``s, deduped by ``source_id``.

    Statements can overlap (a transaction near a cycle boundary may appear on two
    statements); the reference-number ``source_id`` is the stable dedupe key.
    """
    seen: set[str] = set()
    out: list[TransactionRecord] = []
    for path in pdf_paths:
        for rec in parse_statement_text(_extract_text(path), source_name=path.name):
            if rec.source_id in seen:
                continue
            seen.add(rec.source_id)
            out.append(rec)
    return out
