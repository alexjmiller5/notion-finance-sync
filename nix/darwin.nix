# nix-darwin module for the notion-finance-sync daily sync.
#
# Fully packaged deploy: `darwin-rebuild switch` builds the app (uv2nix, from
# uv.lock) into the store, generates config.toml from nix options, and installs a
# launchd user agent that runs it — no repo checkout, no `uv sync`, no config file
# to place by hand.
#
# Still irreducibly manual (TCC/SIP-protected or secret/stateful — see DEPLOY.md):
#   - store the 1Password service-account token in the Keychain (`just store-op-token`
#     or the one-liner in the README)
#   - grant the sync process Full Disk Access (Messages DB, for SMS 2FA)
#   - the first interactive login per bank (establishes its Chrome profile)
self:
{ config, lib, pkgs, ... }:

let
  cfg = config.services.notion-finance-sync;
  app = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
  tomlFormat = pkgs.formats.toml { };
  configFile = tomlFormat.generate "notion-finance-sync-config.toml" cfg.settings;

  # Wrapper: pull the OP token from the login Keychain (kept out of Nix), point
  # the app at the generated config + writable state dir, then exec it.
  runner = pkgs.writeShellScript "notion-finance-sync-run" ''
    set -euo pipefail
    export NFS_CONFIG=${lib.escapeShellArg "${configFile}"}
    export NFS_STATE_DIR=${lib.escapeShellArg cfg.stateDir}
    export PATH="${lib.makeBinPath [ cfg.opPackage pkgs.coreutils ]}:/usr/bin:/bin"
    mkdir -p "$NFS_STATE_DIR"
    if ! token="$(/usr/bin/security find-generic-password -a ${lib.escapeShellArg cfg.user} -s ${lib.escapeShellArg cfg.keychainService} -w 2>/dev/null)"; then
      echo "ERROR: 1Password token not in Keychain (item '${cfg.keychainService}'). Run: just store-op-token" >&2
      exit 1
    fi
    export OP_SERVICE_ACCOUNT_TOKEN="$token"
    unset token
    exec ${app}/bin/notion-finance-sync "$@"
  '';
in
{
  options.services.notion-finance-sync = {
    enable = lib.mkEnableOption "the notion-finance-sync daily bank -> Notion sync";

    user = lib.mkOption {
      type = lib.types.str;
      description = "Login user the sync runs as (needs the Keychain token, Messages access, Chrome profiles).";
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
        here — they stay in 1Password.
      '';
      example = lib.literalExpression ''
        {
          email.gmail_address = "you@example.com";
          bilt.phone = "5551234567";
          notion = {
            transactions_database_id = "...";
            transactions_data_source_id = "...";
            tasks_data_source_id = "...";
            property_ids = { NAME = "title"; AMOUNT = "..."; /* ... */ };
          };
          onepassword = {
            vault = "your-vault-id";
            service_account_token_ref = "op://Personal/<token item>/password";
            bank_items = { bofa = "BofA"; wells_fargo = "Wells Fargo"; /* ... */ };
          };
        }
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
      description = "macOS Keychain generic-password service holding the 1Password service-account token.";
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

    package = lib.mkOption {
      type = lib.types.package;
      default = app;
      defaultText = lib.literalExpression "self.packages.\${system}.default";
      description = "The packaged app (built from uv.lock via uv2nix).";
    };
  };

  config = lib.mkIf cfg.enable {
    homebrew = lib.mkIf cfg.installChrome {
      enable = true;
      casks = [ "google-chrome" ];
    };

    # launchd opens StandardOutPath before running the program, so the state dir
    # must already exist (owned by the user, who the agent runs as).
    system.activationScripts.postActivation.text = lib.mkAfter ''
      mkdir -p ${lib.escapeShellArg cfg.stateDir}
      chown ${lib.escapeShellArg cfg.user} ${lib.escapeShellArg cfg.stateDir}
    '';

    # USER agent: runs in the login session so it can reach the Keychain, the
    # Messages DB, and the per-bank Chrome profiles.
    launchd.user.agents.notion-finance-sync = {
      serviceConfig = {
        Label = "com.notion-finance-sync.daily";
        ProgramArguments = [ "${runner}" ];
        StartCalendarInterval = [ { Hour = cfg.hour; Minute = cfg.minute; } ];
        RunAtLoad = false;
        StandardOutPath = "${cfg.stateDir}/launchd.log";
        StandardErrorPath = "${cfg.stateDir}/launchd.err.log";
      };
    };
  };
}
