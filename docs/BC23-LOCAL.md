# BC23 local builder tools

This is trusted-fixture/self-owned-code tooling, not an untrusted submission
sandbox. Run from the pinned flake on x86_64-linux. Commands perform no network
requests; Nix fetches the pinned closure before execution.

The package exports engine, API documentation, rules, maps and official inert
fixtures. `BC23_MAPS` selects packaged maps; use an explicit `.map23` file.
`BC23_FIXTURE_SOURCES` contains the disclosed testplayerempty/testplayerclock
fixtures, whose only purpose is engine/instrumenter readiness. They are not a
builder-authored candidate or undisclosed strategy.

    nix develop
    bcenv build --source "$BC23_FIXTURE_SOURCES" --package testplayerempty --output work/fixture-build
    bcenv run --team-a work/fixture-build --team-b work/fixture-build --map "$BC23_MAPS/MAP.map23" --output work/match-001
    bcenv package --build work/fixture-build --output work/package-001

Choose an actual map filename from the packaged map directory. Source input is
Java-only, with package-relative paths; outputs must be outside it. The build
snapshots sources and calls Java 8's compiler and official BC23 Verifier. No
Gradle wrapper, mutable Maven resolution, Scala build, live update or GUI is
needed for this bounded Java slice. Scala support is deferred.

Each output directory is created exclusively. Results contain hashes and logs,
including failed compiler/verifier attempts. Artifact mutation and engine drift
are rejected. Source ZIP entries have normalized order/timestamps/mode. Both
sides must reference verified build artifacts; explicitly swap them for a pair.
One map per invocation avoids ambiguity about alternate-order.

The unused websocket server is explicitly disabled; headless replay collection
does not require a live client or listening port. The engine heap is capped at 512 MiB. Each compiler/verifier/game process group
has a wall timeout (default 120 seconds, maximum 600). These controls do not
constitute CPU quotas, disk quotas or an OS sandbox. No credentials belong in a
match worker. Only trusted fixtures should run until deployment isolation and
aggregate budgets are enforced. Observer-owned immutable records, lineage
retention and clean-agent isolation remain separate release gates.

## Dependency provenance

The official release fat JAR and Javadoc JAR are fixed-hash Nix inputs; the
source archive is pinned independently to official tag 2.0.3's commit. The build
checks the released JAR's embedded version. This is reproducible artifact
acquisition, not a demonstrated bit-for-bit rebuild of the official JAR from
source. The upstream source does not declare one repository-wide engine license;
component notices are preserved without claiming the whole engine uses a
component's license. Review redistribution terms before external distribution.
