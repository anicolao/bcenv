# BC2023 replay evidence

`ReplaySummary` is a Java 8 CLI compiled against the pinned official 2.0.3
engine JAR and its generated `battlecode.schema` classes. It reads a gzip
`.bc23` GameWrapper and prints one JSON object to stdout. Exit 0 means a
structurally complete event stream for the fields examined; exit 2 means
invalid input or an incomplete stream. The runner must preserve that failure.

```
javac -source 8 -target 8 -cp "$ENGINE_JAR" -d "$CLASS_DIR" \
  seasons/2023/replay/ReplaySummary.java seasons/2023/replay/ReplaySummaryTest.java
java -Xmx512m -cp "$CLASS_DIR:$ENGINE_JAR" ReplaySummary replay.bc23
java -Xmx512m -cp "$CLASS_DIR:$ENGINE_JAR" ReplaySummaryTest
```

Use the repository's Nix-provided JDK and engine. No generated schema is copied
into this repository. JSON `format` is `bcenv.bc2023.replay-summary.v1`.

## Evidence and interpretation

- Team slots derive from official TeamMapping IDs (A=1, B=2), never vector
  position, name, or package. Names and packages remain separate fields.
- Game and match winners come from their respective footers. Match
  `total_rounds` is the footer value; `round_events` and `last_round_id` describe
  observed round records. Gaps are flagged, because the schema permits skipped
  rounds. Missing game footer yields `status: incomplete` and exit 2.
- Resource totals sum recorded team deltas. They are **net changes**, not gross
  collection, stockpiles, spending, efficiency, or carrier productivity. Missing
  team resource records produce no invented total for that team.
- Movement counts count replay movement records. Samples give recorded new
  coordinates. They do not identify intended movement versus current effects.
- Action counts count the schema's aesthetic events. Samples include official
  action names, robot IDs, rounds, and raw targets. Targets are intentionally
  not decoded into unverified damage, hits, mining amounts, or coordinates.
- Team attribution for sampled robot events uses initial/spawned body IDs;
  unknown attribution is JSON null. Spawn count excludes initial map bodies.
- Samples are the first 32 records per category per match. Counts and net sums
  cover all extracted records; `samples_complete` states whether samples cover
  all three categories. This is not a complete event export.

## Bounds and validation

Compressed and uncompressed input are each capped at 64 MiB. There are at most
100,000 event wrappers, 100 matches, 100,000 live body IDs per match, and
5,000,000 processed core records.
The header must declare the pinned spec version `2.0.3`.
Parallel extracted arrays, event ordering, match index vectors, team IDs,
action codes, increasing round IDs, and gzip integrity are checked. Invalid
input emits an error object without partially trusted metrics. Run with a
bounded heap and a runner timeout as additional process-level safeguards.

This is **not** a full FlatBuffers verifier. Non-extracted arrays (including
terrain, indicators, profiler, bytecodes, wells and islands) are not validated
or analyzed. `all_schema_fields_validated` is always false. `status: complete`
does not claim those fields are valid or that unavailable metrics exist.

Tests build tiny synthetic records with the official FlatBuffers builders;
they establish extraction mechanics, not engine gameplay validity. End-to-end
engine replays must also be smoke-tested by the runner.

Official provenance: battlecode/battlecode23 tag `2.0.3`, commit
`5d142a87` (full commit recorded in the package lock), `schema/battlecode.fbs`,
`engine/src/main/battlecode/server/GameMaker.java`, and
`engine/src/main/battlecode/util/TeamMapping.java`.

## Bounded query

`ReplayQuery` adds an independently selected raw-record view without changing
`ReplaySummary` or its schema. Invoke it through `bcenv replay query` as described
in [the tool reference](../../../docs/BC23-LOCAL.md#bounded-raw-replay-queries).
Nix builds both classes; local exports copy the complete compiled class tree.
The internal Java CLI is `ReplayQuery REPLAY START END ROBOT_OR_all LIMIT` and
prints `bcenv.bc2023.replay-query.v1`. Exit 2 means invalid or incomplete input.

Core validation uses the unchanged summary parser before collecting any query
rows, including records outside the range or after the retained-record cap.
The source hash covers the same bounded compressed byte snapshot that is parsed.
Record kinds are initial_body, spawn, team_resource_delta, movement, action and
death. Vector indexes are zero-based. The initial-body round 0 is an explicit
query convention, not an official Round event. Body metadata is joined by ID
from initial/spawn arrays and dropped after deaths; missing identity is null.
No death location or cause is inferred. Record cap and selection completeness
are separate from source stream and requested-round coverage.

ReplayQueryTest builds multi-match synthetic official-schema fixtures with late
rounds, spawns and deaths, signed raw targets, missing identities, gaps, invalid
tails and caps. It cross-checks selected records using the official generated
array accessors independently of the query's traversal. These fixtures validate
extraction mechanics, not gameplay. A separate CLI integration check exercises
the packaged command, provenance, failures and non-mutation.
