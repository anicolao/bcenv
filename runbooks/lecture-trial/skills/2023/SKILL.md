---
name: bcenv-2023-lecture-trial-tools
description: Discover an authentic source opponent and run replay-grounded local experiments.
---

Use ../../bcenv from the assigned workspace; it runs there as /workspace in a
credential-free runtime. Read docs/BC23-LOCAL.md and reference/specs/ and
reference/api-source/. No installation or Gradle update is needed.

The source catalog under sources/ is a frozen, documented offline upstream mirror.
Find the season-correct lecture bot, check its origin, commit, license and hashes,
then retrieve only its Java package into work/opponents/ and build it yourself.
The catalog is a reproducibility aid: this tests local source discovery/integration,
not unaided live-web searching. Do not execute upstream scripts or build wrappers.
Keep its license/provenance separately; do not mix its code into your own source.

Create candidate Java under competitor/src/ with a package and RobotPlayer.run.
Use bcenv build with explicit --source, --package, --output. Only verified builds
may run or package. Run explicit side-swapped pairs on the two development maps
specified in trial.json. Keep source/build/replay hashes and side identities.

Use bounded replay queries to test a specific question, choosing round ranges,
robot IDs when helpful and explicit limits. Inspect coverage and truncation. Net
team deltas are not collection or balances; global replays do not reveal bot-local
knowledge. Action vector order is not within-round execution order. A finished
match with robot stdout disabled does not establish exception-free execution.

Package selected verified source. Replica, live services, submission and official
viewer are not available or needed. Stop at the stated limits without tool repairs.
