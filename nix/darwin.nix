# nix-darwin module for the notion-finance-sync daily sync.
#
# Wires up everything the Mac Mini deploy needs that Nix CAN express declaratively:
#   - Google Chrome (real Chrome — SeleniumBase UC mode uses channel="chrome";
#     not packaged for darwin in nixpkgs, so installed as a homebrew cask)
#   - a launchd USER agent that runs the daily sync as the login user, so it has
#     the login Keychain (op token), the Messages database (SMS 2FA), the
#     per-bank Chrome profiles, and Full Disk Access
#   - log files under the checkout's data/ dir
#
# Things Nix CANNOT do (documented manual steps — see README "Deploy"):
#   - store the 1Password service-account token in the Keychain (`just store-op-token`)
#   - grant the sync process Full Disk Access (TCC is SIP-protected)
#   - the first interactive login per bank (establishes the persistent profile)
{ config, lib, pkgs, ... }:

let
  cfg = config.services.notion-finance-sync;
in
{
  options.services.notion-finance-sync = {
    enable = lib.mkEnableOption "the notion-finance-sync daily bank -> Notion sync";

    user = lib.mkOption {
      type = lib.types.str;
      description = "Login user the sync runs as (needs the Keychain token, Messages access, and Chrome profiles).";
      example = "alexmiller";
    };

    checkoutDir = lib.mkOption {
      type = lib.types.str;
      description = ''
        Absolute path to the cloned repo. The sync runs from here via `uv`
        (uv.lock pins the Python env); data/ (sessions, snapshots, logs) is
        written under it, so it must be a writable path in the user's home.
      '';
      example = "/Users/alexmiller/notion-finance-sync";
    };

    hour = lib.mkOption {
      type = lib.types.int;
      default = 3;
      description = "Hour (0-23, local time) the daily sync fires.";
    };

    minute = lib.mkOption {
      type = lib.types.int;
      default = 30;
      description = "Minute the daily sync fires.";
    };

    keychainService = lib.mkOption {
      type = lib.types.str;
      default = "notion-finance-sync-op-token";
      description = "macOS Keychain generic-password service name holding the 1Password service-account token.";
    };

    opPackage = lib.mkOption {
      type = lib.types.package;
      default = pkgs._1password-cli;
      defaultText = lib.literalExpression "pkgs._1password-cli";
      description = "The 1Password CLI (`op`) package — the app reads bank creds from 1Password at runtime.";
    };

    installChrome = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Add the google-chrome homebrew cask (disable if you manage Chrome elsewhere).";
    };
  };

  config = lib.mkIf cfg.enable {
    # Real Chrome for SeleniumBase UC mode (channel="chrome").
    homebrew = lib.mkIf cfg.installChrome {
      enable = true;
      casks = [ "google-chrome" ];
    };

    # USER agent (not a daemon): runs in the user's GUI session so it can reach
    # the login Keychain, the Messages DB, and the per-bank Chrome profiles.
    launchd.user.agents.notion-finance-sync = {
      serviceConfig = {
        Label = "com.notion-finance-sync.daily";
        ProgramArguments = [
          "/bin/bash"
          "${cfg.checkoutDir}/deploy/run_sync.sh"
        ];
        StartCalendarInterval = [
          { Hour = cfg.hour; Minute = cfg.minute; }
        ];
        RunAtLoad = false;
        StandardOutPath = "${cfg.checkoutDir}/data/launchd.log";
        StandardErrorPath = "${cfg.checkoutDir}/data/launchd.err.log";
        EnvironmentVariables = {
          PROJECT_DIR = cfg.checkoutDir;
          OP_TOKEN_KEYCHAIN_SERVICE = cfg.keychainService;
          # uv (per-user nix profile) + op + bash/coreutils (nix) + security (system).
          PATH = lib.concatStringsSep ":" [
            (lib.makeBinPath [ pkgs.bash pkgs.coreutils cfg.opPackage ])
            "/etc/profiles/per-user/${cfg.user}/bin"
            "/Users/${cfg.user}/.nix-profile/bin"
            "/usr/bin"
            "/bin"
            "/usr/sbin"
            "/sbin"
          ];
        };
      };
    };
  };
}
