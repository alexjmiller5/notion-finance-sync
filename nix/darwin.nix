# nix-darwin module for the notion-finance-sync daily sync.
#
# Fully packaged deploy: `darwin-rebuild switch` builds the app (uv2nix, from
# uv.lock), assembles a signed macOS .app bundle around it, generates config.toml
# from nix options, and installs a launchd user agent that runs the bundle.
#
# Why a .app: TCC (Full Disk Access, for reading Messages/chat.db during SMS 2FA)
# keys grants on code identity. Granting a signed .app at a stable path — re-signed
# each rebuild with the SAME self-signed cert — gives a stable designated
# requirement, so ONE FDA grant survives every future update. Verified: FDA on the
# bundle inherits across the exec into the (unsigned, store-path) python.
#
# The signing cert is created automatically at activation (once, idempotent), so the
# only irreducibly manual bits (TCC/SIP-protected or interactive) are:
#   - grant Full Disk Access once to /Applications/NotionFinanceSync.app
#   - the first interactive login per bank (establishes its Chrome profile)
# The OP token comes from an agenix-decrypted file (tokenFile); Keychain is a fallback.
self:
{ config, lib, pkgs, ... }:

let
  cfg = config.services.notion-finance-sync;
  app = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
  tomlFormat = pkgs.formats.toml { };
  configFile = tomlFormat.generate "notion-finance-sync-config.toml" cfg.settings;

  # Wrapper: resolve the OP token (agenix file first, Keychain fallback), point the
  # app at the generated config + writable state dir, then exec it.
  runner = pkgs.writeShellScript "notion-finance-sync-run" ''
    set -euo pipefail
    export NFS_CONFIG=${lib.escapeShellArg "${configFile}"}
    export NFS_STATE_DIR=${lib.escapeShellArg cfg.stateDir}
    export PATH="${lib.makeBinPath [ cfg.opPackage pkgs.coreutils ]}:/usr/bin:/bin"
    mkdir -p "$NFS_STATE_DIR"

    token=""
    token_file=${lib.escapeShellArg (toString (cfg.tokenFile or ""))}
    if [ -n "$token_file" ] && [ -r "$token_file" ]; then
      token="$(cat "$token_file")"
    else
      token="$(/usr/bin/security find-generic-password -a ${lib.escapeShellArg cfg.user} -s ${lib.escapeShellArg cfg.keychainService} -w 2>/dev/null || true)"
    fi
    if [ -z "$token" ]; then
      echo "ERROR: no 1Password token (agenix file '$token_file' unreadable and Keychain item '${cfg.keychainService}' missing)." >&2
      exit 1
    fi
    export OP_SERVICE_ACCOUNT_TOKEN="$token"
    unset token
    exec ${app}/bin/notion-finance-sync "$@"
  '';

  # The .app bundle: a tiny signed Mach-O exec that hands off to the runner. Built
  # unsigned in the store; activation copies it to a stable path and codesigns it.
  appBundle = pkgs.runCommandCC "notion-finance-sync-app" { } ''
    mkdir -p "$out/Contents/MacOS"
    cat > "$out/Contents/Info.plist" <<'PLIST'
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0"><dict>
      <key>CFBundleIdentifier</key><string>${cfg.bundleId}</string>
      <key>CFBundleName</key><string>${cfg.appName}</string>
      <key>CFBundleExecutable</key><string>notion-finance-sync</string>
      <key>CFBundlePackageType</key><string>APPL</string>
      <key>LSBackgroundOnly</key><true/>
    </dict></plist>
    PLIST
    # exec passes its args straight through to the runner (store path baked in)
    cat > stub.c <<EOF
    #include <unistd.h>
    int main(int argc, char **argv) {
      argv[0] = (char *)"${runner}";
      execv("${runner}", argv);
      return 127;
    }
    EOF
    $CC -O2 -o "$out/Contents/MacOS/notion-finance-sync" stub.c
  '';

  appExe = "${cfg.appInstallPath}/Contents/MacOS/notion-finance-sync";
