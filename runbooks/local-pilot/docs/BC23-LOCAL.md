# Local pilot tool reference

Run these commands from the assigned workspace; ../../bcenv is the host-visible
launcher. It presents this directory as /workspace inside the runtime, so normal
relative source/output paths work. The environment is already installed from
fixed Nix inputs. Do not install, update or fetch anything.

    ../../bcenv build --source competitor/src --package mybot --output work/build-001
    ../../bcenv build --source /opt/battlecode23/fixtures/src --package testplayerempty --output work/fixture-build
    ../../bcenv run --team-a work/build-001 --team-b work/fixture-build --map /opt/battlecode23/maps/DefaultMap.map23 --output work/match-001
    ../../bcenv run --team-a work/fixture-build --team-b work/build-001 --map /opt/battlecode23/maps/DefaultMap.map23 --output work/match-002
    ../../bcenv package --build work/build-001 --output work/package-001

Use new output names for subsequent builds/matches/packages. Input source must
contain only package-relative Java source files. Outputs must be outside input
artifacts. The compiler targets Java8 and invokes the official verifier. Build
outputs include artifact.json, compile.log and verify.log. Package outputs are
package.json and source.zip. The ZIP contains only the exact verified sources.

Each match produces result.json, engine.log, replay.bc23, replay-summary.json and
replay-extractor.log. Completed status requires successful execution and
core replay validation bound to map, slots, packages and engine version. A
failure or missing/invalid replay cannot be counted as a loss or win. The
runner snapshots binaries and checks their identities. No manual replay
extraction command is necessary; every successful match includes its summary.

The summary provides footer winner/rounds, event counts and up to 32 samples per
category. It does not reconstruct the entire world or every action. Resource
deltas are net team changes, not gross collection. Movement counts do not prove
navigation quality, and aesthetic action counts are not damage totals. Robot
stdout is disabled; completed execution does not establish exception-free bot
behavior. Engine logs identify final victory reason and fixture freezes.

Read reference/specs/ and reference/api-source/ or reference/api/. Engine/maps
are pinned to 2.0.3. testplayerempty returns immediately and freezes its robots;
testplayerclock yields once then returns. Both are disclosed inert official
fixtures, not active opponents. The unused websocket server is disabled.

Per-process timeout defaults to 120 seconds (flag --timeout, maximum 600), and
the engine heap cap is 512 MiB. Trial-level bounds are in trial.json. Tool commands
run in a minimal user/network-namespace filesystem with no host mounts or
inherited credentials. This is not a security boundary for the agent's general
host tools; respect the assigned-context restriction in the kickoff.

For raw records beyond the first summary samples, query an explicit inclusive
round range. Use an optional robot ID and a bounded cap; choose a new output:

    ../../bcenv replay query --replay work/match-001/replay.bc23 --round-start 100 --round-end 110 --limit 1000 --output work/query-001
    ../../bcenv replay query --replay work/match-001/replay.bc23 --round-start 100 --round-end 110 --robot-id 42 --limit 1000 --output work/query-002

query-result.json records execution and input identity; replay-query.json holds
raw records, source SHA256, matching counts, explicit truncation and coverage.
The replay and summary are unchanged. The cap is 1–10000 (default 1000), shared
across kinds and matches. Round 0 selects initial bodies. Later records expose
spawn/death IDs and types, movement coordinates, action codes/names/raw targets,
and net team deltas. Identity comes from earlier initial/spawn rows; unknown
identity remains null. Robot filtering keeps both teams' resource delta rows.

Records are ordered by match/event, then spawn, team delta, movement, action,
death vectors, preserving each vector's row order. This does not imply temporal
ordering between vectors within a round. Targets are not decoded. Team deltas
are not collection or bot-local balances; movement does not identify intent.
Nothing reconstructs world state, visibility, or bot knowledge. Inspect
truncated, selected_records_complete, source_stream_complete, and per-match
all_requested_positive_rounds_present separately. Missing rounds or empty
queries do not establish absence of activity. Only core arrays are validated;
this is not a full FlatBuffers verifier. Inputs are capped at 64 MiB compressed
and uncompressed; the query inherits the 512 MiB heap and process timeout.
