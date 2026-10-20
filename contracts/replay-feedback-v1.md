# BC2023 bounded replay feedback projection v1

**Status: synthetic-tested, trusted-side projection only. No service integration,
listener, authenticated route, heldout access, deployment or trial is enabled.**
`services/replay_feedback.py` is deliberately outside `runtime/` and must stay
out of competitor runtime packages. `ScrimmageFacade.replay` still withholds
replays, and the real-heldout gate remains unconditionally closed.

The intended future use is bounded raw gameplay evidence for an authorized
agent to diagnose its own game. It preserves both sides' naturally revealed
gameplay, including adverse events. It removes identity and operational
metadata; it does not promise that recognizable gameplay cannot fingerprint a
bot or map. Authorization to reveal an entire replay is distinct from what a
robot could see during play. This projector does not implement fog-of-war.

## Input, trust and failure

The sole entry point is:

```python
project_replay_feedback(
    query_json: bytes,
    *,
    submission_id: str,
    job_id: str,
    own_team_id: int,
    expected_spec_version: str,
    limits: FeedbackLimits | None = None,
) -> bytes
```

Input is UTF-8 JSON with the shape defined by
`seasons/2023/replay/ReplayQuery.java`, with
`format: bcenv.bc2023.replay-query.v1`. This integrated branch includes the
reviewed public `3.0.13` producer from PR8 and retains the separately version-bound
`2.0.3` diagnostic package. The projector does not infer or upgrade a producer
version; host and replay versions must still match exactly.
The host must explicitly select
`expected_spec_version: 3.0.13` for official public engine evidence or
`expected_spec_version: 2.0.3` for the separately retained diagnostic engine.
These are the only allowed frozen public version strings. Input `spec_version`
must exactly match the host's expectation, and the output records that same
version. There is no default, source-controlled auto-detection, arbitrary version
acceptance or silent pooling between versions. Released schema classes are
byte-identical for these two versions; this does not mean their rules or gameplay
are interchangeable. This accepts neither a raw `.bc23` replay nor a
`ReplaySummary`, engine log, adapter artifact, filename or URL. It reads no files,
starts no subprocesses and makes no network requests. There is no raw replay
download fallback.

The host supplies the owner's opaque `s_<32 lowercase hex>` submission handle,
an opaque `j_<32 lowercase hex>` job handle, and `own_team_id` 1 or 2. These
handles are not source hashes, opponent identities, schedule positions or access
tokens. Values in source metadata cannot override them. The future host must
generate opaque job handles independently of hidden state. Handle shape alone
does not establish that they are opaque, belong to this user or correspond to the
evidence.

Before calling, the trusted host must independently establish:

- successful pinned parser execution on complete, authorized, immutable evidence;
- source snapshot identity and parser/tool integrity, privately;
- the authenticated owner, ownership of these handles and authorization to see
  this game's replay evidence;
- the own-team mapping, selected query and quotas allowed by that authorization;
- the expected engine/spec version from the job's frozen configuration, without
  deriving it from the untrusted evidence or mixing diagnostic/public runs;
- protected service/worker/filesystem/network boundaries and exclusion of this
  trusted code from competitor tools and files.

**This function is not a replay parser, authenticity verifier, ownership check,
authorization mechanism or security boundary.** A forged JSON object can satisfy
shape checks; caller assertions are not evidence. No checks here prove full
FlatBuffers correctness, simulator legality, a secure deployment, strength or
trial readiness. Raw engine summaries and adapter artifacts remain private even
if their projection succeeds. Passing this function's tests cannot open any gate.

The complete-stream flags must be exactly `status: complete`,
`source_stream_complete: true`, `extracted_fields_valid: true`, and
`all_schema_fields_validated: false`. Every match must have a footer. An incomplete
or invalid source fails with no partial rows or metrics. An honest query prefix
truncated by its record limit is supported; it is not an incomplete source stream.

Failures raise `FeedbackRejected` with only one of these fixed codes:
`invalid_evidence`, `invalid_context`, `input_limit`, `query_limit`, `output_limit`.
No parser error text or attacker-supplied value is included. Invalid trusted limit
construction raises `ValueError('invalid_feedback_limits')`. A future transport
must map these to bounded errors without exposing exception context, traces or
source input. There is no public transport or error-response shape in this version.

## Output allowlists

Success is canonical UTF-8 JSON **bytes**, sorted keys and compact separators,
with `schema: bcenv.bc2023.replay-feedback.v1` and `status: available`.
The complete output top-level key set is:

- `schema`, `status`, `spec_version`, `submission_id`, `job_id`, `own_team_id`
- `query`, `matches`, `records`, `coverage`, `limits`

