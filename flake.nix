{
  description = "Isolated Battlecode development and supervision images";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      lib = nixpkgs.lib;
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      roles = [
        "supervisor"
        "competitor"
      ];
      image =
        role:
        lib.nixosSystem {
          inherit system;
          modules = [
            self.nixosModules.${role}
            "${nixpkgs}/nixos/modules/virtualisation/google-compute-image.nix"
            {
              networking.hostName = "bcenv-${role}";
              virtualisation.diskSize = 16384;
              virtualisation.googleComputeImage.compressionLevel = 1;
              # Keep rebuilding declarative: deploy a new image, not the upstream
              # module's generic /etc/nixos/configuration.nix via nixos-rebuild.
              virtualisation.googleComputeImage.configFile = toString (
                pkgs.writeText "configuration.nix" ''
                  { ... }: { assertions = [{ assertion = false; message = "Rebuild bcenv images from the pinned flake; see docs/IMAGES.md."; }]; }
                ''
              );
            }
          ];
        };
      devSystems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
        "x86_64-darwin"
      ];
    in
    {
      nixosModules = {
        base = import ./nix/modules/base.nix;
        competitor = import ./nix/modules/competitor.nix;
        supervisor = import ./nix/modules/supervisor.nix;
      };
      nixosConfigurations = lib.genAttrs roles image;
      packages.${system} =
        lib.listToAttrs (
          map (role: {
            name = "${role}-gce";
            value = self.nixosConfigurations.${role}.config.system.build.googleComputeImage;
          }) roles
        )
        // {
          image-test = pkgs.writeShellApplication {
            name = "bcenv-test-gce-image";
            runtimeInputs = [
              pkgs.qemu_kvm
              pkgs.coreutils
              pkgs.gnutar
              pkgs.gnugrep
            ];
            text = builtins.readFile ./scripts/test-gce-image.sh;
          };
        };
      checks.${system} = {
        roles = import ./nix/tests/roles.nix { inherit pkgs; };
        workspace =
          pkgs.runCommand "bcenv-workspace-tests"
            {
              nativeBuildInputs = [
                pkgs.python3
                pkgs.git
              ];
            }
            ''
              export HOME=$TMPDIR/home
              mkdir -p "$HOME"
              cp -r ${./scripts} scripts
              chmod -R u+w scripts
              python -m unittest discover -s scripts/tests -v
              touch $out
            '';
      };
      formatter = lib.genAttrs devSystems (s: nixpkgs.legacyPackages.${s}.nixfmt);
      devShells = lib.genAttrs devSystems (
        s:
        let
          p = nixpkgs.legacyPackages.${s};
        in
        {
          default = p.mkShell {
            packages = [
              p.git
              p.python3
              p.nodejs_22
              p.nixfmt
              p.shellcheck
            ];
          };
        }
      );
    };
}
