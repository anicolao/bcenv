{ pkgs, engine, bcenv, galaxy }:
let
  src = pkgs.lib.cleanSource ../.;
  jar = "${engine}/share/battlecode23/engine.jar";
in {
  engine = engine;
  engine-api = pkgs.runCommand "bcenv-engine-api-${engine.version}" { nativeBuildInputs = [ pkgs.jdk8 ]; } ''
    mkdir classes
    cp ${./EngineApiTest.java} EngineApiTest.java
    javac -source 8 -target 8 -cp ${jar} -d classes EngineApiTest.java
    java -cp classes:${jar} EngineApiTest '${engine.version}'
    touch "$out"
  '';
  prompt-tooling = pkgs.runCommand "bcenv-prompt-tooling" {
    nativeBuildInputs = [ pkgs.nodejs pkgs.git ];
  } ''
    export HOME="$TMPDIR/home"
    mkdir -p "$HOME"
    cp -r ${src} source
    chmod -R u+w source
    cp -r ${pkgs.callPackage ./npm-cache.nix {}} cache
    chmod -R u+w cache
    cd source
    git init -q
    # npm tarball scripts use /usr/bin/env, absent in Nix sandboxes. Patch
    # interpreter paths before explicitly executing the prepare hook.
    npm ci --offline --cache ../cache --no-audit --no-fund --ignore-scripts
    patchShebangs node_modules
    # Husky generates new scripts in each test repository as well.
    substituteInPlace node_modules/husky/index.js \
      --replace-fail '/usr/bin/env sh' '${pkgs.runtimeShell}'
    npm run prepare
    npm test
    mkdir -p "$out"
    cp -r node_modules .husky "$out/"
  '';
  python-unit = pkgs.runCommand "bcenv-python-unit" { nativeBuildInputs = [ pkgs.python3 ]; } ''
    cp -r ${src} source
    chmod -R u+w source
    cd source
    python -m unittest discover -s tests -v
    touch "$out"
  '';
  galaxy-contract = pkgs.runCommand "bcenv-galaxy-contract" { nativeBuildInputs = [ pkgs.python3 ]; } ''
    export GALAXY_SOURCE=${galaxy}
    python ${src}/seasons/2023/replica/test_contracts.py -v
    touch "$out"
  '';
  replay-parser = pkgs.runCommand "bcenv-replay-parser" { nativeBuildInputs = [ pkgs.jdk8 ]; } ''
    mkdir classes
    javac -source 8 -target 8 -cp ${jar} -d classes ${src}/seasons/2023/replay/*.java
    java -cp classes:${jar} ReplaySummaryTest
    java -Xmx512m -cp classes:${jar} ReplayQueryTest
    touch "$out"
  '';
  replay-query = pkgs.runCommand "bcenv-replay-query" { nativeBuildInputs = [ pkgs.jdk8 bcenv pkgs.python3 ]; } ''
    mkdir classes
    javac -source 8 -target 8 -cp ${jar} -d classes ${src}/seasons/2023/replay/*.java
    java -cp classes:${jar} ReplayQueryTest --write-fixture synthetic.bc23
    python ${./verify-replay-query.py}
    mkdir -p "$out"
    cp -r query-integration-evidence.json synthetic.bc23 capped complete late empty broken truncated "$out/"
  '';
  fixture-game = pkgs.runCommand "bcenv-fixture-game" { nativeBuildInputs = [ bcenv pkgs.python3 ]; } ''
    export HOME="$TMPDIR"
    fixtures=${engine}/share/battlecode23/fixtures/src
    map=${engine}/share/battlecode23/maps/DefaultMap.map23
    bcenv build --source "$fixtures" --package testplayerempty --output build-empty --timeout 60
    bcenv build --source "$fixtures" --package testplayerclock --output build-clock --timeout 60
    bcenv run --team-a build-empty --team-b build-clock --map "$map" --output match-one --timeout 60 || { cat match-one/result.json match-one/engine.log match-one/replay-summary.json; exit 1; }
    bcenv run --team-a build-empty --team-b build-clock --map "$map" --output match-repeat --timeout 60
    bcenv run --team-a build-clock --team-b build-empty --map "$map" --output match-swapped --timeout 60
    bcenv package --build build-empty --output submission --timeout 60
    mkdir -p invalid-source/broken
    printf '%s\n' 'package broken; public class RobotPlayer { invalid syntax }' > invalid-source/broken/RobotPlayer.java
    if bcenv build --source invalid-source --package broken --output invalid-build --timeout 60; then
      echo 'Malformed Java unexpectedly compiled' >&2
      exit 1
    fi
    python -c 'import json; assert json.load(open("invalid-build/artifact.json"))["status"] == "failed"'
    python ${./verify-fixture-game.py}
    sha256sum match-one/replay.bc23 match-one/replay-summary.json > replay-before.sha256
    bcenv replay query --replay match-one/replay.bc23 --round-start 33 --round-end 35 --limit 100 --output fixture-query
    python -c 'import json; q=json.load(open("fixture-query/replay-query.json")); assert q["source_stream_complete"] and q["selected_records_complete"]; assert q["matches"][0]["selected_round_events"] == 3; assert len([r for r in q["records"] if r["kind"] == "team_resource_delta"]) == 6'
    sha256sum -c replay-before.sha256
    mkdir -p "$out"
    cp repeat-evidence.json "$out/"
    cp -r build-empty build-clock match-one match-repeat match-swapped submission invalid-build fixture-query "$out/"
  '';
}
