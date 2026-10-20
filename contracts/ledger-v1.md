# Durable synthetic job ledger v1

`services/ledger.py` adds a Python-standard-library SQLite implementation of
source retention, request idempotency, ownership checks, reservations, quotas and
terminal state persistence. This component is **synthetic only**. Its tests run
small synthetic Python worker processes against temporary databases. It has no
listener, authentication, credentials, engine adapter, compiler, scheduler, model
transport or heldout execution. Public source code belongs outside the competitor
`runtime/` package; deployed databases must belong to a separately protected
trusted service.

This is a new optional component alongside the in-memory
[scrimmage scaffold](scrimmage-v1.md), not a change to that scaffold's durability
claims or an implementation/replacement of historical Galaxy. The integration
gaps in `docs/REPLICA-2023.md` remain. No accepted competition trial budget,
strength claim or tested production isolation follows from these defaults.

## Trust and ownership

The host constructs `DurableLedger(path, limits=..., intake=..., clock=...)` with
a local database path and trusted, fixed configuration. Candidate requests cannot
select database paths, clocks, workers, budgets, commands, opponents or runtime
configuration. Principal and worker strings are **caller-supplied trusted test
context, not authentication or authorization proof**. A real frontend must derive
principals from independently authenticated sessions. It must prevent candidates
from calling trusted methods or reading/modifying database files, Python objects
or the service process. None of those boundaries is enforced by this library.

Public submission IDs are independently generated `s_<32 lowercase hex>` opaque
identifiers. A valid principal's malformed, unknown and another principal's IDs
all produce the same `LedgerError('not_found')` for lookup, replay, reservation
and completion, subject to the caller's own quotas. IDs, content hashes,
idempotency keys, worker names and generations are not bearer credentials.
There is no listing/enumeration API. This uniform response is not a constant-time
or traffic-analysis guarantee. Shared global quota exhaustion remains observable.

## Small API

Candidate-facing methods, once a future transport authenticates the principal:

- `submit(principal, idempotency_key, archive, package)` returns a bounded
  `bcenv.scrimmage.v1` accepted or rejected result. It reuses strict source-only
  ZIP intake; original archive metadata is discarded. The key is 1–128 ASCII
  letters/digits/underscores/hyphens and is scoped to its principal.
- `result(principal, submission_id)` returns a fresh allowlisted public result.
- `replay(principal, submission_id)` performs the same ownership/rate checks and
  always returns `unavailable: replay_withheld`. It does not expose raw or
  projected replay bytes, storage paths or URLs.

Trusted-only methods:

- `reserve_job(principal, submission_id, worker)` reserves one explicitly selected
  queued job. It returns a frozen `JobReservation` containing the opaque ID,
  non-secret integer generation, worker name, deadline and immutable source.
  There is no queue selection/scheduling loop or worker registration.
- `check_reservation(principal, submission_id, worker, generation)` rechecks a
  live running claim and its immutable source in one transaction, without public
  read-rate charges. It returns the matching frozen reservation or a closed
  error. This is a trusted-only point-in-time check, not a held lock or physical
  resource admission guarantee; expiry/terminal state may change after return.
- `finish(principal, submission_id, worker, generation, evidence)` records a
  terminal result only for the matching live reservation. Evidence is normalized
  through the existing result allowlist; the adapter's `validated_complete`
  assertion is **not evidence validation**. This ledger does not validate games.
- `recover_expired()` records terminal failures for expired running claims and
  returns their count. Normal operations also run expiry recovery.

Source bytes are exposed only by the trusted reservation result. There are no
mutation, deletion, eviction, retry, quota reset or lease renewal APIs.

## Identity, persistence and transactions

Accepted source retains the facade's canonical manifest identity: declared
package and sorted source paths, lengths and exact byte hashes. Frozen source
bytes are stored as SQLite BLOBs with the manifest and SHA-256 content address.
SQL triggers reject ordinary update/delete of source rows; hash, byte-length,
count, total-size and manifest checks run when source is reserved, reused or a
live job finishes. An administrator owning the database can bypass those checks;
this is consistency validation, not tamper-proof storage or access control.

Accepted idempotency binds the full normalized request: source identity includes
package, paths and bytes. ZIP compression, entry order and timestamps do not
create a different request. Malformed intake cannot be normalized, so rejected
request identity binds package, bounded original archive hash and rejection
code. An exact owner/key/request retry returns the existing current result;
a different request using that owner/key returns `idempotency_conflict`.
Only source ZIP/package are request inputs in v1. Future options must become part
of the request identity before use. Repeating a request still consumes rate
budget but does not reserve another job or another retained-source allowance.

Each call opens a fresh connection and uses `BEGIN IMMEDIATE`. Identity/schema
validation during construction also uses a serialized transaction, including
concurrent first opens. Source insertion, job creation, reservation fencing and
quota checks commit atomically. WAL mode and `synchronous=FULL` are used; there is
a five-second SQLite busy timeout. Schema version, application ID and the entire
intake/ledger configuration persist. Incompatible databases/configurations fail
closed; no migrations or silent configuration changes are implemented.

Durability relies on SQLite's supported local-filesystem locking/fsync behavior
and the host's storage integrity. Tests establish process restart and abrupt
process-exit behavior, not power-loss resilience on arbitrary storage, backups,
replication or recovery from database tampering. Database/WAL/SHM files and all
operational tooling require trusted ownership and host-level capacity limits.
The component bounds retained logical content and event counts; it does not set
a hard disk-byte ceiling or protect against unbounded WAL retention caused by
external long-lived database readers. Do not give candidates direct SQL access.

## Exact state model

