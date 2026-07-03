"""HTTP fetchers for BofA endpoints.

All take an authenticated ``httpx.Client`` (cookies minted by ``session.py``) and
return raw payloads for the pure parsers. Kept thin and side-effect-free beyond
the HTTP call so they can be mocked (respx) or driven from captured fixtures.

Endpoints (see data/snapshots/bofa/backfill/BACKFILL_STATUS.md):
- deposit:  POST /ogateway/addapi/v1/activity           (JSON, cursor paginated)
- card list: GET /myaccounts/details/card/account-details.go
- card detail: GET /myaccounts/details/card/transaction-details.go
- rewards:  GET /customer/myrewards/points/landing.go    (HTML)
"""

from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

ORIGIN = "https://secure.bankofamerica.com"

_CARD_BASE = ORIGIN + "/myaccounts/details/card/account-details.go"
_CARD_DETAIL = ORIGIN + "/myaccounts/details/card/transaction-details.go"
_DEPOSIT_ACTIVITY = ORIGIN + "/ogateway/addapi/v1/activity"
_DEPOSIT_DETAIL = ORIGIN + "/ogateway/addapi/v1/transaction/detail/content"
_REWARDS_LANDING = ORIGIN + "/customer/myrewards/points/landing.go"


def fetch_deposit_detail(client: httpx.Client, transaction_token: str, account_token: str) -> str:
    """Full untruncated description for one deposit txn (the UI 'View/Edit' call).

    The activity list truncates ``preferredDescription`` to ~64 chars; this
    per-txn endpoint returns ``payload.transaction.longDescription`` in full.
    Returns "" on any failure so a bad detail never breaks the whole scrape.
    """
    try:
        resp = client.post(
            _DEPOSIT_DETAIL,
            json={
                "payload": {"transactionToken": transaction_token, "accountToken": account_token}
            },
            headers={"request-locale": "en-us"},
        )
        resp.raise_for_status()
        txn = resp.json().get("payload", {}).get("transaction", {})
        return (txn.get("longDescription") or txn.get("shortDescription") or "").strip()
    except Exception:  # noqa: BLE001 — enrichment is best-effort
        return ""
_STMT_STX_RE = re.compile(r"stx=([0-9a-f]+)&(?:amp;)?target=stmtFromDateList")


# --------------------------------------------------------------------------
# deposit (checking / savings) — JSON API, cursor paginated
# --------------------------------------------------------------------------
def fetch_deposit_activity(
    client: httpx.Client, adx: str, *, page_size: int = 300, max_pages: int = 40
) -> dict:
    """Fetch ALL available deposit activity, following the paging cursor.

    Returns a synthesized response dict of the same shape the parser expects
    (``payload.depositActivity.transactionList.transactions`` holding every page
    concatenated).
    """
    all_txns: list[dict] = []
    summary = None
    token = None
    for _ in range(max_pages):
        paging = {"pagingRequestedItemCount": page_size}
        if token:
            paging["pagingRequestedItemToken"] = token
        resp = client.post(
            _DEPOSIT_ACTIVITY,
            json={"payload": {"accountToken": adx}, "pagingRules": paging},
            headers={"request-locale": "en-us"},
        )
        resp.raise_for_status()
        data = resp.json()
        activity = data.get("payload", {}).get("depositActivity", {})
        if summary is None:
            summary = activity.get("summary")
        txns = activity.get("transactionList", {}).get("transactions", [])
        if not txns:
            break
        all_txns.extend(txns)
        token = data.get("pagingRules", {}).get("pagingNextPageItemToken")
        if not token:
            break
    return {
        "payload": {
            "depositActivity": {
                "summary": summary,
                "transactionList": {"transactions": all_txns},
            }
        }
    }


# --------------------------------------------------------------------------
# card — HTML statement list + per-txn detail + rewards
# --------------------------------------------------------------------------
def fetch_card_statement(client: httpx.Client, adx: str, stx: str | None = None) -> str:
    """Fetch a card's activity/statement HTML (current if ``stx`` is None)."""
    params = {"adx": adx}
    if stx:
        params["stx"] = stx
        params["target"] = "stmtFromDateList"
    resp = client.get(_CARD_BASE, params=params, headers={"request-locale": "en-us"})
    resp.raise_for_status()
    return resp.text


def statement_stx_options(statement_html: str) -> list[tuple[str, str]]:
    """Return ``[(label, stx)]`` from the statement dropdown (for pagination)."""
    soup = BeautifulSoup(statement_html, "html.parser")
    sel = None
    for s in soup.find_all("select"):
        if any("Current transactions" in (o.get_text() or "") for o in s.find_all("option")):
            sel = s
            break
    if sel is None:
        return []
    out: list[tuple[str, str]] = []
    for opt in sel.find_all("option"):
        label = opt.get_text(strip=True)
        m = _STMT_STX_RE.search(opt.get("value") or "")
        if m and "Current" not in label:
            out.append((label, m.group(1)))
    return out


def fetch_txn_detail(client: httpx.Client, adx: str, stx: str, txn: str) -> str:
    """Fetch a single transaction's detail HTML fragment."""
    resp = client.get(
        _CARD_DETAIL,
        params={"adx": adx, "stx": stx, "txn": txn},
        headers={"request-locale": "en-us"},
    )
    resp.raise_for_status()
    return resp.text


def fetch_detail_by_url(client: httpx.Client, detail_url: str) -> str:
    """Fetch a txn detail fragment from the full URL stashed on a statement row.

    Statement rows carry the ready-made ``transaction-details.go?...`` path in
    ``raw_data['detail_url']`` (from the row's expand arrow), so we don't have to
    reconstruct adx/stx/txn.
    """
    url = detail_url if detail_url.startswith("http") else ORIGIN + detail_url
    resp = client.get(url, headers={"request-locale": "en-us"})
    resp.raise_for_status()
    return resp.text


def fetch_rewards(client: httpx.Client, adx: str) -> str:
    """Fetch a card's rewards landing HTML (per-transaction points)."""
    resp = client.get(
        _REWARDS_LANDING,
        params={"request_locale": "en-us", "adx": adx},
        headers={"request-locale": "en-us"},
    )
    resp.raise_for_status()
    return resp.text
