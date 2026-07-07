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

This builds the app into the store, wraps it in **`/Applications/NotionFinanceSync.app`**
(signed with the stable cert from step 5), generates `config.toml`, installs the
**google-chrome** cask + the **`op`** CLI, and creates the
`com.notion-finance-sync.daily` launchd **user** agent (fires 03:30 daily, runs the
`.app`). State (Chrome profiles, snapshots, logs) lives in `~/Library/Application Support/notion-finance-sync/`.
(Order note: run `scripts/make-signing-cert.sh` from step 5 before the *first*
rebuild, so the `.app` gets the stable signature and your FDA grant sticks.)

The agent doesn't sync yet — the manual bits below come first.

## 3. **[manual]** Route 2FA to the Mac Mini

On your **iPhone**: Settings → Messages → **Text Message Forwarding** → enable the
Mac Mini (same Apple ID; confirm the code it shows). Email-2FA banks use the Gmail
app password from 1Password instead.

## 4. **[manual]** Provide the 1Password token via agenix

The service-account token is the bootstrap secret (unlocks every other secret) — it
can't come *from* `op`, so it lives **age-encrypted in `nix-config`** (recipients:
the Mini's SSH host key + your laptop key, in `secrets/secrets.nix`) and is decrypted
at activation to `/run/agenix/op-token`. Encrypt/rotate it from the laptop:

```bash
cd nix-config/secrets
EDITOR=nano nix run github:ryantm/agenix -- -i ~/.ssh/mac_mini -e op-token.age  # paste ops_… token
cd .. && git add secrets/op-token.age && git commit -m "OP token" && git push
```

(The runner reads that file and exports `OP_SERVICE_ACCOUNT_TOKEN`. Fallback: if the
file is unreadable it uses a Keychain item `notion-finance-sync-op-token`, stored via
`security add-generic-password -a "$USER" -s notion-finance-sync-op-token -U -A -w`.)

## 5. **[manual]** Signing cert + Full Disk Access (once)

The sync reads `~/Library/Messages/chat.db` (SIP-protected) for SMS 2FA. FDA is
granted to the **signed `NotionFinanceSync.app`** — and because activation re-signs
it each rebuild with a **stable self-signed cert**, that one grant survives updates.

```bash
sudo bash scripts/make-signing-cert.sh    # once: creates the cert in the System keychain
# (then step 2's darwin-rebuild installs + signs /Applications/NotionFinanceSync.app)
```

Then System Settings → Privacy & Security → **Full Disk Access** → **[+]** →
`/Applications/NotionFinanceSync.app`, toggle on.

Verify (as the app would): grant works if a launchd-run helper can open the DB —
the first bank bootstrap (step 6) exercises it. `sqlite3 ~/Library/Messages/chat.db
"SELECT COUNT(*) FROM message"` from a Full-Disk-Access terminal should return a
number, confirming the DB is readable at all.

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
