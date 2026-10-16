{ stdenvNoCC, lib, python3, jdk8, makeWrapper, engine }:
stdenvNoCC.mkDerivation {
  pname = "bcenv-2023";
  version = "0.1.0";
  src = lib.cleanSourceWith {
    src = ../.;
    filter = path: type:
      let
        relative = lib.removePrefix (toString ../. + "/") (toString path);
      in baseNameOf path != "__pycache__" && (type == "directory" || (lib.hasPrefix "runtime/" relative || lib.hasPrefix "seasons/2023/" relative
          || lib.hasPrefix "skills/" relative || lib.hasPrefix "prompts/" relative
          || lib.hasPrefix "contracts/" relative)
        && !(lib.hasSuffix ".pyc" relative));
  };
  nativeBuildInputs = [ jdk8 makeWrapper ];
  installPhase = ''
    runHook preInstall
    mkdir -p "$out/lib/bcenv" "$out/lib/bcenv/replay-classes"
    cp -r runtime seasons skills prompts contracts "$out/lib/bcenv/"
    javac -source 8 -target 8 -cp ${engine}/share/battlecode23/engine.jar \
      -d "$out/lib/bcenv/replay-classes" seasons/2023/replay/ReplaySummary.java seasons/2023/replay/ReplayQuery.java
    makeWrapper ${python3}/bin/python3 "$out/bin/bcenv" \
      --add-flags "$out/lib/bcenv/seasons/2023/adapter.py" \
      --set BCENV_ENGINE_JAR ${engine}/share/battlecode23/engine.jar \
      --set BCENV_JAVA ${jdk8}/bin/java \
      --set BCENV_JAVAC ${jdk8}/bin/javac \
      --set BCENV_REPLAY_CLASSPATH "$out/lib/bcenv/replay-classes:${engine}/share/battlecode23/engine.jar" \
      --set BC23_MAPS ${engine}/share/battlecode23/maps \
      --set BC23_FIXTURES ${engine}/share/battlecode23/fixtures/classes \
      --set BC23_FIXTURE_SOURCES ${engine}/share/battlecode23/fixtures/src \
      --set BC23_API_DOCS ${engine}/share/doc/battlecode23/api
    runHook postInstall
  '';
  meta.mainProgram = "bcenv";
}
