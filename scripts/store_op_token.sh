#!/usr/bin/env bash
#
# Store the 1Password service-account token in the macOS Keychain.
#
# The token is the bootstrap secret that lets the daemon read all other secrets
# from 1Password, so it can't live in 1Password itself (chicken-and-egg) and per
# project policy must not sit plaintext on disk. The login Keychain (encrypted at
# rest) is the right home on macOS.
#
# You'll be prompted for the token value (hidden) — it never touches shell
# history or a file. `-A` lets the sync process read it without a GUI prompt,
# which a headless launchd job needs (acceptable on a dedicated single-user Mac
# Mini; tighten with `-T <tool>` if you prefer).
#
set -euo pipefail

KEYCHAIN_SERVICE="${OP_TOKEN_KEYCHAIN_SERVICE:-notion-finance-sync-op-token}"
KC_USER="$(id -un)"

echo "Storing 1Password service-account token in the login Keychain."
echo "  Keychain service: $KEYCHAIN_SERVICE"
echo "  Account:          $KC_USER"
echo "Paste the token when prompted (input hidden; it will ask twice)."

security add-generic-password -U -A \
  -a "$KC_USER" -s "$KEYCHAIN_SERVICE" \
  -l "notion-finance-sync OP service-account token" \
  -w

echo
echo "Stored. Verify with:"
echo "  security find-generic-password -a \"$KC_USER\" -s \"$KEYCHAIN_SERVICE\" -w >/dev/null && echo OK"
