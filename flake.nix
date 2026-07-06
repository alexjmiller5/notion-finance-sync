{
  description = "notion-finance-sync — direct-bank-scraper daily sync to Notion, packaged with uv2nix and deployable on a nix-darwin Mac Mini";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, uv2nix, pyproject-nix, pyproject-build-systems }:
    let
      inherit (nixpkgs) lib;
      systems = [ "aarch64-darwin" "x86_64-darwin" "aarch64-linux" "x86_64-linux" ];
      forAllSystems = f: lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      # Load the uv workspace (pyproject.toml + uv.lock) and build an overlay of
      # all locked dependencies. Prefer prebuilt wheels (matters on darwin).
      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
      overlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };

      # Per-package build fixups go here if a dep doesn't build cleanly.
      pyprojectOverrides = _final: _prev: { };

      mkPythonSet = pkgs:
        let
          python = pkgs.python312;
        in
        (pkgs.callPackage pyproject-nix.build.packages { inherit python; }).overrideScope (
          lib.composeManyExtensions [
            pyproject-build-systems.overlays.default
            overlay
            pyprojectOverrides
          ]
        );
    in
    {
      # The app as a self-contained venv in the store; the runnable command is
      # `${packages.default}/bin/notion-finance-sync`. No checkout, no uv sync.
      packages = forAllSystems (pkgs:
        let
          pythonSet = mkPythonSet pkgs;
        in
        {
          default = pythonSet.mkVirtualEnv "notion-finance-sync-env" workspace.deps.default;
        });

      # nix-darwin module for the Mac Mini deploy (Chrome cask + op + launchd agent).
      darwinModules.default = import ./nix/darwin.nix self;
      darwinModules.notion-finance-sync = self.darwinModules.default;

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
