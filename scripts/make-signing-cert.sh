#!/usr/bin/env bash
# One-time: create a self-signed code-signing certificate in the System keychain so
# `darwin-rebuild`'s activation can sign NotionFinanceSync.app with a STABLE identity.
# A stable cert => stable designated requirement => the one-time Full Disk Access
# grant to the .app survives every future rebuild. Idempotent.
#
# Usage: sudo scripts/make-signing-cert.sh [common-name]
set -euo pipefail
CN="${1:-notion-finance-sync-signing}"
KEYCHAIN=/Library/Keychains/System.keychain
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

if /usr/bin/security find-identity -v -p codesigning "$KEYCHAIN" 2>/dev/null | grep -q "$CN"; then
  echo "code-signing identity '$CN' already present — nothing to do."
  exit 0
fi

# LibreSSL-compatible extensions via config (macOS openssl is LibreSSL; no -addext)
cat > "$tmp/req.cnf" <<CNF
[req]
distinguished_name = dn
x509_extensions = v3
prompt = no
[dn]
CN = $CN
[v3]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
CNF

openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout "$tmp/key.pem" -out "$tmp/cert.pem" -config "$tmp/req.cnf"
openssl pkcs12 -export -inkey "$tmp/key.pem" -in "$tmp/cert.pem" -out "$tmp/id.p12" -passout pass:

# import identity (cert+key); -A + codesign in the ACL so root can sign unattended
/usr/bin/security import "$tmp/id.p12" -k "$KEYCHAIN" -P "" -T /usr/bin/codesign -A
# trust it for code signing (self-signed root)
/usr/bin/security add-trusted-cert -d -r trustRoot -p codeSign -k "$KEYCHAIN" "$tmp/cert.pem"

echo "created code-signing identity '$CN':"
/usr/bin/security find-identity -v -p codesigning "$KEYCHAIN" | grep "$CN"
