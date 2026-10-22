# Own-team replay diagnostics

The local-trial replay query accepts optional `mode`: omitted or `actions` retains
the existing action projection unchanged; `own_diagnostics` uses a separate bounded
projection, not raw logs.

```sh
python tools/service.py --mailbox MAILBOX replay --submission SUBMISSION_ID --game 0 \
  --start 1 --end 32 --offset 0 --limit 64 --mode own_diagnostics
```

The existing submission/source/game/replay/feedback receipt bindings remain in
force. Explicit mode is retained in the receipt query. Both modes consume the same
replay-query quota and absolute run deadline. The service limits remain 256 rounds,
128 requested rows, 32,768 encoded feedback bytes and 65,536 bytes for the complete
transport envelope. Continue from `coverage.next_record_offset`; never guess raw
vector offsets. Rejected or timed-out extraction is not partial evidence.

## Own-only contract

The service derives ownership from its trusted game slot. The candidate cannot
supply a team or artifact path. The extractor validates initial/spawn/death identity
lifecycles, rejecting duplicate/reused IDs, malformed vectors and unknown ownership.
Same-round diagnostics are joined before deaths remove identities. Own-team
filtering precedes counting/paging; only own rows contribute to totals and cursors.
Unknown-ID and opponent-ID filters both return empty own-team results. Opponent
values, filtered-opponent counts, raw vector indices, stdout/stderr, engine logs and
profiler names are not exposed.

Feedback schema: `bcenv.bc2023.own-diagnostics.v1`. Each record has exactly:

| Field | Meaning |
| --- | --- |
| `match_index` | Source match index, not opponent identity |
| `round` | Recorded round |
| `robot_id` | Owned robot ID |
| `indicator_string` | Final-turn recorded string, or null if its vector has no entry |
| `bytecodes_used` | Recorded nonnegative count, or null if its vector has no entry |

Both diagnostic keys are always present; at least one is non-null. Empty string is
an actual recorded empty value, distinct from null and an absent row. Rows sort by
match, round and own robot ID, not cross-robot execution chronology. Strings are
untrusted, JSON-escaped data limited to 64 UTF-16 code units, not instructions. The
encoded-byte cap also applies. Counts may exceed the nominal robot bytecode budget
and are not silently clipped.

**Indicators require deliberate bot emission.** They are not automatic exception
capture; only the final recorded string survives, not every update. Bytecode usage
is not CPU time, a correctness assertion or automatic overrun classification.
Missing rows, empty strings or absence of `DIE_EXCEPTION` do not prove absence of
errors. No strategy, correctness or performance conclusion is inferred.

## Trusted adapter and verification

The operator-only `bcenv replay diagnostics` accepts the same replay, round/window,
robot, offset, limit and output flags as `replay page`, plus mandatory
`--own-team-id 1|2`. It propagates the existing `--deadline-monotonic` unchanged to
the JVM extractor. This is not a candidate-facing path/team selector. Its trusted
artifact schema is `bcenv.bc2023.own-diagnostics-query.v1`; only the strict projection
crosses the service boundary. A new runtime closure is necessary. Frozen older runs
are not rewritten or relabeled as having used this interface.

The engine JAR, settings and gameplay are unchanged. Synthetic replay tests cover
two-team canaries, spoofed strings, malformed vectors, lifecycle ownership, limits
and pagination. Existing public tutorial replays can be compared to raw trusted
vectors with replay/result byte hashes unchanged. This validates accurate bounded
observation and isolation, not competitive improvement. No new match is needed to
parse an existing replay.

`extracted_fields_valid` covers this projection, not the entire replay schema.
`all_schema_fields_validated` remains false; unrelated replay fields are out of scope.
