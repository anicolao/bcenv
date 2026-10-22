# Opt-in operator-file stop

This is a cooperative, same-user control path for the trusted operator. It is not
an authenticated boundary, a candidate API, supervisor-death containment, or a
certificate that every descendant process is absent. Existing launches without
these options retain their prior behavior.

## Launch options and scope

Both host entrypoints, `services.launch_local_trial` and
`services.local_trial`, accept paired `--operator-control DIRECTORY` and
`--run-id RUN_ID` options for mailbox operation. Both options must be supplied or
both omitted. `RUN_ID` is exactly 32 lowercase hexadecimal characters. The
operator directory must be fresh: its final component must not exist, its parent
must exist, and directory components must not be symlinks. The watcher creates
the directory with mode `0700`; this does not establish authentication against
other processes running as the same user.

The control directory must not overlap the service artifact root or candidate
mailbox, in either direction. Keep it outside the candidate workspace entirely;
do not give the candidate its path, UUID, or control-publication responsibility.
The operator supplies a fresh run ID and retains it in private launch records.
Paired-option/freshness validation occurs before the launcher's worker probe.
This feature does not authorize a probe, process fixture, or game by itself.

Launch parity binds whether the feature is enabled, the control schema, the
256-byte input bound, the 100 ms polling interval, and the 1 s watcher-join check.
Run-specific control directory and UUID are deliberately excluded from the parity
identity, but retained in the private launch record. They are not included in the
candidate-visible stopped receipt. Integration and live launches must agree on
the mode and fixed contract, not share a control directory or reuse a run UUID.

## Committed request

The watcher observes only the fixed filename `cancel.json`. A request is a UTF-8
JSON object with exactly three fields:

```json
{"schema":"bcenv.operator-stop.v1","run_id":"00000000000000000000000000000000","operation":"cancel"}
```

The example UUID is illustrative; the request must contain the exact UUID of the
current launch. No command, PID, path, signal, extra field, duplicate JSON key, or
other operation is accepted. The file must be regular, opened without following
symlinks, and no larger than 256 bytes. A stale UUID is an invalid request, not a
request to another service. The file is never a mechanism for reading candidate
or evaluator artifacts.

An operator should write the complete bounded record to a fresh, exclusive
staging file inside the private directory, close it, then atomically rename it
to `cancel.json`. Staging filenames are ignored: there is no directory scan.
Do not edit the committed record in place. A missing `cancel.json` is normal.
Once a valid request or malformed/read-error condition is observed, the watcher
records one bounded in-memory observation and exits; it does not repeatedly
process the file or append a disk log.

## Cancellation and cleanup ordering

The watcher calls only `trial.request_cancel()`. That operation first sets the
shared one-way `stop_requested` Event, then closes the cancellation token's sole
writer. The Event is the admission fence, not merely a message for the main
thread to fence later. It is checked on request admission and worker prelaunch.
The watcher does not acquire `trial.lock`, call `trial.cancel()`/`trial.close()`,
join workers, persist state, or publish a stopped marker. This permits it to
request cancellation while the main thread is inside a lock-held synchronous
adapter wait.

A request whose admission check already passed is in flight. There remains a
cooperative precheck/spawn race; there is **no atomic universal no-spawn
guarantee**. Already owned execution relies on the existing cancellation checks,
direct-child waits and normal-return cleanup acknowledgments.

The owning main thread stops and joins the watcher, attempts existing trial
cleanup even if watcher cleanup raises, verifies cleanup/acknowledgment and
watcher success, and only then atomically publishes the existing bounded
`stopped.json` receipt. Its operator metadata contains bounded status/timestamps,
not raw control contents, a control path, or a UUID. No watcher action alone
constitutes a successful stop receipt.

Malformed input, read failure, token-close/cancellation failure, and watcher
start/join failure latch a closed error and withhold successful stopped-marker
publication. Invalid input also requests cancellation once, fail-closed, without
claiming a valid operator request. If both control validation and cancellation
fail, the first control error remains recorded and cancellation failure is
separately observable. Failure of diagnostics must not bypass the main cleanup
attempt.

## Limits and qualification

The fixed 100 ms poll interval and 1 s join timeout are application-level checks,
not hard scheduler, filesystem, kernel, or process-lifetime bounds. A stalled
filesystem operation can outlive the join check; the owner must report failure,
not success. This feature adds no watchdog, process search, arbitrary signal
sender, OS resource quota, or recovery authority.

Pure/mocked tests establish validation and control-flow properties, not runtime
cleanup qualification. This implementation does not establish the state or death
of any old BCENV012 helper and does not retroactively qualify a previous probe.
A future runtime qualification needs separate authorization, exact-artifact
review, and independently retained evidence of request/fence/token ordering,
inner/outer owned waits and acknowledgment, post-fence rejection, watcher join,
and final receipt publication. Do not substitute tool terminal status, a marker
alone, or a child's eventual natural exit for that evidence.
