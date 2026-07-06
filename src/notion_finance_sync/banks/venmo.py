"""Venmo scraper — Venmo **web API** (account.venmo.com/api/stories), cookie-authed.

Why this and not the mobile API / web login form:
- The mobile app API (api.venmo.com) gives a long-lived token, but its password grant
  is blocked by a 240 security lock and can't be minted headless.
- The web login form (id.venmo.com) is behind DataDome (captcha on the auth POST).

The web *API* (`account.venmo.com/api/stories`) is cookie-authed and works from plain
``httpx`` once you have a logged-in session's cookies — DataDome does NOT block the
authenticated JSON GET. Cookies are captured once via ``scripts/venmo_web_capture.py``
(auto-fills creds, auto-reads the SMS 2FA from Messages, ticks "remember this device",
saves cookies + the user's external id). The daily sync then just replays those cookies.
Re-run the capture when the session expires. See ``data/snapshots/venmo/FINDINGS.md``.

Field mapping (SPEC §17):
- ``name``     "Sent to {person}" / "Received from {person}" (from the signed amount)
- ``payee``    counterparty display name
- ``memo``     Venmo note (``note.content``)
- ``amount``   signed: the web ``amount`` is a display string ("- $7.61" / "+ $13.00")
- ``category`` **None** — Venmo doesn't categorize; SPEC §11 → Needs Review
- ``transacted_at`` the real UTC timestamp (``date``) → derives the Eastern
               ``transaction_date``; model-only, never written to Notion
- ``bank`` Venmo · ``account_type`` P2P · ``credit_card_account`` "Venmo Account".

Only ``type == "payment"`` stories are records (P2P sends/receives). ``transfer``
stories are Venmo↔bank cash-outs (own-money moves the bank side already records) and
are skipped. The pure ``parse_stories`` half is offline/TDD-tested.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import structlog

from notion_finance_sync.config.paths import SESSIONS_DIR
from notion_finance_sync.models import (
    AccountType,
    BankName,
    CanonicalCategory,
    CategoryMap,
    TransactionRecord,
    TransactionStatus,
)

logger = structlog.get_logger()

ORIGIN = "https://account.venmo.com"
EASTERN = ZoneInfo("America/New_York")
NOTION_ACCOUNT = "Venmo Account"  # curated Notion "Credit Card / Account" select value
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

_SESSION_DIR = SESSIONS_DIR / "venmo"
_COOKIES_FILE = _SESSION_DIR / "cookies.json"
_EXTID_FILE = _SESSION_DIR / "external_id.txt"

_AMOUNT_RE = re.compile(r"([\d,]+\.?\d*)")


def _parse_amount(s: str | None) -> float:
    """Parse Venmo's display amount ("- $7.61" / "+ $13.00" / "$13.00") to a float."""
    s = (s or "").strip()
    m = _AMOUNT_RE.search(s)
    if not m:
        return 0.0
    value = float(m.group(1).replace(",", ""))
    return -value if s.lstrip().startswith("-") else value


def _parse_ts(s: str | None) -> datetime | None:
    """Parse a Venmo ISO datetime (UTC, no offset) into a tz-aware UTC datetime."""
    if not s:
        return None
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def parse_stories(raw: dict, *, my_user_id: str) -> list[TransactionRecord]:
    """Parse an ``/api/stories?feedType=me`` response into TransactionRecords.

    Handles every story ``type`` Venmo emits:

    - ``payment`` / ``authorization`` / ``disbursement`` — signed ``amount``, direction
      from the sign (negative = money out, positive = in), counterparty from
      ``title.receiver`` (when I sent) or ``title.sender``. ``authorization`` is a
      Venmo debit-card purchase (counterparty = merchant); ``disbursement`` is an
      inbound payout (e.g. a settlement). Category left None → Needs Review (SPEC §11).
    - ``refund`` — signed ``amount``; no title.sender/receiver, so the counterparty is
      ``note.name``. Category None.
    - ``transfer`` — a Venmo↔bank cash-out; ``amount`` is UNSIGNED, so treat it as an
      outflow (money leaving the Venmo balance to ``note.name`` bank). Category =
      Transfer (excluded from spending reports; also prevents double-counting the many
      transfers to Bank of America, which is separately scraped).
    """
    me = str(my_user_id)
    records: list[TransactionRecord] = []
    for story in raw.get("stories") or raw.get("data") or []:
        stype = story.get("type")
        if stype not in ("payment", "authorization", "disbursement", "refund", "transfer"):
            continue

        note = story.get("note") or {}
        title = story.get("title") or {}
        ts = _parse_ts(story.get("date"))
        txn_date = ts.astimezone(EASTERN).date() if ts else date.today()
        category: CanonicalCategory | None = None
        memo = note.get("content", "") if isinstance(note, dict) else (note or "")

        if stype == "transfer":
            # Unsigned amount; a bank cash-out is money leaving the Venmo balance.
            amount = -abs(_parse_amount(story.get("amount")))
            bank_name = (note.get("name") or "bank") if isinstance(note, dict) else "bank"
            last4 = note.get("lastFour") if isinstance(note, dict) else None
            cp_name = bank_name
            name = f"Transfer to {bank_name}" + (f" ...{last4}" if last4 else "")
            memo = ""
            category = CanonicalCategory.TRANSFER
        else:
            amount = _parse_amount(story.get("amount"))
            i_sent = amount < 0
            if stype == "refund":
                cp_name = (note.get("name") or "") if isinstance(note, dict) else ""
            else:
                party = (title.get("receiver") if i_sent else title.get("sender")) or {}
                cp_name = party.get("displayName") or party.get("username") or ""
            name = f"Sent to {cp_name}" if i_sent else f"Received from {cp_name}"

        records.append(
            TransactionRecord(
                source_id=str(story.get("id")),
                source_account_id=me,
                name=name,
                amount=amount,
                transaction_date=txn_date,
                status=TransactionStatus.POSTED,
                payee=cp_name,
                memo=memo,
                category=category,
                bank=BankName.VENMO,
                account_type=AccountType.P2P,
                credit_card_account=NOTION_ACCOUNT,
                account_name="Venmo",
                raw_data=story,
            )
        )
    return records


