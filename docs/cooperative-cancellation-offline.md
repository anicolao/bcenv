# Cooperative cancellation: offline review candidate

This change is **not runtime-qualified or ready for a new trial**. Exact pinned
Nix wrapper/adapter descriptor preservation and actual nested-process cleanup
must be tested only after a clean environment is established. BCENV-012 is not
resumed or repaired by this change. No supervisor-death, transport-loss,
host-failure, hostile-code or global descendant-containment guarantee is made.

## Contract

- Close admission under the dispatch lock before publishing cancellation.
- The service owns a pipe. Only its read end is inherited by the trusted adapter;
  closing the sole writer signals cancellation without an artifact write.
- Each inner `execute` checks before launch and while waiting, and on cancellation
  terminates its own process group and waits for its direct child. A cancellation
  concurrent with Popen can still create a child; the next observation cancels it.
  This is not an atomic cross-process no-spawn guarantee.
- Outer adapter execution does not immediately kill the adapter on voluntary
  cancellation. It grants at most five seconds (clipped by the existing outer
  cutoff on the polling path) for the adapter to cancel/reap its direct child.
  Exceptional cleanup grants five seconds before escalation. If escalation or
  failed cleanup prevents an acknowledgment, stopped is not published.
- Main-thread SIGINT/SIGTERM handlers in the mailbox launch entrypoints request
  cooperative cancellation rather than raising through nested spawn code. Prior
  handlers are restored. Abrupt termination that cannot run these handlers is
  outside the guarantee. Direct execute callers have exception cleanup too.
- Adapter acknowledgment is emitted only after a normal adapter operation return.
  A service-owned `execute` observation must separately establish a completed
  outer direct-child wait. An inner acknowledgment alone is insufficient. A
  definite no-start needs no inner acknowledgment; uncertain Popen acquisition
  does not establish that no child exists.
- Shutdown cancels queued futures and joins workers even if cancellation-token
  publication fails. A missing/invalid adapter acknowledgment or uncertain outer
  wait prevents stopped publication. Marker-write failure occurs only after
  cleanup, so it cannot skip joining. The ≤4096-byte stopped receipt uses an
  exclusive temporary, checked complete write, close, and atomic replacement;
  partial writes never publish the terminal filename. This is atomic visibility,
  not an fsync-backed crash-durability guarantee. An existing marker is scoped to cooperative
  worker joins and trusted adapter direct-child waits, not all OS processes.

Cleanup remains dependent on live cooperative Python owners. A failed token-close,
failed wait, unknown spawn outcome or missing acknowledgment is **unverified**,
not a claim of quiescence. Closing admission is distinct from killing active work.
The original absolute game deadline and no-retry/no-resume experiment rules remain.

## Offline test boundary

Only `tests.test_cooperative_cancel`, the entirely mocked
`tests.test_match_timeout`, and explicitly named pure/mocked methods in
`tests.test_absolute_deadline` were run. New tests patch Popen and killpg to fail
closed unless replaced with inert mocks. They use temporary files/pipe descriptors
and mocked workers/executors; they launch no service, probe, process fixture, JVM
or game. Existing subprocess-based deadline tests and the full suite are deferred.
The immutable Nix source filter includes runtime/ and the adapter; that is source
packaging evidence, not proof the actual wrapper preserves the inherited FD.

Required clean-runtime follow-up: rebuild exact reviewed runtime; inspect launcher
parity; test nested cancellation and acknowledgment ordering, synchronous replay
interruption, writer non-inheritance, absent/invalid descriptor handling, queued
work, and marker-write failures using bounded inert fixtures. These tests are not
authorization to run them while previous helper quiescence is unknown.

## Prospective separate-runner qualification subset

