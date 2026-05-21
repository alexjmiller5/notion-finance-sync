# notion-finance-sync — Specification

**Status:** Design complete, scaffolding in progress
**Last updated:** 2026-05-20
**Replaces:** `notion-ai-budgeting-app` (SimpleFIN + LunchFlow aggregator era)

---

## 1. Overview & goals

A personal Python service running on Alex's Mac Mini that:

1. Logs into each of Alex's bank/credit-card/investment accounts directly (no aggregator)
2. Scrapes transactions, categories, rewards data, and investment events
3. Writes them to a single Notion `Transactions` database
4. Runs daily on a schedule and on-demand via a local HTTP endpoint
5. Auto-recovers from transient failures, escalates persistent failures to Notion tasks

**Why direct scraping vs aggregators:** Aggregators (Plaid, SimpleFIN, LunchFlow, Teller) don't expose MCC codes or the bank's own category labels. They strip per-transaction rewards data. Direct scraping captures everything the bank's web UI shows, which is the richest source available.

**Why the Mac Mini:** Always-on machine with iCloud-synced SMS (for SMS 2FA) and Gmail access. Residential IP is an asset, not a liability — banks see normal home traffic.

---

## 2. System architecture

### Phases of a sync run

```
┌──────────────────────────────────────────────────────────────────┐
│ Phase 1: Bank scrapers (serial, one bank at a time)              │
│                                                                  │
│   for each bank in priority order:                               │
│     - launch SeleniumBase UC+CDP browser with persistent profile │
│     - log in (auto-fill from 1Password; handle 2FA via shared    │
│       SMS/Gmail readers if challenged)                           │
│     - scrape transactions, balances, account metadata            │
│     - upsert to Notion (insert new, update changed, release      │
│       missing pendings)                                          │
│     - on failure: record in health/tracker.json, retry up to     │
│       3x within the run                                          │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 2: Enrichers (after all bank scrapers complete)            │
│                                                                  │
│   - Bilt portal enricher: correlate Bilt points across cards    │
│   - BofA rewards enricher: correlate per-txn cashback           │
│   - Wells Fargo rewards enricher: correlate Autograph points    │
│   (US Bank rewards deferred — too complex per design decision)  │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│ Phase 3: Health check + escalation                               │
│                                                                  │
│   - Banks that failed 3x today → create Notion task in Tasks DB │
│   - Reset success counters for healthy banks                    │
└──────────────────────────────────────────────────────────────────┘
```

### Repository layout

```
notion-finance-sync/
├── docs/
│   └── SPEC.md                          ← this file
├── pyproject.toml                       ← uv, py3.12+, pinned deps
├── justfile                             ← just sync | just serve | just migrate ...
├── README.md                            ← setup + manual steps
├── .gitignore
├── .python-version
├── src/notion_finance_sync/
│   ├── config/
│   │   └── settings.py                  ← pydantic-settings + 1Password
│   ├── models/
│   │   └── transactions.py              ← TransactionRecord, HoldingSnapshot
│   ├── notion/
│   │   └── client.py                    ← cherry-picked from old project
│   ├── sync/
│   │   ├── diffing.py                   ← build_transaction_changes()
│   │   └── orphan.py                    ← Pending→Released (simplified)
│   ├── browser/
│   │   └── factory.py                   ← SeleniumBase UC+CDP factory
│   ├── twofa/
│   │   ├── sms.py                       ← Messages.app SQLite reader
│   │   └── email.py                     ← Gmail reader
│   ├── banks/
│   │   ├── _base.py                     ← BankScraper Protocol
│   │   ├── bofa.py
│   │   ├── wells_fargo.py
│   │   ├── us_bank.py
│   │   ├── bilt.py
│   │   ├── everbank.py
│   │   ├── venmo.py
│   │   ├── etrade.py
│   │   ├── fidelity.py
│   │   ├── bofa_investments.py
│   │   ├── td.py                        ← closed, PDF-only
│   │   └── fidelity_ira_closed.py       ← closed, PDF-only
│   ├── enrichers/
│   │   ├── _base.py                     ← Enricher Protocol
│   │   ├── bilt_portal.py
│   │   ├── bofa_rewards.py
│   │   └── wells_rewards.py
│   ├── health/
│   │   ├── tracker.py                   ← consecutive-failure counter
│   │   └── notion_task.py               ← creates Tasks DB rows on escalation
│   ├── server/
│   │   └── app.py                       ← FastAPI: /sync, /sync/{bank}, /health
│   └── backfill/
│       ├── runner.py                    ← orchestrates historical import
│       ├── dedup.py                     ← fuzzy match at API/PDF seam
│       └── pdf_parsers/                 ← per-bank statement parsers
│           ├── bofa.py
│           ├── td.py
│           └── fidelity_ira_closed.py
├── scripts/
│   ├── sync.py                          ← CLI: --bank, --interactive, --since
│   ├── backfill.py                      ← CLI for historical import
│   └── migrate_schema.py                ← one-shot Notion schema migration
├── tests/                               ← cherry-picked + new
├── data/                                ← gitignored
│   ├── sessions/{bank}/                 ← Chrome user-data dirs (cookies, profile)
│   ├── statements/{bank}/               ← PDF statement archive
│   ├── manual/                          ← CSV manual-input files for closed accts
│   ├── snapshots/                       ← raw HTML/JSON saved before parsing
│   ├── health.json                      ← consecutive-failure tracker state
│   └── card_quarterly_pools.json        ← running cap pool totals for Calculated Rewards
├── config/
│   └── cards.yaml                       ← gitignored, per-card reward rules
└── deploy/
    └── com.alexmiller.notion-finance-sync.plist  ← launchd daily timer
```

