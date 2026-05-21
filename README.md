# notion-finance-sync

Personal direct-bank-scraper service writing transactions and investment events to Notion. Runs on a Mac Mini, syncs daily and on-demand.

Replaces the aggregator-based `notion-ai-budgeting-app` (SimpleFIN + LunchFlow era). See [docs/SPEC.md](docs/SPEC.md) for the full design.

## Quick start

```bash
uv sync                      # install deps
just migrate                 # one-shot Notion schema migration
just sync-bank bofa          # try one bank end-to-end
just serve                   # start the FastAPI server for on-demand syncs
just install-launchd         # install the daily-sync timer
```

## Manual setup steps (imperative configuration unavoidable)

These steps cannot be automated and must be done once per machine:

### 1. `uv` installed

```bash
brew install uv
```

### 2. 1Password CLI signed in

```bash
op account add
op signin
op whoami   # should print your account
```

### 3. Project vault + service account (already done — for reference only)

This project uses a **dedicated 1Password vault** called `Notion Finance Sync`
and a **service account** scoped to that vault for unattended runs.

Both were created during initial setup with:
```bash
op vault create "Notion Finance Sync" --icon vault-door
op service-account create "notion-finance-sync-svc" \
  --vault "Notion Finance Sync:read_items,write_items"
```

The service-account token is stored in your Personal vault at:
- `op://Personal/Notion Finance Sync Service Account Token/password`

For unattended runs (the daily launchd job), export the token into
`OP_SERVICE_ACCOUNT_TOKEN` before invoking sync:

```bash
export OP_SERVICE_ACCOUNT_TOKEN=$(op read "op://Personal/Notion Finance Sync Service Account Token/password")
just sync
```

For local development, your regular `op signin` session has access to both
vaults — no env var needed.

### 4. Populate per-bank credentials in the Notion Finance Sync vault

For each session you intend to sync, create a 1Password Login item in the
`Notion Finance Sync` vault with `username` and `password` fields.

Required items (one per active session):
- `op://Notion Finance Sync/BofA/{username,password}`
- `op://Notion Finance Sync/Wells Fargo/{username,password}`
- `op://Notion Finance Sync/U.S. Bank/{username,password}`
- `op://Notion Finance Sync/Bilt/{username,password}`
- `op://Notion Finance Sync/Everbank/{username,password}`
- `op://Notion Finance Sync/Venmo/{username,password}`
- `op://Notion Finance Sync/E*Trade/{username,password}`
- `op://Notion Finance Sync/Fidelity/{username,password}`

CLI shortcut for one (repeat per bank, replacing placeholders):
```bash
op item create --category=login --vault="Notion Finance Sync" \
  --title="BofA" --url="https://www.bankofamerica.com/" \
  username="YOUR_USERNAME" password="YOUR_PASSWORD"
```

Or use the 1Password app/web UI.

### 5. Notion API integration secret

Create a Notion internal integration scoped to the Transactions database, then store the secret in the project vault:

```bash
op item create --category=password --vault="Notion Finance Sync" \
  --title="Notion API Key" credential="secret_xxxxxxxxxxxx"
```

Reference path: `op://Notion Finance Sync/Notion API Key/credential`

### 6. Gmail OAuth for email 2FA reading

Create an OAuth 2.0 desktop client in Google Cloud Console (Gmail API scope: `gmail.readonly`), then store credentials in the project vault:

```bash
op item create --category="api credential" --vault="Notion Finance Sync" \
  --title="Gmail OAuth" \
  client_id="..." client_secret="..." refresh_token="..."
```

(One-time browser auth flow generates the refresh token. A helper script in `scripts/` will do this the first time you run the email handler.)

Reference paths:
- `op://Notion Finance Sync/Gmail OAuth/client_id`
- `op://Notion Finance Sync/Gmail OAuth/client_secret`
- `op://Notion Finance Sync/Gmail OAuth/refresh_token`

### 7. Full Disk Access for Messages.app SQLite

The SMS 2FA reader reads `~/Library/Messages/chat.db`. macOS requires explicit Full Disk Access:

1. Open **System Settings → Privacy & Security → Full Disk Access**
2. Add the terminal app you use (Terminal.app or iTerm2) and/or the Python interpreter (`/usr/bin/env`, `~/.local/bin/uv`)
3. Restart the terminal

Verify with:
```bash
sqlite3 ~/Library/Messages/chat.db "SELECT COUNT(*) FROM message"
```

If you get `Error: unable to open database file`, Full Disk Access isn't granted.

### 8. Notion schema migration

```bash
just migrate
```

This:
- Renames `SimpleFIN ID` → `Transaction Source ID`
- Renames `SimpleFIN Account ID` → `Source Account ID`
- Adds: `Bank Category`, `Calculated Rewards`, `True Rewards`, `Related Transactions`, `Related Transactions Amount`, `Net Amount`, `Quantity`, `Ticker`, `Price Per Share`, `Bilt Points`, `Bilt Partner`
- Adds new select options: `Bank` += {Venmo, E*Trade, Fidelity}; `Account Type` += {P2P, Brokerage, 401k, IRA}
- Populates the 18-category canonical taxonomy in the `Category` select

### 9. Install launchd daily timer

```bash
just install-launchd
```

Schedules `just sync` to run daily at ~03:30 local time + ±20 min jitter.

## Architecture (one-liner)

Phase 1: per-bank SeleniumBase scrapers (serial) → Notion. Phase 2: enrichers (Bilt portal, BofA rewards, Wells rewards) correlate to existing rows. Phase 3: health check, create Notion tasks for banks failing 3x today.

See `docs/SPEC.md` for the full design.

## On-demand sync

```bash
# All banks
curl -X POST http://127.0.0.1:8765/sync

# One bank
curl -X POST http://127.0.0.1:8765/sync/bofa
```

## When something breaks

A bank that fails 3x in one day creates a Notion task. The task suggests:

```bash
uv run python scripts/sync.py --bank <name> --interactive
```

`--interactive` runs the same sync flow but pauses with a terminal prompt whenever automation hits a wall (unsolvable CAPTCHA, novel security challenge, etc.). You handle the human bit, hit ENTER, automation resumes. Same profile persists either way.
