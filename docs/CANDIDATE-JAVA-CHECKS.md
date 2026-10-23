# Cooperative candidate Java checks (future trials only)

The existing official adapter already returns nonzero on compilation or verifier
failure. This helper improves visibility, not that exit behavior: `build` emits
JSON with `compile_status`, `official_verifier_status`, and verified/failed
artifact status. A failed check is not a successful build. Do not chain a submit
with `;` when successful verification was intended as its precondition.

The trusted operator uses the existing pinned Nix development environment:

```sh
nix develop -c python tools/export_candidate_check.py --workspace /absolute/candidate
```

The root must exist. Export refuses existing tooling/launcher paths. It copies
only this helper, the public adapter and artifact utilities, records their hashes
and pinned Java/compiler/engine paths and hashes, and creates `candidate-check`. The launcher verifies its embedded runtime hashes
on every invocation (drift detection, not same-user tamper protection).
No opponent or evaluator data is exported. The candidate needs only its own root
and the launcher; it need not inspect the service or any sibling workspace.
Within that root:

```sh
./candidate-check build --source src --package mybot --output build-001
./candidate-check test --build build-001 --tests tests --main-class checks.Main --output test-001
```

Use a fresh output directory for every invocation; outputs cannot live inside
input trees. All input/output paths are resolved and required to stay within the
exported candidate root (including symlink resolutions). Java source inventories
reject symlinks. This is a cooperative interface constraint, not a filesystem
sandbox: arbitrary same-user code can still read or write outside that root.
The exported launcher/tooling and manifest are not hostile-tamper resistant.

## Actual Java semantics, limited claims

Tests are candidate-authored `.java` files with an ordinary `public static void
main(String[] args)`. They may call actual methods in the candidate's verified
classes and use the public API. Java assertions are enabled. Throw an exception
or exit nonzero on failure; a test that merely prints a failure message and exits
zero completes execution successfully; it does not establish that a claim was
checked. No game-strategy fixtures or suggested assertions are provided. The test main must be compiled from the test inputs, and test classes
cannot replace candidate or public engine/API class names. Each Java process
runs with its fresh output directory as its working directory.

The runner validates the verified build's source/binary inventory and pinned
engine identity, snapshots candidate classes and test sources, compiles tests,
and executes the actual Java classes. It records test source and binary hashes,
class name, candidate source/binary and engine hashes, subprocess status and log
hashes. Input build and snapshots are checked again afterwards. Tests run on an
**uninstrumented JVM**: successful execution establishes only that those Java tests
exited successfully, not Battlecode instrumented/bytecode/engine equivalence, test
coverage, strategic competence, or match performance. No scrimmage is launched.

Every compiler/verifier/test process has a 256 MiB heap, 64 KiB combined log cap,
and 30-second default wall timeout (configurable 1–120 seconds per phase). A build
has at most two phases; a test has at most two. Timeout/output overflow kills the
process group and returns a nonzero check result with explicit phase status.
Ambient JVM-option/classpath variables are removed. Heap is not total native
memory or filesystem quota; detached malicious descendants are not contained.
Retained outputs include logs, snapshots, manifests and `check.json`. Build checks
also retain the official-format `artifact.json` and official verifier result.
This adds no service operation and changes no strict adapter or existing trial.

## Validation

`python -m unittest discover -s tests -p test_candidate_check.py -v` runs the
focused suite. Actual Java integration cases require all three trusted runtime
environment variables (`BCENV_ENGINE_JAR`, `BCENV_JAVA`, `BCENV_JAVAC`); without
them those cases explicitly skip rather than claiming Java was executed. The
existing Nix development shell provides these. If a host cannot instantiate the
development shell, the operator can use `nix shell .#python .#jdk -c ...` with
those exact paths from the restored pinned runtime; this is not a reason to
replace the JDK with a system installation. The operator, not the competitor,
prepares the export environment.

## Relocated Nix stores / native-agent launch

An exported canonical `/nix/store/...` interpreter may only exist inside the
operator's Nix execution wrapper. A successful test *inside* that wrapper does
not establish that a separate native-agent command can launch it. For this case,
the trusted operator can supply `--launch-prefix-json` to the exporter: a JSON
array containing an absolute executable path and fixed arguments. For example,
use the existing trusted Nix wrapper, `shell`, `--offline`, the exact restored
Python and JDK store paths, and `-c`. No flake lookup or system JDK fallback is
needed when those closures are already restored.

The exporter then creates a `/bin/sh` outer `candidate-check` launcher which
quotes each fixed argument, appends the canonical inner launcher, and forwards
candidate arguments unchanged. The candidate cannot supply or override the
prefix, workspace, or runtime through supported CLI options. The manifest records
the prefix, prefix-executable hash, and both launcher hashes. The outer shell
records but does not enforce the prefix-executable hash; runtime hashes are
still checked by the inner launcher. These records are drift/provenance evidence,
not hard protection against same-user changes to scripts or their dependencies.

