"""Bilt scraper (Bilt Blue card via the api.biltrewards.com JSON API).

Recon (2026-07-03, data/snapshots/bilt/recon_20260703/FINDINGS.md): Bilt exposes a
clean bearer-token JSON API — **no browser needed for normal syncs**. Keycloak
(www.bilt.com/realms/BILT, client `identity-svc`) issues a 60-day rolling refresh
token; each sync refreshes it and persists the new pair to
``data/sessions/bilt/tokens.json`` (gitignored). Only when the refresh token has
expired does the scraper fall back to the browser login: phone -> SMS OTP (sender
+16465189979) -> email magic link (new devices) -> read ``persist:auth`` from
localStorage.

Sign convention: the API reports purchases POSITIVE / payments NEGATIVE — inverted
vs ours — so amounts are negated (spend negative, inflow positive).

The Bilt portal session this login establishes also serves the Phase-2
``bilt_portal`` cross-card points enricher.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import structlog

from notion_finance_sync.models import (
    AccountType,
    BankName,
    CanonicalCategory,
    CardNetwork,
    CategoryMap,
    TransactionRecord,
    TransactionStatus,
)

logger = structlog.get_logger()

API_ORIGIN = "https://api.biltrewards.com"
KEYCLOAK_TOKEN_URL = "https://www.bilt.com/realms/BILT/protocol/openid-connect/token"
KEYCLOAK_CLIENT_ID = "identity-svc"
LOGIN_URL = "https://www.bilt.com/login/phone"

TOKENS_PATH = Path(__file__).resolve().parents[3] / "data" / "sessions" / "bilt" / "tokens.json"

# Bilt sends the OTP by SMS from this number; real message (2026-07-03):
#   "DON'T share this code with anyone. Bilt agents will NEVER ask for this
#    code. Your Bilt Auth verification code is: 775837"
BILT_SMS_SENDER = "+16465189979"
BILT_SMS_REGEX = r"(?i)verification code is:?\s*(\d{6})"

# Bilt dates render in ET in the UI/statements (createdAt is UTC).
_EASTERN = ZoneInfo("America/New_York")

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

# Backend (api.cardless.com) rejects date ranges much beyond 180 days; 90-day
# windows keep us comfortably inside the limit.
_WINDOW_DAYS = 90
_PAGE_SIZE = 100

NOTION_ACCOUNT = "Bilt Blue"  # curated Notion "Credit Card / Account" select value


def parse_transactions(
    raw: dict,
    *,
    account_name: str = "Bilt Blue Card",
) -> list[TransactionRecord]:
    """Parse a ``transactions/v2`` response into ``TransactionRecord``s.

    Pure: JSON dict -> records. Handles both the ``settled`` and ``pending``
    arrays; amounts are negated to our convention (spend negative).
    """
    txns = raw["transactions"]
    records: list[TransactionRecord] = []
    for t, status in [(x, TransactionStatus.PENDING) for x in txns.get("pending", [])] + [
        (x, TransactionStatus.POSTED) for x in txns.get("settled", [])
    ]:
        merchant = t.get("merchant") or {}
        merchant_category = merchant.get("category")
        desc = (t.get("description") or "").strip()
        # createdAt is UTC; the logical (statement) date is its Eastern calendar day.
        ts = datetime.fromisoformat(t["createdAt"].replace("Z", "+00:00"))

        if t.get("type") in ("PAYMENT",):
            category = CanonicalCategory.TRANSFER  # card-payment legs (autopay, rent adj.)
        elif t.get("displayCategory") == "RENT":
            category = CanonicalCategory.RENT
        else:
            category = BiltScraper.CATEGORY_MAP.get(merchant_category or "")

        records.append(
            TransactionRecord(
                source_id=t["transactionId"],
                source_account_id=t.get("accountId", ""),
                name=desc,
                amount=-float(t["amount"]["amount"]),
                transaction_date=ts.astimezone(_EASTERN).date(),
                status=status,
                payee=(merchant.get("name") or desc).strip(),
                memo=desc,
                bank_category=merchant_category or t.get("displayCategory"),
                category=category,
                bank=BankName.BILT,
                credit_card_account=NOTION_ACCOUNT,
                card_network=CardNetwork.MASTERCARD,
                account_type=AccountType.CREDIT_CARD,
                account_name=account_name,
                raw_data=t,
            )
        )
    return records


def match_true_rewards(records: list[TransactionRecord], entries: list[dict]) -> None:
    """Set ``true_rewards`` on card records from the ``/loyalty/activity`` feed.

    Bilt-card earnings are the feed's ``category == "BILT_MC"`` + EARNED entries
    (SPEC §11: Bilt Blue per-txn points are scraped inline). The feed has no
    transaction id, so entries correlate by merchant name + date (±3 days) —
    amounts can differ (points accrue on the pre-FX amount). "Housing Points"
    entries carry no amount/merchant and match the nearest rent purchase by date.

    Cross-card entries (DINING / RIDESHARE etc.) are NOT consumed here — they
    belong to other banks' rows via the Phase-2 ``bilt_portal`` enricher.
    """
    spends = [r for r in records if r.amount < 0]
    matched: set[str] = set()

    def entry_date(e: dict) -> date:
        dt = datetime.fromisoformat(e["datetime"].replace("Z", "+00:00"))
        return dt.astimezone(_EASTERN).date()

    def nearest(candidates: list[TransactionRecord], e_date: date) -> TransactionRecord | None:
        pool = [
            r
            for r in candidates
            if r.source_id not in matched and abs((r.transaction_date - e_date).days) <= 3
        ]
        return min(pool, key=lambda r: abs((r.transaction_date - e_date).days), default=None)

    for e in entries:
        if e.get("category") != "BILT_MC" or e.get("pointState") != "EARNED":
            continue
        e_date = entry_date(e)
        names = {(e.get("title") or "").casefold(), (e.get("rawTitle") or "").casefold()} - {""}

        if e.get("title") == "Housing Points":
            candidates = [r for r in spends if r.category == CanonicalCategory.RENT]
        else:
            candidates = [
                r
                for r in spends
                if r.payee.casefold() in names
                or (r.raw_data.get("merchant") or {}).get("rawName", "").casefold() in names
            ]
        rec = nearest(candidates, e_date)
        if rec is not None:
            rec.true_rewards = float(e.get("totalPoints") or 0)
            matched.add(rec.source_id)


def _date_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Chunk [start, end] into contiguous <=90-day windows (API range limit)."""
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=_WINDOW_DAYS), end)
        windows.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return windows


