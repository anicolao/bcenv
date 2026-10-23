# Prospective opt-in resumable-v1 service wiring

**Draft, not runtime-qualified and not a trial launch.** This increment wires a
new service to the Stage1 epoch authority. It does not migrate, recover or change
any existing012/013 protocol or service. Legacy LocalTrial/client/entrypoint
behavior stays the default. No new security mechanism, lease heartbeat, automatic
failover, host requirement, native-author creation or extra game fixture is added.

## Authority and execution boundary

`ResumableAuthority` subclasses the bounded transaction machinery in
`epoch_authority.py`; it does not use the synthetic admit/start/finalize API.
Its own durable tables govern every candidate operation, lifetime counter,
source/note consumption, batch/game/replay, phase, checkpoint, reference and
canonical request response. SQLite FULL/WAL transactions are short; external
source parsing, copying, hashing, provider execution and child waits are outside
them. Expected domain denials commit intended ingress/read charges through a
payload savepoint. Unexpected errors roll back and poison that writer. Ambiguous
COMMIT or response delivery never authorizes replay of an operation.

`ResumableTrial` uses the existing real AdapterWorker build/package/game/replay
route and unchanged projections. It never schedules work discovered in recovery
rows. Only the work ticket returned by a newly committed request is scheduled.
One executor handles training/validation batches, with at most two accepted
pending training versions. Child game admissions do not consume a second batch
slot. Synchronous replay may overlap a background batch, as in the previous
interface. Its external wait holds neither SQL nor a global admission lock.

The canonical state machine is admit → external work → finalize. Each transition
checks the authority/config identity, epoch, original clock domain and deadline.
A game row records admission, not an assertion that an OS process really started.
There is no atomic SQLite/Popen operation: an old owner admitted just before a
CAS may still physically spawn afterward. Epoch-private paths and transactional
finalization prevent authoritative stale results, not orphan CPU, old file writes
or global physical worker exclusion. One current authorized batch stream is not
one physical worker across all epochs.

Every completion binds its exact source, verified build/package, eight game
receipts/replay hashes and a bounded file-size/hash manifest. Replays revalidate
committed artifacts before/after extraction. Finalist lock verifies both original
manifests; selection verifies those plus both completed validation manifests.
SQL stores an internal manifest up to1MiB with its hash; transitions and public
responses carry only bounded identity summaries, not the full manifest. A
449-path synthetic regression represents the previously observed tutorial shape.

## Fixed capacity and intentional new protocol semantics

The original6h wall/monotonic deadlines and same-boot clock domain are immutable;
downtime and startup/probe delay count. Clock regression, domain mismatch or
expiry fails closed. Limits are24 training versions,192 training game admissions,
16 validation cells,208 total capacity,72 notes,450 replay admissions,2000 reads
and2200 mailbox attempts.16 validation cells are reserved atomically with both
finalists; actual attempts remain separately counted. Reserved/queued work is
not fabricated into a physical game count. UNKNOWN never refunds capacity.
Existing120s ordinary/300s match-child limits share the original absolute cutoff.
512MiB JVM heap is not RSS/aggregate-process containment.

The explicit `resumable_mailbox` client requires pinned authority ID and epoch;
it never adopts a newer ready file. Every envelope binds request ID, canonical
body digest and origin. Ordinary public replies are capped at60000 encoded bytes;
receipt wrappers at64000, leaving space inside the unchanged65536-byte RPC cap.
Source ZIP intake retains the existing1MiB ZIP/2MiB expanded limits; up to72MiB
of distinct note-source ZIPs can be retained so valid note capacity is not cut by
an unrelated source-store ceiling.

| Operation | Durable behavior |
|---|---|
| note | Training only; source-bound note, prior committed references, one note debit. |
| submit | One-time note consumption + version/source/batch/accepted response atomically. |
| result / validation_status | Charged read of authoritative state; status includes actual remaining time. |
| replay | Charged read + replay admission. Provider runs synchronously but response remains the immutable replay_pending admission with replay ID. |
| replay_result | Separate charged read of the immutable terminal replay outcome/reference; never reexecutes extraction. |
| receipt | Separate charged read of a prior request's immutable original response and origin; absence never permits retry. |
| lock_finalists | Both original IDs/provisional choice + two validation batches/16 reservations in one commit. |
| select_finalist | Immutable selection only after both complete validations and four externally revalidated manifests. |
| checkpoint | Optional candidate-authored text≤32KiB referencing existing submission IDs; mailbox charge, no source ingestion. Assertions are not verified test facts. |

