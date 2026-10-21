# Prospective experiment-note profile

This is an optional **future-run** extension to the cooperative local-trial
service. It does not change BCENV-004's evidence or certify earlier predictions.
It records what an agent submitted before seeing that submission's results;
it does not judge whether a prediction is useful, novel, honest, or causal.

## Enable explicitly

Set `require_experiment_notes: true` in a new trusted run configuration. The default
is false, preserving the legacy submit protocol. `max_notes` defaults to 24 and
must be 1–32. Each note has at most 4096 UTF-8 bytes and eight distinct references.
The run root and mailbox must be fresh. There is no state restoration/resume
support; restarting with a new root establishes a new run, not continued order.
Neither the frozen BCENV-004 runtime nor any active competitor is updated.

## Client workflow

1. Write a UTF-8 prediction file before submitting the proposed source.
2. Send a note using the same source directory/package intended for submission:

   `python3 local_trial_client.py --mailbox BOX note --source src --package bot --text-file prediction.txt`

   Add `--replay-reference r_...` for each relevant earlier server-issued replay
   reference. Zero references are allowed, including the first submission.
3. Retain the returned `note_id`, `note_sha256`, `source_sha256`, and audit receipt.
4. Submit unchanged source with both binding arguments:

   `python3 local_trial_client.py --mailbox BOX submit --source src --package bot --note-id n_... --note-sha256 HASH`

   A changed source/package, mismatched hash, missing/unknown note, or reused note
   is rejected before scheduling work. Once accepted, the note cannot authorize a
   second submission, including after a compilation rejection. A new prediction
   note is required. Notes cannot be replaced, edited, or deleted through the API.
5. Result receipts retain the note binding and original submission audit receipt.
   Replay responses in this profile additionally return `replay_reference`.

HTTP uses the same payloads and ordering as the mailbox. Both transports return
closed errors without copying arbitrary paths from exceptions. Clients never
automatically resubmit after uncertain timeouts.

## What is bound

The note request carries `operation`, `package`, `source_zip_base64`, `text`, and
`replay_references` only. Source ZIPs pass the existing bounded normalized-source
validator. The server calculates the source digest itself; it does not trust a
client-supplied digest. The normalized digest includes package and all source file
paths, sizes, and hashes, while ignoring ZIP timestamp/compression/order.

A note gets a random opaque ID and a `note_sha256`, computed over canonical JSON
of exactly `note_id`, `source_sha256`, `package`, `text`, and the resolved prior
`replay_references` objects. Canonical JSON is UTF-8 with sorted keys, compact
separators, default JSON ASCII escaping, and no NaN/infinity. Its separate audit
receipt binds this content to the server order and time.

A replay reference binds an opaque reference ID to the prior source submission
ID/source hash, game index, frozen replay digest, exact requested query, sanitized
feedback digest, and audit receipt. Unknown, future, duplicate, or more than eight
references are rejected. References belong to the current in-memory run; IDs from
another run cannot be imported. References acknowledge a specific returned page,
not that the agent understood it or that the whole replay was inspected.

All dispatch and audit changes are serialized under the service lock. A replay
reference must have been issued before the note operation can accept it. A note
must already exist before submission; the returned strictly increasing audit
sequence proves that note < submit < completion. Wall-clock timestamps are
informative but need not be monotonic. The sequence remains authoritative if the
clock moves backward. Later reads do not rewrite original receipts.

## Audit evidence and limitations

Each appended audit event has `sequence`, `time_ns`, `previous_sha256`, `operation`,
`response`, and `event_sha256`. The event digest is SHA-256 of canonical JSON of the
first five fields, excluding `event_sha256`. The first previous digest is 64 zeroes;
subsequent entries point to the last appended event. The file is flushed and fsynced
before a receipt is returned. An audit write failure halts new work; a partially
written final line can remain and must not be presented as valid evidence.

This is append-only through the service, **not adversarial tamper resistance**.
Agents share the same OS user and filesystem; a process that ignores cooperative
rules could alter both data and hashes. Independent observers must preserve/freeze
receipts and source artifacts, audit tool use, and report limitations. No external
timestamp authority, signature, rollback resistance, persistent authentication,
or production isolation guarantee is added. No hidden evaluator material belongs
in notes. Held-out evaluation remains separate after the competitor is stopped.

## Verification

Synthetic tests cover ordering and hash-chain recomputation, exact source binding,
missing/unknown/reused notes, package/source changes, bounded text/references,
closed fields, immutable returned copies, prior replay binding, clock regression,
audit failure, and legacy-profile compatibility. They do not demonstrate a real
model's prospective reasoning or improve any previously recorded trial.
