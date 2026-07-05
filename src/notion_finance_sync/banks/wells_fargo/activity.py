"""Pure parser for the Wells Fargo online activity JSON response.

The SPA's ``POST /accounts/inquiry/accountdetails/home/{id}/transactions/fetch`` returns
a body wrapped in a proprietary envelope: ``/*WellFargoProprietary%<json>%WellFargoProprietary*/``.

As of recon (2026-07-03) Alex's only card (Autograph …8000) has ZERO transactions, so a
POPULATED per-transaction object was never observed and its field shape is unknown. This
module therefore does exactly what the live scraper needs today: strip the envelope and
read the ``transactionCount``. A non-zero count is the trigger to notify Alex to build out
the full online parser (see ``scraper.fetch_recent``).
"""

from __future__ import annotations

import json
from typing import Any

_PREFIX = "WellFargoProprietary%"
_SUFFIX = "%WellFargoProprietary"


def strip_envelope(body: str) -> dict[str, Any]:
    """Strip the ``/*WellFargoProprietary%...%WellFargoProprietary*/`` wrapper and parse JSON."""
    s = body.strip()
    start = s.find(_PREFIX)
    if start != -1:
        s = s[start + len(_PREFIX) :]
    end = s.rfind(_SUFFIX)
    if end != -1:
        s = s[:end]
    return json.loads(s)


def transaction_count(data: dict[str, Any]) -> int:
    """Return the reported transaction count from a parsed activity response (0 if absent)."""
    td = data.get("transactions", {}).get("transactionData", {})
    criteria = td.get("requestedCriteria", {})
    for source in (criteria, td):
        value = source.get("transactionCount")
        if isinstance(value, int):
            return value
    return 0


def has_transactions(body: str) -> bool:
    """True when the raw activity response reports at least one transaction."""
    return transaction_count(strip_envelope(body)) > 0
