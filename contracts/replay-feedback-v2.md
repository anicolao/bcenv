# BC2023 bounded replay pages v2

Status: trusted-side producer/projector implementation. This contract does not
implement a service route, authentication, ownership, immutable replay storage,
worker/result binding or a security boundary. No heldout gate is opened. Any
controller/ledger integration needs separate review. Engineering limits below
are implementation ceilings, not approved trial or experiment budgets.

V1 remains available unchanged: its five-argument Java invocation, `replay query`
CLI, JSON format and `project_replay_feedback` entry point still describe a
prefix. V2 adds actual source-side offsets, not pagination over a stored prefix.

## Producer and CLI

The explicit Java invocation is:

```sh
java -cp "$CLASSES:$ENGINE_JAR" ReplayQuery --page \
  replay.bc23 ROUND_START ROUND_END ROBOT_ID_OR_all LIMIT RECORD_OFFSET
```

The host-side adapter command is:

```sh
bcenv replay page --replay replay.bc23 --round-start 35 --round-end 40 \
  --record-offset 2 --limit 64 --output page-output
```

`--record-offset` and both round bounds are required. `--robot-id` is optional;
when absent all robots are selected. The page command defaults to 64 rows.
The adapter's private `query-result.json`, `replay-query.json` and
`replay-query.log` retain existing artifact conventions. None is itself a public
feedback response. Private filenames, logs, source hash and engine hash must not
be forwarded to a competitor.

Offsets are zero-based counts **after** the inclusive round and robot filters,
in stable match/event/schema-group/vector-row presentation order. Team resource
rows remain selected even with a robot filter. Match scope is all matches in the
authorized source, separately indexed. A caller cannot assume the rows of a
single round fit in a page. The group order is initial body (round zero), spawn,
team resource delta, movement, action, death; it is not within-round chronology.

The Java selection overload with five parameters
`Selection(start, end, robot, limit, offset)` selects v2. The original
four-parameter overload selects v1. The complete source is parsed and core-array
validated before materializing output, including data before/after the page and
outside the window. Identity joins process the entire stream, so a late page
retains identities observed earlier. Counting continues after the page fills.
Skipped rows are not materialized or accumulated as an unbounded prefix.

The existing parser limits remain: 64 MiB compressed and uncompressed input,
100,000 events, 100 parser matches and 5,000,000 core records. V2 additionally
bounds inclusive round span to 256 and record offset to 0–5,000,000; source row
limit is 1–10,000. Negative, overflowing or excessively large parameters fail.
After full validation/counting, an offset greater than the matching count fails.
An offset equal to the matching count is a valid empty terminal page. An empty
window at offset zero is valid and does not assert the requested rounds exist.
Parameter validation occurs before reading input; valid-parameter out-of-range
queries still validate the complete source before the actual-EOF check.

### Source JSON shape

V2 has `format: bcenv.bc2023.replay-query.v2`. All source v1 fields retain their
meaning, with these explicit page additions:

- `query.record_offset`: exact requested matching-row offset.
- `page_records_complete: true`: the full requested bounded slice was emitted.
- `page_end_offset`: offset plus `returned_record_count`.
- `next_record_offset`: that end offset if smaller than the whole-window matching
  count; otherwise null.
- `limits.max_round_span: 256`, `limits.max_record_offset: 5000000`.

`returned_record_count = min(record_limit, matching_record_count - record_offset)`.
`matching_record_count` and `matching_counts_by_kind` cover the entire selected
window. Every match's existing round/resource coverage also covers the whole
window, independent of the page. The page may omit rows both before and after
its slice. `truncated` is true whenever the page has fewer rows than the whole
window; `selected_records_complete` is its inverse. In particular, a final page
at nonzero offset is not a complete window, even though `next_record_offset` is
null and `page_records_complete` is true.

`source_stream_complete`, `status`, `extracted_fields_valid` and
`all_schema_fields_validated` retain their separate meanings. Missing final
footers yield incomplete status and nonzero CLI exit; malformed evidence fails.
No complete-source claim is inferred from page completeness. Not all schema
fields are validated: terrain, indicators, bytecodes, wells and islands remain
outside this raw core-array extraction.

## Projector API

