# Synthetic controller-to-ledger composition v1

`services/controller_ledger.py` connects the existing six-tool synthetic
controller to the SQLite ledger using a small run-owned source artifact store.
The composition is offline and synthetic: no provider call, listener, account,
authentication, compiler, game or real sandbox/export implementation is added.
It does not enable `launch_real` or the permanently closed heldout gate.

## Interfaces and authority

The controller's existing six tools and their argument schemas are unchanged.
There is no seventh export tool. `workspace_exec` still passes bounded argv only
to a trusted sandbox adapter. A trusted backend may additionally return exact
`ExportedExecReply(status, stdout, stderr, source)` with an optional exact
`SourceExport(package, archive)` object. That object has exact immutable `bytes`,
not a path, file object, mutable buffer, iterator, mapping or duck-typed object.

`CapturingSandboxAdapter(backend, artifacts, principal, run_id)` implements the
existing sandbox interface. It delegates reads/writes and inert exec requests,
checks the exact reply type, fixed status, byte outputs and declared output cap,
and accepts an export only on `succeeded`. It calls the artifact store and puts
the newly minted handle into the existing `ExecReply.artifact_id` field. A backend
cannot choose that handle by returning a regular `ExecReply` with its own ID.

An `artifact_id` string in stdout, stderr, a file, candidate JSON, command argv
or a returned path conveys no authority. None is parsed as an export. The source
side channel is a **trusted adapter assertion**, not evidence that a candidate
process exited, all descendants were stopped, source files were captured safely,
or a filesystem/network boundary was enforced. A future isolated export adapter
must independently establish those facts. This Python module never opens
candidate-selected paths or runs commands. The synthetic tests use an in-memory
workspace, scripted model replies and temporary SQLite databases.

`principal` and `run_id` are fixed trusted construction context, each 1–64 ASCII
letters/digits/underscores/hyphens. They are not tool arguments, credentials or
authentication proof. A real frontend must authenticate the principal and own
run creation/recovery. Another object in the same trusted Python process can
inspect or mutate private attributes; this component supplies no in-process
security boundary.

## Immutable artifact snapshots

`SourceArtifactStore.capture(principal, run_id, SourceExport(...))` checks exact
input types and envelope limits, then calls the existing strict
`accept_source_zip`. It publishes a new independent `a_<32 lowercase hex>` handle
only after validation and quota checks finish. Failed exports do not publish a
handle or consume artifact retention. There is no overwrite API.

The store holds a frozen `SourceSnapshot(package, archive, content_sha256)` with
validated immutable original ZIP bytes and the normalized source identity. It
retains the bounded ZIP because the existing durable submit API revalidates that
ZIP before storing normalized source BLOBs; original ZIP metadata does not become
part of accepted source identity or public results. Different ZIP compression,
entry order or timestamps can therefore have the same normalized identity.

`resolve(principal, run_id, artifact_id)` returns the trusted immutable snapshot
only for its exact owner and run. Unknown, malformed, other-owner and other-run
handles return the same fixed `unknown_artifact` code. Replacing workspace files
or backend replies after capture cannot change the stored bytes. Immutable bytes
may be shared without copying; mutable buffers and subclasses/duck-typed exports
are refused rather than implicitly converted. These invariants do not defend
against a host administrator or a compromised trusted adapter/process.

Default artifact retention is 16 records and 8 MiB of ZIP bytes per owner/run,
and 64 records and 64 MiB globally. Every capture, including duplicate content,
charges the full record and byte amount. A lock serializes quota checks and
publication within one store. Strict intake limits remain 1 MiB compressed,
128 Java files, 512 KiB per source file, 2 MiB expanded source, expansion ratio
100, 240-character paths and 16 package levels. Package/path/ZIP surprises are
rejected by the existing intake implementation.

The store is process-local and does not evict, persist handles or implement
cross-process accounting. Validation happens before the retention lock; request
buffers, concurrent transient validation memory and backend callback allocation
need external limits. Retention bounds are not a complete process-memory or disk
quota. A real session lifecycle and durable artifact registry remain future work.

## Durable submission and restart behavior

