# Local pilot 001: initial bot and learning-loop evidence

**Outcome: local diagnostic workflow passed.** A fresh no-history agent authored
its own initial BC23 bot, built and verified it, played both sides against the
declared inert fixture, recorded a prediction, changed its own candidate, tested
both sides again, and packaged selected source. It received no mid-run coaching.
This is not a full replica or security-enforced clean-room acceptance result.

## Frozen inputs and scope

- Launch: 2026-10-16 21:39:30 UTC. Agent reported completion around 21:42 UTC;
  independent artifact audit completed at 21:44 UTC.
- Kickoff: `prompts/2023/local-pilot.txt`, SHA-256
  `6191901bbd82fcd850b8cfbfd01147500c0b9463e03e4ea7c4f6854941aaaa7f`.
- Runtime package: `/nix/store/zrr2chl185cs2fg46knlhkk9nqgwh1pc-bcenv-2023-0.1.0`.
- Engine: official BC23 2.0.3; map: packaged DefaultMap; explicit A/B order.
- Four sequential games, within the declared 12-game/20-minute limit.
- Native fresh agent session; actual model ID and effective reasoning effort
  unavailable in returned platform metadata. No Codex CLI runner was used.
- Agent-wide isolation was behavioral. Runtime tool invocations used a minimal
  credential-free user/network-namespace chroot without host mounts.

## Observed results

| Candidate | Candidate slot | Winner slot | Final round | PLACE_ANCHOR events |
| --- | --- | --- | --- | --- |
| Initial build | A | A | 361 | 5 |
| Initial build | B | B | 494 | 5 |
| Agent's changed build | A | A | 201 | 5 |
| Agent's changed build | B | B | 193 | 5 |

The agent predicted that its own change would produce island-capture wins before
round 300 in both side positions. Both later games met that prediction. Engine
logs separately confirm victory by capturing 75% of sky islands. The exact
prediction and interpretation remain in the preserved competitor work. The
prediction file's timestamp precedes the selected build; this supports ordering
but is not a trusted append-only observer journal.

The opponent's headquarters freeze on round 1 because its official fixture
method returns. These wins establish functioning bot behavior and tool use,
**not competitive strength**. No active opponent, map suite, seed suite or
statistical strength estimate was tested. The builder did not select or write
the competitor's strategy or its code change.

## Independent artifact audit

The observer read artifacts without modifying competitor files or running more
games. It checked:

- Zero hash mismatches among the frozen release inputs.
- Source/binary inventories and successful compiler/official-verifier exits for
  both candidate builds and the fixture.
- Four complete result records; zero engine/extractor exits; matching replay and
  summary SHA-256; source/binary-to-slot identity and winner consistency.
- Selected source ZIP bytes equal the current candidate and the verified source
  snapshot; metadata identities agree.

Selected source-tree identity:
`aaa110faf6ec5be8ee3f6151922013b391e0544da5b96c555e57490e7b4e706e`.
Selected binary-tree identity:
`5f6c48a3b7c3b9630e87e703eb4eefd7fc65b67539ba997d7e9ea48ad84440ed`.
Selected source ZIP:
`ef5717b10fd719959f6cdde6e584e87b50e48b383d836869f696afbc3de2b142`.
Private retained archive (kickoff, release identity, workspace, code and evidence):
`7831b7b385353120f3310a0a61efb26929c569dd23dd4d1d306fee5025332557`.
The candidate source and archive are retained privately, not published by this
implementation PR. Logical deliverable paths inside the archive:
`pilot-001/root/workspace/competitor/src/mybot/RobotPlayer.java`,
`pilot-001/root/workspace/work/package-001/source.zip`, and
`pilot-001/root/workspace/work/final-report.md`.

## Environment findings and next gates

The agent succeeded without changing the runtime. It identified stale packaged
skill references to an absent year manifest, trial specification and local-tools
document. START_HERE and the explicit local-only kickoff supplied enough context
for this pilot, but a future export should resolve every referenced instruction
rather than relying on that override. The preserved package remains unchanged.

Replay metrics validate only extracted fields. Bounded samples are incomplete;
net resource deltas are not gross collection, and action counts are not inferred
strategy quality. Robot stdout is disabled; success does not prove absence of
caught bot exceptions. The JVM primordial-thread-stack warning was nonfatal.

Official viewer validation, full replay state reconstruction, actual Galaxy
submission/queue/worker/callback execution, stronger agent isolation, recorded
model configuration and full acceptance trials remain open. This result answers
the narrower question: the supplied local tooling enabled a fresh agent to
start implementing and testing its own BC23 bot.
