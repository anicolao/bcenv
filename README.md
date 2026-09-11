# bcenv

**A Battlecode environment for an AI agent to independently compete in the annual Battlecode competition.**

bcenv aims to give an AI agent the tools and feedback it needs to go from a new season's rules to a competition-ready bot. The agent should be able to understand the game, develop strategies, write and debug code, run matches, evaluate results, and prepare a submission with minimal human intervention.

## Status

This project is in its initial design stage. There is no implementation or runnable environment yet. The capabilities below describe the intended direction, not features that are currently available.

See [VISION.md](VISION.md) for the long-term objective, scope, and measures of success. Draft research and architecture proposals are in [HISTORICAL_LEARNINGS.md](HISTORICAL_LEARNINGS.md) and [INITIAL_DESIGN_SKETCH.md](INITIAL_DESIGN_SKETCH.md).

## Planned workflow

bcenv will support an agent through a complete competition development loop:

1. **Learn the season.** Inspect the rules, game APIs, starter code, and submission requirements.
2. **Build a baseline.** Create a legal bot that compiles and completes matches.
3. **Experiment.** Develop strategies and implement candidate improvements.
4. **Evaluate.** Run matches across maps, opponents, and seeds where supported, then inspect logs, replays, and results.
5. **Iterate.** Diagnose failures, compare candidates, and retain improvements supported by evidence.
6. **Compete.** Validate and package the selected bot, and submit it through supported mechanisms when authorized.

The environment should preserve source versions, experiment settings, and results so the agent can build on previous work and humans can inspect its decisions.

## Intended components

- **Season integrations:** Access to a season's documentation, engine, tooling, and submission format through a consistent interface.
- **Development workspace:** Tools for editing, building, checking, and debugging bot code.
- **Match runner:** Repeatable local matches and batches of experiments with explicit resource limits.
- **Evaluation tools:** Results, diagnostics, and comparisons that help distinguish useful changes from noise.
- **Agent interface:** Structured actions and observations suitable for autonomous development.
- **Competition preparation:** Validation and packaging of submission artifacts, with submission support where permitted and authorized.

Specific languages, agent frameworks, and infrastructure have not been selected. Season-specific dependencies and setup instructions will be documented as implementations are added.

## Contributing

Install Node.js (version 20 or newer) and npm, then run `npm ci` to install the development tooling and activate Husky's Git hooks. This tooling enforces the development prompt record; the Battlecode environment itself is not implemented yet.

Record every user task prompt verbatim in [PROMPTS.md](PROMPTS.md), including follow-up instructions. Preserve typos, whitespace, and punctuation. Append a numbered entry without changing any existing bytes, then stage the file with your changes. New headings may optionally include a 3–6-word summary, for example `## Prompt 4: Clarify prompt heading summaries`, above the complete verbatim prompt. If a prompt spans multiple commits, append its exact text again for each subsequent commit and note the repetition outside the prompt text.

Every commit must add non-whitespace content to the staged prompt record. The hooks reject changes to existing content, deletion, and additions that exist only in the working tree. The first commit must include a nonempty record. A merge that needs a record update can be completed by appending and staging the prompt, then committing. Fast-forward merges create no new commit and do not run these checks.

Run `npm test` to test enforcement or `npm run check:prompts` to check the current index. Hooks verify append-only bytes, not whether text truly matches the original prompt; contributors are responsible for verbatim transcription. Local hooks can be bypassed, so they are not a server-side guarantee. Hook installation follows the [Husky setup documentation](https://typicode.github.io/husky/how-to.html#manual-setup).

Early contributions should help establish the smallest complete workflow: an agent builds a baseline bot, runs a match, reads the result, and makes a measurable improvement.

Keep proposals grounded in that workflow, distinguish implemented behavior from planned behavior, and document assumptions that depend on a particular competition season.

## License

bcenv is licensed under the [GNU General Public License, version 3 (GPLv3)](LICENSE).

SPDX license identifier: `GPL-3.0-only`.

Battlecode engines, starter kits, and other third-party dependencies remain subject to their respective licenses.
