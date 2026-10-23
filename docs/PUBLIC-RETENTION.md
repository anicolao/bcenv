# Opt-in public development evidence retention

This operator-only toolkit preserves reviewed, committed public-development evidence. It does not change legacy service defaults, restore service authority, resume an author, retry a job, or revive an expired trial. Losing a VM may still make a run incomplete even when its evidence survives remotely.

The designated ordinary-Git destination is public `anicolao/bcenv-artifacts` (repository ID 1431035719). The user explicitly authorized a public destination. Candidate/public-development content still requires a byte-exact publication allowlist; held-out source, private evaluator plans/infrastructure/raw benchmarks, restricted aggregate outcomes, credentials and unrelated filesystem content remain excluded. A public folder is not an access boundary. No archive repository credentials or access are supplied to the candidate.

## Finite implementation seams

1. `tools/public_retention_snapshot.py` takes a consistent SQLite backup into memory and builds a NEW typed evidence projection. It never publishes the original DB/WAL, filesystem paths, canonical arbitrary JSON, engine command lines or logs. Only committed own sources, source-bound notes/checkpoint, bounded typed public state/results and explicitly approved closed artifacts are eligible. Free text and source bytes are not automatically secret-free: the exact content hashes require operator allowlist approval. Public replay evidence requires a separate known-public-origin digest allowlist, not a private-membership filter. No opponent source or broad workspace/test scan is permitted. Optional own test files and strictly typed source/test-bound status receipts require explicit closed-file digests; these are labeled operator-approved closed records, not SQL-committed artifacts or proof that tests actually ran. Committed replay references retain approved typed projection rows and provenance, not arbitrary nested feedback metadata.
2. Closed payloads use SHA256-addressed bounded chunks and an exact file inventory. `tools/public_retention_git.py` accepts an injected trusted operator Git runner, checks expected remote state and history limits, commits immutable checkpoint paths, attempts one compare-and-swap publication and verifies exact-commit retrieval into a fresh directory before reporting retained. It does not discover or export credentials. A failed/ambiguous push or readback is not success and is never automatically retried.
3. `tools/public_retention.py` verifies an independently approved inventory digest and every exact file/hash/size, then reconstructs only evidence/own files into a fresh workspace. `src`, `tests` and `work` begin empty except the explicit restoration receipt. No active source selection, service SQLite authority, epoch takeover, native task continuation, runtime installation or experimental execution is performed. A failed copy remains partial; it is not overwritten or treated as verified.

## Capacity and cadence

Each Git blob is at most49 MiB; cumulative unique reachable archived payload/manifests across dedicated archive history are at most512 MiB, including deleted/replaced files in earlier commits. The first implementation fails closed if the dedicated remote has unexpected branches/tags rather than ignoring their history. It does not delete refs or rewrite history. Git storage overhead and provider retention/account availability remain distinct; no perpetual-retention guarantee is claimed.

Before a prospective run, measure fixed bytes and reserve a conservative bound for allowed generated evidence; historical file counts do not establish byte fit. If a required checkpoint exceeds the bound, fence new admissions without silently omitting evidence or increasing the cap. At most 28 candidate checkpoint sets: initial fixed inputs, 24 combined training/transfer batch boundaries, 2 validation boundaries and terminal evidence. Explicit checkpoint requests coalesce within that same ceiling, not additional sets. Notes and replay reads join the next checkpoint; the interval since the last verified checkpoint remains at risk.

## Opt-in service boundary

`launch_resumable_trial initialize --public-retention` binds the public-retention configuration and fixed-input digest into the original authority. Absent that option/configuration, legacy defaults are unchanged. The initial retention gate permits status/receipt reads but blocks new work until the initial checkpoint is read back and ACKed. The authority atomically creates the next pending gate with each finalized batch. Candidate note/submit/transfer/checkpoint/replay/lock/select mutations are refused while pending; immutable status/result/receipt reads remain charged. An already accepted queued batch waits outside SQL before start, with cancellation and the original absolute deadline; waiting never fabricates an infrastructure result or refunds a ticket.

The trusted operator uses `tools/public_retention_operator.py` in two phases: prepare an export against the stable descriptor, independently approve its inventory, then publish/read back once and ACK exact run/epoch/checkpoint, effect descriptor, inventory/evidence digests and remote commit. Git credentials and these APIs are never candidate mailbox operations. No SQL write transaction spans copying, Git or waiting. Charged reads during export do not change the stable closed-effect descriptor; the evidence retains its own snapshot counter cut. A failed/ambiguous publication leaves a sticky gate and no automatic retry or unfreeze. An uncertain ACK response requires canonical operator reconciliation; instance poisoning is not an atomic cross-connection stop guarantee.

Initial + up to 26 closed batch sets + one terminal set is a maximum 28. Explicit checkpoint text coalesces with this cadence. The original clock runs during retention. Pending/failed/terminal retention refuses epoch advance/reopen: this is evidence durability, not general service restart persistence.

Physical cleanup never waits for archival success. After existing stop/cleanup observations are fixed, `retention_register_terminal` makes an evidence-only terminal descriptor, even if legacy logical close refused an expired deadline. It records actual logical-close/cleanup assertions and all actual batch states. Terminal registration explicitly supersedes an unretained pending set, preserves its gap/sticky failure and rejects stale ACK; it never labels that prior set retained. Terminal export/ACK after expiry may preserve evidence but cannot reopen work, reset counters/deadlines or reinterpret unknown effects. A run may remain incomplete while evidence survives.

