# Synthetic controller-to-ledger composition: independent review

Review date: 2026-10-20 (UTC)

## Result

No remaining must-fix findings in the reviewed synthetic composition. This is
not approval to execute untrusted code or deploy a public service.

Reviewed on `feat/controller-ledger-adapter`, based on `df7a20d`:

- `services/controller_ledger.py`
- `tests/test_controller_ledger.py`
- `contracts/controller-ledger-v1.md`
- Test-only SQLite cleanup in `tests/test_ledger.py`

Implementation SHA-256:
`738c263437acb3427b8543f4481622c9966a7ce1ee42897fbc899c7a2badc2ff`

Final test SHA-256 values:

- `tests/test_controller_ledger.py`:
  `1dd69392c4e4d94b4f71a94e89465dc0e8ddb097a37686417e2f8e6b2e0cc60d`
- `tests/test_ledger.py`:
  `ac85db99623772fcef925ca0281c226d5459a7894ae941b3a00f8d067a9c87b4`

Contract SHA-256:
`9665809c0b1a19027b9802fd9decac0dc0874f940f14466318bf6c675ba14dc4`

## Resolved finding

The adapter initially accepted a ledger configured for more than 128 games,
while the existing controller always projects at a 128-game cap. A valid larger
ledger result could consequently become `invalid_evidence` in controller output.
The adapter now rejects incompatible game caps at construction. Its regression
test and independent compatibility probe pass, and the contract documents this
restriction.

## Verification

- **181 repository tests passed** under the pinned Nix Python check, including
  21 composition tests. The final staged test-hygiene rerun completed in 1.672
  seconds with zero SQLite `ResourceWarning` messages. The reviewer inspected
  that log and verified the final pinned build succeeds.
- **11 independent probes passed** in a separate pinned-Nix derivation. Coverage
  includes a real child-process restart, required trusted recapture, recovery of
  the exact existing durable job, owner/run separation, malformed and guessed
  references, immutable snapshots, package mismatch, failed capture, forged
  stdout/path claims, concurrent normalized resubmission, quota behavior and
  closed exception projection.
- Duplicate content receives no cross-owner artifact-retention discount.
  Eight simultaneous same-run submissions of normalized-equivalent captures
  produce one durable job while charging eight submission attempts.
- Backend-controlled stdout and paths do not grant authority, even when they
  name a valid artifact already present in the store. The controller still
  requires issuance through its current loop.
- Staged and unstaged `git diff --check` passed. No implementation files were
  edited by the reviewer.

A test-only follow-up resolved the observed SQLite warnings by explicitly
closing helper/fixture connections while retaining their transaction context
managers. The reviewer verified the exact diff against the earlier reviewed
snapshot. The intentional abrupt-exit crash fixture remains unchanged. Production
code and its hash are unchanged, so the 11 independent probes remain applicable.

## Limits and unpassed integration gates

Principal/run strings and typed export objects are trusted context, not
authentication or evidence of isolated source capture. Process teardown,
filesystem/network isolation, resource enforcement and safe live source export
remain separate work. No real commands, games, providers, credentials or host
security changes were used for this review.

Artifact handles and issued-submission visibility are process-local. Restart
requires trusted recapture and submit; this does not restore a controller
conversation. Same-owner/run/content retries recover the same immutable job,
including terminal failures, and do not request a fresh evaluation. Artifact
creation, ledger commit and transcript delivery are not one atomic transaction.

Actual game-evidence validation, worker supervision, deployment authentication,
durable artifact/controller lifecycle and replay delivery remain unverified.
Replay stays withheld, and real-launch/heldout gates remain closed. The contract
accurately distinguishes this tested synthetic composition from a complete
learning loop or secure deployed service.
