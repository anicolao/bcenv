# Prospective engine-timeout censoring

This optional evaluator-side policy is not candidate feedback, a service admission rule, or a change to any historical trial. It supplies no private benchmark identities or outcomes. No stopped evaluation is resumed by adopting it.

## Narrow implementation scope

The reviewed design also proposed optional bounded CPU/RSS/host sampling. That telemetry is **deferred**: the existing qualified runtime already supplies the lifecycle evidence needed for a conservative timeout decision when called with an absolute deadline. This increment changes no runtime, engine, Java heap, output cap, match timeout, process topology or cleanup grace. It cannot diagnose starvation, computation cost or blocking. Historical durations and post-terminal resource snapshots do not establish those causes.

The evaluator must prospectively pin its runtime and policy source, independently verify selected source/class identity and disclose any runtime change relative to a prior evaluation. It must not relabel older receipts as qualified evidence. Existing receipts without positive wait evidence remain insufficient.

## Positive evidence, not exit-code inference

The pinned adapter's `match.process` field belongs to engine execution. A match-level timeout plus engine process timeout can qualify; compilation, packaging, verifier, extraction, summary and transport failures cannot. In particular, a successful engine followed by an extraction timeout is not an engine-timeout censor.

The invoked timeout and original absolute global deadline must be bound to the receipt. The trusted evaluator must validate the raw engine command against its pinned Java executable, unchanged heap, engine classpath, main class and configuration recipe; input metadata hashes alone do not prove that invocation. The policy's artifact-validity attestation assumes this check and is not authentication or an independent filesystem/runtime verifier. No cancellation descriptor is used. Require finite ordered application observations: launch check, termination request, successful engine reap and finish, with finish strictly before the original global deadline. Require a started engine with no launch error, positive `child_reaped`, invocation-specific cleanup acknowledgement and actual successful outer adapter wait. The outer process may return the expected nonzero timeout result; successful wait means the wait completed, not that the match succeeded.

The launch check precedes process creation and is not the exact spawn or wait-start instant. Rounded elapsed seconds are a consistency check, not a reconstructed deadline trigger. Under the audited non-cancelling runtime path, a normal timeout ending strictly before the global deadline conservatively identifies the per-call allowance rather than global expiry. This is a source-grounded inference, not a new kernel guarantee.

Known inner engine wait, adapter acknowledgement and outer wait support that owned chain only. They do not prove arbitrary descendants or unrelated old helpers have died. Missing, contradictory or ambiguous evidence is fail-stop; `timeout` or `-9` alone is insufficient.

## Durable record before any continuation

A qualified timeout is permanently censored/unscored under its original assignment identity and debit. Publish and confirm the durable censor record before admitting the next untouched assignment. Persistence failure or ambiguity stops. Recheck the original never-reset global deadline before continuation; expiry stops even after a successful censor commit. There is no retry, replacement, refund, increased cap or timeout-as-loss. The evaluator retains original schedule order and original admission rules.

The policy is not a scheduler and cannot itself prove that an opaque ID has never previously run. The trusted evaluator binds original assignment index/identity, preserves its schedule and debit ledger, and refuses duplicate or out-of-order work. Cooperative filesystem publication is not adversarial authorization or universal power-loss containment.

## Reporting and validation

Report planned assignments, attempted/valid/scored counts, wins/losses among valid games, censored positions/reasons and unattempted assignments separately. A scored-only fraction is conditional on completion and can be biased by informative censoring. Do not infer performance improvement or timeout cause from it.

Local verification is limited to pure/mocked policy and existing-runtime wait tests with real process creation and signalling blocked. Normal remote CI remains unchanged. Future already-required public fixtures may exercise the receipt path, with no extra games or deliberate timeout matrix by default. A successful ordinary fixture does not prove the actual timeout branch. Candidate exports and public service behavior remain unchanged.

## Shipped generic seam and remaining integration

`tools/evaluation_timeout_policy.py` provides bounded validation and exclusive durable censor publication. `tools/evaluation_assignment.py` provides panel-free single-assignment orchestration with mandatory trusted input/artifact validators and the reviewed policy functions. It does not implement a concrete private scheduler, actual runtime/input verifier, terminal replay scorer or a resumable evaluation. Those remain separately reviewed future integration gates. The caller must preserve the finite original schedule, reject reused/out-of-range indices and supply concrete validators; injectable callbacks in tests are not production evidence.

The sanitized integration tests use opaque synthetic inputs, mocked subprocess completion and actual temporary-filesystem fsync/readback with the real policy. No production process is launched. These tests join existing ordinary test discovery; no CI workflow expansion is required.
