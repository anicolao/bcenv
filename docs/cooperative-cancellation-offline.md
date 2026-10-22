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