`DurableScrimmageAdapter(ledger, artifacts, principal, run_id)` implements the
existing scrimmage adapter interface. Ledger and artifact intake configurations
must match, and the ledger game cap cannot exceed the unchanged controller's
128-game projection cap. Incompatible caps fail at construction rather than
turning an otherwise valid ledger score into a projection failure.
`submit(SubmitRequest(artifact_id, package))` resolves the exact scoped
snapshot, rejects package mismatch, and calls the ledger with those immutable
bytes. It never accepts a host source path or a client-selected idempotency key.

The private idempotency key is SHA-256 of a versioned, unambiguous encoding of
run ID and normalized source identity; the ledger additionally scopes it to the
principal. This means **identical normalized content within one run is one job**.
Repeated submissions and trusted recaptures return that job's current result,
including an immutable terminal failure. A changed source, different run or
different owner creates a distinct job if quotas permit. There is no same-run
nonce for repeated evaluation of unchanged content, and no implicit retry of a
failed/crashed job. A future evaluation policy must explicitly design any such
capability rather than silently changing this identity rule.

Artifact handles and the adapter's issued-submission visibility set are
ephemeral. Reconstructing the store/adapter after restart does not authorize old
handles. Trusted recapture of the same source under the same owner/run and a
subsequent submit recover the existing durable job; the new handle differs.
The caller cannot import a submission ID as authority. `result` and `replay`
require an ID issued by a successful submit through that adapter instance, then
apply the ledger's ownership/rate checks. Wrong-owner/run and guessed IDs return
uniform `unknown_submission` at this layer. The controller separately requires
handles issued in its current loop. Durable controller transcripts, continuation
or automatic recovery of a model conversation are not implemented.

The ledger's durable idempotency, retained record/source limits, public read and
submit rates, generation fencing, conservative expiry and clock-regression
recovery still apply unchanged. An adapter failure after the ledger commits can
leave a durable job whose response was not delivered; repeating the same request
recovers it subject to quotas. Artifact creation, controller transcript delivery
and ledger submit are not one distributed transaction. An accepted job only
means intake passed; no worker is scheduled by this adapter.

## Closed results and errors

Ledger responses are reprojected through the existing public allowlist. When a
ledger result is complete, the adapter recreates `validated_complete` only as a
trusted-side marker required by the controller's second projection; that marker
never appears in controller public feedback and independently proves no game.
Logs, paths, opponent names, scheduling metadata and arbitrary extension fields
are not copied into scrimmage feedback. Workspace stdout/stderr remain workspace
output, not a redaction layer for an incorrectly isolated backend.

Replay performs scoped lookup and ledger rate accounting, then returns `None` to
the existing controller seam, which publishes `unavailable: replay_withheld`.
No raw replay or separate replay projection is connected. The full tactical
learning loop is still incomplete.

`ArtifactError` uses only fixed codes: `invalid_context`, `invalid_request`,
`invalid_export`, `source_rejected`, `artifact_limit`, `unknown_artifact`,
`unknown_submission`, `package_mismatch`, `invalid_configuration`. Ledger failures
retain their fixed internal codes. The existing controller turns adapter
exceptions into bounded `infrastructure_failure: adapter_failure` without raw
exception text. Failed export/intake therefore creates no ledger rejection
record and currently gives coarse tool feedback. It does not become a game loss.
A future transport may add a reviewed bounded error mapping without exposing
source parser or backend exception text.

## Verification and remaining gates

Focused command:

```sh
python3 -m unittest discover -s tests -p test_controller_ledger.py -v
```

The normal pinned Nix Python check includes these tests. They exercise all six
tools through the composition, immutable capture after workspace mutation,
mutable/duck-typed rejection, bogus stdout/path/ID claims, cross-owner/run handles,
package mismatch, malicious archives, failed export without handle publication,
concurrent artifact quotas, durable idempotency after recapture/restart, worker
failure recovery, result projection and closed real-launch/heldout gates. Games
and provider calls are never executed; synthetic worker evidence is not strength
evidence.

Authentication, trusted process teardown and source capture, filesystem/network
isolation, live resource enforcement, actual worker supervision, game artifact
validation, durable controller/artifact lifecycle, replay delivery and a supported
model provider remain independent integration work. The historical Galaxy
relationship in `docs/REPLICA-2023.md` is unchanged.