Objects are rebuilt from these allowlists; no source dictionary is forwarded.
Unknown keys are discarded at every nested output level, including on known
record kinds. An unknown record **kind** rejects the whole input rather than
silently dropping evidence. Wrongly typed or invalid known fields also reject it,
even if the row would be beyond the output cap. Fields belonging to a different
record kind are not copied.

Never copied: team or opponent names, Java packages, map names, paths, source or
binary hashes, replay hashes, raw logs, free-text parser semantics/errors,
indicators, profiler information, source provenance dictionaries, schedules,
transport metadata, unknown extensions or their nested values. No free-form
source string is released. Retained strings must equal exact, pinned enums and
are emitted from fixed constants.

`query` retains inclusive `round_start`/`round_end`, nullable `robot_id`,
`source_record_limit`, fixed `round_bounds_inclusive: true`, fixed
`team_resource_rows_included_with_robot_filter: true`, and fixed
`match_scope: all_matches`. Match indexes are replay-local; they do not identify
the backend's hidden schedule. All matches in the supplied query must be
authorized by the host. There is no selection of hidden opponents or maps here.

Each `matches` entry contains exactly the existing numeric/boolean/null coverage
fields, excluding `map`:

- `match_index`, `observed_round_events`, `last_observed_round`,
  `reported_total_rounds`, `match_footer_present`
- `source_round_ids_contiguous`,
  `source_round_records_cover_reported_rounds`,
  `source_resource_records_cover_observed_rounds`
- `selected_round_events`, `first_selected_round`, `last_selected_round`,
  `all_requested_positive_rounds_present`

Each record retains `kind`, `match_index`, `event_index`, `round`, `row_index`,
`team_id` and a derived `team_slot` equal to `own`, `opponent` or null. Record-kind
allowlists are:

| Kind | Additional retained fields |
| --- | --- |
| `initial_body`, `spawn` | `robot_id`, `body_type_code`, `body_type`, `identity_observed_round`, `x`, `y` |
| `movement` | `robot_id`, `body_type_code`, `body_type`, `identity_observed_round`, `x`, `y` |
| `action` | `robot_id`, `body_type_code`, `body_type`, `identity_observed_round`, `action_code`, `action`, `raw_target` |
| `death` | `robot_id`, `body_type_code`, `body_type`, `identity_observed_round` |
| `team_resource_delta` | `adamantium_delta`, `mana_delta`, `elixir_delta` |

Unknown robot identity has all four source identity fields null (`team_id`, body
code/name, identity-observed round), and consequently `team_slot: null`. Births
require known identity and an identity-observed round equal to the birth round.
Known action/body enum names must agree with their numeric codes.

The body codes 0–5 are HEADQUARTERS, CARRIER, LAUNCHER, AMPLIFIER, DESTABILIZER,
BOOSTER. Action codes 0–15 are LAUNCH_ATTACK, THROW_ATTACK, SPAWN_UNIT,
PICK_UP_RESOURCE, PLACE_RESOURCE, DESTABILIZE, DESTABILIZE_DAMAGE, BOOST,
BUILD_ANCHOR, PICK_UP_ANCHOR, PLACE_ANCHOR, CHANGE_HEALTH, CHANGE_ADAMANTIUM,
CHANGE_MANA, CHANGE_ELIXIR, DIE_EXCEPTION. These are schema orders, not ordinals
from some other Java enum.

## Bounds and consistency

Hard ceilings are not caller-expandable. A trusted `FeedbackLimits` may tighten
the configurable limits but cannot raise them, use booleans as integers or
disable them:

| Budget | Hard ceiling |
| --- | ---: |
| Encoded query input | 4,194,304 bytes |
| Supplied source records | 10,000 |
| Returned projection records | 1,024 |
| Inclusive requested round span | 256 |
| Final encoded output, including coverage and handles | 262,144 bytes |
| Matches in one query | 8 |
| JSON nesting depth | 32 |

Input byte/depth limits are checked before JSON decoding. The caller still needs
streaming body limits before allocating `query_json`, process memory/CPU limits,
request concurrency limits, authorization and aggregate quotas. This function
is bounded per call, not a deployment resource controller.

JSON must be well-formed UTF-8 with no duplicate object keys at any depth,
floating-point numbers, NaN or infinities. Integers in the input, including
discarded extensions, must fit signed 32-bit schema values. Robot IDs and round
numbers are 0–2,147,483,647; event indexes are 1–99,999; row indexes are
0–4,999,999 (resource row indexes additionally 0–1). Coordinates, signed raw
targets and resource deltas retain the full signed 32-bit range. This validates
schema width, **not** map geometry, robot lifetime or gameplay legality.
Matching counts are bounded by the parser's 5,000,000 core-record ceiling.

Additional checks cover:

- source returned count equals the actual list length and
  `min(matching_record_count, source_record_limit)`;
- matching counts sum correctly, use only known kinds in the projection, cover
  all returned rows and equal returned counts for an untruncated source;