After exporting inside the runtime, the operator must invoke `candidate-check`
from an independent ordinary execution context and verify a generic build and
actual Java test succeed. Keep this smoke source/output separate from the fresh
competitor workspace. Export into that fresh workspace only after the check
passes. Prefix and wrapper failures propagate nonzero; the helper does not retry
or install a fallback runtime.

The Nix `python-unit` CI check supplies these pinned runtime variables, so actual
Java integration tests execute there rather than silently skipping.

## Execution receipts and claim evidence (future exports)

Java-test results use `bcenv.candidate-check.v2`. A zero-exit test main with
unchanged snapshots reports `status: "execution_succeeded"`, not `"passed"`.
`execution_status` records `not_run`, `succeeded`, or the subprocess failure
status. `assertions_enabled: true` means the runner is configured with `-ea`; it
does not mean any assertion ran (and the JVM may not have run at all). Every
receipt reports `assertion_coverage: "unknown"` and
`correctness: "not_established"`. Empty mains, printed success messages and real
successful assertions cannot be distinguished as correctness evidence from exit
status alone. A failed assertion, compilation failure, timeout, output overflow or
integrity failure still produces nonzero. Build receipts remain v1. Existing
exports and historical results are unchanged; v1 `passed` meant execution only.

For each correctness claim in an experiment report, provide a concise inventory:

| Claim / scenario ID | Expected condition | Executable check | Observed result | Evidence and limitation |
| --- | --- | --- | --- | --- |

Identify the actual assertion or explicit failure condition and its test source
location; cite the receipt, exact tested source/binary hashes and relevant logs.
Distinguish a printed observation from an executable check. If no check ran, mark
the claim **untested**; if an output was inspected manually, label that as manual
observation rather than an asserted outcome. Record failed and incomplete checks
as well as successful executions. Do not infer per-scenario success merely from
the overall test process exit code. This inventory is candidate-authored evidence
for review, not machine-verified coverage or a trusted correctness oracle. The
helper does not count assertion syntax, validate claim inventories, generate test
scenarios, or supply bot strategy.

## Local failure evidence and prospective stopping policy

Local checks are development experiments, not scrimmage admissions or final
validation. An ordinary compile rejection, assertion failure, or local timeout
need not by itself terminate a future trial **if** the fixed prospective protocol
allows revision, the operation has a retained terminal result, owned direct-child
cleanup is positively evidenced, and there is no uncertain accepted service effect.
The author may choose a supported revision or distinct local diagnostic within the
same clock and unchanged helper limits. Preserve the failed attempt. Do not
blindly repeat the identical timed-out operation, erase failures, or treat more
waiting time as a correctness fix. No extra operator acknowledgement is required
for each ordinary, fully observed local failure.

A receipt containing `execution_error`, `cleanup_failed` or `launch_error` is a
helper/infrastructure error, not an ordinary compiler rejection; positive child
cleanup alone does not turn it into a usable local experiment. Stop and report it.
An interrupted/BaseException path may produce no terminal receipt and does not
claim that every cleanup stage completed. Treat that as unknown, not permission
to continue.

Stop on unknown child/helper ownership or terminal outcome, uncertain service
requests, service infrastructure_failure/run_halted, or deadline expiry. UNKNOWN
scrimmage jobs and incomplete validation retain their no-retry/no-substitution
rules. This distinction is prospective: it does not reopen a stopped experiment,
authorize a replacement author, modify source strategy, or change any budget.

A bounded local `output_limit` result is also a failed local experiment, not an
implicit helper/infrastructure failure, provided the same positive wait/cleanup,
external helper terminal and no-uncertain-service-effect conditions hold. Preserve
the truncated log and limit result; the output cap is unchanged and is not bypassed.

Local phase receipts retain requested timeout, effective compiler/JVM command and
owned cleanup observations. Caller invocation/options are separate from the
injected heap-bounded command. A successful direct-child wait is positive evidence;
exit -9 or a `timeout` status alone is not. The helper's prepared-return receipt is
written before it exits and **cannot prove its own future process exit**. Retain
the actual tool/helper terminal observation as well. A normal scrimmage-service
stop is not evidence about a separate local helper. Group cleanup is cooperative
owned-process cleanup, not arbitrary-descendant or kernel containment.

The default remains30 seconds per phase, explicit range1–120; heap256MiB and
64KiB output limits are unchanged. Local checks do not inherit the service's120s
build limit or absolute deadline; candidate work remains governed by the original
trial wall clock. Tests still establish only observed execution/assertion evidence,
not game semantics or general correctness. No automatic retry is introduced.
