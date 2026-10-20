# bcenv

**A Battlecode environment for an AI agent to independently compete in the annual Battlecode competition.**

bcenv aims to give an AI agent the tools and feedback it needs to go from a new season's rules to a competition-ready bot. The agent should be able to understand the game, develop strategies, write and debug code, run matches, evaluate results, and prepare a submission with minimal human intervention.

## Status

This project is in its initial design stage. There is no implementation or runnable environment yet. The capabilities below describe the intended direction, not features that are currently available.

See [VISION.md](VISION.md) for the long-term objective, scope, and measures of success. Draft research and architecture proposals are in [HISTORICAL_LEARNINGS.md](HISTORICAL_LEARNINGS.md) and [INITIAL_DESIGN_SKETCH.md](INITIAL_DESIGN_SKETCH.md).

## Proposed environment

An outer LLM supervisor runs in an isolated VM or container and manages one or more competitor VMs. Each competitor boots a NixOS image, uses Nix to prepare the season's tooling and Battlecode checkout, and runs its own LLM development agent inside that environment. The supervisor provisions and monitors competitors, arranges independent evaluation, and reports outcomes to the human.

The same declarative environment definitions should support disposable local test deployments and cloud deployment on a provider such as GCE, giving contributors a repeatable framework for improving bcenv itself. See the [design sketch](INITIAL_DESIGN_SKETCH.md) for the proposed boundaries and lifecycle.

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

The draft design proposes NixOS images and Nix-managed environments, with GCE as a reference cloud provider. Agent frameworks, exact deployment tooling, and season-specific setup remain design choices; no VM images or runtime implementation are available yet.

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

## BC23 implementation foundation

See [implementation status](docs/IMPLEMENTATION-2023.md), [local tools](docs/BC23-LOCAL.md),
and [replica gates](docs/REPLICA-2023.md). This is separate from the proposed
[bc-planner experiment plan](https://github.com/anicolao/bc-planner/pull/8).

- [Authentic lecture-opponent diagnostic and export](docs/LECTURE-TRIAL-001.md)

## Scrimmage service foundation

[Bounded scrimmage contract](contracts/scrimmage-v1.md): trusted-side source intake,
closed public results and synthetic adversarial tests. This is a non-executing
scaffold outside the competitor runtime, not a deployed service or enforced agent
boundary. Real heldouts are unsupported; replay learning and durable execution
remain implementation gates.

[Fixed-tool controller simulation](contracts/controller-v1.md) exercises a
provider-neutral transport seam and bounded workspace/scrimmage dispatch using
synthetic adapters. No real inference or enforced sandbox is implemented.

[Bounded replay feedback projection](contracts/replay-feedback-v1.md) strips
operational metadata and preserves capped gameplay evidence with explicit version
and completeness markers. Synthetic tests cover malformed inputs and honest
truncation; this is not yet a service replay route or an authenticity check.

[Durable synthetic job ledger](contracts/ledger-v1.md) adds transactional source
retention, owner-scoped idempotency/quotas and fenced crash recovery. Principal
strings are trusted test context, not authentication; database fencing does not
terminate workers. [Independent review](docs/DURABLE-LEDGER-REVIEW.md) records
the passing tests, corrected defects and unpassed deployment gates.

[Synthetic controller-to-ledger composition](contracts/controller-ledger-v1.md)
connects trusted immutable source-export receipts to run-owned artifacts and
durable jobs. It preserves the six tools and refuses stdout/path claims as
export authority. Actual sandbox capture, authentication and replay delivery
remain separate gates.

[Synthetic worker lifecycle](contracts/worker-lifecycle-v1.md) connects reservation
fencing to an inert callback and bounded closed receipts. It tests expiry and
uncertain-commit behavior without starting a worker process or game; identity
echoes are consistency assertions, not authentic game evidence.