```python
from services.replay_feedback_v2 import (
    ReplayPageQuery, FeedbackLimits, FeedbackRejected,
    project_replay_feedback_page,
)

expected = ReplayPageQuery(
    round_start=35, round_end=40, robot_id=None,
    record_offset=2, record_limit=64,
)
encoded = project_replay_feedback_page(
    query_json,
    submission_id="s_" + "a" * 32,
    job_id="j_" + "b" * 32,
    own_team_id=1,
    expected_spec_version="3.0.13",
    expected_query=expected,
    limits=FeedbackLimits(max_records=64, max_output_encoded_bytes=12288),
)
```

The example IDs are shape examples, not real authorization. The required frozen
`ReplayPageQuery` has exactly these five fields and validates hard bounds.
The required trusted expected query must match the source selection exactly.
The host's `expected_spec_version` must be exactly `3.0.13` or `2.0.3`, and input
must match it. Version is never auto-detected from evidence. These schemas are
compatible, but their rules/gameplay are not pooled. The integrated producer
uses the pinned runtime's `GameConstants.SPEC_VERSION`; it does not silently
parse the other runtime's replay.

The function reads no paths, starts no processes and makes no network calls.
It accepts bytes only, not a replay binary, URL, summary or raw engine artifact.
It returns canonical sorted-key compact UTF-8 JSON bytes, never source objects.
The host must establish authorized complete immutable evidence, correct own
slot, owner/run/job/result binding, parser success/integrity and quotas before
calling. A valid-looking JSON document does not establish authenticity.

Keep `services/` out of competitor runtime packages. The outer route must bind
opaque replay/view IDs privately; the projector does not issue those IDs or
verify ownership. Views must freeze the complete replay source, explicit query,
limits, expected rules version and producer/projector version. A private source
hash is a consistency aid, not authorization or a public identifier.

## Public output and allowlists

Top-level keys are exactly:

`schema`, `status`, `spec_version`, `submission_id`, `job_id`, `own_team_id`,
`query`, `matches`, `records`, `coverage`, `limits`.

`schema` is `bcenv.bc2023.replay-feedback.v2`; status is `available`.
`query` has exactly `round_start`, `round_end`, `robot_id`, `record_offset`,
`source_record_limit`, `round_bounds_inclusive: true`,
`team_resource_rows_included_with_robot_filter: true`, and
`match_scope: all_matches`.

`matches`, the six `records` allowlists, enum/numeric bounds and derived
`team_slot` are identical to [v1](replay-feedback-v1.md). They retain raw signed
resource changes, action code/name/target, robot IDs, identity observation,
coordinates and birth/death evidence for both sides. No winner/side/sign-based
filtering or causal interpretation occurs. Unknown robot identity remains null.

Every object is rebuilt. Opponent/team/package/map names, paths, replay/source/
binary hashes, logs, schedules, arbitrary strings and unknown extensions are
never forwarded. Unknown fields are dropped; an unknown record kind or invalid
known field rejects the entire page, including rows withheld by local output
caps. Known enum strings are checked against the fixed codes and emitted from
constants. Recognizable gameplay may still reveal bot or map characteristics;
this is not a fog-of-war or anonymity guarantee.

### Coverage fields

`coverage` has exactly:

| Field | Meaning |
| --- | --- |
| `source_stream_complete` | Always true on success; incomplete evidence rejects. |
| `extracted_fields_valid` | Always true on success. |
| `all_schema_fields_validated` | Always false; scope is extracted core arrays. |
| `matching_record_count` | Whole-window matching rows, including unseen rows. |
| `matching_counts_by_kind` | Whole-window totals for the six fixed kinds. |
| `source_returned_record_count` | Actual rows supplied in this source page. |
| `returned_record_count` | Actual whole rows released in this response. |
| `source_page_records_complete` | Always true; exact requested source slice required. |
| `source_page_end_offset` | Input offset plus supplied row count. |
| `source_next_record_offset` | Producer continuation before local truncation, or null. |
| `output_page_end_offset` | Input offset plus actually released row count. |
| `next_record_offset` | Output end if more matching rows remain, otherwise null. |
| `has_more` | Whether the output end is smaller than the matching count. |
| `supplied_page_records_complete` | Whether all supplied rows were released. |
| `selected_records_complete` | Whether this single output contains the entire selected window. |
| `truncated` | Whether this output has fewer rows than the entire window. |
| `truncation_reasons` | Fixed ordered reasons below. |

