"""Parse the Fidelity 401k activity JSON (`transactions/history` response).

Pure: JSON dict -> list[TransactionRecord]. See data/snapshots/fidelity/FINDINGS.md.

Each top-level transaction is one (fund, date, category) event; `dcDetails` is the
per-contribution-source breakdown (Employee ROTH / Employer Match / 3% Basic),
uniform in fund. `amtDetail.net` is signed cash; `quantity` is signed shares.

Fidelity exposes NO native transaction id, so `source_id` is a deterministic
sha256 of the stable identifying tuple (idempotent diff dedupes on it).

"Change in Market Value" rows (txnCatDesc "realizedGainLoss", quantity 0) are
value-restatement noise and are dropped.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from notion_finance_sync.models import (
    AccountType,
    BankName,
    CanonicalCategory,
    CategoryMap,
    TransactionRecord,
    TransactionStatus,
)

# Fidelity txnCatDesc -> canonical category. Contributions/exchanges are money
# moving into / within the retirement account (not spending, not spendable
# income) -> Transfer. Dividends/interest -> Income. Fees -> Other.
CATEGORY_MAP: CategoryMap = {
    "contribution": CanonicalCategory.TRANSFER,
    "exchangeIn": CanonicalCategory.TRANSFER,
    "exchangeOut": CanonicalCategory.TRANSFER,
    "dividend": CanonicalCategory.INCOME,
    "interest": CanonicalCategory.INCOME,
    "fees": CanonicalCategory.OTHER,
}

# txnCatDesc values that are pure value-restatement, not a money/share event.
_SKIP_CATDESC = {"realizedGainLoss"}


def _synth_source_id(t: dict, fund_code: str) -> str:
    parts = [
        str(t.get("acctNum", "")),
        str(t.get("dateDetail", {}).get("tradedDate", t.get("date", ""))),
        str(t.get("catDetail", {}).get("txnTypeCode", "")),
        str(t.get("catDetail", {}).get("txnCatDesc", "")),
        fund_code,
        str(t.get("amtDetail", {}).get("net", "")),
        str(t.get("quantity", "")),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _memo(t: dict) -> str:
    """Per-contribution-source breakdown, e.g. 'ROTH $51.77 + EMPLOYER MATCH $38.83'."""
    parts = []
    for d in t.get("dcDetails", []):
        fd = d.get("fundDetail", {})
        src = fd.get("srcName")
        net = fd.get("net")
        if src is not None and net is not None:
            parts.append(f"{src} ${net:.2f}")
    return " + ".join(parts)


def parse_activity(
    raw: dict,
    *,
    account_name: str = "Capital One 401k ASP",
    source_account_id: str | None = None,
    credit_card_account: str | None = None,
    only_acct: str | None = None,
) -> list[TransactionRecord]:
    """Parse a `transactions/history` response into TransactionRecords.

    Args:
        raw: parsed JSON response body.
        account_name: free-text Notion account label.
        source_account_id: override the bank-native account id (defaults to each
            row's ``acctNum``).
        credit_card_account: curated Notion select value. Left ``None`` until the
            "Capital One 401k" option exists in Notion (see FINDINGS).
        only_acct: if set, keep only rows whose ``acctNum`` matches. The login
            exposes multiple accounts (401k + Roth IRA); this module is 401k-only.
    """
    txns = raw.get("data", {}).get("transactions", [])
    records: list[TransactionRecord] = []
    for t in txns:
        if only_acct is not None and str(t.get("acctNum", "")) != only_acct:
            continue

        cat = t.get("catDetail", {})
        cat_desc = cat.get("txnCatDesc", "")
        if cat_desc in _SKIP_CATDESC:
            continue

        details = t.get("dcDetails", [])
        fund = details[0].get("fundDetail", {}) if details else {}
        fund_code = str(fund.get("fundCode", ""))
        long_name = fund.get("longName", "")
        description = t.get("description", "") or cat_desc

        amt = t.get("amtDetail", {})
        traded = t.get("dateDetail", {}).get("tradedDate") or t.get("date")

        records.append(
            TransactionRecord(
                source_id=_synth_source_id(t, fund_code),
                source_account_id=source_account_id or str(t.get("acctNum", "")),
                name=f"{description}: {long_name}" if long_name else description,
                amount=float(amt.get("net", 0.0)),
                transaction_date=datetime.fromtimestamp(traded, tz=UTC).date(),
                status=TransactionStatus.POSTED,
                payee="",
                memo=_memo(t),
                bank_category=description,
                category=CATEGORY_MAP.get(cat_desc),
                bank=BankName.FIDELITY,
                credit_card_account=credit_card_account,
                account_type=AccountType.FOUR_OH_ONE_K,
                account_name=account_name,
                quantity=float(t["quantity"]) if t.get("quantity") not in (None, "") else None,
                ticker=fund_code or None,
                price_per_share=float(amt["price"]) if amt.get("price") is not None else None,
                raw_data=t,
            )
        )
    return records
