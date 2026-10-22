# Opt-in continuity capacity profile v1

`profile: "continuity-v1"` is a trusted operator configuration, not a request
field or a new trial authorization. Omitting it preserves the legacy capacity
profile. This profile changes capacity and accounting only: it does not change
engine properties, strategies, maps, opponents, projections or per-request
source/output limits. Use the same reviewed public development schedules and
runtime command through the shared **host-Python** launcher.

## Resolved bounds

| Bound | continuity-v1 |
| --- | ---: |
| Service-start absolute deadline | 21,600 seconds (6 hours) |
| Training submissions | 24 |
| Fixed games per submission | 8 |
| Training game attempts | 192 |
| Locked finalists / validation games | 2 / 16 |
| Total game attempts | 208 |
| Prospective source-bound notes | 72 |
| Replay requests | 450 |
| Total reads (including replay requests and status/result polling) | 2,000 |
| Mailbox attempts | 2,200 |
| Accepted pending submissions / executor workers | 2 / 1 |
| Service-owned logical-storage high-water threshold | 1,073,741,824 bytes |

Unchanged per-request limits include 1,500,000 request bytes, 65,536 response
bytes, 4,096 UTF-8 bytes per note, and replay pages bounded to 256 rounds,
128 records and 32,768 feedback bytes. Existing source-archive checks remain
unchanged; capacity is not permission to enlarge individual requests.

The same two-finalist atomic lock, immutable source/binary identity checks,
paired eight-cell validation schedule and final-choice rules apply. Game maxima
are not promises: failure, deadline or a depleted allowance can end the run
without validation or selection. A version is not retried or substituted.

The typed profile identifier and capacities resolve before readiness. Unknown
profiles, null/bool/nonnumeric capacities, contradictory explicit capacities and
unknown continuity configuration fields are rejected. Existing defaults are not
silently enlarged. When adapting an older configuration, remove its old explicit
capacity overrides or replace them with the exact values above; retain its
runtime command, schedules, child timeouts and all other reviewed settings.
Both entrypoints share resolution. For this opt-in profile, launch parity binds
the full resolved profile and every effective worker/schedule/time-limit setting; semantically identical
implicit and explicit defaults compare equally. Raw config hashes remain in
launch records for separate exact-byte comparison. Legacy launch parity retains
its existing quota-insensitive compatibility behavior; it does not provide this
new full-capacity binding.

## Time, loss and finite requests

The clock starts once, at service initialization, and never resets. UTC and
monotonic start/deadline metadata and remaining time appear in validation status.
The candidate receives only the time remaining after readiness. The inherited
absolute deadline clips every adapter child; cleanup grace is not extra game
execution time. Existing per-child build/package/replay-query limits remain
at most 120 seconds; the separately configured match/initial-summary limits
remain at most 300 seconds. No concurrency increase is introduced.

Fresh roots are mandatory. There is no queue recovery, checkpoint restart,
worker retry or session continuation after loss. Worker failure halts further
work. The trusted coordinator must stop the service when the candidate session
is lost; this host service does not detect native-agent liveness by itself.
Already running bounded operations are not instantaneously canceled by a
storage check; their existing child/deadline supervision remains in force.

Continuity uses the bounded mailbox, not HTTP. Malformed names, invalid JSON,
oversized requests, duplicate request IDs and invalid operations consume mailbox
attempts; queue overflow halts rather than accepting an unbounded backlog.
The unchanged pending-file bound is 32. The stop record distinguishes total
`processed` mailbox attempts from `dispatch_attempts` that reached parsed service
dispatch; malformed JSON/names and duplicates need not reach dispatch. Replay requests are a subset of reads,
not 450 additional reads. At all stated maxima, 2,000 reads + 72 notes + 24
submissions + lock + select use 2,098 mailbox attempts, leaving 102 for other or
invalid attempts. Polling can exhaust the read allowance before the clock or
game budget. Quota exhaustion does not grant a new clock, service or allowance.

Continuity validation status includes an actual `usage` snapshot under the
admission lock, including the current status request's read and dispatch debit
and, when transported through the mailbox, its mailbox attempt. `read_debits`
and `replay_debits` include increments for rejected over-limit requests (they
are not clamped to successful responses). `note_debits` counts accepted notes;
`training_version_debits` counts accepted training submissions, including later
build/game failures, but excludes the two immutable validation copies.
`mailbox_attempts` includes invalid transport attempts; `dispatch_attempts` counts
only parsed requests reaching dispatch. Snapshots are observations, not future
reservations; worker progress can change immediately afterward. The final stop
record retains these counters when writing succeeds. Legacy responses do not
add this usage schema, and exhausted read allowance still rejects status reads.

## Storage semantics: detection/admission, not a hard disk quota

The guard sums regular-file **logical lengths**, not allocated disk blocks,
recursively within the service root and the entire attached mailbox. This covers
service-owned source/build/package snapshots, engine/replay outputs, logs,
temporary child outputs **inside those roots**, mailbox requests/responses and
control/temporary files. It excludes the candidate workspace outside the
mailbox, shared runtime/store files, public map/opponent roots and temporary
files outside the accounted roots. Hard links count per directory entry;
overlapping registered roots are deduplicated. Symlinks, special files and scan
or evidence-write failures fail closed. Concurrent temporary-file cleanup can
make an entry disappear during a scan; this also halts the run, even below the
threshold. There is no retry that silently ignores such a race. The workspace remains cooperative,
not filesystem-confined.

Checks occur at initialization, request and adapter boundaries, game admission,
validation-copy boundaries and immediately before lock/selection authorization.
Each checkpoint records its scope, sampled bytes, high-water observation, limit,
monotonic time and stage locally. Exceedance halts further work without retry.
There is **no OS quota, hard filesystem cap, reserved space or numeric overshoot
guarantee**: concurrent/in-flight writes can overshoot between or during checks.
A successful scan is neither an atomic snapshot nor a disk reservation.

Checkpoint logs and bounded control receipts themselves add bytes after a scan.
If a valid selection's own receipt crosses the threshold, the already-authorized
immutable choice remains selected while subsequent work halts; it is not
retroactively relabeled as a rejected selection. Evidence on scan/write/full-disk
failure is best effort, not a guarantee that fsync will succeed.

Synthetic tests exercise capacity boundaries, malformed requests, source/lock
integrity, deadline inheritance and storage crossings. They do not establish
native-agent six-hour reliability, bot competence or a successful future run.

## Atomic mailbox publication

Only canonical `<32 lowercase hex digits>.json` files are committed requests.
The unchanged client first writes `<same ID>.tmp` and atomically renames it.
Canonical staging files are not debited, dispatched or removed by the consumer;
they must be regular files of at most 1,500,000 bytes (including zero while being
written). Bad staging types, symlinks or observed oversize fail closed. Staging
still counts toward the 32-entry queue and service-owned storage scope, and does
not extend the deadline. Malformed noncanonical names remain charged attempts.

A staging name disappearing during inspection is an ordinary commit race. A
committed request disappearing after its attempt is debited retains that debit
and its seen ID: if no payload was read, an invalid-request response is retained;
if dispatch occurred, its response is retained. Missing cleanup targets are
harmless; neither case causes a retry or second dispatch. Other cleanup errors
are not suppressed. The separate storage scanner's documented fail-closed
concurrent-disappearance behavior is unchanged.
