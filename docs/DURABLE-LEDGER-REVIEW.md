# Durable synthetic job ledger: independent review

Review date: 2026-10-20 (UTC)

## Result

No remaining must-fix findings in the reviewed synthetic ledger scope. This is a
review of source retention, SQLite transactions, quotas, ownership checks and job
state handling. It is not approval to deploy or execute untrusted bots.

Reviewed files on `feat/durable-service-ledger`, based on `04c8159`:

- `services/ledger.py`
- `tests/test_ledger.py`
- `contracts/ledger-v1.md`

Reviewed implementation SHA-256:
`b98577bd5e558c2c59862041fd0b0a85172b0c815a08f532f06d5e44c2eb2a96`

## Findings resolved during review

- Global source retention now charges logical bytes for every accepted job.
  Physical deduplication no longer gives another owner a quota-based oracle for
  whether guessed source content is already stored.
- Initial database identity, schema and configuration checks now share a
  serialized transaction, preventing inconsistent observations during concurrent
  first opens.
- Clock high-water tracking preserves fractional observations and exact integer
  values. Regression commits terminal infrastructure failures and refuses work
  until time catches up, including after reopening the database.
- Out-of-range clock integers are checked before float conversion. A supplied
  clock value of either sign with magnitude `2**1024` produces `invalid_clock`.
  Malformed persisted clock metadata produces `storage_integrity`. These concern
  trusted clock/storage correctness, not public authentication or candidate input.

## Verification

- The pinned Nix `python-unit` check passed: **160 tests**, including 34 ledger
  tests. The final build log reports 160 tests in 1.577 seconds. The reviewer also
  reran the pinned build command successfully against the final staged files.
- **13 additional independent probes passed** in a separate derivation using the
  repository's pinned Nix Python. They cover owner lookups across queued,
  running and expired states; source-quota privacy; constructor serialization;
  simultaneous terminal reports; cross-owner running quotas; immutable detached
  results; malformed completion evidence; rollback persistence and fencing;
  subsecond and large-integer clocks; and malformed persisted clock metadata.
- The repository suite covers cross-process idempotency, concurrent pending
  quotas and cold initialization, abrupt process exit, transaction rollback,
  restart persistence, stale reports and a live worker that remains alive after
  database recovery but cannot publish a late score.
- Both staged and unstaged `git diff --check` passed.

No games, providers, listeners, credentials, heldouts or host-security changes
were used for this review. The reviewer did not edit the implementation files.

## Deployment gates remain unpassed

- Principal and worker strings are trusted synthetic context. Authentication,
  route authorization and protection of the process/database need independent
  implementation and testing.
- `validated_complete` remains an adapter assertion. Actual game/artifact
  validation and integration with execution, controller and replay delivery are
  not established by these tests.
- Database fencing does not terminate processes or release physical resources.
  Worker supervision, resource and network isolation, and fenced external
  artifact publication require separate enforcement.
- Production storage durability, disk/WAL capacity, backup recovery, transport
  streaming limits and operational clock guarantees remain unverified. The
  permanently closed real-launch and heldout gates remain unchanged.

The contract accurately describes these limits. No production-security,
historical-Galaxy parity, bot-strength or complete learning-loop claim is made.