---

## 3. Notion schema

**Database:** Transactions (`REDACTED_NOTION_DB_ID`)
**Data Source ID:** `REDACTED_NOTION_DATA_SOURCE_ID`
**API version:** `2026-03-11` (uses `/v1/data_sources/{id}/...` endpoints, NOT the deprecated `/v1/databases/`)

### Schema migration (one-shot script `scripts/migrate_schema.py`)

**Field renames:**
| Old name | New name |
|---|---|
| `SimpleFIN ID` | `Transaction Source ID` |
| `SimpleFIN Account ID` | `Source Account ID` |

**Fields to retire (no longer populated, may stay in schema or be removed):**
- `Data Source Leader` (was: SimpleFIN/LunchFlow/Equal — meaningless with single-source)
- `Data Source Log`
- `Descriptions Match`
- `Description Diff`

**New fields to add:**

| Field | Type | Purpose |
|---|---|---|
| `Bank Category` | text | Raw category label from the bank's UI (audit / re-mappable) |
| `Calculated Rewards` | number (dollar) | Computed from `config/cards.yaml` (rates, caps, multipliers) |
| `True Rewards` | number (dollar) | Scraped directly from bank/portal (null for US Bank — deliberate) |
| `Related Transactions` | self-relation (bidirectional) | Manual linking of reimbursements / refunds / rollovers |
| `Related Transactions Amount` | rollup (sum of related txns' `Transaction Amount`) | Powers `Net Amount` |
| `Net Amount` | formula (`Transaction Amount + Related Transactions Amount`) | Reimbursement-net spending |
| `Quantity` | number (fractional, signed) | Share count for investment txns |
| `Ticker` | text | Stock symbol (not select — too many possible values) |
| `Price Per Share` | number (dollar) | Cost basis per share |
| `Bilt Points` | number | Cross-card Bilt rewards (orthogonal to `Calculated`/`True`) |
| `Bilt Partner` | checkbox | Merchant is a Bilt Neighborhood Dining partner |

**Select option additions:**
- `Bank`: add `Venmo`, `E*Trade`, `Fidelity` (BofA investment accounts stay under existing `Bank of America`)
- `Account Type`: add `P2P`, `Brokerage`, `401k`, `IRA`
- `Category`: populate with the canonical 18-category taxonomy (see §10)

---

## 4. Bank inventory

### Active accounts (live scrapers in v1)

| Bank | Module | Login covers | Notes |
|---|---|---|---|
| Bank of America | `banks/bofa.py` + `banks/bofa_investments.py` | All BofA cards (AQHA, Komen, Travel Rewards, Unlimited Cash Rewards, NEA, Advantage Plus) + Checking + Savings + **Roth IRA** + **Investment Management** | High priority. One login, many accounts. |
| U.S. Bank | `banks/us_bank.py` | Cash+ Visa Signature + Harris Teeter Rewards | High priority. `True Rewards` deliberately null. |
| Wells Fargo | `banks/wells_fargo.py` | Autograph card (current) + historical old-Bilt-era transactions | Lower priority. |
| Bilt | `banks/bilt.py` | Bilt Blue card + Bilt portal session | Lower priority. Bilt portal feeds cross-card enricher. **Long-lived session** — Alex rarely re-logs on personal devices; Bilt verifies via SMS to phone, no username/password in 1Password. |
| Everbank | `banks/everbank.py` | Checking | High priority. |
| Venmo | `banks/venmo.py` | Venmo account | High priority. |
| E*Trade | `banks/etrade.py` | Brokerage (monthly RSU vests) | High priority. |
| Fidelity | `banks/fidelity.py` | 401k (biweekly contributions) | High priority. Old README noted Fidelity sync was painful — may need probing. |

### Closed accounts (PDF-only / manual modules)

| Bank | Module | History source | Notes |
|---|---|---|---|
| TD Bank | `banks/td.py` | PDFs delivered by TD after Alex requests them | Account closed ~2020; Alex opened ~2018. |
| Old Fidelity IRA | `banks/fidelity_ira_closed.py` | Some PDFs + some manual entry | Rolled into BofA Roth IRA. Use `Related Transactions` to link the rollover events. |
| Old Bilt card (→ Wells Fargo Autograph) | **NO separate module** | Wells Fargo statements (which span both pre- and post-conversion) + Bilt portal for rewards correlation | Wells Fargo was always the issuer; conversion was a product rename. Manual spot-checking around the conversion date. |

---

## 5. Data models

### `TransactionRecord` (the primary type)

```python
@dataclass
class TransactionRecord:
    # Identity
    source_id: str                # Bank-native txn ID (writes to Transaction Source ID)
    source_account_id: str        # Bank-native account ID

    # Spending fields
    name: str                     # Title for the Notion row
    amount: float                 # Signed: negative = spend, positive = receive/income
    transaction_date: date        # The "logical" date
    transacted_at: datetime | None  # The actual timestamp if exposed
    status: Literal["Pending", "Posted", "Released"]

    # Descriptive
    payee: str                    # Merchant or counterparty
    memo: str                     # Bank-provided memo + scraper additions
    bank_category: str | None     # RAW bank label
    category: str | None          # CANONICAL category (from CATEGORY_MAP)

    # Account context
    bank: str                     # Notion select value
    credit_card_account: str | None  # Notion select value (per-card)
    card_network: str | None      # Visa | Mastercard
    account_type: str             # Credit Card | Debit Card | Checking | Savings | P2P | Brokerage | 401k | IRA
    account_name: str

    # Rewards (filled in Phase 1 by the bank scraper + Phase 2 by enrichers)
    calculated_rewards: float | None  # From YAML, computed
    true_rewards: float | None    # From bank UI, scraped (null for US Bank)
    bilt_points: float | None     # From Bilt portal enricher
    bilt_partner: bool            # Merchant is Bilt Neighborhood Dining

    # Investment fields (null for spending txns)
    quantity: float | None        # Share count (signed)
    ticker: str | None
    price_per_share: float | None
```

### `HoldingSnapshot` / `BalanceSnapshot`

Reserved for future portfolio-value computation (out of v1 scope). v1 records investment txns as `TransactionRecord`s with `quantity` + `ticker` + `price_per_share` populated.

---

## 6. Browser stack

**Primary:** SeleniumBase in **UC + CDP mode** (`SB(uc=True) → sb.activate_cdp_mode(...)`).
- Headed mode (not headless). Mac Mini doesn't need an active display attached.
- Real Chrome (`channel="chrome"`), not bundled Chromium.
- Persistent profile per bank login: `data/sessions/{session_id}/` (entire Chrome user-data directory).

**Fallback per-bank (if a specific bank defeats the primary):**
- Patchright (Playwright drop-in with stealth patches)
- Camoufox (custom-patched Firefox with C++-level fingerprint spoofing)

Don't mix stacks within a single bank — pick one tool per bank module.

**Why this stack:** SeleniumBase UC + CDP is the most actively maintained open-source Cloudflare/Akamai/Datadome bypass in 2026 (daily commits from Michael Mintz, ~12k stars). Patchright and Camoufox are kept as escape hatches for banks where SeleniumBase eventually fails.

---

## 7. 2FA flow

**Alex's banks support SMS and email 2FA only.** No bank in scope offers TOTP. No push 2FA needed (rare for the banks in scope). No security-question handler needed (Alex's banks don't use these mid-session).

**Two shared functions:**
- `twofa/sms.py::get_sms_code(after, sender_pattern, code_regex, timeout_s)` — reads `~/Library/Messages/chat.db`
- `twofa/email.py::get_email_code(after, sender_pattern, code_regex, timeout_s)` — reads Gmail via IMAP using an App Password (not OAuth). Simpler than OAuth for a single-user personal project; app password lives at `op://Notion Finance Sync/Gmail App Password/credential`.

**Per-bank config** declares which method + sender pattern + code regex per bank. Bank scrapers, after submitting credentials, detect the 2FA page and call the configured function.

**Retry:** 3 attempts within the same run, ~5-10 min between attempts. If all 3 fail → abort this bank for the day; tomorrow's run starts fresh.

**Reference code for the SMS + Gmail polling logic:**
- Messages extension: `/Users/alexmiller/desktop/coding/active-projects/messages`
- Mail extension: `/Users/alexmiller/desktop/coding/reference-repos/mail`

These are Alex's existing TypeScript Raycast extensions for generic 2FA discovery. Port the access patterns to Python, but **make queries bank-specific** (precise sender + regex) rather than generic.

**macOS requirement (manual setup, documented in README):** Full Disk Access granted in System Settings → Privacy & Security → Full Disk Access for the Python interpreter / terminal running the scraper. Required for Messages.app SQLite access.

---

## 8. Sync pipeline

### Daily run

Triggered by `launchd` at ~03:30 local time + **±20 min random jitter**. Banks scrape **serially** to avoid burst-pattern detection and to coordinate 2FA SMS reading. Within a single bank, scrapes can parallelize (transactions + rewards-page) — that's safe.

### On-demand

Local FastAPI server (`uvicorn`) on the Mac Mini exposes:
- `POST /sync` — sync all banks
- `POST /sync/{session_id}` — sync one bank (`bofa`, `wells_fargo`, etc.)
- `GET /health` — recent run status, per-bank health counters

Hit it from iOS Shortcut, curl, or any LAN-connected device.

### --interactive flag (manual escape hatch)

```bash
uv run sync.py --bank bofa --interactive
```

Same sync flow, but pauses at any "needs human" wall (unsolvable CAPTCHA, novel security challenge, unexpected push-2FA). After human intervention, automation resumes. **No separate `claim_session.py` ceremony.** First-time logins use this same path or just run unattended — the daily scraper has every capability a setup ceremony would.

---

## 9. State machine (Pending / Posted / Released)

```
   [scrape completes successfully]
       │
       ▼
   ┌─────────┐ txn returned with posted=true ┌────────┐
   │ Pending │ ───────────────────────────────▶ │ Posted │
   └────┬────┘                                  └────────┘
        │ txn not in this scrape's results
        │   OR bank UI shows explicit "Released" / "Reversed"
        ▼
   ┌──────────┐
   │ Released │   no revival (txn that disappears does not come back)
   └──────────┘
```

**Trust the bank UI as ground truth.** With one source per bank, the dual-aggregator buffer from the old project is gone. The bank's own web UI is the most up-to-date data source available, so disappearance = released, immediately.

**The one guard:** orphan detection runs ONLY after a *successful* scrape (no exceptions, expected post-login state reached, returned data for the account). Failed/partial scrapes do not modify Pending statuses.

---

## 10. Categories

### Canonical taxonomy (18 categories)

**Rewards-relevant:**
`Airfare` · `Travel` · `Dining` · `Groceries` · `Gas` · `Streaming` · `Online Shopping` · `Convenience` · `Department Stores` · `Wholesale Clubs` · `Transit`

**Functional:**
`Bills & Utilities` · `Healthcare` · `Cash & ATM` · `Transfer` · `Income` · `Rent` · `Other`

**Notable choices:**
- `Airfare` split from `Travel` (different reward tiers on most cards)
- `Convenience` (not `Drugstores`) — captures bodegas, 7-11, corner stores, CVS/Walgreens casual buys
- Prescription/pharmacy spending → `Healthcare` (alongside doctor visits, copays)

### Per-bank `CATEGORY_MAP`

Each bank module declares `CATEGORY_MAP: dict[str, str]` mapping raw bank labels → canonical labels.

### Discovery-first mapping

During each bank's initial backfill + first daily syncs, the scraper **discovers** all unique raw category labels and surfaces them. Alex populates `CATEGORY_MAP` from this discovered list. ONLY AFTER the map is mature do truly-novel unmapped labels trigger the "Other + create Notion task" escalation path.

### Bank Category field

The new `Bank Category` text field stores the **raw** bank label alongside the canonical `Category`. Auditable. Re-mappable later without rescraping.

---

## 11. Rewards (two tracks)

### `Calculated Rewards` — from YAML config

```yaml
# config/cards.yaml (gitignored)
bofa_aqha_customized_cash:
  notion_select: "AQHA Customized Cash Rewards"
  rewards_type: "Cashback"
  preferred_rewards_multiplier: 2.625   # BofA Diamond tier
  rates_by_category:
    "Online Shopping": 3.0
    "Groceries": 2.0
    "Wholesale Clubs": 2.0
    "*": 1.0
  quarterly_cap_pools:
    bonus:
      categories: ["Online Shopping", "Groceries", "Wholesale Clubs"]
      cap_amount: 2500
      cap_period: "quarter"
```

**Computation:**
1. Look up rate by canonical category, fall back to `*`
2. Apply BofA Preferred Rewards multiplier if applicable
3. Check running quarterly pool total (queries Notion for current quarter, persisted in `data/card_quarterly_pools.json`)
4. If pool exhausted, fall back to base rate
5. Multiply final rate × `Transaction Amount` → write to `Calculated Rewards`

### `True Rewards` — from bank UI / portal

| Account | True Rewards source |
|---|---|
| Bilt Blue | Per-txn multiplier in main transaction view (scraped inline) |
| BofA cards | Monthly rewards summary page (correlated by date+amount via `bofa_rewards` enricher) |
| Wells Fargo Autograph | Rewards center (correlated via `wells_rewards` enricher) |
| **U.S. Bank Cash+** | **NULL — deliberate.** Rewards data not exposed cleanly enough. |
| Everbank / Venmo | NULL (no rewards on those accounts) |

**Divergence between Calculated and True is an audit signal** (caps hit, choice category mismatch, Bilt Rent Day bonus, shopping-portal bonus, statement credit, etc.).

### Rewards use `Transaction Amount`, NOT `Net Amount`

Bilt awards points on the full $120 dinner regardless of friends Venmoing you back. So:
- **Spending analysis** (category totals, monthly budgets) → use `Net Amount`
- **Rewards calculation** → use `Transaction Amount` (gross)

---

## 12. Bilt cross-card points (the unique design)

Bilt points can be earned on **any** card via Bilt's Neighborhood Dining program (pay at a Bilt-partnered restaurant with a BofA card → still earn Bilt points). The Bilt portal is the system of record for these — they don't appear in the originating bank's data.

**Two fields:**
- `Bilt Points` (number) — independent of `Calculated`/`True Rewards`
- `Bilt Partner` (checkbox) — flags Neighborhood Dining merchants

**Mechanism:** the `bilt_portal` enricher runs after all bank scrapers, pulls all Bilt point earnings from the Bilt portal, correlates each to an existing Notion row by `(date, amount, merchant)`, updates `Bilt Points` on matched rows.

---

## 13. Enricher pattern

Two-phase sync: scrapers write rows, enrichers update them.

```python
class Enricher(Protocol):
    SOURCE: str          # "bilt_portal" | "bofa_rewards" | "wells_rewards"
    UPDATES_FIELDS: list[str]   # Notion fields this enricher touches

    def fetch_external_data(self) -> list[ExternalRewardEntry]: ...
    def correlate_to_notion(self, entries, notion_txns) -> list[NotionUpdate]: ...
```

Correlation primitive: fuzzy match by `(date, amount, merchant_normalized)`. Reusable across enrichers.

**v1 enrichers:**
- `enrichers/bilt_portal.py` — Bilt points across all cards
- `enrichers/bofa_rewards.py` — BofA cashback per-txn correlation
- `enrichers/wells_rewards.py` — Wells Autograph points correlation
- (US Bank rewards enricher deferred — too messy)

---

## 14. Related Transactions / Net Amount

**Use case:** Alex pays $120 for a group dinner; three friends Venmo back $30 each. Without linking, it looks like he spent $120 on Dining and got $90 of unrelated income. With linking, his Dining category accurately reflects his $30 share.

**Schema (already in §3):**
- `Related Transactions` — self-relation, bidirectional
- `Related Transactions Amount` — rollup sum
- `Net Amount` — formula: `Transaction Amount + Related Transactions Amount`

**Workflow:** Manual linking by Alex in Notion. The scraper writes new rows; Alex links reimbursements after the fact.

**Other use cases this pattern handles:**
- Refunds linked to original purchase (Net Amount = 0)
- Splitting any expense (Lyft, group gifts, etc.)
- IRA rollovers (Fidelity transfer-out ↔ BofA Roth IRA transfer-in)

**For most transactions** (`Related Transactions` empty), `Net Amount = Transaction Amount` automatically. Zero workflow change.

---

## 15. Health monitoring

### Per-bank failure tracker (`health/tracker.py`)

State persisted to `data/health.json`:
```json
{
  "bofa": {"consecutive_failures": 0, "last_success": "2026-05-19T03:42:11Z", "last_error": null},
  "fidelity": {"consecutive_failures": 1, "last_success": "2026-05-18T03:38:02Z", "last_error": "2FA code never arrived"}
}
```

### Escalation to Notion task

After 3 failed attempts within the same day, create a row in the Tasks DB (`REDACTED_NOTION_TASKS_ID`) with:
- Title: e.g., "Fix BofA scraper — 3 consecutive failures today"
- Description: latest error message + suggested action ("Run `uv run sync.py --bank bofa --interactive`")
- Priority + tags appropriately

Schema of Tasks DB to be fetched on first run (use `notion_fetch` on the data source ID to learn the property structure).

---

## 16. Investment accounts

### Active investment scrapers in v1

| Account | Module | Bank login | Events scraped |
|---|---|---|---|
| BofA Investment Management | `banks/bofa_investments.py` | shares `bofa` session | Buys, sells, dividends, fees |
| BofA Roth IRA | `banks/bofa_investments.py` (or sub-class) | shares `bofa` session | Contributions, buys, dividends |
| E*Trade brokerage | `banks/etrade.py` | own session | Monthly RSU grants, buys/sells, dividends |
| Fidelity 401k | `banks/fidelity.py` | own session | Biweekly payroll contributions, buys, dividends |

### Field semantics

| Event | `Transaction Amount` | `Quantity` | `Ticker` | `Price Per Share` |
|---|---|---|---|---|
| Stock grant (E*Trade RSU vest) | market value at vest | shares | symbol | vest price |
| 401k payroll deposit | cash deposited | shares auto-bought | fund | buy price |
| Active buy | -cash spent | +shares | symbol | buy price |
| Active sell | +cash received | -shares | symbol | sell price |
| Cash dividend | +amount | null | symbol | null |
| DRIP | 0 net | +shares | symbol | buy price |
| Fee | -amount | null | null | null |

### Portfolio value reconstruction

OUT of v1 scope. Notion stores events; value rollup (`shares × current_price`) is a downstream concern requiring an external price feed (yfinance, Alpha Vantage). Build that later as a separate read-side concern.

### Closed Fidelity IRA

PDF-only / manual-input module. `SUPPORTS_LIVE = False`. Rollover into BofA Roth IRA captured as a transfer-out event linked via `Related Transactions` to the BofA transfer-in event. Manual-input UX TBD until Alex actually starts the backfill.

---

## 17. Venmo specifics

### Field mapping

| Notion field | Source |
|---|---|
| `Name` | "Sent to {person}" / "Received from {person}" |
| `Payee` | Counterparty's display name |
| `Memo` | Venmo note text (including emojis) |
| `Transaction Amount` | Signed (negative = sent, positive = received) |
| `Bank` | "Venmo" |
| `Credit Card / Account` | "Venmo Account" |
| `Account Type` | "P2P" |
| `Category` | null (Venmo doesn't categorize) |
| `Cashback Percentage`, `Calculated Rewards`, `True Rewards`, `Bilt Points` | null |
| `Source Account ID` | Alex's Venmo user ID |
| `Transaction Source ID` | Venmo's txn ID |

### Double-counting prevention (funding leg)

When Alex Venmos John via his BofA debit card as funding source, **both** BofA and Venmo show a row. The Venmo row is the "real" spending event (has the counterparty + note). The BofA row is just the funding leg.

**Rule:** bank scrapers auto-set `Category = "Transfer"` when payee text matches Venmo/Zelle/Cash App patterns. `Transfer` is excluded from spending-category reports. Same logic for Zelle and Cash App.

### Reimbursement (the `Related Transactions` case)

When Alex pays for a group dinner on his card and friends Venmo him back, he **manually** links the dining transaction to the Venmo receipts via `Related Transactions`. `Net Amount` then reflects only his real share. **This is different from the funding-leg case** — both can apply to the same bank, but the workflows are distinct.

---

## 18. Backfill (one-time historical import)

### Scope

- Target depth: ~5 years (lifetime of Alex's oldest active account, BofA)
- Most accounts: opened within last 2 years → live scraping (`fetch_historical`) covers the lifetime
- BofA: the only account needing meaningful PDF parsing for pre-online history
- TD Bank, old Fidelity IRA: PDF/manual-only (closed)
- Old Bilt card history: lives in Wells Fargo statements (no special module)

### Priority for BofA

1. **Live scrape pushed back as far as the UI and backend API allow.** Probe the JSON endpoint's true date limit during initial dev — UI filters often cap at 18-24 mo while backend accepts more.
2. **PDFs only fill the residual gap** (whatever the live scrape can't reach). PDFs have thinner data — no category, no rewards detail.

### Code location

`src/notion_finance_sync/backfill/` (runner, dedup, pdf_parsers). CLI: `scripts/backfill.py`. Shares `BankScraper` Protocol, Notion client, `TransactionRecord` with daily sync.

### Dedup at the API/PDF seam

- Primary key: `(account_id, amount, date)`
- Fuzzy fallback: `±1 day`, normalized payee
- Live-source data preferred over PDF-source when both exist

### LLM categorization for PDF-only data

**Deferred entirely.** PDF-sourced transactions get `Category = null`. Future-Alex problem.

### PDF storage

`data/statements/{bank}/` inside the repo, gitignored. Alex drops files manually (BofA gaps, TD after calling, Fidelity IRA digging).

---

## 19. Concurrency

- **Serial across banks**, parallel within a bank where helpful (e.g., transactions + rewards-page in parallel for the same bank)
- Daily run total: ~15-25 min across 8 active scrapers + enrichers
- On-demand single-bank: 1-3 min
- Schedule: `launchd` ~03:30 ± 20 min random jitter

**Rationale:** anti-bot vendors share signal across banks (a burst across 8 banks in 30 seconds is more suspicious than the same load spread over 15 min). Mac Mini resource simplicity. 2FA SMS reading coordinates better serially.

---

## 20. Setup (manual steps)

Documented in README:

1. **Install dependencies:** `uv sync`
2. **1Password CLI configured** (`op` command works, signed in to relevant vault)
3. **Project-scoped 1Password vault** named `Notion Finance Sync` (created via `op vault create`). Service account `notion-finance-sync-svc` has read+write scoped only to this vault. Service-account token lives in Personal vault at `op://Personal/Notion Finance Sync Service Account Token/password`.
4. **Per-bank 1Password items** in the project vault. The session_id-to-item mapping lives in `settings.OP_BANK_ITEM_BY_SESSION`. Required items:
   - `op://Notion Finance Sync/BofA/{username,password}` (covers all BofA accounts incl. investment + IRA — one login)
   - `op://Notion Finance Sync/Wells Fargo/{username,password}`
   - `op://Notion Finance Sync/U.S. Bank/{username,password}`
   - `op://Notion Finance Sync/Everbank/{username,password}`
   - `op://Notion Finance Sync/Venmo/{username,password}`
   - `op://Notion Finance Sync/E*Trade/{username,password}`
   - `op://Notion Finance Sync/Fidelity/{username,password}`
   - **Bilt deliberately omitted** — Bilt uses SMS-to-phone verification (no username/password flow), and sessions are long-lived on personal devices. Once `data/sessions/bilt/` is established the scraper usually proceeds without any 2FA. Phone-verification handler kicks in if a fresh challenge appears.
5. **Notion API integration secret** at `op://Notion Finance Sync/Notion Finance Sync Notion Internal Integration Secret/credential`
6. **Gmail App Password** at `op://Notion Finance Sync/Gmail App Password/credential`. Used for IMAP access to read 2FA codes from bank emails. App passwords are created in **Google Account → Security → App Passwords** (requires 2FA enabled on the Google account).
7. **Full Disk Access** granted to terminal / Python interpreter in System Settings → Privacy & Security → Full Disk Access (for Messages.app SQLite reads)
8. **Run schema migration once:** `just migrate`
9. **First daily run** — schedule via `just install-launchd` (places the plist + loads it). The launchd plist sets `PYTHONPATH=src` so the editable install works without an active `uv run` context.

---

## 21. Out of v1 scope (deferred)

- LLM categorization (deferred entirely; PDF transactions stay `Category = null`)
- Portfolio value rollup (`shares × current_price`) — investments only record events
- Notion-driven card config (YAML for v1; Notion-as-config is the v2 idea)
- US Bank `True Rewards` enricher (data not exposed cleanly enough)
- Shopping portal bonuses, Bilt Rent Day dining boost, statement credits, limited-time offers (will surface as `True > Calculated` divergences)
- Notification channels (Telegram/Pushover/macOS push) — Notion tasks are the alerting channel
- Push 2FA, security-question handler, hardware-key 2FA — Alex's banks don't use these
- Investments DB / separate Holdings DB — single Transactions DB for v1 covers it via the new `Quantity`/`Ticker`/`Price Per Share` fields

---

## 22. Tech stack summary

| Concern | Tool |
|---|---|
| Language | Python 3.12+ |
| Env / deps | `uv` |
| Config | `pydantic`, `pydantic-settings` |
| Linting / formatting | `ruff` |
| Testing | `pytest` + `pytest-asyncio` + `pytest-mock` + `respx` |
| Logging | `structlog` |
| HTTP | `httpx` (default) — `curl-cffi` only as per-bank fallback if JA3 fingerprinting blocks `httpx` |
| Browser automation | SeleniumBase (UC + CDP mode) — fallbacks: Patchright, Camoufox |
| Web framework | FastAPI + uvicorn |
| Secrets | 1Password CLI (`op`) |
| Scheduling | `launchd` (macOS native) |
| Task runner | `just` / `justfile` |
| Storage | Notion (Transactions DB) |

---

## 23. Glossary

- **Session** — a single bank-login profile (one Chrome `user_data_dir`). One session can cover multiple accounts (e.g., the `bofa` session covers all BofA cards + checking + savings + IRA + investment mgmt).
- **Scraper** — a Phase 1 module that logs into a bank and writes new transactions to Notion.
- **Enricher** — a Phase 2 module that pulls data from a separate source (e.g., Bilt portal) and updates *existing* Notion rows by correlation.
- **Calculated Rewards** — `cards.yaml`-derived reward $ amount.
- **True Rewards** — bank-UI-scraped reward $ amount.
- **Net Amount** — `Transaction Amount + sum(Related Transactions.Amount)` — reimbursement-net spending.
- **Source ID** — a transaction's bank-native unique identifier (writes to `Transaction Source ID` Notion property).
- **Discovery phase** — early period of a bank's lifecycle when its `CATEGORY_MAP` is being filled in from observed raw labels.

---
