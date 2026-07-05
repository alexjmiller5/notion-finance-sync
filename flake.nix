{
  description = "notion-finance-sync — direct-bank-scraper daily sync to Notion, deployable on a nix-darwin Mac Mini";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      # Systems a dev shell is offered for (the deploy target is aarch64-darwin).
      systems = [ "aarch64-darwin" "x86_64-darwin" "aarch64-linux" "x86_64-linux" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      # The main deliverable: a nix-darwin module that wires up the daily sync
      # (Chrome via homebrew, a launchd user agent, log files). Import it into a
      # darwinConfiguration and set `services.notion-finance-sync.checkoutDir`.
      #
      #   imports = [ notion-finance-sync.darwinModules.default ];
      #   services.notion-finance-sync = {
      #     enable = true;
      #     user = "alexmiller";
      #     checkoutDir = "/Users/alexmiller/desktop/coding/active-projects/notion-finance-sync";
      #   };
      darwinModules.default = import ./nix/darwin.nix;
      darwinModules.notion-finance-sync = self.darwinModules.default;

      # Dev shell with the exact toolchain the project uses (uv, ruff, just).
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [ pkgs.uv pkgs.ruff pkgs.just pkgs.python312 ];
          shellHook = ''
            export PYTHONPATH="$PWD/src"
            echo "notion-finance-sync dev shell — 'just' to list tasks, 'uv sync' to install."
          '';
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixpkgs-fmt);
    };
}
