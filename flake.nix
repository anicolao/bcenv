{
  description = "Battlecode 2023 reproducible headless development foundation";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      packagesFor = system:
        let
          pkgs = import nixpkgs { inherit system; };
          engine = pkgs.callPackage ./nix/battlecode23.nix {};
          bcenv = pkgs.callPackage ./nix/bcenv.nix { inherit engine; };
        in { inherit pkgs engine bcenv; };
    in {
      packages = forAllSystems (system:
        let p = packagesFor system; in {
          battlecode23 = p.engine;
          default = p.bcenv;
            bcenv = p.bcenv;
          jdk = p.pkgs.jdk8;
          python = p.pkgs.python3;
          nodejs = p.pkgs.nodejs;
          git = p.pkgs.git;
          npm-cache = p.pkgs.callPackage ./nix/npm-cache.nix {};
        });
      devShells = forAllSystems (system:
        let p = packagesFor system; in {
          default = p.pkgs.mkShell {
            packages = [ p.bcenv p.engine p.pkgs.jdk8 p.pkgs.python3 p.pkgs.nodejs p.pkgs.git ];
            JAVA_HOME = "${p.pkgs.jdk8}";
            BC23_ENGINE_JAR = "${p.engine}/share/battlecode23/engine.jar";
            BC23_MAPS = "${p.engine}/share/battlecode23/maps";
            BC23_FIXTURE_SOURCES = "${p.engine}/share/battlecode23/fixtures/src";
            BCENV_ENGINE_JAR = "${p.engine}/share/battlecode23/engine.jar";
            BCENV_JAVA = "${p.pkgs.jdk8}/bin/java";
            BCENV_JAVAC = "${p.pkgs.jdk8}/bin/javac";
            BCENV_REPLAY_CLASSPATH = "${p.bcenv}/lib/bcenv/replay-classes:${p.engine}/share/battlecode23/engine.jar";
            BC23_FIXTURES = "${p.engine}/share/battlecode23/fixtures/classes";
            BC23_API_DOCS = "${p.engine}/share/doc/battlecode23/api";
          };
        });
      checks = forAllSystems (system:
        let
          p = packagesFor system;
          galaxy = p.pkgs.fetchzip {
            url = "https://github.com/battlecode/galaxy/archive/51762ec8b982132c250aa5a603978bc7147eae67.tar.gz";
            hash = "sha256-27/dTLzY7w2Pc92CtxbqrBLp0t5Eu125Mgv60xzoBhs=";
          };
        in import ./nix/checks.nix { inherit (p) pkgs engine bcenv; inherit galaxy; });
    };
}