Internal state is `queued`, `running`, `rejected`, `infrastructure_failure` or
`complete`. Public `queued` and `running` both appear as `accepted`; no worker,
lease, generation, content hash, source, private schedule or owner is published.

1. Valid bounded intake creates `queued`; invalid bounded intake creates an
   immediately terminal `rejected` record without a source BLOB.
2. Reserving a queued job atomically sets `running`, worker, lease deadline and
   the next database-wide generation. The integer increases for every successful
   reservation, survives restart and is a fencing value, never a secret.
3. The matching live reservation may become exactly one terminal `complete`,
   `rejected` or `infrastructure_failure` state. Invalid/incomplete evidence
   becomes `infrastructure_failure: invalid_evidence`, never a win or loss.
4. At/after its deadline, a running job becomes terminal
   `infrastructure_failure: worker_unavailable`. There is no automatic retry or
   requeue. Queued jobs survive restart unchanged; running jobs remain reserved
   until observed expiry or clock regression.
5. Matching exact terminal-report retries are idempotent. Different terminal
   reports return `terminal_conflict`; mismatched worker/generation returns
   `stale_reservation`. Expired/recovered jobs cannot later become scores.

Database fencing **does not stop a live worker process** or undo external side
effects. A tested synthetic child remains alive after recovery and its late
score is refused. Real execution requires independently enforced termination,
resource isolation and generation-aware artifact publication at every external
write. Releasing logical running capacity after expiry is not proof that CPU,
processes, files or other physical resources have been released.

## Clock and recovery policy

The clock is trusted host context, normally wall-clock time. A durable high-water
value stores the original finite observation, including fractional seconds.
On any observed regression, all running jobs are committed as terminal
`worker_unavailable` failures and the operation raises `clock_regression`.
The high-water value remains unchanged. Work is refused until time catches up;
rate windows cannot reopen from a rollback. Recovery commits before that error,
and restart/another-connection tests verify it. Merely clamping a backwards clock
would extend leases, so the ledger does not do that.

Lease deadlines and rate events use integer seconds; a lease can therefore end
less than one second early. Expiry occurs on the next call/recovery invocation,
not via a timer thread. Frozen clocks, unseen rollback/forward jumps or a clock
changed without a call cannot provide elapsed-time guarantees. Real deployment
needs a trustworthy clock and independent process deadlines. A malformed clock
returns `invalid_clock` without producing a score. Restoring an old database
backup is not supported fencing recovery: it could roll back generations and
quotas, and requires a separately designed operational recovery protocol.

## Quotas and bounded errors

Default logical limits (fixed trusted configuration, not client overrides):

| Resource | Per principal | Global |
| --- | ---: | ---: |
| Retained records, including terminal/rejected | 64 | 256 |
| Pending jobs, queued plus running | 4 | 16 |
| Live running reservations | 2 | 4 |
| Logical retained source bytes | 16 MiB | 64 MiB |
| Submit attempts per rolling 3,600 seconds | 20 | 200 |
| Result/replay reads per rolling 60 seconds | 120 | 1,200 |

Reservation lease is 30 seconds. Public results allow at most 128 games and
4,096 serialized bytes. Existing ZIP intake defaults apply: 1 MiB compressed,
128 source files, 512 KiB per source file, 2 MiB total source, expansion ratio 100,
240-character paths and 16 package levels.

Every new accepted job charges its full logical source length to both owner and
global retention, including identical source bytes submitted by another owner.
Physical content deduplication is an internal storage optimization and gives no
quota discount or public hash. Consequently global quota responses cannot reveal
whether another owner already stored a guessed source. No terminal record or
source silently stops counting. Rate-event retention is bounded by the two
global rate caps; expired events are removed when that rate category is charged.

Well-shaped bounded submit attempts consume rate budget before intake,
idempotency or record/pending/source quota decisions, including malformed ZIPs,
exact retries and later quota denials. Wrong/missing IDs consume read budget.
Invalid envelope/context types and over-limit bodies fail before database work;
the future transport must reject them while streaming and enforce its own
request/concurrency/memory/rate bounds. Trusted reservation/completion/recovery
calls are not public routes and do not use the public read/submit limiter.

`LedgerError.code` and its string are a closed short code. Supported codes are
`invalid_context`, `invalid_request`, `request_limit`, `not_found`,
`idempotency_conflict`, `rate_limit`, `record_limit`, `source_limit`,
`pending_limit`, `active_limit`, `job_not_queued`, `stale_reservation`,
`terminal_conflict`, `invalid_terminal_result`, `storage_unavailable`,
`storage_integrity`, `incompatible_database`, `configuration_mismatch`,
`invalid_clock` and `clock_regression`. A future transport maps these to bounded
responses and must never serialize Python tracebacks, SQL errors or host paths.
Worker outcomes retain the existing result schema and fixed reason allowlist.

## Remaining integration gates and tests

The permanently closed `require_heldout_attestation` and `launch_real` gates
remain unchanged. Durable state alone cannot enable them. This ledger is not
wired into the controller, scratch public-engine bridge or replay projection.
Ownership authentication, service packaging/isolation, actual worker supervision,
complete artifact validation, model transport, replay publication and deployment
operations are still separate work. Aggregate synthetic evidence does not close
the submit/evaluate/replay/revise learning goal.

Run `python3 -m unittest discover -s tests -p test_ledger.py -v` for the focused
suite, or the existing pinned Nix `python-unit` check for all Python tests. Tests
cover real SQLite persistence and child-process crashes, rollback, concurrent
cold initialization/idempotency/reservations/quotas, cross-owner guessing,
logical deduplication quotas, immutable source checks, terminal fencing,
subsecond clock regression, bounded public projection and closed execution gates.
They execute no bots or games and create no accounts or credentials.
