# Durable synthetic replay route and controller protocol v2

## Status and scope

This is an **offline fixture integration** of the durable ledger, immutable source
artifacts, replay page projector v2, six-tool controller, and offline Responses
codec. Shipped callback tests are inert synthetic fixtures. A separately approved
read-only proof may inject a fixed offline parser for existing public artifacts;
that external harness is not an inert callback or an implemented live execution
boundary. Its synthetic job/result association must remain labeled fabricated.
There is no engine execution, live provider, listener, authentication, game evidence validator,
process supervisor, or heldout launch. `launch_real` remains closed. A valid
fabricated callback can produce a synthetic complete result and replay pages.
Identity echoes and fingerprints do not prove source authenticity or execution.

The default controller protocol is still `v1`, with its exact existing registry
and unconditional replay withholding. Nothing converts a historical job,
a legacy ledger submission, or a legacy terminal result into a replayable job.
Public source architecture is distinct from protected deployed state. This code
is trusted service code under `services/`, outside the competitor runtime image.

## Small trusted-side API

- `ReplayPolicy(spec_version, producer_identity, projector_identity)` freezes an
  explicit `2.0.3` or `3.0.13` profile plus two private 64-hex implementation
  identifiers. These identifiers are assertions, not measured-code attestation.
- `ReplayStore(ledger, policy, limits=ReplayLimits())` owns a versioned additive
  SQLite extension in the **same database** as `DurableLedger`.
- `store.submit(principal, run_id, key, archive, package)` uses the existing bounded
  source ZIP intake. New job creation, normalized request/idempotency identity,
  and owner/run/policy/job-ID binding commit in one transaction, including source
  rejections. The public v1 `ledger.submit` has no binding option. There is no
  retroactive binding API. Policy and run participate in normalized identity.
- `store.result(principal, run_id, submission_id)` uses durable run visibility.
- `SyntheticReplayRoute(store, provider)` injects the synthetic query seam.
- `route.complete_with_replay(principal, run_id, JobReservation, evidence,
  tuple[ReplayFixture])` is the only route registration operation.
  `ReplayFixture(registration_key, source: bytes, own_team_id)` requires exact
  immutable bytes and bounded fixed metadata, never paths or URLs.
- `ReplayScrimmageAdapter(route, artifacts, principal, run_id)` composes the
  existing run-owned source-artifact snapshots with the controller. Recapture of
  identical normalized source in one run resolves to one durable job. Artifact
  handles remain process-local; accepted job/run bindings survive restart.

Principal, run, worker and provider identities are trusted caller context, **not
credentials or authentication proof**. The database and its filesystem remain
host-owned. A model cannot select a database, producer, profile, raw replay source,
source registration, opponent, map, command, or filesystem path through replay.

### Atomic completion and immutable source storage

Completion first checks the live worker reservation and accepted source. Outside
SQL, the injected provider projects a round-zero descriptor from each complete
asserted replay source. This is a point-in-time check followed by a callback race,
not a process lock or physical-resource admission guarantee. The authoritative
transaction rechecks owner/run/policy, worker, generation, lease, immutable accepted
source, normalized complete result, and source retention quotas. It writes the
terminal result, completion marker, and **all** replay BLOB registrations together.
An error rolls back payload writes. Invalid preparation never publishes a source.

The lower-level trusted `store.complete` accepts exact `PreparedReplay` assertions;
identical normalized terminal result/source batches are idempotent, and changed
batches conflict. `complete_with_replay` checks existing terminal state before invoking a provider.
An exact repeat compares owner/run, worker/generation, accepted source, normalized
result, every registration key/raw byte/team slot, and the persisted immutable
batch metadata; it returns the prior result without a provider call. This remains
valid after source expiry and does not extend replay visibility. An uncertain
commit must be inspected rather than automatically retried. Legacy `ledger.finish` remains unchanged: even a
run-bound job finished through that method cannot acquire a replay afterward.
Expired/recovered failures cannot become complete or acquire replays.

A source stores opaque replay ID, bounded raw BLOB, private digest/size, accepted
source identity, terminal result digest, generation, own team slot, bounded match
coverage descriptor, and expiry. Its immutable run binding contains the private
policy and opaque job ID. Every read checks source bytes/hash/size, accepted source
integrity, completion marker, result and generation. SQL triggers reject updates
and deletes of bindings, completions, sources and views. These are integrity
checks within a trusted database, not protection against a malicious database
administrator. No source digest, implementation hash, BLOB path or raw URL is
included in public output.

