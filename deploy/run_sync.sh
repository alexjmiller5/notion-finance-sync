#!/usr/bin/env bash
#
# launchd wrapper for the Mac Mini daily sync.
#
# Sources the 1Password service-account token from the macOS Keychain (encrypted
# at rest — never stored plaintext on disk), exports it as OP_SERVICE_ACCOUNT_TOKEN
# so the `op` CLI can read bank credentials unattended, loads non-secret config
# from .env if present, then runs the sync.
#
# The daily launchd job (deploy/*.plist) invokes this script. To store the token:
#   just store-op-token
#
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/Users/alexmiller/desktop/coding/active-projects/notion-finance-sync}"
KEYCHAIN_SERVICE="${OP_TOKEN_KEYCHAIN_SERVICE:-notion-finance-sync-op-token}"
KC_USER="$(id -un)"
UV_BIN="${UV_BIN:-$HOME/.local/bin/uv}"
[[ -x "$UV_BIN" ]] || UV_BIN="$(command -v uv)"

cd "$PROJECT_DIR"

# Non-secret runtime config (GMAIL_ADDRESS, APP_*, OP_TOKEN_KEYCHAIN_SERVICE ...).
# NEVER put the token here — it lives in the Keychain.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  KEYCHAIN_SERVICE="${OP_TOKEN_KEYCHAIN_SERVICE:-$KEYCHAIN_SERVICE}"
fi

# 1Password service-account token from the login Keychain -> env for `op`.
if ! token="$(security find-generic-password -a "$KC_USER" -s "$KEYCHAIN_SERVICE" -w 2>/dev/null)"; then
  echo "ERROR: could not read Keychain item '$KEYCHAIN_SERVICE' for user '$KC_USER'." >&2
  echo "       Store it first with:  just store-op-token" >&2
  exit 1
fi
export OP_SERVICE_ACCOUNT_TOKEN="$token"
unset token

export PYTHONPATH="$PROJECT_DIR/src"
exec "$UV_BIN" run python scripts/sync.py "$@"
