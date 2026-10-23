# Opt-in epoch authority: first internal increment

`services/epoch_authority.py` is a deliberately disconnected SQL authority seam.
Only explicitly creating `resumable-authority-v1` opts into this module. No existing
profile/default, LocalTrial, client, launcher, engine, projection or provider uses
it. **Recovery is not enabled.** Existing interrupted attempts are not migrated,
resumed, retried, selected or evaluated by this change. Native-author continuation
capability and physical cleanup are independent unresolved requirements.

## Trust and policy boundary

This is an internal operator/test API, not a candidate API or security boundary.
The caller must prepare and independently validate normalized source/package
identity, exact source blobs, stable semantic effect identities, outcomes and
artifact manifests outside SQL. `source_digest` hashes the immutable supplied
blob; `source_fingerprint` is a separate trusted assertion of normalized source
plus package, not a claim that this module parses or normalizes an archive.

The full canonical request payload, blob digest, source fingerprint, effect and
debits bind request identity. A submission's effect-retry fingerprint ignores
request/note/tag metadata and binds configuration plus normalized source/package.
Other kinds bind kind/configuration/source fingerprint plus a distinct `effect`
object: callers must derive it from the exact query/mode/own-team/artifact binding,
excluding delivery/request identifiers. Real domain derivation is **not wired**.
Changing arbitrary payload metadata cannot evade the submission restriction;
lying about the trusted fingerprint/effect is outside this seam's guarantees.
This is exact-identity protection, not semantic-similarity detection or coaching.

Callers supply intended debits. The authority checks nonnegative typed values
against fixed lifetime ceilings (24 versions,192 training,16 validation,72 notes,
450 replays,2000 reads,2200 mailbox), optionally lower configured ceilings. It does
not derive charges from real requests, implement transport-invalid accounting,
finalist locks/selection, note-use rules, or validate game/result completeness.
Thus it does **not** yet enforce the actual continuity-v1 profile. No rate window,
lease expiration, quota refund, clock reset, replay upsert or retry queue exists.
Candidate receipt retrieval and its required charge are deferred; `receipt()` and
`snapshot()` are execution-free **operator inspection**, not unmetered candidate
endpoints. Finalized records are internal committed outcomes, not validated scores.

## Transactions and invariants

A single SQLite database uses explicit application/schema/config identity,
foreign keys, WAL and FULL synchronization. Each mutation uses a fresh connection
and one bounded-acquisition `BEGIN IMMEDIATE` transaction; no implicit retry loop.
Admit commits immutable source/request identity, requested debits, queued job,
canonical receipt and event together. Start fences and changes QUEUED to RUNNING,
with at most one logical RUNNING job in the current epoch. Finalize commits a
single immutable outcome and receipt from RUNNING only. Queued work is never
inferred as recoverable from the database or filesystem.

Operator advance requires an exact typed expected epoch and ACTIVE state. One
CAS winner creates a new RECOVERING epoch and permanently marks **all** outstanding
old jobs UNKNOWN, including QUEUED jobs which may never have launched. Counters,
original clocks/cutoffs, committed results and receipts remain. Old success,
failure or other callbacks cannot mutate current authority. UNKNOWN fingerprints
cannot be admitted under a new request ID. Old accepted IDs never reexecute;
same-ID/different-digest conflicts and same-ID/same-digest is still a denial.

Reopen requires the current RECOVERING epoch, matching canonical-author identifier
and an opaque validation proof digest. These are **trusted caller assertions**,
not verification of a live native author. An immutable authority UUID is generated once at exclusive database creation.
Reconnect requires both its expected UUID and configuration digest; each mutation
and operator-read transaction rechecks both. Canonical receipts include the UUID,
which survives epoch advancement. Configuration equality alone is not run identity:
two same-config databases have different UUIDs and cannot be silently substituted
when the caller retains the expected UUID. This is identity binding, not
cryptographic protection against hostile same-user SQL/file modification. Epoch bindings require distinct canonical absolute service/artifact/
mailbox/control/author directory names, disjoint from all earlier names. No files,
symlinks, mounts or directory ownership are inspected: real validation/copying and
safe destination ownership remain external integration gates. Logical fencing
neither kills old processes nor prevents hostile same-user filesystem access.

Clock sampling occurs after SQL lock acquisition and again immediately before
commit authorization. Both wall and monotonic cutoffs apply; domain changes,
regression and expiration reject. Original start/deadline are never reset and
downtime counts. The caller must supply a trusted, bounded, same-boot/domain clock
provider; this module does not derive boot identity. Receipts record authorization
time, not a promise that physical disk COMMIT completed before the cutoff.

## Failure and external-work seam

Expected domain denials roll back all payload changes and charge nothing in this
internal increment. Actual charged-domain-denial policy is deferred, not silently
implemented by committing arbitrary exceptions. SQL/unexpected errors poison the
writer instance and return a closed error, with no worker dispatch. A COMMIT
exception is explicitly UNKNOWN even if the transaction actually committed. The
same instance rejects subsequent writes. Read-only receipt reconciliation may
show the exact committed bytes/origin/digest; **absence does not authorize retry**.
There is no automatic reopen/replay of an uncertain mutation. Future operator
integration must retain uncertainty across its own restart; constructing another
trusted writer is not an execution-retry permission. SQLite durability still
depends on its filesystem; no cross-filesystem atomicity or crash-survival miracle
is claimed. Failed initialization leaves its exclusively created file untouched,
never an automatically reset database.

Mock integration is `admit -> start -> external work -> finalize`. No external
work callback executes inside the authority. Tests block a fake external callback,
advance via another connection, then reject the stale finalizer. They demonstrate
short transactions and commit fencing, not actual process/launcher integration.
An old worker may still run/write its old paths; the precheck-to-spawn race is not
eliminated. Real dispatch must separately bind epoch-specific output paths,
recheck its admission/deadline and preserve actual owned cleanup evidence. Response
publication/outbox delivery, interrupted selection, artifact validation/copy and
storage/high-water integration are later increments requiring separate review.

## Finite internal storage and test scope

Canonical JSON objects are capped at64KiB; source blobs at1MiB each and64MiB total
unique payload. At most2200 jobs,8192 transition receipts and64 epochs are accepted.
These logical bounds are not a filesystem quota or numeric WAL/disk-overshoot
bound; aggregate old-root/DB/WAL accounting is deferred. Schema/source/config
failure is fail-closed, never fallback to phase files or directory scanning.

Focused tests use SQLite temporary files, fake clocks, threads and mocked work/
commit failures only. No local process helper, runtime fault injection, game,
native task or recovery is executed. Existing remote CI fixtures remain unchanged;
this increment adds no real qualification case and proves no six-hour continuity.
