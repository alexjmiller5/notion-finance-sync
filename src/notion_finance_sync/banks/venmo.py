"""Venmo scraper — Venmo **mobile API** (api.venmo.com/v1), NOT the web.

Why the mobile API: the Venmo *web* login (id.venmo.com) is behind DataDome, which
gates the auth POST behind a captcha that defeats unattended automation. The mobile
app API is a plain JSON API on a different host with no DataDome. One SMS-OTP login
yields a **long-lived access token** (never expires until logout); with the token +
device-id persisted, daily syncs are pure ``httpx`` — no browser. See
``data/snapshots/venmo/FINDINGS.md`` for the full recon.

Field mapping (SPEC §17):
- ``name``     "Sent to {person}" / "Received from {person}" (from direction)
- ``payee``    counterparty display name
- ``memo``     Venmo note (incl. emojis)
- ``amount``   signed: negative = sent, positive = received
- ``category`` **None** — Venmo doesn't categorize; SPEC §11 → these land as Needs
               Review for Alex to categorize / link via Related Transactions.
- ``transacted_at`` the real UTC timestamp (Venmo exposes it) — used to derive the
               Eastern ``transaction_date``; model-only, never written to Notion.
- ``bank`` Venmo · ``account_type`` P2P · ``credit_card_account`` "Venmo Account".

The pure ``parse_stories`` half is offline/TDD-tested; only ``_login`` / ``_fetch_*``
do I/O. Login identifier must be an **email/phone** (the mobile OAuth rejects the
username); read from ``VENMO_LOGIN_EMAIL`` env or ``GMAIL_ADDRESS`` (both .env,
gitignored — keeps the personal email out of git).
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path
from random import randint
from zoneinfo import ZoneInfo

import httpx
import structlog

from notion_finance_sync.config.settings import get_bank_password, get_gmail_address
from notion_finance_sync.models import (
    AccountType,
    BankName,
    CategoryMap,
    TransactionRecord,
    TransactionStatus,
)
from notion_finance_sync.twofa.sms import get_sms_code

logger = structlog.get_logger()

BASE_URL = "https://api.venmo.com/v1"
USER_AGENT = "Venmo/7.44.0 (iPhone; iOS 13.0; Scale/2.0)"
EASTERN = ZoneInfo("America/New_York")
NOTION_ACCOUNT = "Venmo Account"  # curated Notion "Credit Card / Account" select value

_SESSION_DIR = Path(__file__).resolve().parents[3] / "data" / "sessions" / "venmo"
_DEVICE_FILE = _SESSION_DIR / "device_id.txt"
_TOKEN_FILE = _SESSION_DIR / "access_token.txt"

# Venmo OTP SMS: sender short-code + code regex still need one live confirmation
# (never reached the OTP step during recon — the account was under a 240 lock). The
# broad pattern below matches "Venmo ... 123456" / "code 123456"; tighten once seen.
_SMS_SENDER = "%"
_SMS_REGEX = r"(?i)venmo\D{0,80}?(\d{6})|code\D{0,10}?(\d{6})"


def _parse_ts(s: str | None) -> datetime | None:
    """Parse a Venmo ISO datetime (UTC, no offset) into a tz-aware UTC datetime."""
    if not s:
        return None
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def _status(value: str | None) -> TransactionStatus:
    if (value or "").lower() == "settled":
        return TransactionStatus.POSTED
    return TransactionStatus.PENDING


def parse_stories(raw: dict, *, my_user_id: str) -> list[TransactionRecord]:
    """Parse a ``/stories/target-or-actor/{id}`` response into TransactionRecords.

    Only ``type == "payment"`` stories become records (refunds / bank transfers /
    top-ups are skipped). Sign is derived from action + whether I'm the actor:
    ``pay`` moves money actor→target; ``charge`` moves money target→actor (when
    settled). Positive = money in, negative = money out. ``amount`` in the payload
    is a magnitude; we apply the sign.
    """
    me = str(my_user_id)
    records: list[TransactionRecord] = []
    for story in raw.get("data", []):
        if story.get("type") != "payment":
            continue
        pay = story.get("payment") or {}
        actor = pay.get("actor") or {}
        target = (pay.get("target") or {}).get("user") or {}
        i_am_actor = str(actor.get("id")) == me
        action = pay.get("action")

        # pay: money leaves the actor. charge: money leaves the target (settled).
        outflow = i_am_actor if action == "pay" else (not i_am_actor)
        mag = abs(float(pay.get("amount") or 0.0))
        amount = -mag if outflow else mag

        counterparty = target if i_am_actor else actor
        cp_name = (counterparty.get("display_name") or "").strip() or counterparty.get(
            "username", ""
        )
        name = f"Sent to {cp_name}" if outflow else f"Received from {cp_name}"

        note = (pay.get("note") or story.get("note") or "").strip()
        ts = _parse_ts(story.get("date_created"))
        txn_date = ts.astimezone(EASTERN).date() if ts else date.today()

        records.append(
            TransactionRecord(
                source_id=str(story.get("id")),
                source_account_id=me,
                name=name,
                amount=amount,
                transaction_date=txn_date,
                transacted_at=ts,
                status=_status(pay.get("status")),
                payee=cp_name,
                memo=note,
                category=None,  # Venmo doesn't categorize -> Needs Review (SPEC §11)
                bank=BankName.VENMO,
                account_type=AccountType.P2P,
                credit_card_account=NOTION_ACCOUNT,
                account_name="Venmo",
                raw_data=story,
            )
        )
    return records


# ---------------------------------------------------------------------------
# I/O half: login (SMS 2FA) + token/device persistence + paginated fetch
# ---------------------------------------------------------------------------
def _login_email() -> str:
    return os.environ.get("VENMO_LOGIN_EMAIL") or get_gmail_address()


def _random_device_id() -> str:
    base = "88884260-05O3-8U81-58I1-2WA76F357GR9"
    return "".join(str(randint(0, 9)) if c.isdigit() else c for c in base)


def _device_id() -> str:
    if _DEVICE_FILE.exists():
        return _DEVICE_FILE.read_text().strip()
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    did = _random_device_id()
    _DEVICE_FILE.write_text(did)
    return did


def _login(client: httpx.Client, device_id: str) -> str:
    """Full password + SMS-OTP login, returning a (long-lived) access token."""
    email, password = _login_email(), get_bank_password("venmo")
    r = client.post(
        "/oauth/access_token",
        headers={"device-id": device_id, "Content-Type": "application/json"},
        json={"phone_email_or_username": email, "client_id": "1", "password": password},
    )
    body = r.json()
    if not body.get("error"):
        return body["access_token"]

    otp_secret = r.headers.get("venmo-otp-secret")
    if not otp_secret:
        raise RuntimeError(f"Venmo login failed (no otp-secret): {body.get('error')}")
    requested_at = datetime.now(tz=UTC)
    client.post(
        "/account/two-factor/token",
        headers={
            "device-id": device_id,
            "Content-Type": "application/json",
            "venmo-otp-secret": otp_secret,
        },
        json={"via": "sms"},
    )
    code = get_sms_code(
        after=requested_at, sender_pattern=_SMS_SENDER, code_regex=_SMS_REGEX, timeout_s=150
    )
    if not code:
        raise RuntimeError("Venmo OTP not received within timeout")
    r3 = client.post(
        "/oauth/access_token",
        params={"client_id": 1},
        headers={"device-id": device_id, "venmo-otp": code, "venmo-otp-secret": otp_secret},
    )
    token = r3.json()["access_token"]
    try:
        client.post("/users/devices", headers={"device-id": device_id})  # trust device
    except Exception as exc:  # noqa: BLE001 — non-fatal
        logger.warning("venmo_trust_device_failed", error=str(exc))
    return token


def _authed_client() -> httpx.Client:
    """httpx client carrying a valid bearer token (reused if saved, else login)."""
    device_id = _device_id()
    client = httpx.Client(
        base_url=BASE_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=30,
    )
    token = _TOKEN_FILE.read_text().strip() if _TOKEN_FILE.exists() else None
    if not token:
        token = _login(client, device_id)
        _SESSION_DIR.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(token)
        logger.info("venmo_login_ok")
    client.headers["Authorization"] = (
        token if token.lower().startswith("bearer") else f"Bearer {token}"
    )
    return client


def _my_user_id(client: httpx.Client) -> str:
    body = client.get("/account").json()
    user = (body.get("data") or {}).get("user") or body.get("user") or {}
    uid = user.get("id")
    if not uid:
        raise RuntimeError("Could not resolve Venmo user id from /account")
    return str(uid)


def _fetch_stories(client: httpx.Client, my_id: str, since: date, *, max_pages: int = 60) -> dict:
    """Page ``/stories/target-or-actor/{id}`` back until older than ``since``.

    Returns a synthesized ``{"data": [...]}`` of every story fetched (parser filters).
    """
    all_stories: list[dict] = []
    before_id: str | None = None
    for _ in range(max_pages):
        params = {"limit": 50}
        if before_id:
            params["before_id"] = before_id
        page = client.get(f"/stories/target-or-actor/{my_id}", params=params).json()
        stories = page.get("data") or []
        if not stories:
            break
        all_stories.extend(stories)
        oldest = _parse_ts(stories[-1].get("date_created"))
        if oldest and oldest.astimezone(EASTERN).date() < since:
            break
        before_id = stories[-1].get("id")
    return {"data": all_stories}


class VenmoScraper:
    SESSION_ID = "venmo"
    BANK_DISPLAY_NAME = "Venmo"
    SUPPORTS_LIVE = True
    CATEGORY_MAP: CategoryMap = {}  # Venmo has no bank categories

    def fetch_recent(self, since: date) -> list[TransactionRecord]:
        client = _authed_client()
        try:
            my_id = _my_user_id(client)
            raw = _fetch_stories(client, my_id, since)
            recs = parse_stories(raw, my_user_id=my_id)
            recs = [r for r in recs if r.transaction_date and r.transaction_date >= since]
            logger.info("venmo_fetched", count=len(recs), since=since.isoformat())
            return recs
        finally:
            client.close()

    def fetch_historical(self, start: date, end: date) -> list[TransactionRecord]:
        client = _authed_client()
        try:
            my_id = _my_user_id(client)
            raw = _fetch_stories(client, my_id, start)
            recs = parse_stories(raw, my_user_id=my_id)
            recs = [r for r in recs if r.transaction_date and start <= r.transaction_date <= end]
            logger.info("venmo_historical", count=len(recs))
            return recs
        finally:
            client.close()

    def download_statements(self, start: date, end: date) -> list[Path]:
        raise NotImplementedError("Venmo has no PDF statements; live API covers full history")

    def parse_statements(self, pdf_paths: list[Path]) -> list[TransactionRecord]:
        raise NotImplementedError("Venmo has no PDF statements; live API covers full history")