# ---------------------------------------------------------------------------
# I/O half: replay the captured web session cookies (no browser at sync time)
# ---------------------------------------------------------------------------
def _load_cookies() -> dict[str, str]:
    if not _COOKIES_FILE.exists():
        raise RuntimeError(
            "No Venmo web-session cookies. Run `scripts/venmo_web_capture.py` to log in "
            "and capture them (auto-reads the SMS 2FA)."
        )
    return json.loads(_COOKIES_FILE.read_text())


def _external_id() -> str:
    if _EXTID_FILE.exists() and _EXTID_FILE.read_text().strip():
        return _EXTID_FILE.read_text().strip()
    raise RuntimeError("No Venmo external_id saved. Re-run scripts/venmo_web_capture.py.")


def _client() -> httpx.Client:
    cookies = _load_cookies()
    csrf = cookies.get("_csrf", "")
    return httpx.Client(
        base_url=ORIGIN,
        cookies=cookies,
        headers={
            "User-Agent": _UA,
            "Accept": "application/json",
            "Referer": ORIGIN + "/",
            "csrf-token": csrf,
            "xsrf-token": csrf,
        },
        follow_redirects=False,
        timeout=30,
    )


def _fetch_stories(client: httpx.Client, ext_id: str, since: date, *, max_pages: int = 500) -> dict:
    """Page ``/api/stories`` (nextId cursor) back until older than ``since``.

    ``max_pages`` is only a safety bound: the loop stops early when the cursor runs
    out or a page predates ``since``. Pages yield ~8-9 payment rows each (padded with
    transfer/other story types), so a multi-year backfill needs a few hundred pages —
    ~6 years of full history is ~200 pages; 500 leaves headroom.
    """
    all_stories: list[dict] = []
    next_id: str | None = None
    for _ in range(max_pages):
        params = {"feedType": "me", "externalId": ext_id}
        if next_id:
            params["nextId"] = next_id
        resp = client.get("/api/stories", params=params)
        if resp.status_code in (301, 302, 401, 403) or "json" not in resp.headers.get(
            "content-type", ""
        ):
            raise RuntimeError(
                f"Venmo web session invalid (HTTP {resp.status_code}). Re-run "
                "scripts/venmo_web_capture.py to refresh cookies."
            )
        body = resp.json()
        stories = body.get("stories") or []
        if not stories:
            break
        all_stories.extend(stories)
        oldest = _parse_ts(stories[-1].get("date"))
        if oldest and oldest.astimezone(EASTERN).date() < since:
            break
        next_id = body.get("nextId")
        if not next_id:
            break
    return {"stories": all_stories}


class VenmoScraper:
    SESSION_ID = "venmo"
    BANK_DISPLAY_NAME = "Venmo"
    SUPPORTS_LIVE = True
    CATEGORY_MAP: CategoryMap = {}  # Venmo has no bank categories

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        client = _client()
        try:
            ext_id = _external_id()
            raw = _fetch_stories(client, ext_id, since)
            recs = parse_stories(raw, my_user_id=ext_id)
            recs = [r for r in recs if r.transaction_date and r.transaction_date >= since]
            logger.info("venmo_fetched", count=len(recs), since=since.isoformat())
            return recs
        finally:
            client.close()

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        client = _client()
        try:
            ext_id = _external_id()
            raw = _fetch_stories(client, ext_id, start)
            recs = parse_stories(raw, my_user_id=ext_id)
            recs = [r for r in recs if r.transaction_date and start <= r.transaction_date <= end]
            logger.info("venmo_historical", count=len(recs))
            return recs
        finally:
            client.close()

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError("Venmo has no PDF statements; live API covers full history")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        raise NotImplementedError("Venmo has no PDF statements; live API covers full history")
