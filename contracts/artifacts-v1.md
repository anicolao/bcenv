# Artifact contract v1

The common runtime supplies canonical JSON identity (UTF-8, sorted keys, compact
separators), SHA-256 file inventories, append-new output directories and bounded
subprocess execution. Season implementations own compilation, verification,
engine configuration and replay semantics. A second season has not yet validated
this reuse boundary.

Every artifact record carries `schema: bcenv.artifact.v1` and `kind`. Build records
link source and binary inventories and identities to the engine digest, package,
exact compiler/verifier commands, exit codes and logs. Package records link a
normalized source-only ZIP to those identities. Match records link explicit A/B
slots to source/binary identities, map/engine digests, exact config and process
outcome. Absolute paths in command/config records are diagnostic; they are not
portable artifact identity inputs.

Build states: `failed`, `verified`. Process states: `succeeded`, `failed`,
`timeout`. Match state must not infer a win from process success: without a
validated complete replay it is `replay_unvalidated`; execution failure is
`infrastructure_failure`, budget exhaustion `timeout`. Successful complete core replay extraction produces
`completed` and a winner slot. Completeness limitations remain in its summary. A post-run snapshot/hash mismatch produces `input_mutated` and clears the winner.
A failed game may still
retain a partial replay. Missing winner is null, never a loss. BC23 replay
summaries supply season-specific event evidence and completeness separately.

Directories must be new; retry into a different attempt directory. Inputs are
snapshotted before compilation. Existing artifacts are checked against their
recorded inventories before use. This is accidental-mutation detection, not a
signature or trust boundary: trusted fixtures/self-owned code only. An agent
with write access to both files and manifests can forge them. Scored runs need
observer-owned immutable records, isolated workers and protected environment
inputs; these are not established by this CLI.