A reviewed remote-only harness adds exactly five ordered inert cases inside the
existing packaged replay-query check, without new workflows/checks or matches:
(1) packaged live-writer/EOF/invalid-FD controls, (2) source nested worker SIGINT
plus queued admission, (3) source synchronous replay-dispatch SIGINT, (4) source
nested SIGTERM, (5) successful cooperative cleanup followed by a deliberate
receipt-replacement failure. The maximum is three packaged wrapper calls with one
synthetic-query JVM, four inert source adapter fixtures and four inner Python
children. Existing ordinary CI fixture matches are unchanged and separate.

Source cases call the actual source service/runtime wait path with inert adapter
fixtures that model its acknowledgment contract; they do not execute the packaged
adapter in flight. Self-signals target the known current CI harness process under
installed cooperative handlers, not any PTY or the VM's tool API. The sender is
disarmed/joined before restoring handlers. Only the original owning context cleans
up its child handles. All five cases share one harness so a failure/uncertainty
stops later cases rather than relying on ordering of independent Nix derivations.

Each case has a 40-second elapsed/admission threshold; the harness has a
600-second elapsed/admission threshold (checked between cases, not a watchdog). Inner fixtures self-return within 8 seconds, and ordinary absolute
process bounds remain in force. These are not forced whole-harness timeouts or unconditional OS reaping deadlines;
the unchanged remote CI job limit is separate.
Missing waits, ambiguous ownership, invalid acknowledgments or cleanup overruns
fail qualification; skipped/unsupported is not pass. Per-case status and actual
wait/ack ordering are retained in the check output and console evidence.

The packaged live-writer success, EOF no-start and invalid-FD failure controls
support functional wrapper passage only. They do not prove sole-writer/inode
ownership, installed in-flight cancellation, this VM's external nix-root wrapper,
or exec-tool PTY signal delivery. Those gates remain unqualified even if this
remote subset passes. No outcome establishes old012 helper termination or permits
trial resumption/replacement. No production fixture hook was introduced.

The candidate-helper exporter also includes its newly required generic
runtime/cancellation.py dependency. Pure isolated-import and missing-dependency
negative-control tests exclude cached source modules and repository fallback;
old frozen exports are not modified. Local validation of this follow-up is
restricted to these guarded pure tests and static inspection. The fixture enable
flag is an explicit check opt-in, not an authentication of runner location. The five real
lifecycle cases are not executed locally while012 helper quiescence is unknown.

### Recorded duplicate deviation and corrected prospective scope

The first remote qualification attempt, commit cccec35 and CI39853320731,
passed both sets of checks but instantiated the harness twice: active and
legacy diagnostic replay-query each ran five cases. Actual totals were ten
cases, six wrapper controls, two synthetic-query JVMs, eight source adapters
and eight inner helpers. This exceeded the prospective single-sequence ceiling;
there was no global fail-stop ordering across both derivations. Both original
records and their limits remain preserved. A green workflow did not erase this
deviation or qualify a VM trial.

The correction is explicit and default-off: checks.nix accepts
cooperativeQualification=false by default; flake.nix passes true only to
activeChecks and explicit false to diagnosticChecks. The same flag controls
verifier entry and artifact copying. Disabled instances retain all old checks
and emit not_selected, never pass/qualified lifecycle evidence. The existing
single Ubuntu job invokes current-system flake check without a matrix or
--all-systems; there are no additional jobs, check resources or matches.

One corrected remote attempt is prospectively authorized, with exactly one
active five-case sequence and no retry. The six existing standard public
fixture matches are unchanged and separate. Local validation remains pure or
mocked. The existing identity step uses nix eval outPath (never build) to read
already-produced active and diagnostic outputs; diagnostic must have no
qualification directory and must report not_selected. Missing, duplicate,
wrong-runtime, source-hash or incomplete evidence fails publication. A single
exclusive-write qualification-identity.json binds the retained sequence,
source/fixture/installed hashes, runtime metadata, tested revision/tree and
exact PR head/tree, and explicitly records the earlier duplicate deviation.
A shallow checkout may fetch the validated exact40hex PR head once, read-only
and bounded, to compare its tree with the tested merge tree. No equality is
silently inferred, and no qualification is re-executed to collect evidence.
