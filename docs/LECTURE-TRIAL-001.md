# Lecture-opponent diagnostic 001

Implementation/evaluation notes, separate from the approved full experiment plan.
This branch depends on the unmerged bounded-replay-query PR5 (4d9813ac022a4be94686cf7d39cdb40edb5eec33).
It neither merges that PR nor claims replica/clean-room acceptance. Planner PR10
remains a reviewable process proposal, not a prerequisite implicitly approved here.

## Preregistered before launch

**Question:** Does a fresh Sol/high agent use a discoverable real opponent to perform
an independently authored, rule-grounded, falsifiable learning loop and write
correct, understandable code? This is not another model-selection experiment.

**Environment intervention:** replace the previous kickoff's mandatory inert
opponent with a source-only catalog of the authentic official BC23 lecture bot.
Require independent provenance/retrieval/build, initial rules/behavior plan,
paired baseline and focused retest, bounded replay diagnosis, and honest selection.
No candidate code, tactical recommendation, diagnosis, or solved improvement is
supplied. Retain the pinned engine 2.0.3 and unmodified bounded query tooling.

**Source:** [official Battlecode lectureplayer](https://github.com/battlecode/battlecode23-lectureplayer/tree/57063ad07b2311c21c99ba7be100a55fcdb3de6b),
commit 57063ad07b2311c21c99ba7be100a55fcdb3de6b, 2023-01-20T02:46:15Z.
The repository LICENSE is AGPL-3.0. Six lectureplayer Java files and LICENSE are
individually hash-pinned in seasons/2023/lecture-opponent.json. The source-only
mirror retains LICENSE and origin metadata; upstream bot code is not relicensed
as bcenv GPL-3.0-only. Source is kept separate from the candidate/tooling.
The user's battlecode2023 checkout contains an older three-file lectureplayer,
so it is not silently substituted for the final official revision.

**Discovery boundary:** local catalog discovery and integration of frozen official
source, not unaided internet search. Builder verifies source and tool readiness
before freezing; competitor receives no prebuilt opponent or audit-game results.
This is a deliberately disclosed reproducibility constraint.

**Configuration/budget:** requested gpt-6.1-sol/high, fresh no-history native agent,
25 minutes, at most 12 sequential games; expected eight (initial/changed ×
DefaultMap/SmallElements × A/B). Preserve visible launch metadata and exact kickoff
bytes/hash. Effective hidden model implementation is not independently measured.
If setup fails, stop and preserve attempt; repair environment only before a fresh
attempt, never coach the running competitor or edit its source. Maximum two fresh
attempts in this iteration, and a new freeze/explicit failure record before retry.

**Observer holdout:** after competitor freezes its choice, run initial and selected
verified artifacts against the same opponent on Rectangle, both sides, at most
four games. Do not disclose this map or results to competitor before completion.
A selected initial candidate means identical build identity, not invented change.
Holdout tests transfer of behavior, not general strength or a statistical ranking.

## Evidence and assessment, in priority order

1. Discovery/integration: identifies official year/version/license, retrieves exact
   unmodified package, independently builds/verifies it, keeps identities separate.
2. Rules comprehension: cites applicable rules/API and states important assumptions;
   review code against them, including state, legality checks, exceptions, resource
   usage and execution limits. Inspect **all** authored source and both snapshots.
3. Experiment quality: pre-change recorded prediction with observable metric,
   rejection criterion and paired comparison; focused change maps to hypothesis;
   no post-hoc rewriting or sole reliance on win count.
4. Replay reasoning: exact source/build/slot lineage, round/event evidence, query
   coverage/truncation, correct global-vs-local and net-resource semantics;
   separates observation from causal explanation and reports contrary evidence.
5. Code quality: understandable decomposition, explicit state transitions, minimal
   focused diff, correct boundaries and failure handling; enumerate real bugs and
   test gaps even if compiler/verifier/games succeed. Do not infer hidden reasoning.
6. Honest outcome: packages selected verified source, explains accept/reject choice,
   preserves failures/limitations and does not claim tournament strength.

Rate each dimension demonstrated / partial / not demonstrated with artifact
citations, not an invented numerical score. A successful workflow is not proof
of good code; a loss or rejected hypothesis is not workflow failure. Report active
opponent spawning/movement/actions from replay rather than assuming activity from
its name. Wins and final rounds are contextual only. Opponent defects and fixed
map seed/determinism can constrain interpretations; no seed-sweep claim.

**Integrity:** freeze release hash manifest and kickoff before launch; record source
and replay hashes; audit every non-runtime release file afterward. Preserve exact
native reports/observable decisions. No private chain-of-thought is requested or
scored. Agent-wide boundary is behavioral. Runtime uses credential-free isolated
namespace/chroot with no host binds; this is not an untrusted-code security claim.

## Results

Pending launch. Do not interpret this preregistration as completed evidence.
