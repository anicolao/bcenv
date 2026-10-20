# Integrated competitive preflight

This branch combines the reviewed public 2023 runtime with trusted-side service
components from PR7, PR9, PR10, PR11 and PR12. It is an integration baseline,
not a competitive bot, deployed scrimmage service or accepted hostile-code runner.
No pull requests have been merged. Exact copied source provenance is recorded in
[the source manifest](COMPETITIVE-INTEGRATION-SOURCES.json).

Components:

- Public official 3.0.13 engine/runtime, with separate 2.0.3 diagnostics
- Strict source intake and closed public result projection
- Fixed six-tool synthetic controller
- Bounded replay-feedback v1 prefix projection (service route still withheld)
- Durable source/job ownership, quotas, reservation fencing and recovery
- Offline-only gpt-6.1-sol/high Responses codec and admission accounting
- Run-scoped immutable source artifacts and controller-to-ledger adapters
- Inert synthetic worker lifecycle and bounded consistency receipts

The trusted `services/` implementations are not copied into the competitor
package. Packaging exclusion does not isolate a native agent that has host
filesystem tools. Actual fixture namespace/source-export evidence remains in
[planner PR12](https://github.com/anicolao/bc-planner/pull/12), with its narrower
resource/concurrency limits. That fixture is not installed as a real launcher.

Independent review reports in this directory preserve the exact earlier
checkpoints; they do not silently claim review of later changes. The worker
review covers the subsequently added ledger reservation check.

## Closed gates

No real provider sender, credential handling, authenticated service listener,
private opponent panel, production workspace ownership, aggregate process-tree
resource controls, live supervisor, authentic match validator or usable replay
route is provided here. Inert completion receipts may be fabricated, as tests
explicitly demonstrate. Existing `launch_real` and heldout methods remain closed.

Before an autonomous trial, the complete deployed boundary must pass independent
acceptance tests, the exact competitor environment and tool schema must be frozen,
and a prospective protocol/budget must be committed. No fixture win or passing
component test establishes competitor strength.

## Integration verification

All 13 existing x86_64-linux flake checks pass, including 230 Python tests,
public/diagnostic parser integration and version isolation. aarch64 remains
untested. Package inspection excludes all seven trusted implementation modules.
[Recorded result](COMPETITIVE-INTEGRATION-CHECKS.json) identifies the tested build;
source-only documentation additions after that build do not change copied code.
The existing deterministic fixture checks are engineering tests, not new public
opponent trials or competitive evidence.