# ---------------------------------------------------------------------------
# Auth: Keycloak token refresh, with browser login as the fallback
# ---------------------------------------------------------------------------
def _save_tokens(tokens: dict) -> None:
    TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKENS_PATH.write_text(
        json.dumps(
            {"access_token": tokens["access_token"], "refresh_token": tokens["refresh_token"]}
        )
    )


def _refresh_access_token(refresh_token: str) -> dict:
    """Exchange the refresh token for a fresh pair (Keycloak public client)."""
    resp = httpx.post(
        KEYCLOAK_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": KEYCLOAK_CLIENT_ID,
            "refresh_token": refresh_token,
        },
        headers={"User-Agent": _USER_AGENT},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def get_access_token(*, interactive: bool = False) -> str:
    """Return a valid access token, refreshing (or re-logging-in) as needed."""
    if TOKENS_PATH.exists():
        stored = json.loads(TOKENS_PATH.read_text())
        try:
            tokens = _refresh_access_token(stored["refresh_token"])
            _save_tokens(tokens)
            return tokens["access_token"]
        except httpx.HTTPStatusError as exc:
            logger.warning("bilt_token_refresh_failed", status=exc.response.status_code)
    tokens = _browser_login(interactive=interactive)
    _save_tokens(tokens)
    return tokens["access_token"]


def _login_failure_screenshot(sb) -> None:
    try:
        folder = Path(__file__).resolve().parents[3] / "data" / "snapshots" / "bilt"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"login_failure_{datetime.now(tz=UTC).strftime('%H%M%S')}.png"
        sb.cdp.save_screenshot(name, folder=str(folder))
        logger.error(
            "bilt_login_failed", screenshot=str(folder / name), url=sb.cdp.get_current_url()
        )
    except Exception as exc:  # noqa: BLE001 — never mask the original error
        logger.error("bilt_login_screenshot_failed", error=str(exc))