The extra replay_result read is an explicit change of this opt-in protocol, not a
hidden free read or automatic retry. After selection, new notes/checkpoints/
replays and epoch continuation are forbidden; immutable status/result/receipt/
replay-result reads may remain until close/deadline. New source-bound notes stop
at finalist lock; generic checkpoint text may continue before selection.

Same request ID/digest duplicates return the original response with no provider
rerun (a new delivery attempt still consumes mailbox capacity). A changed digest
conflicts. UNKNOWN source fingerprints forbid exact normalized source/package
resubmission under a new ID, even if compilation completed before interruption.
UNKNOWN replay fingerprints bind producer configuration, normalized query/mode,
source, committed replay digest and own slot—not delivery/batch IDs. Omitted mode
and explicit actions are identical; own_diagnostics remains distinct. This is
exact identity policy, not detection of semantically equivalent bot edits.

Foreign authority/epoch envelopes are noncanonical transport rejections and do
not mutate another epoch's counters. A separate2200-observation per-epoch bound
also limits rejected transport traffic; canonical lifetime budgets never reset.
Staging tmp files are not requests. Delivery/unlink follows canonical commit;
partial or absent delivery is uncertainty, not another execution opportunity.

### Finite maintenance receipts

Stage1's8192 default remains unchanged. This opt-in subclass allows262144
canonical transition receipts. A conservative supported-flow bound is below
151000: up to64×2200 transport/storage observations (140800),4400 request-side
storage observations,2200 ingress transactions,520 batch adapter checkpoints,
900 replay adapter checkpoints,416 game admission/checkpoint transitions,
52 batch starts/completions,450 replay completions, and fewer than512 bounded
recovery/advance/close records. The cap remains a finite fail-closed internal
bound, not permission for arbitrary trusted maintenance calls.

Storage checks include service/artifact/mailbox/control roots from **all epochs**
and the authority DB/WAL/SHM. Candidate author workspace remains excluded as in
the prior service guard; it is not silently brought under a new quota. The1GiB
logical high-water check is sampled detection/admission, not a filesystem quota,
reservation, atomic scan, or bounded-overshoot guarantee. Old-epoch growth can
halt the current epoch. Missing old roots or scan races fail closed.

## Manual recovery, not automatic restart

Operator `advance` is a typed expected-epoch CAS. In the same transaction it
permanently marks every outstanding batch/replay/game UNKNOWN, including queued,
unlaunched and reserved work, and moves the new epoch into RECOVERING. Late
completion or an old close/storage/denial cannot mutate current authority.
Known infrastructure halt remains sticky; interrupted locked validation becomes
incomplete/unselectable and cannot be repaired by retrying its missing cells.
There is no rollback of an uncertain CAS or lease-based lock stealing. Operator
receipt inspection may resolve whether it committed; absence is not retry authority.

Recovery validates previously committed manifests, then copies their exact
bounded bytes into fresh epoch artifacts and rehashes the copies. It reconstructs
only accepted own source blobs, committed own notes/references/request receipts,
and optional committed checkpoint text into a new author directory. Active src/
and tests/ start empty; it does not select a strategy/source for the author.
Uncommitted edits, tests, work and uncommitted output files are lost. No broad
abandoned-workspace snapshot, mutable-path fallback or result promotion occurs.
Copy failure leaves RECOVERING closed. The final CAS requires exact manifest
bindings and a trusted proof for the original canonical author.

**Committed-artifact recovery is not turnkey native-author recovery.** A separately
allowlisted/hash-reviewed fixed tools/rules/client export and workspace path
rebinding must be provisioned and reviewed before any native continuation. The
saved exact canonical task must be addressed with followup_task and returned
retained context verified. Roster absence alone is not unaddressability; do not
create a replacement task. The operator proof includes exact author/epoch,
canonical_task, continuation_method=followup_task, followup_succeeded=true,
retained_context_confirmed=true, same_existing_task=true and proof_digest.
These are trusted operator assertions, not authenticated platform evidence.
The service performs no task lookup, creation, followup or model override.
A fresh pinned client/new mailbox epoch is required; old helper defaults cannot
silently become current. Original deadline and SQL reconciliation precede author
work. Any missing required proof/provisioning remains a launch gate.