- round/record bounds, query robot filter and source truncation flags;
- sequential match indexes, monotonic match/event/group/row ordering,
  consistent event-to-round association and no duplicate resource team row;
- footer/contiguous-round/selected-round coverage arithmetic, nullable bounds,
  and resource counts consistent with asserted resource coverage per match;
- visible distinct positive rounds do not exceed each match's selected-round
  count; resource-complete matches have both teams' rows in each observable
  complete round group, and full counts in fully supplied matches, even when
  other matches have incomplete resource coverage. A source cap inside the
  final round's resource group may legitimately retain only its first row.

These checks catch contradictory supplied evidence but do not rederive the
source replay or prove it was parsed honestly. Honest round gaps, missing
resource coverage and requests outside observed rounds remain visible, with
their original limitations, instead of being filled in.

## Coverage and truncation

`coverage` has exactly:

- `source_stream_complete: true`, `extracted_fields_valid: true`,
  `all_schema_fields_validated: false`
- `matching_record_count`, `matching_counts_by_kind`
- `source_returned_record_count`, `returned_record_count`
- `source_truncated`, `projection_truncated`, `truncated`,
  `selected_records_complete`, `truncation_reasons`

`matching_record_count` and its per-kind counts refer to the entire source query's
matching records, even beyond its row cap. The projector cannot reconstruct
missing rows. `source_returned_record_count` counts rows supplied to it;
`returned_record_count` counts rows actually released. `source_truncated` is the
validated upstream prefix flag. `projection_truncated` means additional supplied
rows were withheld by a local bound. `truncated` means returned rows are fewer
than all matching rows. `selected_records_complete` means every matching record
present in the source was released, **not** that requested rounds existed or
all schema fields were parsed. Inspect per-match round and resource coverage.

Truncation reasons are a fixed list in this order, with applicable entries only:
`source_record_limit`, `projection_record_limit`, `projection_output_limit`.
Caps retain the exact presentation-order prefix, regardless of side, action,
resource sign or outcome. The first prefix is not a representative sample. It
may end mid-round or mid-group and omit an adverse later event. Absence in a
truncated result is never evidence an event did not occur. There is no winner-
or behavior-based filtering and no suppression of exception/death events.

All input rows are validated first, including rows beyond output caps. The byte
cap then finds the longest whole-row prefix fitting the **final** encoded
response, including correct coverage and truncation labels. No row is partially
serialized. If only coverage fits, zero rows with explicit truncation is allowed;
if even that cannot fit, the function fails with `output_limit`. It never
silently drops coverage. `limits` reports the effective `max_records`,
`max_round_span` and `max_output_encoded_bytes`.

## Interpretation and testing

These are raw official replay array records, not reconstructed world state,
per-robot knowledge or causal analysis:

- Initial bodies use the query convention of synthetic round 0.
- Within a round, group presentation is spawn, team resource delta, movement,
  action, death. That is not a cross-group temporal ordering.
- Identity is the parser's join from initial/spawn records within a match.
  Death removes that join; unknown identity remains unknown.
- Movement coordinates are recorded new locations. Intent, path quality and
  current effects are not inferred.
- Actions are schema aesthetic events. Raw targets remain signed and undecoded;
  even health/resource change actions are not converted into inferred hits,
  damage, kill credit or gross collection.
- Team resource deltas are net changes, not balances, gross mining, spending,
  efficiency or robot-local inventory. Both teams' rows remain under a robot
  filter. Birth/death records retain their evidence without inventing location,
  cause, score or strategy quality.

Tests in `tests/test_replay_feedback.py` independently construct JSON matching
the checked-in Java query fixture and pinned official schema. They exercise all
six kinds, both sides, negative deltas, signed targets, initial/spawn/death
identity, unknown identities, multiple matches, gaps and missing-round coverage,
upstream/local/byte truncation, enum/numeric bounds, metadata stripping at every
level, nested/string injection, duplicate JSON keys, corrupt tails beyond caps,
per-match coverage contradictions, explicit host-selected versions, cross-version
rejection, hard limits and closed existing facade/heldout gates. They are synthetic
projection tests, not a replay-parser integration or a real engine run. No
actual heldout replay, bot, opponent schedule or trial is used.

Run with the existing Python or repository-pinned Python:

```sh
python3 -m unittest discover -s tests -p test_replay_feedback.py -v
```

Schema source: [official battlecode.fbs at the pinned 2.0.3 revision](https://github.com/battlecode/battlecode23/blob/5d142a87579af22c2c04ce046a3cbd448dc1869e/schema/battlecode.fbs).
Repository sources: `ReplayQuery.java`, `ReplaySummary.java` and
`ReplayQueryTest.java` under `seasons/2023/replay/`, plus
`contracts/artifacts-v1.md` and `contracts/scrimmage-v1.md`. Future version support,
parser integration and any facade release require separate review.
