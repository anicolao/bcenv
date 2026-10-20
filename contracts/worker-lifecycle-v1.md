# Synthetic durable worker lifecycle v1

`services/worker_lifecycle.py` supplies a small trusted lifecycle seam around the
existing durable ledger. It reserves one explicit job, delivers immutable source
to an injected **inert synthetic callback**, normalizes a bounded receipt and
attempts one terminal write. No engine, compiler, provider, real game, opponent
selection, scheduler, worker process, authentication, credential or listener is
implemented. The code and tests do not enable real launch or heldout use.

A syntactically valid fabricated receipt can produce a synthetic `complete`
record. The tests deliberately demonstrate this. Receipt identity echoes and
`validated_complete` are consistency assertions, **not signatures, authenticity
proof, validated game artifacts or strength evidence**. A real season worker and
its independently reviewed artifact validator remain prerequisites for actual
results. Existing `contracts/artifacts-v1.md` and historical Galaxy integration
gaps in `docs/REPLICA-2023.md` remain unchanged.

## Fixed request and trusted interfaces

Construct `SyntheticWorkerBridge(ledger, callback, worker='synthetic-worker')`
with trusted host-owned objects. The callback is a typed callable taking a frozen
`SyntheticWorkRequest` and returning exact immutable `bytes`. The worker label
is trusted context, not an authentication credential. The ledger must be an exact
`DurableLedger` instance. There is no candidate-supplied callback, command,
configuration object, host path, opponent, map, environment or process launcher.

`run_job(principal, submission_id)` is **trusted-side only**, not a seventh model
tool. The principal and exact job selection come from trusted caller context;
no authentication or scheduling is supplied. Its request contains only:

- Opaque submission ID and current non-secret reservation generation
- Frozen `AcceptedSubmission` source: declared package, sorted immutable source
  file bytes and canonical source identity, revalidated by the ledger
- One constructor-fixed `SyntheticJobSpecification`: schema
  `bcenv.synthetic-job.v1`, profile `inert-single-game`, `games: 1`, and SHA-256
  of the canonical specification JSON

The specification accepts no constructor overrides. It identifies a synthetic
one-outcome fixture, not an actual map/opponent pairing or a real evaluation
budget. It is not exposed as a model-controlled work specification. Python
immutability here prevents ordinary API mutation, not hostile modification by
another actor controlling the trusted process or filesystem.

## Lifecycle and fencing

Each `run_job` invocation follows this bounded sequence:

1. `ledger.reserve_job` atomically checks owner, queued state, running quotas and
   source integrity, then records the worker/generation/deadline. A denied,
   already-running or terminal job does not invoke the callback.
2. New trusted-only `ledger.check_reservation` independently rechecks owner,
   worker, exact generation, running state, deadline and source integrity. It
   returns a freshly validated frozen reservation without charging public read
   quota. Expiry and clock-regression recovery remain transactional.
3. Invoke the injected callback once with the checked immutable request.
4. Bound and validate the receipt. Ordinary callback exceptions become fixed
   `infrastructure_failure: internal_error`; malformed/mismatched/over-limit
   receipts become `infrastructure_failure: invalid_evidence`. No callback text,
   traceback, raw JSON or log is forwarded.
5. Call `ledger.finish` with the same worker/generation, outside the callback
   exception handler. This is the authoritative atomic post-callback check and
   terminal write, including fresh expiry/recovery/source validation.

The **check-to-callback race is deliberate and explicit**. The reservation may
expire, the clock may regress, or another trusted actor may write a terminal
state immediately after the precheck. A check is not a held lock, lease renewal,
physical-resource admission guarantee or proof that execution remains allowed.
Late callback success cannot overwrite the ledger's terminal recovery failure.
Concurrent bridge invocations for one queued job invoke the callback once because
only one can reserve that job. This does not claim exactly-once external effects.

The bridge does not preempt callbacks, kill processes, enforce deadlines or
release physical resources. A blocking callback blocks this Python call. It can
continue beyond its logical lease; finish then refuses a stale score. A process
abort or other `BaseException` is not swallowed: the claim remains running until
later trusted recovery observes expiry and records terminal `worker_unavailable`.
Existing recovery never requeues/retries the job or turns a failure into a loss.
An independent worker supervisor is still required for actual execution.

