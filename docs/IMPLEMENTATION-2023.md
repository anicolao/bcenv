# BC23 implementation log

This log belongs to **bcenv implementation**. The plan was reviewed and separately rebase-merged at [bc-planner PR #8](https://github.com/anicolao/bc-planner/pull/8),
reviewed head `607bb821b4ff27dd10e58f64249e8a8df8e8d682`, merged main
`7bf66b0122e06432ca53f43443f5bfe071335720`. Its substantive text was unchanged. Implementation started under the
separate October 16 instruction recorded verbatim in PROMPTS.md; the subsequent explicit approval is also recorded. Neither instruction authorizes
merging this implementation PR.

## Bounded milestone

Deliver a pinned Java-only BC23 local foundation: year-neutral artifact/process
helpers, BC23 build/verify/package/run adapter, real inert-fixture game evidence,
headless replay evidence, discoverable skills and the proposed exact kickoff.
Inspect actual historical Galaxy contracts and make the next replica gates
concrete. Builders do not create a strategy or candidate. A full acceptance trial waits for complete environment readiness. The subsequent
request for an initial bot authorizes a separately labeled local-only diagnostic
pilot, described below.

## Ownership and reuse

- Base: runtime/artifacts.py and contracts/artifacts-v1.md.
- BC23: seasons/2023, skills/2023, prompts/2023; candidate work stays separate.
- Common workflow skill: skills/common.
- Plan/research/evaluation remain in bc-planner.
- Reuse the exact nixpkgs lock from [bcenv PR #3](https://github.com/anicolao/bcenv/pull/3)
  (`0154877b28ff38ee2b757216d0368fb6d1a0af81`) while leaving its image branch
  unmerged. Its VM supervisor/persistence work is complementary, not replaced.
- This branch starts from main `7f868dc5`; it does not implicitly approve image
  deployment, year forks, permissions or any paid infrastructure.

## Evidence

All six x86_64-linux `nix flake check` outputs passed after the final websocket
fix: pinned engine/version/fixtures; 11 common/adapter Python tests; 11
source-bound Galaxy contract tests; synthetic replay tests; offline npm/Husky
prompt-enforcement tests; and real fixture games. The fixture check compiles and
verifies both disclosed packages, plays a 2,000-round match, repeats with equal
normalized replay summary, swaps slots, packages source, and rejects malformed
Java with persisted failed-build evidence. These inert fixtures freeze when
returning from run(); they are not meaningful strategic opponents.

Review found and fixed the historical scaffold's wrong map-path property
(`bc.game.map-path` is the actual engine key), JVM stderr contaminating machine
JSON, weak replay-to-input identity binding, and binary mutation/nested-output
hazards. Runtime materialization preflight found the official default websocket
queue could overflow even in headless mode; explicitly disabling the unused
websocket yielded a completed game in the exact network-isolated pilot runtime.

The canonical workspace-root Nix build uses the existing approved bootstrap and
an empty sandbox-paths setting, leaving sandboxing enabled and adding no host
bind mount. All package acquisition is fixed-hash Nix. Worktree npm ci used the
Nix-built cache offline and enabled normal Husky hooks; npm test passed with the
existing host Node. The pinned Node loader wrapper cannot fork the Node test
runner correctly outside canonical paths; the Nix sandbox's pinned Node test
passed, rather than treating the host pass as its substitute. aarch64 output is
declared but not executed. GitHub CI must still pass for the published commit.

## Local-only diagnostic pilot

The user asked whether the tests actually enable an LLM to implement an initial
BC23 bot. `prompts/2023/local-pilot.txt` is a new bounded kickoff for that narrower
question; the full proposed kickoff remains unchanged. The pilot gets only
packaged rules/API/tools/skills and fresh candidate/work paths. It aims to author
an initial legal bot, run both sides against the inert fixture, perform one
prediction/change/replay check and freeze source, within 20 minutes/12 games.
No replica submission is required or available for this pilot.

`scripts/materialize-local-pilot.py` exports a verified runtime with a minimal
credential-free filesystem, Python/JDK closure and no historical planning or
implementation notes. Tool commands execute in a user/network namespace chroot
with no host mounts. The agent's general filesystem/tool access is restricted
by instructions only: this is **behavioral agent isolation**, not a full
security-enforced clean room. No autonomy or strength result is claimed from
builder fixture tests.

The coordinator launched a no-history competitor at 2026-10-16 21:39:30 UTC from
the frozen materialization. Pilot kickoff SHA-256:
`6191901bbd82fcd850b8cfbfd01147500c0b9463e03e4ea7c4f6854941aaaa7f`.
Runtime package: `/nix/store/zrr2chl185cs2fg46knlhkk9nqgwh1pc-bcenv-2023-0.1.0`.
Builder preflight evidence was removed from the agent-visible filesystem before
launch and preserved separately. No mid-run edits, coaching or rescue are
permitted. Results are pending and will be reported separately after termination.

## Deliberate staging and remaining gates

Both local own-code games and API-scheduled scrimmages on the 2023 Galaxy replica
remain mandatory end goals. This slice does **not** call a local queue a replica.
The historical backend/Saturn interfaces have real cloud dependencies that need
local adapters; see [REPLICA-2023.md](REPLICA-2023.md). There is no submission or
scheduling command in this release and no public competition contact.

The official viewer, full replay world reconstruction, backend migrations and
synthetic teams, real queue/storage/callback integration, crash/redelivery tests,
local-versus-queued parity, security isolation, frozen trial budget/spec and
fresh-agent acceptance trials remain pending. The packaged kickoff is the exact
proposal, not evidence that all referenced capabilities exist. The manifest
explicitly blocks launch until those gates pass.

The first release packages base/year paths together in bcenv to avoid making
unreviewed repository/fork access decisions. Logical boundaries are explicit;
a separately pinned year fork and clean materialization remain review/integration
work. Cross-year reuse is an interface design goal, not yet an empirical result.

### Pilot model disclosure and publication

The local pilot uses a fresh native agent session, not a Codex CLI invocation.
No model/effort override was selected. The actual model ID and effective effort
are not exposed by this platform's spawn/status result and are recorded as
unavailable, not inferred from inherited defaults. The public implementation
branch/draft PR was separately approved; publication does not authorize merging.