def _extract_magic_link(after: datetime) -> str | None:
    """Poll Gmail for Bilt's login magic-link email and return the URL."""
    from notion_finance_sync.twofa.email import _query_recent_email_bodies

    deadline = datetime.now(tz=UTC) + timedelta(seconds=150)
    while datetime.now(tz=UTC) < deadline:
        for body in _query_recent_email_bodies(after=after, sender_pattern="bilt"):
            m = re.search(r"https://www\.bilt\.com/login/email/magic\?key=[\w.\-]+", body)
            if m:
                return m.group(0)
        time.sleep(5)
    return None


def _browser_login(*, interactive: bool = False) -> dict:
    """Full browser login: phone -> SMS OTP -> (magic link) -> localStorage tokens.

    Bilt has NO username/password (not in 1Password by design) — auth is the
    phone number + SMS to it. ``BILT_PHONE`` (10 digits) lives in ``.env``
    (gitignored — keeps the personal number out of source control, like
    ``GMAIL_ADDRESS``).
    """
    import os

    from notion_finance_sync.browser.factory import open_session
    from notion_finance_sync.twofa.sms import get_sms_code

    phone = os.environ.get("BILT_PHONE")
    if not phone:
        raise RuntimeError("BILT_PHONE is not set. Add it to .env (gitignored).")

    with open_session("bilt") as sb:
        try:
            sb.activate_cdp_mode(LOGIN_URL)
            sb.cdp.wait_for_element_visible('input[name="phone"]', timeout=30)

            code_requested_at = datetime.now(tz=UTC)
            sb.cdp.type('input[name="phone"]', phone)
            sb.cdp.click('button[type="submit"]')

            sb.cdp.wait_for_element_visible('input[name="otp"]', timeout=45)
            code = get_sms_code(
                after=code_requested_at,
                sender_pattern=BILT_SMS_SENDER,
                code_regex=BILT_SMS_REGEX,
                timeout_s=150,
            )
            if not code:
                if interactive:
                    input("Bilt SMS code not auto-read. Enter it in the browser, press ENTER... ")
                else:
                    raise RuntimeError("Bilt SMS OTP was not received within timeout")
            else:
                # six single-char boxes; typing the full code into the first box
                # distributes across them (verified live 2026-07-03).
                sb.cdp.click('input[name="otp"]')
                sb.cdp.type('input[name="otp"]', code)

            # New devices get an email magic link ("Check your inbox").
            time.sleep(4)
            page = sb.cdp.get_page_source()
            if "Check your inbox" in page:
                link = _extract_magic_link(after=code_requested_at)
                if not link:
                    raise RuntimeError("Bilt magic-link email not received within timeout")
                sb.cdp.open_new_tab(link)
                sb.cdp.wait_for_text("Are you attempting to log in?", timeout=30)
                sb.cdp.click('button:contains("Yes, it\'s me")')
                sb.cdp.wait_for_text("Confirmed", timeout=30)
                sb.cdp.close_active_tab()
                sb.cdp.switch_to_tab(0)

            # Logged in when the authed shell renders (points pill / Wallet nav).
            sb.cdp.wait_for_text("Wallet", timeout=60)
            raw_auth = sb.cdp.evaluate("localStorage.getItem('persist:auth')")
            persisted = json.loads(raw_auth)
            tokens = {
                "access_token": json.loads(persisted["accessToken"]),
                "refresh_token": json.loads(persisted["refreshToken"]),
            }
            logger.info("bilt_login_ok")
            return tokens
        except Exception:
            _login_failure_screenshot(sb)
            raise


# ---------------------------------------------------------------------------
# API fetchers
# ---------------------------------------------------------------------------
def _api_client(access_token: str) -> httpx.Client:
    return httpx.Client(
        base_url=API_ORIGIN,
        headers={"Authorization": f"Bearer {access_token}", "User-Agent": _USER_AGENT},
        timeout=30.0,
    )


