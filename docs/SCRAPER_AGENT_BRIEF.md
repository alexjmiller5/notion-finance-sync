# Bank Scraper Agent Brief

You are building the **live scraper for ONE bank** in `notion-finance-sync`, a personal-finance
system that logs directly into banks (no aggregators), scrapes transactions richer than Plaid,
and writes them to a Notion Transactions database. The **Bank of America scraper is already
built, live-validated, and backfilled 900 real transactions into Notion** — it is your reference
implementation. Copy its shape.

---

## 0. Your assignment

**BANK = `______`**  (one of: `us_bank`, `wells_fargo`, `everbank`, `venmo`, `etrade`, `fidelity`, `bilt`)

Everything below is generic; the per-bank specifics are in §9.

---

## 1. Read these BEFORE writing code (do not blind-code)

- `docs/SPEC.md` — the whole spec. Especially §4 (bank inventory), §5 (data models), §10 (canonical
  categories), §16 (investment accounts), §17 (auto-Transfer rules), §9 (orphan/pending).
- `src/notion_finance_sync/banks/bofa/` — **THE reference.** Read every file:
  - `session.py` — SeleniumBase UC+CDP login → 2FA → cookies (the hardened pattern).
  - `fetchers.py` — authenticated `httpx` calls to the discovered endpoints.
  - `card.py` / `deposit.py` — pure parsers (HTML statement + JSON activity).
  - `assemble.py` — enrich + dedupe records.
  - `scraper.py` — `BofAScraper` implementing `fetch_recent` / `fetch_historical`.
  - `categories.py`, `rewards.py` — category code→label map, rewards parsing.
- `src/notion_finance_sync/banks/_base.py` — the `BankScraper` Protocol you implement.
- `src/notion_finance_sync/models/transactions.py` — `TransactionRecord` + all enums
  (`BankName`, `AccountType`, `CanonicalCategory`, `TransactionStatus`, `CardNetwork`).
- `tests/test_bofa_*.py` — the TDD pattern + how fixtures are loaded.
- `data/snapshots/bofa/recon_20260701_235336/FINDINGS.md` — how BofA's endpoints were reverse-
  engineered; write your own equivalent for your bank.
- Your stub: `src/notion_finance_sync/banks/{BANK}.py` — already scaffolded with `SESSION_ID`,
  `BANK_DISPLAY_NAME`, `SUPPORTS_LIVE=True`, a starter `CATEGORY_MAP`, and `NotImplementedError`
  method bodies. You fill it in. It is already imported + registered in `banks/registry.py`.

---

## 2. The architecture (two layers)

1. **Impure nav/session half** — login + 2FA + capture cookies. Uses SeleniumBase in UC+CDP mode
   via `browser/factory.py::open_session(session_id)` (persistent Chrome profile at
   `data/sessions/{BANK}/`). Mirror `bofa/session.py`.
2. **Pure parser half** — offline, deterministic, TDD-able. Parses captured HTML/JSON →
   `list[TransactionRecord]`. Mirror `bofa/card.py` / `bofa/deposit.py`.

`fetch_recent` / `fetch_historical` are **synchronous** (they open the browser directly). The
orchestrator calls them via `asyncio.to_thread` — see §6.

---

## 3. RECON FIRST (mandatory — do not guess endpoints blind)

Log in live, drive the UI to the transactions view, and **capture the raw responses + endpoints**.
Save artifacts to `data/snapshots/{BANK}/` and write a `FINDINGS.md`. Determine:

- **Transport:** server-rendered HTML (like BofA cards) or a JSON API (like BofA checking)?
- **Stable per-transaction id** → `source_id` (must be stable across syncs; the diff dedupes on it).
- **Amount sign convention** (BofA cards give unsigned magnitude + a type icon; BofA checking gives
  signed amounts). Normalize to: **negative = spend/outflow, positive = income/inflow.**
- **Category** — is a raw category label/code exposed? (→ `bank_category` + map to `CanonicalCategory`.)
- **Pagination** — statement dropdown? cursor token? date range? How far back can live go?
- **Description quality** — is the full merchant/description in the list, or only in a per-txn detail
  view? (BofA checking truncates the list; the full text needs a detail fetch. Check for this.)

Tip: use `chrome-devtools-mcp` or the SeleniumBase CDP network capture to watch XHRs. An
authenticated `httpx`/`fetch()` from the logged-in session works; **direct top-nav URL navigation
can trigger a re-auth bounce** (BofA lesson) — prefer `fetch()` from the logged-in page.

---

## 4. Secrets & credentials (you have everything)

The **1Password service-account token is already in the macOS Keychain.** Load it into the env:

```bash
export OP_SERVICE_ACCOUNT_TOKEN="$(security find-generic-password -a "$(id -un)" -s notion-finance-sync-op-token -w)"
export PYTHONPATH=src   # editable-install quirk; needed for `uv run python ...`
```