The recorded completed-task marker and later naturally interrupted engineering
task followups demonstrate only those observed same-task continuations—not
future interruption survival, durable transport or recovery of012/013.

## Operator entrypoint and cleanup

Use `python -m services.launch_resumable_trial` only inside the selected runtime
context. It exposes explicit initialize/serve/advance/recover/inspect/receipt
commands; no subcommand loops or retries. Initialize starts the original clock.
Authority ID/config digest, expected epoch and transition ID are explicit.
Every epoch's service/artifact/mailbox/control/author paths are unique/disjoint;
the database stays outside them. No existing LocalTrial roots are adopted.

The new launcher pins all top-level services/runtime Python files, public maps/
build inputs, direct adapter command and documented runtime environment overrides.
External immutable runtime/engine closure verification remains a separate launch
requirement; this inventory is not a claim to hash every transitive interpreter
or system dependency. It runs the existing bounded --help probe once with the
original absolute cutoff and cancellation token. The optional kwargs leave the
legacy default call behavior unchanged. An epoch observer runs before probe and
continues during synchronous replay; stale epoch or deadline requests EOF
cancellation without a dispatch lock. No tool Ctrl-C/PID guess is needed.

Operator notification sets the local admission Event and token EOF. SQL
logical_close is the authoritative stop linearization, before executor joins;
file observation and the Event are not atomic SQL fencing or physical termination.
Cleanup executes even if SQL/persistence fails. Only successful owned direct-child
wait/ack evidence, observer/operator joins and normal cleanup permit the atomic
epoch stopped receipt. Ambiguous canonical close is not retried. No stopped
receipt claims global helper absence or supervisor-death containment.

## Validation boundary

Local verification is selected pure SQLite, fake workers/selectors/processes,
thread synchronization and temporary-file tests only. No actual subprocess
fixture, game, fault injection, native competitor or service run is authorized
by this document. Existing standard remote CI may run its unchanged fixtures
only after independent review/publication authorization. Actual installed/Nix
integration, normal stop, interrupted transport and same-author provisioning
remain unqualified for this new profile until separately authorized evidence.


## One authorized inert integration (2026-10-23)

An independently audited single execution passed at00:54:37–00:54:39 UTC using
source `4c4678087e5379d9abd1fbd0380694c927f7a830`, unchanged installed runtime
`/nix/store/0azcmfv2bqdbm6ijgkz1rpbsqw829hmy-bcenv-2023-0.1.0`, and a synthetic
adapter/finite Python child. Both service owners returned normally; there were
six committed RPCs, one epoch CAS, no retries/tool signals and **zero games**.
The active child was cancelled after readiness, directly waited/reaped and
acknowledged before the old owner joined. Stale finalization was rejected with
identical authoritative before/after snapshots. The batch remained UNKNOWN;
original authority, accounting and six-hour deadlines were conserved. Final
counters were mailbox6, reads3, notes1, versions1 and zero games/replays.

Recovery restored exact committed neutral source, note, checkpoint and original
receipts, excluding uncommitted markers/incomplete artifacts. A charged new-epoch
receipt read returned the original admission unchanged. One operator stop closed
the current owner normally without changing counters/deadlines. This case had no
COMPLETE batch: it does **not** qualify completed-game copying, production-engine
cancellation, packaged production CLI, candidate/native continuity, global process
absence or old012/013 helper quiescence. It authorizes no trial launch.

Retained local evidence commitments (SHA256; no raw execution payload published):

- Final plan: `e4e05ed8d5a8c0f6ae55ac5a5a474254ff64faad02a6cb29783891f4b4a024f4`.
- Input manifest: `da92d6758c19200312bf4d6ef0912cb5e18cae6bc17d08763a0dee66b41936c8`.
- Static review: `9002c42d86d42c5a9deeda6716c926eb75abe74c977ede60f9f149dcf5f8b83c`.
- Execution receipt: `824fb03d61641d94021300571c818e7b8a88ede32be0ea2310bbeb057ff63357`.
- Final fixture receipt: `b89ea746a21559e6f1cad99a65ba73c9560f5a9fd0253002bb0f92df4d505015`.
- Independent result review: `c74eae738e1ca4055a8ff36dd3deecc4a4f9371ad08b95a651f7edadb2c29027`.

The preceding qualification limits remain applicable outside this exact inert
composition. Existing remote CI coverage is separate from this one-case result.
