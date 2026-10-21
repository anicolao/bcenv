# Cooperative local service

This optional profile is for a single cooperative native agent on the existing
VM. It does not replace or pass the hardened controller's launch gates. Same-user
agents can technically access host tools/files; instructions and audit constrain
the experiment, not OS permissions. Keep only public development opponents here.
Stop and freeze the competitor before materializing any held-out evaluator data.

The service accepts normalized source ZIP bytes, builds/verifies through the
pinned public 3.0.13 adapter, executes a trusted fixed development schedule,
returns bounded results, and projects bounded authentic replay pages. Clients
never supply trusted build/map/worker paths. No credential or external listener
is needed. Immutable per-submission directories and audit logs retain failures.

From the repository root, with dependencies already provisioned by Nix:

    python3 -m services.local_trial --config TRUSTED_CONFIG.json --root NEW_RUN --mailbox NEW_MAILBOX
    python3 -m services.local_trial_client --mailbox NEW_MAILBOX submit --source candidate/src --package bot
    python3 -m services.local_trial_client --mailbox NEW_MAILBOX result --submission s_ID
    python3 -m services.local_trial_client --mailbox NEW_MAILBOX replay --submission s_ID --game 0 --start 1 --end 5 --offset 0 --limit 64

The service owner supplies `command` (fixed bcenv argv), `games` (label,
opponent_build, map_path, own_slot), `timeout` (at most 120 seconds), and
`max_submissions`. Four games per submission and six submissions cap the trial
at 24 match attempts. A rejected build consumes a submission without playing.
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

Validation before real integration: 306 Python tests, including 20 local-service
tests; independent review exercised separate-exec submission/result/replay,
infrastructure halt, build timeout classification and replay mutation refusal.
Synthetic tests are not real match or competitor evidence. The prospective
BCENV-004 protocol lives in bc-planner's 2023-cooperative-local-trial.md.
