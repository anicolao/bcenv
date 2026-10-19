# BC2023 final-rules engine migration

## Decision and provenance

Use the official public **3.0.13** distribution for new competitive comparisons.
Its exact source tag resolves to
`9b890f83672c98b81cc153f618bd56e1ab6134b5`; the GitHub release was published on
2023-02-02. The released JAR embeds `3.0.13`. The source archive, engine JAR and
Javadoc JAR are independently fixed-hash inputs in `nix/battlecode23-pins.nix`.
This pins released artifacts; it does not demonstrate a bit-identical source
rebuild or certify the historical production deployment.

The official **3.0.14** release is labeled “Release for final tournament,”
published 2023-02-04, at `1a9e55f6a9c0204f1f5f5cf6d6c2cac0d1f108b6`.
Its public engine source differs from 3.0.13 only in `SPEC_VERSION`. The other
changed files update client/spec version strings and set `IS_PUBLIC: NO`.
Crucially, that workflow also injects `battlecode/private-maps` before building,
and uploads the result with private access. The official 3.0.14 Maven engine and
Javadoc endpoints returned HTTP 403 with valid TLS during the 2026-10-19 audit.
No attempt was made to bypass access controls. Accordingly, **public gameplay
source equivalence is not exact tournament-distribution or map equivalence**.

Tag 3.0.15 is a later visualizer change and still declares spec 3.0.14 in its
public engine source. It is not a cleaner replacement for the known public
3.0.13 artifact and matching source revision.

Primary evidence:
- [3.0.13 release](https://github.com/battlecode/battlecode23/releases/tag/3.0.13)
- [3.0.13 exact tag](https://api.github.com/repos/battlecode/battlecode23/git/ref/tags/3.0.13)
- [3.0.14 release](https://github.com/battlecode/battlecode23/releases/tag/3.0.14)
- [3.0.13 to 3.0.14 source comparison](https://github.com/battlecode/battlecode23/compare/3.0.13...3.0.14)
- [3.0.14 release workflow](https://github.com/battlecode/battlecode23/blob/1a9e55f6a9c0204f1f5f5cf6d6c2cac0d1f108b6/.github/workflows/release.yml)

## Why 2.0.3 results are not final-rule results

The public 2.0.3 to 3.0.13 comparison contains 61 commits. No public method
signatures change in `battlecode.common`: only `Anchor`, `GameConstants` and
`RobotType` have changes there. Source compatibility does not imply equivalent
behavior. Important effective differences, checked against source and released
constant values, are:

| Mechanic | 2.0.3 | 3.0.13 |
| --- | --- | --- |
| Launcher mana cost | 60 | 45 |
| Launcher damage | 30 | 20 |
| Launcher move cooldown | 15 | 20 |
| Carrier action cooldown | 8 | 10 |
| Amplifier mana cost | 30 | 15 |
| Standard anchor cost, each of mana/adamantium | 100 | 80 |
| Standard/accelerating anchor healing | 1 / 2 | 4 / 6 |
| Island healing reach | occupied island tiles | within distance squared 4 |
| Elixir conversion cost | 700 | 600 |
| Well upgrade cost | 1700 | 1400 |

The official changelog says launcher damage changed from 40; the actual pinned
2.0.3 `RobotType` source and released constant are 30. This comparison uses the
executed versions' values rather than copying that historical changelog claim.

Further fixes matter even when code compiles: carriers can attack with a held
anchor, damage uses inventory weight, and the attack consumes anchors; well
upgrading checks the well's resource type; cloud-sensing range is restricted
according to whether the sensing robot's current location is in a cloud.
The last correction is specifically the 3.0.13 fix. Island healing/boost
coverage and an accelerating-anchor invariant also changed.

The released map inventory grows from 32 to 103. All original 32 map bytes are
unchanged, including DefaultMap; 71 maps are added. A map pool must be frozen
explicitly rather than inferred from whichever engine directory is installed.

[Full source comparison](https://github.com/battlecode/battlecode23/compare/2.0.3...3.0.13).

## Replay compatibility and safeguards

The public schema sources did not change, and all **21 generated schema class
files are byte-identical** in the released 2.0.3 and 3.0.13 JARs. Replay file
extension and underlying generated-schema accessors are compatible. The replay
header's spec-version string changes, and recorded actions may reflect the new
mechanics. Schema continuity is not an authorization to pool old and new scores.

`ReplaySummary` now requires the `GameConstants.SPEC_VERSION` from the exact
engine JAR used to compile it. Its tests generate that same version and retain
wrong-version rejection. `ReplayQuery` reuses that validation. The Python runner
reads `battlecode_version` from its actual engine JAR and requires the summary
to match it; each result also records this version. Existing engine SHA-256,
source/binary identity, map identity and mutation checks remain intact.

New and diagnostic parser/query/real-fixture checks run separately. A dedicated
cross-version check feeds a real 2.0.3 replay to the 3.0.13 parser and a real
3.0.13 replay to the 2.0.3 parser; both must return exit 2 and an invalid-version
report. The parser remains bounded partial evidence extraction, not a complete
world-state reconstruction or a proof of strategy quality.

## Preserve earlier evidence

The default `battlecode23` and `bcenv` packages use 3.0.13. Explicit
`battlecode23-diagnostic-2_0_3` and `bcenv-diagnostic-2_0_3` packages preserve the
old artifact pin and matching strict parser. Historical pilot/lecture reports,
runbooks, prompts and measurement artifacts are unchanged. Those historical
exporters require the diagnostic engine and select its matching manifest;
passing the final-rules engine fails before creating an export.

Recompile every public opponent and candidate from frozen source under the new
engine. Never rewrite old build hashes to satisfy engine checks. Keep each new
measurement's engine hash, source hashes, map hash, slot order and output path.
Old 2.0.3 reports stay diagnostic evidence and cannot be relabeled as final-rule
results. This change supplies no candidate strategy and makes no tournament
strength or complete replica-readiness claim.

## Verification scope

Run `nix flake check` on x86_64-linux. It covers both profiles' released constants
and API checks, strict summary/query parsing, real inert-fixture matches,
repetition with only the official arbitrary-winner fields normalized, swapped
slots, source packaging, malformed-source rejection and cross-version refusal.
Python export/adapter tests, Galaxy contract inspection and offline prompt
record tooling remain part of the check set. aarch64 outputs are declared but
not runtime-tested here. Hosted CI and historical tournament deployment are
separate evidence gates.
