"""Venmo recon via the MOBILE API (api.venmo.com/v1) — sidesteps DataDome.

The Venmo *web* login (id.venmo.com / account.venmo.com) is behind DataDome, which
gates the auth POST behind an unsolvable-for-automation captcha (see FINDINGS.md).
The *mobile* app API is a plain JSON API on a different host with no DataDome. One
SMS-OTP login yields a long-lived access token; a persisted device-id means 2FA is
only needed on first login. This is the path the scraper uses (pure httpx, no browser).

Endpoints (reverse-engineered; ref: github.com/mmohades/Venmo):
- POST /oauth/access_token           login (username/password) -> token OR 2FA
- POST /account/two-factor/token     {"via":"sms"} -> sends the OTP SMS
- POST /oauth/access_token?client_id=1  submit OTP -> token
- GET  /stories/target-or-actor/{id} transaction feed (limit<=50, before_id cursor)
- GET  /account                      my profile (user id)

    PYTHONPATH=src uv run python scripts/recon_venmo.py

Artifacts -> data/snapshots/venmo/api_recon_<ts>/ (gitignored). Persists device-id +
token to data/sessions/venmo/ (gitignored) so re-runs skip 2FA.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from random import randint

import httpx

from notion_finance_sync.config.settings import get_bank_password, get_bank_username
from notion_finance_sync.twofa.sms import _query_recent_messages, get_sms_code

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "snapshots" / "venmo" / f"api_recon_{datetime.now(tz=UTC):%Y%m%d_%H%M%S}"
OUT.mkdir(parents=True, exist_ok=True)
SESSION_DIR = ROOT / "data" / "sessions" / "venmo"
SESSION_DIR.mkdir(parents=True, exist_ok=True)
DEVICE_FILE = SESSION_DIR / "device_id.txt"
TOKEN_FILE = SESSION_DIR / "access_token.txt"

BASE = "https://api.venmo.com/v1"
USER_AGENT = "Venmo/7.44.0 (iPhone; iOS 13.0; Scale/2.0)"
TWO_FACTOR_ERROR_CODE = 81109


def random_device_id() -> str:
    base = "88884260-05O3-8U81-58I1-2WA76F357GR9"
    return "".join(str(randint(0, 9)) if c.isdigit() else c for c in base)


def get_device_id() -> str:
    if DEVICE_FILE.exists():
        return DEVICE_FILE.read_text().strip()
    did = random_device_id()
    DEVICE_FILE.write_text(did)
    return did


def save(tag: str, data) -> None:
    text = data if isinstance(data, str) else json.dumps(data, indent=2)
    (OUT / f"{tag}.json").write_text(text)


def login(client: httpx.Client, device_id: str, username: str, password: str) -> str:
    # 1) username/password
    r = client.post(
        "/oauth/access_token",
        headers={"device-id": device_id, "Content-Type": "application/json"},
        json={"phone_email_or_username": username, "client_id": "1", "password": password},
    )
    print(f"[login] POST /oauth/access_token -> {r.status_code}")
    body = _json(r)
    save("01_login_response", {"status": r.status_code, "headers": dict(r.headers), "body": body})

    if not body.get("error"):
        token = body["access_token"]
        print("[login] no 2FA needed")
        return token

    # 2) 2FA
    otp_secret = r.headers.get("venmo-otp-secret")
    err_code = body.get("error", {}).get("code")
    print(f"[login] 2FA required (err={err_code}); otp_secret={'yes' if otp_secret else 'NO'}")
    if not otp_secret:
        raise RuntimeError(f"No venmo-otp-secret header (check password). body={body}")

    code_requested_at = datetime.now(tz=UTC)
    r2 = client.post(
        "/account/two-factor/token",
        headers={
            "device-id": device_id,
            "Content-Type": "application/json",
            "venmo-otp-secret": otp_secret,
        },
        json={"via": "sms"},
    )
    print(f"[login] POST two-factor/token (send SMS) -> {r2.status_code}")
    save("02_send_sms", {"status": r2.status_code, "body": _json(r2)})

    code = get_sms_code(
        after=code_requested_at,
        sender_pattern="%",
        code_regex=r"(?i)venmo\D{0,80}?(\d{6})|(\d{6})\D{0,40}?venmo|code\D{0,10}?(\d{6})",
        timeout_s=150,
    )
    if not code:
        try:
            msgs = _query_recent_messages(code_requested_at, "%")
            save("02b_recent_sms", msgs)
            print(f"[login] no code matched; {len(msgs)} recent SMS saved for regex tuning")
        except Exception as exc:  # noqa: BLE001
            print(f"[login] sms read failed: {exc}")
        raise RuntimeError("Venmo OTP not received/matched within timeout")
    print("[login] OTP read from Messages")

    # 3) submit OTP
    r3 = client.post(
        "/oauth/access_token",
        params={"client_id": 1},
        headers={"device-id": device_id, "venmo-otp": code, "venmo-otp-secret": otp_secret},
    )
    print(f"[login] POST /oauth/access_token (submit OTP) -> {r3.status_code}")
    body3 = _json(r3)
    save("03_otp_response", {"status": r3.status_code, "body": _redact(body3)})
    token = body3["access_token"]

    # 4) trust this device so future logins skip 2FA
    try:
        rt = client.post("/users/devices", headers={"device-id": device_id})
        print(f"[login] trust device -> {rt.status_code}")
    except Exception as exc:  # noqa: BLE001
        print(f"[login] trust device failed (non-fatal): {exc}")

    return token


def _json(r: httpx.Response) -> dict:
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return {"_raw": r.text[:2000]}


def _redact(body: dict) -> dict:
    b = dict(body)
    if "access_token" in b:
        b["access_token"] = b["access_token"][:8] + "...REDACTED"
    return b


def main() -> int:
    username = os.getenv("VENMO_USERNAME") or get_bank_username("venmo")
    password = os.getenv("VENMO_PASSWORD") or get_bank_password("venmo")
    device_id = get_device_id()
    print(f"[recon] user={username!r} device_id={device_id[:8]}… artifacts -> {OUT}")

    client = httpx.Client(
        base_url=BASE,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=30,
    )
    try:
        if TOKEN_FILE.exists():
            token = TOKEN_FILE.read_text().strip()
            print("[recon] reusing saved access token")
        else:
            token = login(client, device_id, username, password)
            TOKEN_FILE.write_text(token)
            print("[recon] token saved")

        auth = token if token.lower().startswith("bearer") else f"Bearer {token}"
        client.headers["Authorization"] = auth

        # my profile / user id
        acct = client.get("/account")
        print(f"[recon] GET /account -> {acct.status_code}")
        acct_body = _json(acct)
        save("10_account", acct_body)
        me = (acct_body.get("data") or {}).get("user") or acct_body.get("user") or {}
        my_id = me.get("id")
        print(f"[recon] my user id = {my_id}, username={me.get('username')}")
        if not my_id:
            print("[recon] could not resolve my user id; see 10_account.json")
            return 1

        # transaction feed (first page)
        feed = client.get(f"/stories/target-or-actor/{my_id}", params={"limit": 50})
        print(f"[recon] GET /stories/target-or-actor/{my_id} -> {feed.status_code}")
        feed_body = _json(feed)
        save("11_stories_page1", feed_body)
        stories = feed_body.get("data") or []
        print(f"[recon] {len(stories)} stories in page 1")
        for s in stories[:8]:
            pay = s.get("payment") or {}
            actor = (pay.get("actor") or {}).get("username")
            tgt = ((pay.get("target") or {}).get("user") or {}).get("username")
            print(
                f"    {s.get('type'):8} {pay.get('action'):6} amt={pay.get('amount')} "
                f"{s.get('date_created')} actor={actor} target={tgt} note={s.get('note')!r}"
            )
        print(f"[recon] done -> {OUT}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