## Receipt profile and bounds

The callback returns exact `bytes`, at most 4,096 bytes, strict UTF-8. A lexical
container-depth check runs before JSON tree construction (maximum depth four,
ignoring quoted delimiters/escapes). JSON rejects duplicate keys and non-finite
constants, and each object is limited to eight fields before projection. Arrays,
missing fields, unknown fields, unsupported statuses and inappropriate scalar
types fail the exact schema. The callback can still allocate arbitrary memory
before returning; this receipt bound is not callback memory enforcement.

The complete top-level object has exactly these six fields:

- `schema`: `bcenv.synthetic-worker-receipt.v1`
- `submission_id`: exact request ID
- `generation`: exact integer request generation; booleans do not qualify
- `source_sha256`: exact immutable accepted-source identity
- `spec_sha256`: exact fixed specification identity
- `outcome`: one of the three exact shapes below

Allowed outcomes:

1. `complete` has only `status` and `summary`. Summary has exactly integer
   `games`, `wins`, `draws`, `losses`; games is one and exactly one outcome count
   is one. Floats, booleans, negatives, additional games and contradictions fail.
2. `rejected` has only `status` and `reason: compile_rejected`. This is a synthetic
   compile-rejection assertion; no compiler runs in the callback fixtures.
3. `infrastructure_failure` has only `status` and a fixed reason:
   `worker_unavailable`, `resource_limit`, `invalid_evidence`,
   `boundary_unverified` or `internal_error`.

The parser then builds normalized evidence through the existing closed public
projection. The internal complete marker is supplied only for a valid synthetic
receipt. `ledger.finish` reprojects again, and its returned/stored public result
contains no generation, source/spec hash, private worker data, free text or marker.
Unknown private fields cause a fixed invalid-evidence failure rather than being
copied. The callback sees identity values and can repeat them, so matching them
establishes consistency only; it cannot establish that a game happened.

## Errors, uncertainty and restart

All ledger errors propagate unchanged as their existing fixed safe codes.
Reservation/precheck failures skip the callback. Finish errors are neither caught
as callback errors nor rewritten into a second terminal failure. In particular,
`storage_unavailable` can describe an **uncertain commit**. The bridge does not
assume the write failed, rerun the callback, retry the write, release the claim
or manufacture another result. The caller must reconcile through trusted ledger
state and its existing ownership/idempotency semantics. A test performs a real
terminal commit followed by a simulated storage error and verifies the committed
result survives without a second callback.

There is no automatic retry, lease renewal, queue loop, receipt persistence,
checkpointed callback delivery or new cancellation API. A second `run_job` call
for a running/terminal job fails reservation. An uncommitted reservation can be
retried only through a separate explicit trusted call after reconciliation; this
component supplies no automatic policy. Once recovery terminalizes a crashed job,
it cannot become a score. The ledger's storage/clock/trusted-owner assumptions
continue to apply unchanged.

## Verification and integration gaps

Focused tests:

```sh
python3 -m unittest discover -s tests -p test_worker_lifecycle.py -v
```

The normal pinned Nix Python check includes them. They cover immutable fixed
requests; fabricated synthetic completion; wrong-owner, stale-generation and
expired preflight denial; source integrity checks; no public-read quota charge;
expiry, clock rollback and competing terminal writes during callbacks; exception
normalization; crash-like interruption/recovery; uncertain commits; concurrent
single reservation; receipt size/depth/field/type/identity failures; no retries;
and permanently closed real-launch/heldout gates. No source code or game runs.

This seam is not automatically wired into the controller or public service. A
future trusted operator could use it to service an explicit accepted synthetic
job. Actual engine execution, immutable build/match/replay validation, ownership
authentication, process and filesystem/network isolation, resource supervision,
replay publication, provider transport and end-to-end trial authorization remain
separate integration gates. Passing these tests is no substitute for them.
