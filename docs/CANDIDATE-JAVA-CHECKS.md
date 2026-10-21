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
zero passes this runner. No game-strategy fixtures or suggested assertions are
provided. The test main must be compiled from the test inputs, and test classes
cannot replace candidate or public engine/API class names. Each Java process
runs with its fresh output directory as its working directory.

The runner validates the verified build's source/binary inventory and pinned
engine identity, snapshots candidate classes and test sources, compiles tests,
and executes the actual Java classes. It records test source and binary hashes,
class name, candidate source/binary and engine hashes, subprocess status and log
hashes. Input build and snapshots are checked again afterwards. Tests run on an
**uninstrumented JVM**: passing establishes only that those Java tests exited
successfully, not Battlecode instrumented/bytecode/engine equivalence, test
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