in
{
  options.services.notion-finance-sync = {
    enable = lib.mkEnableOption "the notion-finance-sync daily bank -> Notion sync";

    user = lib.mkOption {
      type = lib.types.str;
      description = "Login user the sync runs as (needs the token, Messages access, Chrome profiles).";
      example = "alexmiller";
    };

    stateDir = lib.mkOption {
      type = lib.types.str;
      default = "/Users/${cfg.user}/Library/Application Support/notion-finance-sync";
      defaultText = lib.literalExpression ''"/Users/''${user}/Library/Application Support/notion-finance-sync"'';
      description = "Writable dir for Chrome profiles, snapshots, statements, health, tokens, logs.";
    };

    settings = lib.mkOption {
      type = tomlFormat.type;
      description = ''
        config.toml contents (non-secret identifiers), generated into the store and
        passed via NFS_CONFIG. Mirrors config.example.toml: sections `email`,
        `bilt`, `notion` (with `property_ids`), and `onepassword`. Secrets are NOT
        here.
      '';
    };

    tokenFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = ''
        Path to a file containing the 1Password service-account token (e.g. an
        agenix-decrypted secret: `config.age.secrets.op-token.path`). Preferred over
        the Keychain. If null/unreadable, the runner falls back to the Keychain item.
      '';
    };

    hour = lib.mkOption {
      type = lib.types.int;
      default = 3;
      description = "Hour (0-23, local) the daily sync fires.";
    };

    minute = lib.mkOption {
      type = lib.types.int;
      default = 30;
      description = "Minute the daily sync fires.";
    };

    keychainService = lib.mkOption {
      type = lib.types.str;
      default = "notion-finance-sync-op-token";
      description = "Keychain generic-password service holding the OP token (fallback when tokenFile is unset).";
    };

    bundleId = lib.mkOption {
      type = lib.types.str;
      default = "com.alexmiller.notion-finance-sync";
      description = "CFBundleIdentifier of the generated .app.";
    };

    appName = lib.mkOption {
      type = lib.types.str;
      default = "NotionFinanceSync";
      description = "Display name of the generated .app.";
    };

    appInstallPath = lib.mkOption {
      type = lib.types.str;
      default = "/Applications/NotionFinanceSync.app";
      description = "Stable path the signed .app is installed to (the thing you grant Full Disk Access).";
    };

    signingIdentity = lib.mkOption {
      type = lib.types.str;
      default = "notion-finance-sync-signing";
      description = ''
        Common name of the self-signed code-signing cert (in the System keychain, so
        root can sign at activation). Created automatically at activation if absent.
        A stable cert => stable designated requirement => FDA grant persists.
      '';
    };

    opPackage = lib.mkOption {
      type = lib.types.package;
      default = pkgs._1password-cli;
      defaultText = lib.literalExpression "pkgs._1password-cli";
      description = "The 1Password CLI (`op`) — the app reads bank creds from 1Password at runtime.";
    };

    installChrome = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Add the google-chrome homebrew cask (SeleniumBase UC uses real Chrome; not in nixpkgs on darwin).";
    };
  };

  config = lib.mkIf cfg.enable {
    homebrew = lib.mkIf cfg.installChrome {
      enable = true;
      casks = [ "google-chrome" ];
    };

    # Homebrew (for the google-chrome cask above) needs Xcode Command Line Tools,
    # but they're Apple-proprietary (softwareupdate only, not a nix package). Install
    # them before the Homebrew step so nothing is manual. Lives in the module because
    # the module is what pulls in the Chrome cask that needs them.
    system.activationScripts.preActivation.text = lib.mkIf cfg.installChrome (lib.mkBefore ''
      if ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
        echo "installing Xcode Command Line Tools (Homebrew prerequisite)..."
        touch /tmp/.com.apple.dt.CommandLineTools.installondemand.in_progress
        prod="$(/usr/sbin/softwareupdate -l 2>/dev/null | grep -i 'label:.*command line tools' | tail -1 | sed 's/^.*[Ll]abel: //')"
        if [ -n "$prod" ]; then
          /usr/sbin/softwareupdate -i "$prod" --verbose || true
        fi
        rm -f /tmp/.com.apple.dt.CommandLineTools.installondemand.in_progress
      fi
    '');

    system.activationScripts.postActivation.text = lib.mkAfter ''
      # 1. Ensure a stable self-signed code-signing cert in the System keychain.
      #    Created ONCE (idempotent) and reused every rebuild, so the .app's signature
      #    — and thus the one-time Full Disk Access grant — stays stable. No script.
      if ! /usr/bin/security find-identity -v -p codesigning /Library/Keychains/System.keychain 2>/dev/null | grep -q ${lib.escapeShellArg cfg.signingIdentity}; then
        echo "creating code-signing identity ${cfg.signingIdentity} (one-time)..."
        _t="$(/usr/bin/mktemp -d)"
        /usr/bin/printf '[req]\ndistinguished_name=dn\nx509_extensions=v3\nprompt=no\n[dn]\nCN=%s\n[v3]\nbasicConstraints=critical,CA:false\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=critical,codeSigning\n' ${lib.escapeShellArg cfg.signingIdentity} > "$_t/req.cnf"
        /usr/bin/openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -keyout "$_t/key.pem" -out "$_t/cert.pem" -config "$_t/req.cnf"
        /usr/bin/openssl pkcs12 -export -inkey "$_t/key.pem" -in "$_t/cert.pem" -out "$_t/id.p12" -passout pass:
        /usr/bin/security import "$_t/id.p12" -k /Library/Keychains/System.keychain -P "" -T /usr/bin/codesign -A
        /usr/bin/security add-trusted-cert -d -r trustRoot -p codeSign -k /Library/Keychains/System.keychain "$_t/cert.pem"
        /bin/rm -rf "$_t"
      fi

      # 2. State dir must exist before launchd opens StandardOutPath (owned by the user).
      /bin/mkdir -p ${lib.escapeShellArg cfg.stateDir}
      /usr/sbin/chown ${lib.escapeShellArg cfg.user} ${lib.escapeShellArg cfg.stateDir}

      # 3. Install the .app to a stable path and sign it with the stable cert.
      echo "installing ${cfg.appInstallPath}..."
      /bin/rm -rf ${lib.escapeShellArg cfg.appInstallPath}
      /bin/cp -R ${appBundle} ${lib.escapeShellArg cfg.appInstallPath}
      /bin/chmod -R u+w ${lib.escapeShellArg cfg.appInstallPath}
      /usr/bin/codesign --force --sign ${lib.escapeShellArg cfg.signingIdentity} ${lib.escapeShellArg cfg.appInstallPath}
    '';

    # USER agent: runs in the login session so it can reach the token, Messages DB,
    # and per-bank Chrome profiles. Runs the signed .app exec (which hands to the runner).
    launchd.user.agents.notion-finance-sync = {
      serviceConfig = {
        Label = "com.notion-finance-sync.daily";
        ProgramArguments = [ appExe ];
        StartCalendarInterval = [ { Hour = cfg.hour; Minute = cfg.minute; } ];
        RunAtLoad = false;
        StandardOutPath = "${cfg.stateDir}/launchd.log";
        StandardErrorPath = "${cfg.stateDir}/launchd.err.log";
      };
    };
  };
}
