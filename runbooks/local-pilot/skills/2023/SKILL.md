---
name: bcenv-2023-local-pilot-tools
description: Author, verify, play and inspect own BC23 source using the supplied offline launcher.
---

# Supplied launcher

From the assigned workspace use ../../bcenv. It runs with that directory as
/workspace inside the packaged runtime. Read docs/BC23-LOCAL.md for complete
commands and paths. No Nix/Gradle command, direct installer, live release lookup
or external service is needed from inside this pilot.

Create Java package-relative source under competitor/src/, including a
RobotPlayer.java with public static void run(battlecode.common.RobotController rc).
Read reference/specs/ for rules and reference/api/ or reference/api-source/ for
API details. Choose your own strategy; fixtures are readiness inputs only.

Build with an explicit package and new output directory. Only verified artifacts
can be played or packaged. Fix your source when compiler/verifier logs identify
a candidate problem. Never change pinned tools, rules, maps or supplied fixtures.

Run explicit A/B side positions separately on DefaultMap. Replay summaries
contain global information that may not have been observable by a robot.
Respect their completeness flags, event/round references, sampling limits and
net-resource semantics. A game can end in a random coin flip when all official
tiebreaks tie; record the actual result and reason rather than inferring strength.

Use package to freeze your verified selected source. There is no submit or
schedule command. Replica use and official viewer validation are not required
or available in this local-only pilot.