Credentials live in the 1Password vault **`Notion Finance Sync`**. Read them via the settings
getters (already wired — do NOT hardcode):

```python
from notion_finance_sync.config.settings import get_bank_username, get_bank_password
u = get_bank_username("{BANK}")   # -> op://Notion Finance Sync/<item>/username
p = get_bank_password("{BANK}")   # -> op://Notion Finance Sync/<item>/password
```

1Password item names per bank (in `settings.OP_BANK_ITEM_BY_SESSION`):
`us_bank`→"U.S. Bank", `wells_fargo`→"Wells Fargo", `everbank`→"Everbank", `venmo`→"Venmo",
`etrade`→"E*Trade", `fidelity`→"Fidelity". **`bilt` is NOT in the vault** (SMS/device-trust — see §9).

**Step 0 sanity check:** `op read "op://Notion Finance Sync/<item>/username"` to confirm your item
exists with `username`/`password` fields. If the item name or field labels differ, fix
`settings.OP_BANK_ITEM_BY_SESSION` (and note it for merge).

The Notion API key + Gmail app password also resolve from the vault via settings getters — already
wired, you don't touch them.

---

## 5. 2FA (recon your bank's method)

- **SMS** → `twofa/sms.py::get_sms_code(after, sender_pattern, code_regex, timeout_s)`. Reads macOS
  Messages `chat.db` (Full Disk Access is granted to the terminal). NOTE: modern iMessages store the
  body in `attributedBody` (binary) with a NULL `text` column — `sms.py` already decodes it. You must
  discover your bank's **sender short-code** and the **code format regex** (BofA = sender `73981`,
  regex handles "…Code 123456."). Validate against real messages like BofA did (50/50).
- **Email** → `twofa/email.py::get_email_code(...)`. Gmail IMAP via app password; `GMAIL_ADDRESS`
  must be set in `.env` (gitignored — no default, keeps the personal email out of source control).
- Set `code_requested_at = datetime.now(UTC)` **just before** triggering the code send, then poll
  `get_*_code(after=code_requested_at, ...)` so you never match a stale code.

---

## 6. Login hardening lessons (apply them — learned the hard way on BofA)

- **Use EXPLICIT waits, not fixed sleeps.** `sb.cdp.wait_for_element_visible(sel, timeout=30)` and
  `sb.cdp.wait_for_any_of_elements_present([...], timeout=45)`. A fixed `sb.cdp.sleep(6)` races past a
  slow page and the login silently fails.
- **Dismiss cookie-consent overlays** before typing — they cover the form so type/click silently
  no-op: `sb.cdp.click_if_visible("#onetrust-accept-btn-handler")` (best-effort, wrap in try/except).
- **Screenshot-on-failure:** wrap the whole login in `try/except`, save a screenshot to
  `data/snapshots/{BANK}/login_failure_*.png` + log the current URL, then re-raise. Blind debugging
  is impossible without it.
- **Assume 2FA every run.** (BofA re-challenges every login — no device-trust bypass. Your bank may
  differ; some, like Bilt, keep a long-lived trusted session. Recon it.)
- Persistent profile per bank: `data/sessions/{BANK}/` (gitignored) via `open_session`.

### CRITICAL async gotcha
The browser login drives SeleniumBase's **own** asyncio loop, which **cannot start inside an
already-running event loop**. Your `fetch_recent`/`fetch_historical` are therefore **synchronous**
and open the browser directly. The orchestrator + backfill runner call them via
`await asyncio.to_thread(scraper.fetch_recent, since)` — that's already handled; just don't make your
scrape methods `async`.

---

## 7. What your scraper outputs

Return `list[TransactionRecord]`. Set every field the bank exposes; **leave enricher-owned fields
null** so the Phase-2 enrichers own them:

- **Always set:** `source_id` (stable), `source_account_id`, `name` (row title — full description,
  not truncated), `amount` (signed), `transaction_date`, `status` (Pending/Posted), `bank`
  (`BankName.*`), `account_type` (`AccountType.*`), `account_name`.
- **Set when known:** `payee` (clean merchant/counterparty), `memo`, `bank_category` (raw label) +
  `category` (`CanonicalCategory` via your `CATEGORY_MAP`), `credit_card_account` (curated Notion
  select value — must EXACTLY match an existing option), `card_network`, `transacted_at` (only if the
  bank exposes a real timestamp — e.g. Venmo does; most banks give date-only → leave `None`).