## Durable page lifecycle

A manifest returns a bounded list of opaque replay IDs with explicit profile,
own team slot, match count, maximum reported rounds and expiry. These counts are
provider assertions validated by the projector; they are not authenticated game
results. The client chooses an explicit inclusive round window of at most 256
rounds, optional robot ID and bounded record limit. The first query freezes an
immutable view with source, round/filter/record policy and expiry. Duplicate
windows reuse the same view; expired views never revive. Page requests use that
view plus an explicit matching-record offset.

Admission is a short `BEGIN IMMEDIATE` transaction. It checks all owner/run/
submission/source/view bindings, expiry, policy, retained view/attempt quotas and
logical active limits, then reserves a monotonically increasing generation and
lease. The provider/projector run outside SQL. The publication transaction
rechecks source/result/accepted-source integrity, ownership, policy, expiry and
attempt generation/lease. The route releases bytes **only after successful
publication**. A stale or expired callback cannot publish. Ledger recovery and
clock rollback also fence running replay attempts, including when invoked through
legacy ledger methods. The ledger's existing exact observed-clock high-water and
rollback refusal rules apply; recovery commits before `clock_regression` is raised.

Attempt states are `running`, `complete`, `failed`, `expired`. Each view/offset
retains its generation, cumulative attempt charge, state, and first successful
page fingerprint. There is **no projected page cache**. An explicit repeat query
runs the provider again and must reproduce the same canonical projected page;
a mismatch commits a failed attempt and returns `replay_conflict`. Window versus
page wrappers do not affect that fingerprint. A prior fingerprint is never erased
by failure or retry. Repeat consistency cannot validate the first result or prove
provider determinism. New explicit requests may retry a failed/expired page,
subject to quotas; neither jobs nor page callbacks are retried automatically.

An uncertain storage commit returns `storage_unavailable`, an **unknown outcome**.
The attempt may already be complete and charged. No failure write or retry is
issued after such uncertainty. Failed callbacks produce a closed reason and a
fenced failure update. Logical expiry/failure does not kill or interrupt a running
callback or release actual OS resources. Blocking/allocation before a callback
returns cannot be bounded by this Python seam.

## Provider/projector contract

`provider.query(ReplayProviderRequest) -> ReplayProviderReceipt` uses exact types:

- Request: immutable `source_bytes`, private `source_sha256`, explicit
  `expected_spec_version`, private `producer_identity`, and `ReplayPageQuery`.
- Receipt: bounded `query_json: bytes` and exact echoes of source digest, profile
  and producer identity. No exception text crosses the public boundary.

Each receipt must describe the **actual slice of the complete immutable source**,
not a slice of a previously truncated prefix. The existing
`project_replay_feedback_page` v2 validates source completeness, page extent,
whole-window counts, every supplied record, explicit expected query and profile.
Only its sanitized output is used. Continuation is the projector's
`coverage.next_record_offset`, based on actual released rows. Exhausted quotas
produce `resource_limit`; they never mean end-of-replay. Only a complete coverage
receipt with no remaining rows has a null continuation.

Sources and descriptors persist as bounded BLOBs to simplify immutable ownership
and restart safety without an external candidate-path lifecycle. This is logical
retention accounting, **not a hard disk, WAL, memory, CPU or process limit**.

## Controller protocol and strict schema

Explicit host options select v2:

- `tool_schemas(limits, protocol="v2")`
- `run_synthetic(..., protocol="v2")`
- `OfflineResponsesTransport(..., controller_protocol="v2")`

Defaults remain v1. The six names are unchanged. The transport freezes the selected
registry and rejects a mismatched registry before exchange. No `TurnRequest`
format changes. The frozen initial-context hash still covers exact system/task
bytes only; it does not purport to hash tool policy. Registry binding is separate.

The v2 `scrimmage_replay` function schema is one strict object with
`additionalProperties: false`, **every field required**, and no top-level `anyOf`:

