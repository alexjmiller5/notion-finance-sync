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

**How the daemon gets the token (Mac Mini deploy).** The service-account token is
the bootstrap secret that unlocks every other secret in 1Password, so it can't
live in 1Password itself and (per project policy) must not sit plaintext on disk.
On macOS it goes in the **login Keychain** (encrypted at rest):

```bash
just store-op-token          # prompts for the token (hidden); stores it in Keychain
```

The launchd daily job runs `deploy/run_sync.sh`, which reads the token from the
Keychain, exports it as `OP_SERVICE_ACCOUNT_TOKEN`, and then runs the sync — so
the `op` CLI can read bank credentials unattended. Non-secret config
(`GMAIL_ADDRESS`, the Keychain service name, `APP_*`) lives in a gitignored
`.env` (`cp .env.example .env`); **never put the token in `.env`.**

Keychain notes: the item is stored with `-A` (any process can read it without a
GUI prompt) because a headless launchd job can't answer a Keychain access dialog.
That's an acceptable trade-off on a dedicated single-user Mac Mini; tighten with
`-T <tool>` if you prefer. Because it's a **LaunchAgent** (runs in your user
session), the login Keychain must be unlocked — fine on an auto-login Mac Mini.

**Local development / manual runs.** No token needed — either run in `manual`
auth mode (the scraper prompts for credentials), or use your normal `op signin`
session and `export OP_SERVICE_ACCOUNT_TOKEN=$(security find-generic-password -a "$USER" -s notion-finance-sync-op-token -w)`.

### 4. Populate per-bank credentials in the project 1Password vault

For each session that needs login automation, create a 1Password Login item
in the `Notion Finance Sync` vault with `username` and `password` fields.

Required items (one per active session):
- `op://<vault>/BofA/{username,password}` — covers BofA cards + checking + savings + Roth IRA + Investment Mgmt (one login)
- `op://<vault>/Wells Fargo/{username,password}`
- `op://<vault>/U.S. Bank/{username,password}`
- `op://<vault>/Everbank/{username,password}`
- `op://<vault>/Venmo/{username,password}`
- `op://<vault>/E*Trade/{username,password}`
- `op://<vault>/Fidelity/{username,password}`

**Bilt is intentionally NOT in this list.** Bilt verifies by sending an SMS
code to Alex's phone number — there's no username/password flow to automate.
Bilt sessions are also long-lived on personal devices (Alex rarely has to
re-login), so once the persistent profile at `data/sessions/bilt/` is
established the scraper can usually proceed without any 2FA step at all.
The Bilt scraper module handles the phone-verification fallback if it does
get prompted.

CLI shortcut for one (repeat per bank, replacing placeholders):
```bash
op item create --category=login --vault="Notion Finance Sync" \
  --title="BofA" --url="https://www.bankofamerica.com/" \
  username="YOUR_USERNAME" password="YOUR_PASSWORD"
```

Or use the 1Password app/web UI.

### 5. Notion API integration secret

Create a Notion internal integration scoped to the Transactions database, then store the secret in the project vault as a Password or API Credential item titled `Notion Finance Sync Notion Internal Integration Secret` with a `credential` field.

Reference path: `op://<vault>/Notion Finance Sync Notion Internal Integration Secret/credential`

### 6. Gmail App Password for email 2FA reading

The email 2FA reader uses Gmail's IMAP gateway with an App Password (not OAuth).

1. Enable 2FA on your Google account.
2. Go to **Account → Security → App Passwords**.
3. Create a new app password named `notion-finance-sync`.
4. Store the 16-character output in 1Password as a Password or API Credential item titled `Gmail App Password` with a `credential` field.

Reference path: `op://<vault>/Gmail App Password/credential`

The Gmail address itself is **required** via the `GMAIL_ADDRESS` env var (set it in your gitignored `.env`, or the deploy environment). It has no hardcoded default, to keep the personal email out of source control.

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

## Deploy to a Mac Mini with Nix (nix-darwin)

> **Rebuilding from scratch (e.g. the Mini died)? Follow [`docs/DEPLOY.md`](docs/DEPLOY.md)**
> — the complete ordered runbook. This section is the summary.

The app is a **Nix package** (built from `uv.lock` via [uv2nix](https://github.com/pyproject-nix/uv2nix)).
`darwin-rebuild switch` builds it, renders `config.toml` from your nix options,
installs Chrome + `op`, and schedules a launchd agent — **no repo checkout, no
`uv sync`, no config file to place.**

**1. Add the flake input:**

```nix
inputs.notion-finance-sync.url = "github:alexjmiller5/notion-finance-sync";
inputs.notion-finance-sync.inputs.nixpkgs.follows = "nixpkgs";
```

**2. Import the module + enable it** (import at the flake-modules level so `inputs`
is in scope; the `settings` block is the non-secret `config.toml` as an attrset —
see [`config.example.toml`](config.example.toml)):

```nix
# in your darwinSystem modules list:  inputs.notion-finance-sync.darwinModules.default
services.notion-finance-sync = {
  enable = true;
  user = "alexmiller";
  hour = 3; minute = 30;          # optional (default 03:30)
  settings = {
    email.gmail_address = "you@example.com";
    bilt.phone = "5551234567";
    notion = {
      transactions_database_id = "…"; transactions_data_source_id = "…";
      tasks_data_source_id = "…";
      property_ids = { NAME = "title"; /* … from gen_property_ids.py … */ };
    };
    onepassword = {
      vault = "<vault-id>";
      service_account_token_ref = "op://Personal/<token item>/password";
      bank_items = { bofa = "BofA"; /* … */ };
    };
  };
};
```

`darwin-rebuild switch` then builds everything and creates the
`com.notion-finance-sync.daily` launchd user agent. State (Chrome profiles,
snapshots, logs) lives in `~/Library/Application Support/notion-finance-sync/`.

**3. One-time manual steps Nix can't do** (TCC/SIP-protected, secret, or
interactive): iPhone → Text Message Forwarding to the Mini; store the 1Password
token in the Keychain; grant Full Disk Access; run each bank's first login once
(`--bank <bank> --interactive`). See [`docs/DEPLOY.md`](docs/DEPLOY.md) steps 3–6.

Requirements: a `nix-darwin` host with `nix-homebrew` (Chrome cask) and
`allowUnfree` for the `1password-cli`. See `nix/darwin.nix` for all module options;
regenerate `property_ids` with `uv run scripts/gen_property_ids.py` if you recreate
the database.

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
