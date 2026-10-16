---
name: bcenv-local-pilot-records
description: Record candidate identity, predictions and honest local diagnostic results.
---

# Local diagnostic workflow

Read START_HERE.md, manifest.json and trial.json in your assigned workspace.
They define this local-only pilot. Full replica/clean-room acceptance is outside
its scope and is not required to complete it.

Only competitor/ and work/ are writable task outputs. Before changing candidate
code, save a concrete behavioral prediction in work/experiments/. Preserve
commands, failed attempts, build/package identities, match and replay references.
Compare observed events against the prediction, distinguishing observations from
hypotheses. A supported negative finding is valid; do not invent improvement.

Use a new output directory for every attempt. Never alter a verified build in
place; rebuild changed source. Freeze/package the exact verified source you
select. Treat process failure and incomplete replay as unknown or failed work,
not a loss. Runtime issues are not permission to edit the supplied environment.

Follow the fixed time/match stopping rules in trial.json. The supplied fixture
is inert; winning against it does not establish competitive strength. General
agent tools are restricted by instructions, not an enforced security boundary.