The same-user filesystem model is cooperative, not protection against malicious concurrent file replacement. Hashes and operator approvals detect/bind discrepancies; they are not authentication, secret detection or universal process containment. No new trial is authorized by these tools. Independent review, exact CI and the separately approved single synthetic public roundtrip remain prerequisites before a future trial; no extra game matrix is introduced.

## Validation scope

Local tests use temporary files/SQLite and injected mocked Git only: no matches, subprocess launches, credentials, network writes or extra runtime qualification matrix. Existing CI runs those pure tests without workflow expansion. Previously retrieved public CI evidence proves the independent ordinary-Git roundtrip route, not that this new toolkit or a full candidate trial has already run live.

## Calling sequence and honest recovery boundary

The trusted operator first calls `export_snapshot(...)` with exact authority/config/fixed-input commitments, explicit approved source/text/artifact digests and any known-public replay allowlist. It returns the outer inventory, including the on-disk `inventory.json`; that inner inventory hashes evidence/chunks without self-reference. The operator reviews the resulting exact inventory and sanitized evidence before publication. This is a content approval assertion, not automatic secrecy detection.

`publish_checkpoint(...)` consumes that outer inventory and an exact expected old remote commit. Its returned `verified_export` identifies the independently fetched and materialized payload within the fresh readback directory. Only after its verified receipt may the coordinator mark that checkpoint retained. `restore_evidence(...)` then consumes the on-disk inventory SHA256 plus expected authority/config identities and a new destination. It reconstructs chunked logical files under `own-files/` and leaves active `src/` empty. Evidence is useful for audit or a separately authorized future input process; it is not permission to restart an expired author or restore an executable ledger.

The exporter independently limits a logical artifact to 64 MiB, chunks to 1 MiB and one export to 256 MiB. The Git blob ceiling applies chunks/metadata, not the reconstructed logical replay file; restoration supports the same 64 MiB logical bound. These are finite implementation limits, not measured assurances that every prospective trial fits. Exceeding any cap is a visible refusal requiring the existing retention-failure policy, never truncation.

Each exported checkpoint is self-contained for its explicit evidence selection; it does not silently merge a prior checkpoint or reconstruct omitted files. Repeated identical chunks are identical Git blobs and count once in reachable-history accounting even when referenced by multiple checkpoint paths. A prospective operator must include all evidence required by that checkpoint contract (or explicitly reference separately retained older checkpoints in its external run inventory). The exporter never decides that a large required artifact may be omitted to fit a cap. The opt-in authority enforces the 28-set ceiling and admission boundary; non-opt-in uses of the standalone toolkit do not acquire those guarantees.

The consistent evidence snapshot includes a strictly typed retention history (at most 28 sets): stable descriptors, their verified effect payloads, prior ACK bindings, sticky failures and terminal superseded-gap/lifecycle observations. It does not dump arbitrary SQL JSON. The snapshot naturally precedes its own publication/ACK; that final ACK is not falsely claimed to be inside the very commit it acknowledges. Its remote commit and returned path-free operator receipt remain separate observations. The terminal lifecycle input is a trusted assertion bound to retained evidence hashes, not a kernel proof that arbitrary descendants are dead.

## Public synthetic roundtrip checkpoint (2026-10-23)

The separately authorized second synthetic case completed normally (exit0) in
about78.6 seconds:223 bounded Git calls, two commits and two pushes, with no engine,
real game, service worker or native author. The initial checkpoint is public commit
[`26b44ded58c447c731285f146d10b487c1d06813`](https://github.com/anicolao/bcenv-artifacts/commit/26b44ded58c447c731285f146d10b487c1d06813);
the post-synthetic-batch checkpoint is
[`116c9172f355e2d726ed51ac30ccd5e58ffee958`](https://github.com/anicolao/bcenv-artifacts/commit/116c9172f355e2d726ed51ac30ccd5e58ffee958).
Exact remote readback preceded the corresponding trusted ACK. The fixture preserved
one synthetic eight-cell batch and all eight admission debits; these were not executed games.
Evidence-only restore verified four selected files containing source/note/synthetic
replay evidence. It did not restore active source, runnable service authority,
uncommitted work or a native author. Bounded replay projections remain pure-test
coverage, not this actual case. No third publication or terminal-retention path was
exercised; logical close is not physical-cleanup or arbitrary-descendant proof.

The first case remains preserved as failed/unknown: its sole push exited128, no ACK
was recorded and no synthetic work was admitted. The remote remained at the prior
initialization commit. That runner lacked the explicit credential-helper invocation
used by successful publication; this is a concrete invocation difference, not a
proven root cause because stderr was discarded. The second case was separately
preregistered, not a resend or recovery of the first uncertain assignment.
Neither case reconstructs missing015–017 evidence or establishes trial continuity,
competence, live terminal retention or indefinite provider durability. Only public
synthetic/engineering payload was published; private evaluator material is excluded.

Independent actual-case review SHA256
`181ef9cf28bd70f949a16eae7cdf323a4fd03ee8f4046476819f8f34a6e474fa`
binds the retained observations. The second archived snapshot is pre-ACK PENDING;
its later ACK and terminal logical close are separately local, not retroactively
inside that commit. This self-reference limit is deliberate and remains visible.