| Field | Type |
| --- | --- |
| `submission_id` | opaque `s_` ID |
| `operation` | `manifest`, `window`, or `page` |
| `replay_id` | opaque `r_` ID or null |
| `view_id` | opaque `v_` ID or null |
| `manifest_offset`, `manifest_limit` | bounded integer or null |
| `round_start`, `round_end`, `robot_id` | bounded integer or null |
| `record_limit`, `record_offset` | bounded integer or null |

The dispatcher additionally enforces operation-specific fields:

- `manifest`: offset 0–8 and limit 1–8; all other selection fields null.
- `window`: replay ID, inclusive start/end, nullable robot ID, limit 1–64;
  manifest/view/offset fields null. Initial record offset is exactly zero.
- `page`: view ID and offset 0–5,000,000; all other selection fields null.

Unknown, extra, inactive non-null, malformed or boolean-as-integer arguments are
rejected before adapter invocation. Existing controller session-local submission
visibility is an additional gate: after restart, a controller must learn the
submission by an authorized idempotent submit before it can query it. The adapter
and store separately enforce durable owner/run/submission visibility.

A second closed projection at the controller boundary revalidates typed immutable
reply bytes, bounded JSON structure, exact keys, fixed enums, numeric/boolean
fields, safe records, request binding and progress. Unknown fields or raw strings
cannot be smuggled through a trusted-looking typed reply. Schema validation still
cannot prove provider authenticity. V1 dispatch rejects arbitrary replay bytes
and continues to emit `replay_withheld`, including with the opt-in adapter.

## Default quotas and public errors

| Resource | Default |
| --- | ---: |
| One replay BLOB | 8 MiB |
| Sources per job / run / database | 8 / 16 / 64 |
| Retained replay bytes per run / database | 16 MiB / 64 MiB |
| Views per job / run / database | 16 / 64 / 256 |
| Cumulative page attempts per run / database | 512 / 2048 |
| Logically active pages per run / database | 1 / 4 |
| Page lease / source TTL / view TTL | 30 / 3600 / 1800 seconds |
| Source record limit / inclusive round span | 64 / 256 |
| Complete replay response payload | 12 KiB |
| Projected page inside that payload | payload cap minus 1 KiB |
| Complete controller tool-result envelope | 16 KiB maximum in v2 |
| Controller aggregate output | existing default 128 KiB |

Existing ledger source/job/read/submit quotas and controller turn/call/time budgets
also apply. Guessed replay reads consume read quota. All retained source bytes are
charged logically per registration, even if identical across owners. Failed and
expired source/view/attempt records remain charged; there is no automatic eviction
or quota reopening on restart/rollback. Settings are trusted configuration and
must match the persisted extension configuration. They are illustrative fixture
budgets, not an accepted autonomous-trial or spending budget.

Unavailable replies contain only schema, submission ID, status and a closed reason:
`not_found`, `not_ready`, `replay_not_available`, `resource_limit`,
`invalid_evidence`, `replay_conflict`, `stale_reservation`, `rate_limit`,
`clock_regression`, `configuration_mismatch`, `storage_unavailable`, or
`storage_integrity`. Provider details, SQL text, owner names, logs, opponent
identities and schedule data are not exposed. Uniform cross-owner/run handle
denials use `not_found`. Unbound legacy jobs also return `not_found` in this v2
scoped store, even to another run of the same principal; v1 withholding is
unchanged. Malformed controller calls use existing controller errors.

## Evidence and remaining deployment gates

Tests exercise atomic submission/registration, rollback without orphan publication,
restart, idempotency, source/result/policy drift, cross-owner/run/ID denial,
concurrent admission, stale generations, expiry during callbacks, clock rollback,
quota exhaustion, conflicting repeat pages, malformed receipts, uncertain commits,
strict schemas, all-six-tool synthetic pagination, payload/envelope/aggregate caps,
legacy withholding, and real-launch closure. They invoke no real game or provider.

Deployment still needs authentication and principal binding; independently tested
filesystem/network/tool isolation; bounded process supervision and teardown;
validated immutable real-engine artifacts bound to outcomes; trusted producer code
measurement and controlled execution; real RPC time/memory/output limits; physical
disk/WAL lifecycle and cleanup; supported live inference transport and authorized
trial budgets; and review of opponent/replay release policy. This fixture does not
claim those gaps are closed by SQLite, hashes, trusted callbacks or prompt text.
