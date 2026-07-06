# Deploy runbook — rebuild the Mac Mini sync from scratch

The complete, ordered process to get the daily sync running on a fresh Mac Mini.
The app is a **Nix package** (built from `uv.lock` via uv2nix): `darwin-rebuild
switch` builds it, renders `config.toml` from your nix options, and installs the
launchd agent — **no repo checkout, no `uv sync`, no config file to place.**

Steps marked **[manual]** can't be automated (TCC/SIP-protected, secret-bearing,
or interactive). Everything else is one `darwin-rebuild`.

---

## 0. Prerequisites

- macOS on the Mac Mini, signed into **[manual]** the Apple ID you'll read 2FA
  SMS from (Settings → Apple ID). Needed for step 3.
- [Determinate Nix](https://determinate.systems/nix) (the config sets
  `nix.enable = false` — Determinate manages the daemon).
- `nix-config` reachable (your nix-darwin flake, with `nix-homebrew`). The
  `notion-finance-sync` flake input is a **public** repo, so no GitHub token.

## 1. Configure it in `nix-config`

The module (`services.notion-finance-sync`) is already wired into `hosts/mac-mini.nix`:
enable it, set `user`, and provide `settings` — the non-secret `config.toml` as a
nix attrset (Notion IDs, the property-ID map, 1Password vault ID, bank→item map,
Gmail, Bilt phone). Secrets are NOT here — they stay in 1Password.

```nix
services.notion-finance-sync = {
  enable = true;
  user = "alexmiller";
  settings = {
    email.gmail_address = "…";
    bilt.phone = "…";
    notion = { transactions_data_source_id = "…"; property_ids = { … }; … };
    onepassword = { vault = "<vault-id>"; bank_items = { … }; … };
  };
};
```

If you recreated the Notion database (new IDs), regenerate the `property_ids`
block: `NFS_CONFIG=… uv run scripts/gen_property_ids.py` prints a `[notion.property_ids]`
TOML block — translate it to the nix attrset (or update `config.toml` and re-derive).

## 2. `darwin-rebuild switch` — the automated everything

```bash
cd ~/…/nix-config
sudo darwin-rebuild switch --flake .#mac-mini
```

This builds the app into the store, generates `config.toml`, installs the
**google-chrome** cask + the **`op`** CLI, and creates the
`com.notion-finance-sync.daily` launchd **user** agent (fires 03:30 daily). State
(Chrome profiles, snapshots, logs) lives in `~/Library/Application Support/notion-finance-sync/`.

The agent doesn't sync yet — the manual bits below come first.

## 3. **[manual]** Route 2FA to the Mac Mini

On your **iPhone**: Settings → Messages → **Text Message Forwarding** → enable the
Mac Mini (same Apple ID; confirm the code it shows). Email-2FA banks use the Gmail
app password from 1Password instead.

## 4. **[manual]** Store the 1Password token in the Keychain

The service-account token is the bootstrap secret (unlocks every other secret) — it
can't live in Nix. Store it in the login Keychain:

```bash
security add-generic-password -a "$USER" -s notion-finance-sync-op-token -w '<TOKEN>' -U
```

(The runner reads it via `security find-generic-password` and exports
`OP_SERVICE_ACCOUNT_TOKEN` so `op` authenticates unattended.)

## 5. **[manual]** Grant Full Disk Access

The 2FA reader opens `~/Library/Messages/chat.db` (SIP-protected). System Settings →
Privacy & Security → **Full Disk Access** → add the launchd-run binary
(`/nix/store/…-notion-finance-sync-env/bin/notion-finance-sync`) — or, more simply,
your terminal, and run the first bootstraps (step 6) from it. Re-login after.

Verify: `sqlite3 ~/Library/Messages/chat.db "SELECT COUNT(*) FROM message"` returns
a number (not `unable to open database file`).

## 6. **[manual]** Bootstrap each bank — one at a time

The first login per bank establishes its persistent Chrome profile + device trust,
so later runs are unattended. Run the packaged binary directly, one bank at a time,
watching the 2FA:

```bash
BIN=$(readlink -f /run/current-system/sw/bin 2>/dev/null); # or find the env in the store
export NFS_STATE_DIR="$HOME/Library/Application Support/notion-finance-sync"
export OP_SERVICE_ACCOUNT_TOKEN=$(security find-generic-password -a "$USER" -s notion-finance-sync-op-token -w)
notion-finance-sync --bank bofa --interactive     # if the binary is on PATH; else use the store path
# …then wells_fargo, us_bank, everbank, venmo, etrade, fidelity, bofa_investments, bilt
```

(Start with `bofa` — most-proven. `bilt` auths by phone device-trust, no vault item.)
The binary's store path is in the launchd runner:
`grep exec ~/…/nix-config/result` after a build, or `launchctl print`.

## 7. Done — unattended

Once every bank bootstraps clean, the launchd agent runs the full sync daily at
03:30. Nothing above repeats **unless a bank's device-trust expires** — then re-do
step 6 for that one bank.

- Logs: `~/Library/Application Support/notion-finance-sync/launchd.{log,err.log}`.
- Health: banks failing 3× in a day auto-create a Notion task (SPEC §3).

## What repeats, what doesn't

| Trigger | Redo |
|---|---|
| New app version | bump the flake input (`nix flake update notion-finance-sync` in nix-config) + `darwin-rebuild switch` |
| Config change (IDs, banks) | edit `settings` in `hosts/mac-mini.nix` + `darwin-rebuild switch` |
| A bank's device-trust expired | step 6 for that bank only |
| Recreated the Notion DB | regenerate `property_ids` (step 1) + `just migrate` |
| Fresh Mac Mini | this whole runbook |