def fetch_cards(client: httpx.Client) -> list[dict]:
    resp = client.get("/bilt-card/cards")
    resp.raise_for_status()
    return resp.json()


def fetch_loyalty_activity(client: httpx.Client) -> list[dict]:
    """Fetch the points-activity feed (per-txn Bilt-card points + cross-card).

    No pagination/date params observed — returns the member's full feed.
    """
    resp = client.get("/loyalty/activity")
    resp.raise_for_status()
    return resp.json().get("entries", [])


def fetch_transactions(client: httpx.Client, card_id: str, start: date, end: date) -> dict:
    """Fetch all pages for one <=90-day window; returns the merged response dict."""
    settled: list[dict] = []
    pending: list[dict] = []
    page = 0
    while True:
        resp = client.get(
            f"/bilt-card/cards/{card_id}/transactions/v2",
            params={
                "startDate": f"{start.isoformat()}T00:00:00Z",
                "endDate": f"{end.isoformat()}T23:59:59Z",
                "pageIndex": page,
                "pageSize": _PAGE_SIZE,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        settled.extend(data["transactions"].get("settled", []))
        pending.extend(data["transactions"].get("pending", []))
        if not data.get("hasMorePages"):
            break
        page += 1
    return {"transactions": {"settled": settled, "pending": pending}}


class BiltScraper:
    SESSION_ID = "bilt"
    BANK_DISPLAY_NAME = "Bilt"
    SUPPORTS_LIVE = True
    BANKS = {BankName.BILT}  # scopes orphan release to this bank's Notion rows

    # merchant.category (MCC-derived enum) -> canonical. Extend as values appear.
    CATEGORY_MAP: CategoryMap = {
        "GROCERIES": CanonicalCategory.GROCERIES,
        "DINING": CanonicalCategory.DINING,
        "RESTAURANTS": CanonicalCategory.DINING,
        "TRAVEL": CanonicalCategory.TRAVEL,
        "AIRFARE": CanonicalCategory.AIRFARE,
        "AIRLINES": CanonicalCategory.AIRFARE,
        "GAS": CanonicalCategory.GAS,
        "TRANSIT": CanonicalCategory.TRANSIT,
        "RIDESHARE": CanonicalCategory.TRANSIT,
        "UTILITIES": CanonicalCategory.BILLS_UTILITIES,
        "STREAMING": CanonicalCategory.STREAMING,
        "ENTERTAINMENT": CanonicalCategory.OTHER,
        "SHOPPING": CanonicalCategory.ONLINE_SHOPPING,
        "PHARMACY": CanonicalCategory.HEALTHCARE,
        "HEALTHCARE": CanonicalCategory.HEALTHCARE,
        "FITNESS": CanonicalCategory.OTHER,
    }

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        return self._fetch_range(since, datetime.now(tz=_EASTERN).date())

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        return self._fetch_range(start, end)

    def _fetch_range(self, start: date, end: date) -> list[TransactionRecord]:
        token = get_access_token()
        records: list[TransactionRecord] = []
        seen: set[str] = set()
        with _api_client(token) as client:
            cards = fetch_cards(client)
            physical = [c for c in cards if c.get("cardType") == "PHYSICAL"] or cards
            card_id = physical[0]["cardId"]  # txns are account-level; one card feed
            for w_start, w_end in _date_windows(start, end):
                raw = fetch_transactions(client, card_id, w_start, w_end)
                for rec in parse_transactions(raw):
                    if rec.source_id not in seen:
                        seen.add(rec.source_id)
                        records.append(rec)
            try:
                match_true_rewards(records, fetch_loyalty_activity(client))
            except Exception as exc:  # noqa: BLE001 — rewards are best-effort enrichment
                logger.warning("bilt_loyalty_activity_failed", error=str(exc))
        records = [r for r in records if start <= r.transaction_date <= end]
        logger.info("bilt_scraped", count=len(records), start=str(start), end=str(end))
        return records

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError("Bilt statements not needed — full history reachable live")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        raise NotImplementedError("Bilt statements not needed — full history reachable live")
