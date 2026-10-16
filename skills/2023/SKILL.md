---
name: bcenv-2023-local-tools
description: Build, verify, package and run own BC23 sources using the pinned engine.
---

# BC23 tooling

Use `nix develop` or `nix run .#bcenv -- ...` from the pinned release. Java 8,
engine 2.0.3, rules, Java API, maps and inert builder fixtures are packaged by the
flake. No Gradle `update`, live release lookup or direct installers are needed.
Inspect the manifest and `docs/BC23-LOCAL.md` for paths and limitations.

Put your own Java source only under competitor/src/, with a package directory
containing RobotPlayer.java. Build with an explicit package and a new output:

    bcenv build --source competitor/src --package mybot --output work/build-001

Only a `verified` artifact may be run or packaged. Compiler and official verifier
logs are in the build directory. Fix your own source when compilation fails;
never modify the engine or supplied fixtures. A failed verifier is not a loss.

    bcenv run --team-a work/build-001 --team-b work/opponent-build --map MAP.map23 --output work/match-001
    bcenv package --build work/build-001 --output work/frozen-001

Run a second match with A/B inputs explicitly swapped; the runner fixes
alternate-order=false for each one-map invocation. Keep slot identity when
interpreting winner. The replay is privileged global evidence, not necessarily
what a robot could observe. Cite event and round references, and consult the
extractor's completeness flags before interpreting missing metrics.

The private replica and official viewer are not available in this first
foundation release. Submission/scheduling has no usable command yet. Do not
contact the live competition site or invent endpoint access. This blocks a full
fresh-agent trial; consult the implementation status rather than claiming the
kickoff complete.
