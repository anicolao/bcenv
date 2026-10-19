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
          diagnosticEngine = pkgs.callPackage ./nix/battlecode23.nix { engineVersion = "2.0.3"; };
          diagnosticBcenv = pkgs.callPackage ./nix/bcenv.nix { engine = diagnosticEngine; };
        in { inherit pkgs engine bcenv diagnosticEngine diagnosticBcenv; };
    in {
      packages = forAllSystems (system:
        let p = packagesFor system; in {
          battlecode23 = p.engine;
          battlecode23-diagnostic-2_0_3 = p.diagnosticEngine;
          bcenv-diagnostic-2_0_3 = p.diagnosticBcenv;
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
          diagnosticChecks = import ./nix/checks.nix {
            inherit (p) pkgs; inherit galaxy;
            engine = p.diagnosticEngine; bcenv = p.diagnosticBcenv;
          };
          activeChecks = import ./nix/checks.nix { inherit (p) pkgs engine bcenv; inherit galaxy; };
        in activeChecks // {
          diagnostic-engine-api = diagnosticChecks.engine-api;
          diagnostic-replay-parser = diagnosticChecks.replay-parser;
          diagnostic-replay-query = diagnosticChecks.replay-query;
          diagnostic-fixture-game = diagnosticChecks.fixture-game;
          replay-version-isolation = p.pkgs.runCommand "bcenv-replay-version-isolation" {
            nativeBuildInputs = [ p.pkgs.jdk8 p.pkgs.python3 ];
          } ''
            set +e
            java -cp ${p.bcenv}/lib/bcenv/replay-classes:${p.engine}/share/battlecode23/engine.jar \
              ReplaySummary ${diagnosticChecks.fixture-game}/match-one/replay.bc23 > old-on-final.json
            old_status=$?
            java -cp ${p.diagnosticBcenv}/lib/bcenv/replay-classes:${p.diagnosticEngine}/share/battlecode23/engine.jar \
              ReplaySummary ${activeChecks.fixture-game}/match-one/replay.bc23 > final-on-old.json
            final_status=$?
            set -e
            test "$old_status" = 2 && test "$final_status" = 2
            python -c 'import json; reports=[json.load(open(p)) for p in ("old-on-final.json", "final-on-old.json")]; assert all(r["status"] == "invalid" and "unsupported replay spec version" in r["error"] for r in reports)'
            mkdir "$out"
            cp old-on-final.json final-on-old.json "$out/"
          '';
        });
    };
}
