{ lib, stdenvNoCC, fetchurl, fetchzip, jdk8, unzip, makeWrapper }:
let
  version = "2.0.3";
  revision = "5d142a87579af22c2c04ce046a3cbd448dc1869e";
  releaseBase = "https://releases.battlecode.org/maven/org/battlecode/battlecode23/${version}";
  engineJar = fetchurl {
    url = "${releaseBase}/battlecode23-${version}.jar";
    hash = "sha256-XoYqoQ6FyutnrmnhaS90UYk+gB4NgVlwMsFEK7ud604=";
  };
  apiDocs = fetchurl {
    url = "${releaseBase}/battlecode23-${version}-javadoc.jar";
    hash = "sha256-t6WyomjUwpGcsyTGm8PAPgusLsEwaVl5aFaZqWH9dP0=";
  };
  upstream = fetchzip {
    url = "https://github.com/battlecode/battlecode23/archive/${revision}.tar.gz";
    hash = "sha256-nrgQJtR6qZWW0hmiGF0y0PJlRQ19HPgQakVzUh6jeOI=";
  };
in stdenvNoCC.mkDerivation {
  pname = "battlecode23-engine";
  inherit version;
  dontUnpack = true;
  nativeBuildInputs = [ jdk8 unzip makeWrapper ];
  installPhase = ''
    runHook preInstall
    root="$out/share/battlecode23"
    mkdir -p "$root/maps" "$root/fixtures/src" "$root/fixtures/classes" \
      "$out/share/doc/battlecode23/api" "$out/share/doc/battlecode23/licenses"
    cp ${engineJar} "$root/engine.jar"
    test "$(unzip -p "$root/engine.jar" battlecode_version | tr -d '\r\n')" = '${version}'
    unzip -q ${apiDocs} -d "$out/share/doc/battlecode23/api"
    # Maps are the exact resources bundled in the pinned released engine.
    unzip -q "$root/engine.jar" 'battlecode/world/resources/*.map23' -d extracted
    cp extracted/battlecode/world/resources/*.map23 "$root/maps/"
    for fixture in testplayerempty testplayerclock; do
      mkdir -p "$root/fixtures/src/$fixture"
      cp ${upstream}/engine/src/test/battlecode/instrumenter/sample/$fixture/RobotPlayer.java \
        "$root/fixtures/src/$fixture/RobotPlayer.java"
    done
    javac -source 8 -target 8 -cp "$root/engine.jar" -d "$root/fixtures/classes" \
      "$root/fixtures/src/testplayerempty/RobotPlayer.java" \
      "$root/fixtures/src/testplayerclock/RobotPlayer.java"
    # Preserve upstream license texts with their original component scope.
    cp -r ${upstream}/specs "$out/share/doc/battlecode23/specs"
    cp -r ${upstream}/engine/src/main/battlecode/common "$out/share/doc/battlecode23/api-source"
    cp ${upstream}/schema/LICENSE "$out/share/doc/battlecode23/licenses/schema-LICENSE"
    cp ${upstream}/client/LICENSE "$out/share/doc/battlecode23/licenses/client-LICENSE"
    unzip -q "$root/engine.jar" 'META-INF/LICENSE*' 'META-INF/NOTICE*' 'license/*' \
      -d "$out/share/doc/battlecode23/licenses/bundled-jar"
    printf '%s\n' '${version}' > "$root/version"
    printf '%s\n' '${revision}' > "$root/source-revision"
    makeWrapper ${jdk8}/bin/java "$out/bin/battlecode23-engine" \
      --add-flags '-cp' --add-flags "$root/engine.jar" \
      --add-flags 'battlecode.server.Main'
    runHook postInstall
  '';
  passthru = { inherit engineJar apiDocs upstream; };
  meta = {
    description = "Pinned official Battlecode 2023 engine, API documentation, maps and inert upstream test fixtures";
    homepage = "https://github.com/battlecode/battlecode23";
    platforms = lib.platforms.linux;
    # No repository-wide license is declared upstream; retain component notices
    # without relabeling the entire engine under a component's license.
    sourceProvenance = [ lib.sourceTypes.binaryBytecode ];
  };
}