Continuation **must** use `next_record_offset`, not `source_next_record_offset`.
For example, a producer page at offset 100 with 64 rows might release only 12
rows after the byte cap. The next query is offset 112, not 164. No supplied rows
are silently skipped. The host must query that slice from the same immutable
complete source; the projector cannot reconstruct missing rows from a prefix.

Fixed reason order: `source_record_offset` when rows precede this page,
`source_record_limit` when rows follow the supplied source page,
`projection_record_limit` when the local row limit withheld supplied rows, and
`projection_output_limit` when the encoded byte limit withheld additional rows.
A final late page can have only `source_record_offset`; it is terminal but not a
complete-window response. Reasons make omission explicit; they are not evidence
that unseen events did not happen or a representative sampling claim.

## Validation, bounds and failure

The implementation reuses v1's strict UTF-8/JSON decoding, duplicate-key,
integer/enum, depth and hard-limit checks. Floats, NaN/infinities, booleans in
numeric fields and integers outside signed 32-bit width reject, even in ignored
metadata. `FeedbackLimits` is the unchanged v1 class; callers may only tighten:
4,194,304 input bytes, 1,024 released rows, 256-round span, 262,144 output bytes
and 8 projected matches. At most 10,000 supplied rows and depth 32 are accepted.
Offset and whole-window count are at most 5,000,000. The host still needs input
stream limits before allocating bytes, parser memory/time/concurrency limits,
retention quotas and aggregate request/output limits.

Checks include exact host query and source page arithmetic; complete source and
match footers; whole-window count consistency; sorted match/event/group/vector
order; coherent event/round associations; exact enum/identity shape; query robot
filter; no duplicate resource team; visible rounds within stated coverage;
global resource lower/upper bounds; and per-match/per-round resource coverage.
A page may start inside a resource group. Only a first row at a nonzero offset
may legitimately have resource vector index 1 with its index 0 outside the page.
Fully observable groups/rounds/matches must agree with asserted completeness.
Genuine omitted boundary rows remain unknown, not silently filled in or declared
absent. Honest round gaps and missing resource coverage remain visible.

All supplied rows are validated before output truncation. The longest whole-row
prefix that fits the final encoded response is retained, including all coverage,
limits and host handles. The byte budget cannot elide coverage. If more rows
remain, a response must release at least one row; otherwise it raises
`FeedbackRejected('output_limit')` rather than issuing a zero-progress loop.
Genuinely empty or exact-EOF pages may release zero rows with null continuation.
The outer controller must independently measure its entire envelope and
cumulative output budget; this function cannot account for that wrapper.

Rejections use only v1's closed codes: `invalid_evidence`, `invalid_context`,
`input_limit`, `query_limit`, `output_limit`. Invalid query construction raises
`ValueError('invalid_replay_page_query')`; invalid limits retain their v1 fixed
code. A transport must not expose exception context, parser logs, source bytes
or traceback. Missing/corrupt/evicted evidence is not EOF, and quotas cannot
trigger a raw replay download fallback.

## Verification

`ReplayQueryTest.java` independently builds official-schema fixtures and tests
all small-fixture offsets and page boundaries, multi-match coverage, a 12,053-row
window with late negative health changes/exception/death beyond 10,000 rows,
identity joins before the offset, complete-stream tail validation, out-of-range
validation ordering and v1 behavior. `--write-long-fixture PATH` exports that
synthetic gzip fixture without running a game.

`tests/test_replay_feedback_v2.py` tests exact concatenation through source,
local-row and byte caps, late adverse evidence, page/resource/match boundaries,
explicit expected query/version, whole-window coverage, empty/EOF states,
no-progress rejection, resource contradictions and adversarial metadata/rows.
`tests/test_replay_query.py` tests the separate CLI dispatch and receipt checks.
Existing v1 tests remain required. None of these tests runs a game or a provider.

```sh
python3 -B -m unittest discover -s tests -v
javac -source 8 -target 8 -cp "$ENGINE_JAR" -d "$CLASSES" seasons/2023/replay/*.java
java -Xmx512m -cp "$CLASSES:$ENGINE_JAR" ReplayQueryTest
```

Use the repository-pinned tooling. A separate read-only query against an already
persisted authorized public replay may validate the parser/projector connection;
its original artifacts must be preserved and provenance kept private. Synthetic
or public-replay checks establish only the scoped behavior exercised, not an
approved deployment, authenticated boundary, heldout authorization or strength.
