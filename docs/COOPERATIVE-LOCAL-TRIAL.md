# Cooperative local service

This optional profile is for a single cooperative native agent on the existing
VM. It does not replace or pass the hardened controller's launch gates. Same-user
agents can technically access host tools/files; instructions and available audit
evidence constrain the experiment, not OS permissions. The service audit is not a
complete request or host-tool-access log; rejected requests and activity outside
the service are not comprehensively recorded. Keep only public development
opponents here.
Stop and freeze the competitor before materializing any held-out evaluator data.

The service accepts normalized source ZIP bytes, builds/verifies through the
pinned public 3.0.13 adapter, executes a trusted fixed development schedule,
returns bounded results, and projects bounded authentic replay pages. Clients
never supply trusted build/map/worker paths. No credential or external listener
is needed. Immutable per-submission directories and audit logs retain failures.

## Start and use the configured profile

The trusted operator starts a fresh service from the repository root, with
runtime dependencies already provisioned by Nix:

    python3 -m services.local_trial --config TRUSTED_CONFIG.json --root NEW_RUN --mailbox NEW_MAILBOX

For the mandatory-note profile, the operator must explicitly set
`require_experiment_notes: true`. It defaults to false for legacy compatibility;
merely writing a prediction file does not enable enforcement. See
[prospective experiment notes](PROSPECTIVE-EXPERIMENT-NOTES.md) for note budgets,
receipts, prior replay references and the exact ordering claim.

From the public client export (not the evaluator checkout), the competitor writes
its own prediction file and runs:

    python3 local_trial_client.py --mailbox NEW_MAILBOX note --source src --package bot --text-file prediction.txt
    python3 local_trial_client.py --mailbox NEW_MAILBOX submit --source src --package bot --note-id n_RETURNED_ID --note-sha256 RETURNED_NOTE_SHA256
    python3 local_trial_client.py --mailbox NEW_MAILBOX result --submission s_RETURNED_ID
    python3 local_trial_client.py --mailbox NEW_MAILBOX replay --submission s_RETURNED_ID --game 0 --start 1 --end 5 --offset 0 --limit 64

Replace placeholders with values from the preceding responses. Submit exactly the
source/package bound by that note; each accepted submission consumes its note.
Prior replay references, if used, come from earlier returned pages, never guessed
IDs. A new source version requires a new note before submission. Optional local
compilation and actual Java tests use the exported
[candidate Java checks](CANDIDATE-JAVA-CHECKS.md); their results do not replace
server verification, the prospective note, or scrimmage evidence.

The service owner supplies `command` (fixed bcenv argv), `games` (label,
opponent_build, map_path, own_slot), `timeout` (integer 1–120 seconds), optional `match_timeout` (integer 1–300 seconds), and
`max_submissions`. The schedule supports 1–8 games per accepted, successfully
built submission. Record explicit per-run configuration and budgets rather than
assuming a universal cap:

- BCENV-004's historical configuration allowed six submissions × four games,
  at most 24 development match attempts.
- BCENV-005's configured budget allows eight submissions × eight games,
  at most 64 development match attempts. This is a cap, not a claim that 64 games
  have run or completed.

A build rejected after submission acceptance consumes a submission without
playing. Request validation rejections are not accepted source versions. Replays
and result reads have separate service limits; prospective notes have their own
configured `max_notes` limit.
Infrastructure failure halts further submissions and remaining scheduled games;
it is not reported as a candidate loss. Existing results remain readable.

File RPC uses fresh mailbox directories, UUID request names, bounded regular-file
reads and atomic publication. Client timeouts are uncertain outcomes; it never
automatically retries a submission. This is not tamper-proof against the same
Unix user. HTTP mode binds only 127.0.0.1; this VM's separate tool invocations
could not reach another invocation's loopback listener, although same-invocation
HTTP works. File RPC passed separate-invocation tests without changing networking.

Output budgets cover requests/results/replay pages, while process timeouts and
the existing 512 MiB JVM heap provide practical safeguards. They are not aggregate
CPU/PID/disk quotas. The service is not restart-recoverable: preserve an interrupted
run and start a separately recorded experiment rather than silently reusing it.
Source/runtime/map/replay identity is checked by the adapter, with replay digest
bound again to each completed service result before and after page extraction.
Terminal reason remains null because the current parser does not extract it.

## Freeze before independent held-out evaluation

1. Have the competitor select and record its final submitted candidate using
   development evidence only, without observer strategy advice. Retain its exact
   source snapshot and normalized source identity, verified build and
   binary identity, configured budgets, note/result/replay receipts and available
   tool-use records. A final narrative alone is not a prospective note.
2. Stop the competitor agent and its candidate-side execution. Resolve already
   accepted service work to a terminal state, or explicitly preserve and report
   interrupted work; do not silently restart it. Stop the development service and
   freeze its evidence. Do not launch a replacement competitor against this run.
3. Only after that freeze may the trusted evaluator materialize genuine held-out
   artifacts in evaluator-owned storage outside the candidate workspace. Evaluate
   the frozen candidate without changing its source or coaching its strategy.
   Do not feed held-out source, identities, detailed results or replay material
   back to a live or resumed competitor. Keep evaluator ownership distinct from
   the public client/service interface.

This temporal separation is procedural confidentiality, not enforced same-user
isolation. Preserve independent evidence of the freeze and disclose gaps rather
than claiming the host made access impossible. This document does not authorize
a new trial or merge and does not retroactively strengthen an earlier run.

## Historical validation

Validation before the initial real integration: 306 Python tests, including 20 local-service
tests; independent review exercised separate-exec submission/result/replay,
infrastructure halt, build timeout classification and replay mutation refusal.
Synthetic tests are not real match or competitor evidence. This historical count
is not the current aggregate suite count; see the linked extensions for their
validation scope. The original BCENV-004 protocol lives in bc-planner's 2023-cooperative-local-trial.md.

### Separate trusted match wall limit

`match_timeout` is operator configuration, never a candidate request field.
When absent it resolves to `timeout`, preserving the prior per-process limits.
Explicit null, booleans, non-integers and values outside 1–300 are rejected.
Both training and locked-finalist workers use this limit only for `run`: the
engine and its initial replay-summary extraction each receive that per-process
limit. It is not a total per-game wall allowance. Build, package and subsequent
replay queries still use `timeout` (at most120 seconds).

The same existing absolute trial deadline clips every adapter subprocess,
including the engine and initial summary. Increasing the match limit does not
reset or extend the trial clock, permit a child launch after cutoff, restore
failed games or guarantee that the maximum game allocation will finish. The
outer worker retains its bounded child-cleanup grace. Launch parity binds both
resolved limits so integration and live settings cannot silently differ.
