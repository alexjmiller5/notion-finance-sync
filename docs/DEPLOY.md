# Deploy runbook — rebuild the Mac Mini sync from scratch

The complete, ordered process to get the daily sync running on a fresh Mac Mini
(e.g. the old one died). Follow top to bottom. Steps marked **[manual]** cannot
be automated (TCC/SIP-protected, secret-bearing, or interactive) — everything
else is `darwin-rebuild`.

Roughly a half-day, most of it waiting on 2FA during the per-bank bootstrap.

---

## 0. Prerequisites (once per machine)

- macOS on the Mac Mini, signed into **[manual]** the Apple ID whose iMessage/SMS
  you'll read 2FA from (Settings → Apple ID). Required for step 5.
- [Determinate Nix](https://determinate.systems/nix) installed (the config sets
  `nix.enable = false` because Determinate manages the daemon).
- Your two private repos reachable: `nix-config` and `notion-finance-sync`.
- A GitHub token available to Nix for the private flake input — either make the
  repo public, or add to `~/.config/nix/nix.conf`:
  `access-tokens = github.com=<a GitHub PAT>`.

## 1. Clone both repos

```bash
mkdir -p ~/Desktop/coding/active-projects && cd ~/Desktop/coding/active-projects
git clone git@github.com:alexjmiller5/nix-config.git
git clone git@github.com:alexjmiller5/notion-finance-sync.git
```

The `checkoutDir` in `nix-config/hosts/mac-mini.nix` must match where you cloned
`notion-finance-sync` (default: the path above).

## 2. Create `config.toml` (non-secret personal identifiers)

```bash
cd ~/Desktop/coding/active-projects/notion-finance-sync
cp config.example.toml config.toml
# edit config.toml: your Notion database + data-source IDs, 1Password vault name,
# and the session_id -> 1Password item map. (Gitignored — never committed.)
```

If you recreated the Notion database (new IDs), also regenerate the pinned Notion
property IDs after step 4/6:
`OP_SERVICE_ACCOUNT_TOKEN=... uv run scripts/gen_property_ids.py`.

## 3. Install the Python env

```bash
uv sync    # builds .venv from uv.lock (uv comes from home-manager)
```

## 4. `darwin-rebuild switch` — the automated half

```bash
cd ~/Desktop/coding/active-projects/nix-config
sudo darwin-rebuild switch --flake .#mac-mini
```

This installs **Google Chrome** (homebrew cask), the **`op`** CLI, and the
**`com.notion-finance-sync.daily`** launchd user agent (fires 03:30 daily). It
does NOT start syncing yet — the manual steps below must come first.

## 5. **[manual]** Route 2FA to the Mac Mini

The SMS reader reads the Mac's Messages database, so the Mini must receive the
codes:

- On your **iPhone**: Settings → Messages → **Text Message Forwarding** → enable
  the Mac Mini. (Same Apple ID; the Mini shows a code to confirm.)
- Some banks 2FA by **email** instead — those use the Gmail app password from
  1Password + `GMAIL_ADDRESS` in `.env` (step 6).

## 6. **[manual]** Secrets + non-secret env

```bash
cd ~/Desktop/coding/active-projects/notion-finance-sync

# 1Password service-account token -> macOS Keychain (encrypted at rest, never on disk):
just store-op-token          # paste the token from 1Password (Personal vault)

# Non-secret runtime env (gitignored). Your Gmail address for email-2FA banks:
echo 'GMAIL_ADDRESS=you@example.com' > .env
```

Bank passwords, the Notion API key, and the Gmail app password stay in 1Password
and are read at runtime via `op` (the token above authenticates it unattended).

## 7. **[manual]** Grant Full Disk Access

The 2FA reader opens `~/Library/Messages/chat.db`, which macOS gates behind Full
Disk Access (SIP-protected — no CLI can grant it):

1. System Settings → Privacy & Security → **Full Disk Access**.
2. Add the process that runs the sync: `uv` (`~/.nix-profile/bin/uv` or
   `/etc/profiles/per-user/<you>/bin/uv`) and/or your terminal.
3. Quit and reopen the terminal.

Verify: `sqlite3 ~/Library/Messages/chat.db "SELECT COUNT(*) FROM message"` — a
number means it's granted; `unable to open database file` means it isn't.

## 8. Notion schema (only if the database is new)

```bash
just migrate     # creates/renames properties; skip if the DB already has the schema
```

## 9. **[manual]** Bootstrap each bank — one at a time

The first login per bank establishes its persistent Chrome profile + device
trust, so later runs are unattended. Do them **one at a time**, watching the 2FA:

```bash
just sync-bank bofa --interactive
# ...confirm rows land in Notion, then the next:
just sync-bank wells_fargo --interactive
# us_bank, everbank, venmo, etrade, fidelity, bofa_investments, bilt ...
```

(Start with `bofa` — it's the most-proven path.) `bilt` auths by phone
device-trust, not a vault password.

## 10. Done — it's now unattended

Once every bank bootstraps clean, the launchd agent runs the full sync daily at
03:30. Nothing above repeats **unless a bank's device-trust expires** (you'll see
it fail in the logs) — then just re-run step 9 for that one bank.

- Logs: `data/launchd.log` and `data/launchd.err.log` in the checkout.
- Manual run anytime: `just sync` (all) or `just sync-bank <bank>`.
- Health: banks failing 3× in a day auto-create a Notion task (SPEC §3).

## What repeats, what doesn't

| Trigger | Redo |
|---|---|
| New code / config | `git pull` + `uv sync` (+ `darwin-rebuild switch` if the module changed) |
| A bank's device-trust expired | step 9 for that bank only |
| Recreated the Notion DB | `config.toml` IDs + `gen_property_ids.py` + `just migrate` |
| Fresh Mac Mini | this whole runbook |
