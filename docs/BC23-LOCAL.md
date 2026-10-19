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
source archive is pinned independently to official tag 3.0.13's commit. The build
checks the released JAR's embedded version. This is reproducible artifact
acquisition, not a demonstrated bit-for-bit rebuild of the official JAR from
source. The upstream source does not declare one repository-wide engine license;
component notices are preserved without claiming the whole engine uses a
component's license. Review redistribution terms before external distribution.

## Bounded raw replay queries

    bcenv replay query --replay work/match-001/replay.bc23 --round-start 100 --round-end 110 --limit 1000 --output work/query-001
    bcenv replay query --replay work/match-001/replay.bc23 --round-start 100 --round-end 110 --robot-id 42 --limit 1000 --output work/query-002

The round range is inclusive and applies independently to every match in the
replay. Round 0 selects initial map bodies, distinct from later spawn records.
Robot filtering selects that ID's spawn, movement, action, and death records;
team resource rows remain for both teams and are explicitly team-scoped.
Identity/type fields come from initial/spawn records, including earlier rounds
outside the query. Unknown identities remain null. Match IDs prevent accidental
cross-match attribution. IDs are not interpreted as persistent across matches.

A new output directory contains `query-result.json` (process status and input
identity), `replay-query.json` (records, coverage and semantics), and
`replay-query.log`. The source replay and existing summary are never rewritten.
The query hashes the exact compressed snapshot it parsed; the adapter also
checks the input hash before and after execution. Missing/invalid/incomplete
replays fail explicitly. This adds no new match or strategy behavior.

`--limit` defaults to 1000 and must be 1–10000. It caps records across all kinds
and matches, not each category separately. `matching_record_count` and
`matching_counts_by_kind` cover every matching core record in the source even
when the retained prefix is capped. `truncated` is explicit;
`selected_records_complete` means the selected records present in the source
were all returned. Separately inspect `source_stream_complete` and per-match
`all_requested_positive_rounds_present`: a missing round is not proof that
nothing happened, and a complete empty selection can lie outside the replay.

Each record includes the original event and vector row indexes. Presentation
order is match/event order, then spawn, team delta, movement, action, death
vectors. It does not establish temporal ordering between those vectors within
a round. Movement coordinates are new recorded positions, not intended paths.
Actions retain the official code, name and raw target without interpretation.
Team resource deltas are net team changes, not collection, spending, balances,
or bot-local resources. The output never reconstructs visibility or bot knowledge.

Both compressed and uncompressed input are capped at 64 MiB; core validation
covers at most 100000 events, 100 matches and 5000000 core records. The Java
query process uses a 512 MiB heap and the usual `--timeout` bound. As with the
summary, this is not a full FlatBuffers verifier; unextracted schema fields are
not validated. See [replay semantics](../seasons/2023/replay/README.md).

## Engine migration and historical evidence

The active runtime is the official public 3.0.13 release at source commit
`9b890f83672c98b81cc153f618bd56e1ab6134b5`. This includes final-season gameplay
rules and the last public cloud-sensing fix. The final-tournament-labeled 3.0.14
public engine source changes only the spec-version string; its release workflow
injects private maps and publishes artifacts privately. Its engine and Javadoc
URLs returned HTTP 403 in this audit. We therefore do not claim the public
3.0.13 distribution is the exact private tournament distribution.

The old package remains available as `battlecode23-diagnostic-2_0_3`, and its
matching adapter/parser as `bcenv-diagnostic-2_0_3`. The original pilot and lecture
trial records and runbooks continue to describe their actual 2.0.3 execution.
Those exporters require the diagnostic engine. Rebuild every competitor from
source under 3.0.13 for new comparisons. Old verified binaries are rejected by
engine SHA-256 checks; do not edit their metadata to reuse them. Give new
measurements new output paths and retain old outputs unchanged.

Replay parsing is exact-version-bound to the JAR used to compile the parser.
Use the matching diagnostic parser for an old 2.0.3 replay. A schema-compatible
version is not automatically accepted, and a successful parse does not make
results under different rules comparable. See [migration evidence](ENGINE-MIGRATION-2023.md).