- **Leave null:** `true_rewards` (unless the bank exposes it directly like BofA's rewards page),
  `bilt_points`, `bilt_partner`, `review_status` (the orchestrator computes it).
- **Investment banks** (etrade, fidelity): populate `quantity`, `ticker`, `price_per_share`;
  `account_type` = `Brokerage`/`401k`/`IRA`. Positive amounts there are normal (dividends/grants).
- **Auto-Transfer (SPEC §17):** Zelle / Venmo / Apple Cash / Cash App funding legs →
  `category = CanonicalCategory.TRANSFER`.

Curated select values (`Category`, `Bank`, `Credit Card / Account`, `Account Type`, `Card Network`)
must **exactly match existing Notion options** — writing a new value auto-creates a duplicate option.
The canonical `Category` list (19): Airfare, Travel, Dining, Groceries, Gas, Streaming, Online
Shopping, Convenience, Department Stores, Wholesale Clubs, Transit, Bills & Utilities, Healthcare,
Cash & ATM, Transfer, Income, Rent, Other, Trip Settlement. If you genuinely need a new card option,
**flag it for Alex** rather than inventing it.

---

## 8. Notion (you do NOT write to Notion directly)

The scraper only returns records. The **orchestrator** diffs them against existing rows (by
`source_id` — idempotent) and writes creates/updates. To test end-to-end:

```bash
just sync-bank {BANK}              # since = today-14d
# or with a window:
PYTHONPATH=src OP_SERVICE_ACCOUNT_TOKEN=... uv run python scripts/sync.py --bank {BANK} --since 2026-06-01
```

This writes real rows to the live Transactions DB (safe to re-run; idempotent). Data source id:
`REDACTED_NOTION_DATA_SOURCE_ID`.

---

## 9. Per-bank notes (SPEC §4 / §16)

| BANK | Covers | Notes |
|---|---|---|
| `us_bank` | Cash+ Visa Signature + Harris Teeter Rewards World Elite | High pri. **`True Rewards` deliberately NULL** (not exposed) — only Calculated Rewards. No rewards enricher. |
| `wells_fargo` | Autograph card (current) + historical old-Bilt-era txns | Lower pri. WF was always the issuer; Bilt→Autograph was a product rename. Bilt portal enricher correlates rewards. |
| `everbank` | Checking | High pri. (This is the "EverBank" Zelle counterparty seen in BofA checking — Alex's other checking account.) |
| `venmo` | Venmo account | High pri. **Has real timestamps → populate `transacted_at`.** `account_type = P2P`. Descriptions are transfer-shaped → Transfer category. |
| `etrade` | Brokerage (monthly RSU vests) | High pri. Investment events: buys/sells/dividends/RSU grants. `quantity`+`ticker`+`price_per_share`, `account_type=Brokerage`. |
| `fidelity` | 401k (biweekly contributions) | High pri. `account_type=401k`. Old README flagged Fidelity sync as painful — expect extra probing. |
| `bilt` | Bilt Blue card + Bilt portal | Lower pri. **NOT in 1Password** — long-lived session, auth by SMS to Alex's phone (device-trust). Feeds the cross-card Bilt enricher. |

---

## 10. Git worktree (so agents don't collide)

Each agent works in its **own worktree + branch** off `feat/bofa-end-to-end`:

```bash
git worktree add ../nfs-{BANK} -b feat/{BANK}-scraper feat/bofa-end-to-end
cd ../nfs-{BANK}
```

- Work only in **your** files: `src/notion_finance_sync/banks/{BANK}.py`, your fetchers/parsers if you
  split them into a `banks/{BANK}/` package, `tests/test_{BANK}_*.py`, `data/snapshots/{BANK}/`.
- **Shared files that will conflict at merge — keep edits minimal + obvious:**
  - `banks/registry.py` — your bank is already imported + registered (stub exists); no edit needed
    unless you rename the class.
  - `config/settings.py::OP_BANK_ITEM_BY_SESSION` — your bank is already mapped; only touch if the
    item name is wrong.
  - `config/cards.yaml` — if you add reward rates, note it for the merge.
- **Never commit** secrets, `.env`, cookies (`data/sessions/`), or snapshots (`data/snapshots/`) —
  all gitignored; `git status` before committing to confirm. **Real transaction data in test fixtures
  must be gitignored** (see how `tests/fixtures/bofa/*.html` is handled) — keep fixtures local, or
  sanitize before committing.
- Commit messages end with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## 11. TDD (Alex's hard rule — non-negotiable)

Tests **first**, then implementation. Use **real captured fixtures** (gitignored if they contain real
data). **Mutation-test** your parsers: flip a sign or field, confirm the test fails, revert.
`just test` (`uv run pytest`) must be green; `just lint` (ruff, line length 100) must be clean.

---

## 12. Definition of done

1. `data/snapshots/{BANK}/FINDINGS.md` documents endpoints, transport, id, sign, category, pagination, 2FA.
2. Parsers built TDD against real fixtures; `just test` green, `just lint` clean.
3. Scraper implements `fetch_recent` + `fetch_historical`, returns real `TransactionRecord`s.
4. **Live-validated:** logged in (2FA auto-read), fetched + printed real records.
5. **End-to-end:** `just sync-bank {BANK} --since <recent>` writes real rows into Notion.
6. Report back: endpoints used, sign convention, category mapping, 2FA method (sender/regex), and any
   curated Notion select options that need adding.
