"""Parse the BofA card rewards landing page + match points to transactions.

Source: ``GET /customer/myrewards/points/landing.go?adx=<adx>`` — server HTML with
``table.transaction-history-table``. Each row: transaction date, posted date,
merchant (+ an expandable ``BASE`` / ``RELATIONSHIP BONUS`` breakdown), type,
status, amount, and a points cell holding ``total base bonus``.

Example (SHELL - NAXOS): $79.66 -> 209.11 pts = 119.49 base + 89.62 (75%) bonus.

``match_rewards`` correlates each reward row to a ``TransactionRecord`` by amount
(+ date proximity + merchant token overlap) and sets ``true_rewards`` (total
points) plus base/bonus in ``raw_data``. This is the bofa_rewards enricher's core.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from notion_finance_sync.models import TransactionRecord

_DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
_NUM_RE = re.compile(r"\d[\d,]*\.\d+")
_PCT_RE = re.compile(r"(\d+)%\s*Bonus", re.IGNORECASE)


def _floats(text: str) -> list[float]:
    return [float(n.replace(",", "")) for n in _NUM_RE.findall(text or "")]


def _first_date(text: str) -> date | None:
    m = _DATE_RE.search(text or "")
    return datetime.strptime(m.group(1), "%m/%d/%Y").date() if m else None


def parse_rewards(html: str) -> list[dict]:
    """Parse the rewards landing HTML into a list of per-transaction reward dicts.

    Keys: transaction_date, posted_date, merchant, type, status, amount,
    total_points, base_points, bonus_points, bonus_pct.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = None
    for t in soup.find_all("table"):
        cls = " ".join(t.get("class") or [])
        if "transaction-history-table" in cls:
            table = t
            break
    if table is None:
        return []

    entries: list[dict] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) < 7:
            continue
        texts = [c.get_text(" ", strip=True) for c in cells]
        # data rows start with an "Open sub transactions" expander cell
        if not any("sub transaction" in t.lower() for t in texts[:1]):
            continue

        dates = [d for d in (_first_date(t) for t in texts) if d]
        # description is the cell carrying the "BASE:" breakdown (col index 3)
        desc = next((t for t in texts if "BASE:" in t), texts[3] if len(texts) > 3 else "")
        merchant = re.split(r"\bBASE:", desc)[0].strip()
        amount_txt = next((t for t in texts if t.strip().startswith("$")), "")
        amount = _floats(amount_txt)[0] if _floats(amount_txt) else None
        status = next((t for t in texts if t in ("Pending", "Earned", "Posted", "Redeemed")), "")
        pts = _floats(texts[-1])
        total = pts[0] if pts else None
        base = pts[1] if len(pts) > 1 else None
        bonus = pts[2] if len(pts) > 2 else None
        pct_m = _PCT_RE.search(desc)

        entries.append(
            {
                "transaction_date": dates[0] if dates else None,
                "posted_date": dates[1] if len(dates) > 1 else None,
                "merchant": merchant,
                "type": next((t for t in texts if t in ("BONUS", "BASE", "REDEMPTION")), ""),
                "status": status,
                "amount": amount,
                "total_points": total,
                "base_points": base,
                "bonus_points": bonus,
                "bonus_pct": int(pct_m.group(1)) if pct_m else None,
            }
        )
    return entries


def _merchant_tokens(s: str) -> set[str]:
    return {w for w in re.split(r"[^A-Za-z0-9]+", (s or "").upper()) if len(w) >= 3}


def match_rewards(
    records: list[TransactionRecord], entries: list[dict], *, day_tolerance: int = 4
) -> int:
    """Set ``true_rewards`` on records that match a reward entry.

    Match key: same absolute amount (to the cent) + transaction dates within
    ``day_tolerance`` days + at least one shared merchant token. Returns the
    number of records matched. Each reward entry is consumed once.
    """
    used: set[int] = set()
    matched = 0
    for rec in records:
        rec_amt = round(abs(rec.amount), 2)
        rec_tokens = _merchant_tokens(rec.payee or rec.name)
        best = None
        for i, e in enumerate(entries):
            if i in used or e.get("amount") is None:
                continue
            if round(e["amount"], 2) != rec_amt:
                continue
            if e.get("transaction_date") and rec.transaction_date:
                if abs((e["transaction_date"] - rec.transaction_date).days) > day_tolerance:
                    continue
            if rec_tokens and _merchant_tokens(e["merchant"]).isdisjoint(rec_tokens):
                continue
            best = i
            break
        if best is None:
            continue
        e = entries[best]
        used.add(best)
        rec.true_rewards = e.get("total_points")
        rec.raw_data.setdefault("base_points", e.get("base_points"))
        rec.raw_data.setdefault("bonus_points", e.get("bonus_points"))
        rec.raw_data.setdefault("bonus_pct", e.get("bonus_pct"))
        matched += 1
    return matched
