# Public finalist validation (opt-in cooperative service)

This phase supplies a paired comparison of two already-submitted immutable bots
on additional public conditions. It is **public development validation**, not an
independent held-out assessment, a correctness certificate, or hard confinement.
No operator chooses a bot or supplies game strategy.

## Trusted configuration and budget

The operator enables a fixed `validation_games` schedule in the trusted service
configuration. Candidate requests never contain map paths, opponent paths, or a
replacement schedule. Both finalists play the same eight configured conditions:
two public maps, the two existing public development opponents, and both slots.
Map selection and the schedule must be recorded prospectively, independently of
candidate code and private evaluation material. Do not modify the schedule after
nomination or substitute a failed cell.

The approved profile retains at most eight training source submissions, with
eight games each (64 training games), and adds exactly one validation batch of
two finalists with eight games each (16 validation games). Total maximum: **80
games**, still within the declared 45-minute candidate wall budget. Rejected
training builds consume submission capacity without playing games. Validation
uses existing accepted source versions, not two additional source submissions.
The trusted `max_run_seconds` setting is at most 2700 seconds. Its service clock
starts at service initialization after launch gates, not when the candidate first
submits or nominates. Before candidate launch, the coordinator must disclose the
actual remaining seconds; startup delay is not free extra time. Public-fixture
integration uses its own fresh service root and separate budget. Launch parity
binds the configured duration, while each run has its own start/deadline timestamps.
No new source-changing operation or game may begin after expiration; in-flight
worker subprocesses receive the same absolute host-monotonic deadline and may
not start after it. The outer adapter gets five seconds of diagnostic/cleanup
grace after cutoff so it can kill and reap a separately-sessioned JVM; that grace
does not authorize any further compiler, engine, or replay subprocess. This
requires the new deadline-aware pinned runtime: an old adapter rejects the flag
and the service fails closed rather than silently dropping the deadline. An interrupted validation
remains incomplete/unscored. The configured note, read, replay and mailbox-request limits still apply. An
unfinished or failed phase is not silently granted extra time, games or retries.

Use the shared operator launcher and the same interpreter/runtime configuration
for prelaunch public-fixture integration and live operation. The validation
configuration must be included in prospective launch review and parity checks.
A successful worker `--help` probe alone is not an end-to-end integration test.

## Candidate interface

Training note, submit, result and replay commands are unchanged. Wait until all
pending training work has completed before nomination. Select **two distinct
completed, verified training submission IDs**, and declare which one you would
choose before receiving validation feedback. Submit both IDs in one request:

```sh
python3 tools/service.py --mailbox mailbox lock-finalists \
  --submission FIRST_TRAINING_ID --submission SECOND_TRAINING_ID \
  --provisional-choice FIRST_TRAINING_ID
```

The service atomically locks both IDs before validation feedback is available.
The returned mapping `validation_ids` associates each original training ID with
its validation result ID. The phase progresses from training through locked
finalists and validation execution to completion, then `selected` after a valid
final choice. The lock closes new source
submissions. Duplicate IDs, ineligible sources, pending training, a second
nomination and additional validation batches are rejected. A nomination is not
a request to copy or modify local source.

Poll the phase and existing result endpoints without re-nominating:

```sh
python3 tools/service.py --mailbox mailbox validation-status
python3 tools/service.py --mailbox mailbox result --submission VALIDATION_ID
python3 tools/service.py --mailbox mailbox replay --submission VALIDATION_ID \
  --game 0 --start 1 --end 5 --offset 0 --limit 64
```

Validation results and replay receipts bind to the original source identity.
Use **validation IDs** for validation result/replay requests; original training
IDs retain their original training evidence. Replay pagination and evidence
limits remain unchanged. Preserve both finalists' results, including negative
evidence; do not combine training and validation scores into an independent
held-out estimate.

After both validation runs complete, choose one of the **original training
finalist IDs**:

```sh
python3 tools/service.py --mailbox mailbox select-finalist \
  --submission CHOSEN_TRAINING_ID
```

Restore that exact submitted source from your own saved version into `src/`;
do not edit it or submit new code. Record the provisional and final choices,
training and validation evidence separately, and known test failures/limitations.
A decision not to switch is valid evidence. Selection does not erase a known
failure or establish correctness. Stop as required by the run protocol.

## Failure, persistence and access limits

Stop on infrastructure failure, halted service, or an uncertain request outcome.
The client does not automatically retry a lock or selection request. Never
attempt to recover by re-nominating, replacing a map/opponent, submitting a new
source, or launching matches outside the interface. The first validation failure
halts further validation; incomplete evidence is unscored, not converted into
wins/losses or silently repeated.

The service retains phase/accounting evidence and hash-chained receipts, but
**does not provide restart recovery**. A run root is exclusively owned and must
be fresh: starting again against an existing root is rejected. Persisted state is
for diagnosis/audit, not authority to replay uncertain operations. An interrupted
run requires a new, explicitly reviewed protocol rather than resuming or
repeating its matches.

The operator owns configuration and evidence outside the competitor workspace.
Only the public source-bound feedback interface is exposed. Cooperative same-user
filesystem restrictions remain procedural, not hostile-tamper protection or
proof that all out-of-workspace reads were prevented. No evaluator infrastructure
or private competitor material belongs in the candidate export.

Deadline-enabled runtime receipts retain the absolute deadline, the monotonic
sample used by the pre-launch guard, and a completion sample. When termination
is requested they also retain its request sample; a reaped-child sample and
boolean record that `wait()` returned. These application observations are not
exact OS process-spawn or signal-delivery timestamps. Refused launches are
explicitly marked `not_started` and never claim a reaped child. Legacy runtime
calls without the deadline flag retain their existing receipt format.
