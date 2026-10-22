# Cooperative service launch parity

Use the **same outer interpreter, working directory and shared entry point** for
public integration and subsequent live launch:

```sh
python3 -m services.launch_local_trial --config TRUSTED_CONFIG.json \
  --root FRESH_SERVICE --mailbox FRESH_MAILBOX --evidence FRESH_OPERATOR_EVIDENCE
```

This operator-only launcher runs the fixed adapter command with `--help` before
creating a service root or mailbox. The probe inherits the exact working directory
and environment used by the service worker; no fallback interpreter, namespace or
security-setting change is attempted. It retains a combined diagnostic capped at
64 KiB and enforces a 30-second timeout, killing the process group on failure.
A failed probe exits nonzero before readiness. Evidence is outside the competitor
workspace. Same-user hostile descendants and host file access are not contained.

For live launch add `--expect-parity INTEGRATION_EVIDENCE/launch.json`. The launcher
checks its own code, service/runtime Python code, outer interpreter path/hash,
working directory, fixed worker command and executable hash, schedule, timeout, resolved match_timeout,
and notes mode. Run paths and quotas may differ; full configs are recorded so
those differences can be reviewed. These are provenance/drift checks, not hostile
tamper protection or a transitive closure signature. The ambient environment is
not recorded or compared: do not dump potentially secret environment values.
The probe and worker inherit one live process environment, but equality across
integration and live launches is not attested. Record nonsecret launch parameters
and retain the mandatory end-to-end gate; do not overstate the parity record.
The service uses the same
in-memory config/worker after probing, without a second wrapper or config read.

`--check-only` retains probe evidence without starting the service. **Neither a
passed probe nor parity match establishes successful end-to-end integration.**
The operator must additionally complete public-fixture submission, replay and
receipt checks through this same launcher, then review the evidence and protocol
CI gates before launching a fresh competitor. A record's `check_only` field
identifies probe-only runs; do not present them as integration evidence.

## Relocated Nix stores

A Nix-shell-wrapped outer service can prevent its worker from creating a second
private mount namespace. In the observed setup, the same pinned adapter `--help`
exited zero from the ordinary host Python context but failed inside the outer Nix
shell with `setting up a private mount namespace: Operation not permitted`.
Do not wrap only the live service in a different runtime from integration. Use
an already available host interpreter for the outer stdlib-only service where
validated, with the fixed Nix command owning the worker closure. This installs no
host dependency and does not disable sandboxing. Alternatively, validate another
consistent launch configuration before use; do not silently change one mid-run.

Regression suite: `python -m unittest discover -s tests -p test_launch_local_trial.py -v`.
It tests pre-readiness rejection, bounded diagnostics, timeout, snapshot reuse and
parity failures without games or candidate source. Real relocated-store nested
versus ordinary-context checks remain host-specific operator evidence.
