# Synthetic worker lifecycle: independent review

Review date: 2026-10-20 (UTC)

## Result and scope

No remaining must-fix findings in this synthetic lifecycle component. Reviewed
on `feat/synthetic-worker-lifecycle`, based on `8f2125d`: the new worker bridge,
its tests and contract, and the small `DurableLedger.check_reservation` addition
with its contract note. No implementation files were edited by the reviewer.

**A fabricated receipt can produce a synthetic `complete` record.** The tests
demonstrate that behavior deliberately. Matching job, generation, source and
specification identities proves consistency only. It does not establish
authenticity, source execution, validated game evidence or bot strength. This
review does not approve deployment or real untrusted-code execution.

## Verification

- **200 repository tests passed** in the reviewer's final pinned-Nix run,
  including 19 new worker-lifecycle tests; runtime was 1.672 seconds.
- **12 independent probes passed** in a separate derivation using the same
  pinned Nix Python. They cover the exact 4,096-byte receipt limit, escaped
  duplicate keys, byte subclasses, deep/large malformed values, cross-job
  receipt replay, exact identity/type binding, private error projection,
  ownership, expiry, clock rollback and source-integrity changes.
- Reservation/precheck failures skip the callback. Uncertain reservation,
  precheck and finish operations propagate their errors without automatically
  retrying, invoking an extra callback or replacing an uncertain committed
  result with a manufactured failure. Both pre-commit and post-commit finish
  failures were probed.
- The independent race probe writes a terminal result after precheck but before
  callback entry. The callback still runs, as explicitly documented; its later
  completion cannot overwrite the existing terminal result.
- Source corruption before precheck prevents callback entry. Corruption during
  the callback prevents score publication; later expiry recovery produces an
  unscored infrastructure failure. These tests simulate trusted-storage damage,
  not protection against a malicious database owner.
- Staged and unstaged `git diff --check` passed. No game, compiler, provider,
  credential, network or host-security integration was exercised.

## Contract assessment and remaining gates

The contract accurately preserves the key distinctions:

- Precheck is a point-in-time observation, not a held lock, lease renewal or
  physical-resource admission guarantee. The check-to-callback race remains.
- `finish` is the authoritative post-callback fence. The bridge does not preempt
  a blocked callback, kill worker processes or prove physical-resource release.
- Ordinary callback exceptions and invalid receipts become bounded unscored
  infrastructure failures. Ledger errors remain outside that normalization.
- There is no automatic retry, scheduler, durable receipt delivery or recovery
  of callback execution. Uncertain writes require explicit state reconciliation.

Authentication, actual worker supervision, isolated source execution, immutable
build/match/replay validation, resource limits, replay publication and authorized
end-to-end trials remain unpassed integration gates. Real-launch and heldout
gates remain closed. No full learning-loop or production-security claim follows
from these tests.

## Reviewed SHA-256 identities

- `services/worker_lifecycle.py`:
  `a1521bd8f915a96fed423391d3d70659ef49a7a390bda95c560bf1446d3b8ead`
- `services/ledger.py`:
  `bb205424fb12ad9528d8c8c03be90a04343b78f6c75ed4c2fa13957036d3dbbb`
- `tests/test_worker_lifecycle.py`:
  `f86724740dbf28cd33ca0e757919b9101aa3da625fdcd4856845cc6add62dc88`
- `contracts/worker-lifecycle-v1.md`:
  `5a95a7b543adc0791fd4e8053c16ea513af771cd1f88f963206d33ebbcef804d`
- `contracts/ledger-v1.md`:
  `4e565cceecc778ff52468ba1e427fc6f765a278b18a0f6f92a7a107b10aa583b`
