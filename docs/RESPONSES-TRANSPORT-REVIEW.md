# Independent offline Responses transport review

Date: 2026-10-20 UTC. Branch: `feat/responses-transport`, base `04c815954154a9be691bb531245f74e35cbd6edf`.

## Verdict

Accept the reviewed checkpoint for its explicitly offline codec/admission scope. No unresolved must-fix findings. This does not authorize or validate a live model sender, spending, sandbox, or competition launch.

The reviewer did not edit author-owned repository files. All independent probes and this report are outside that checkout in `/workspace/shared/responses-transport-review/`.

## Findings resolved by the author

1. **Cross-turn output item IDs could poison replay.** Two successful responses using item ID `fc_repeated` with distinct call IDs were accepted, and the third request replayed duplicate item IDs. Reused reasoning item IDs behaved similarly. The final code rejects IDs already committed to history, and commits new IDs only after the complete attempt succeeds. The original function/reasoning repros now reject; an independent controller test confirms no second tool dispatch. Invalid/failed attempts do not poison the IDs permitted on an explicit retry.
2. **Returned model/tier provenance was ignored.** A synthetic response reporting `wrong-model` and `ultrafast` was accepted despite the fixed outbound request. Completed responses now require literal `gpt-6.1-sol` and actual tier `default`; missing, changed, or unrecognized snapshot identities fail closed. Valid observed usage and reservations survive mismatch rejection. This checks reported protocol metadata, not provider authenticity or account entitlement.
3. **Invalid injected clocks defeated soft deadline checks.** NaN, infinity, boolean, and regressing clocks previously permitted completion; a string clock raised a raw TypeError. Final code validates bounded finite numeric, non-boolean, non-regressing samples and maps clock failures to `invalid_clock`. The exchange receives the remaining attempt time after counting. These were trusted-host configuration issues, not model-controlled sandbox escapes.
4. **Commentary followed by a final message was conservatively rejected.** The final parser supports preceding commentary plus exactly one final non-commentary message while refusing ambiguous order/multiple finals. Only final text reaches the controller.

## Verification performed

All Python execution used the repository's pinned flake and existing Nix runner. No package installation outside the pinned flake, credentials, token-count API, model inference, HTTP sender, or launch-gate change was used.

- Required command: `/workspace/scratch/1523a3f802eb/nix-canonical-root/nix-root build .#checks.x86_64-linux.python-unit --option sandbox-paths '' --no-link -L`
- Final repository result: **155 tests passed**, including **29 transport test methods**. Nix build log reports 1.068 seconds for test execution.
- Independent `regression.py`: **8 tests passed**, covering cross-type/cross-turn item reuse, no partial second dispatch, missing/mismatched provenance, retained reservations, retry ID atomicity, late clock failure accounting, commentary exclusion, and usage-ceiling rejection.
- Independent `adversarial.py`: **104 malformed-provider fixture cases rejected**, zero invalid accepted responses and zero marker leaks through exception text/audit representations. Covers types, statuses, JSON, tool fields, reasoning fields, numeric usage, and role poisoning.
- Independent clock probes: **5 cases rejected** with `invalid_clock`, zero exchange calls: NaN, infinity, regression, boolean, and nonnumeric.
- Original `probe.py` repros: **3 now rejected**: repeated function ID, repeated reasoning ID, and mismatched model/tier.
- `git diff --check` and `git diff --cached --check`: passed.

These counts are distinct: 155 repository unittest methods, 8 independent unittest methods, and the separate synthetic case matrices/probes. They are not live API or isolation tests.

## Reviewed checkpoint hashes (SHA-256)

- `services/responses_transport.py`: `da54bce79e4de81b616731b13991d5b6128a60ea862a901e2a7730f5f550925c`
- `tests/test_responses_transport.py`: `f906fc49f65dc2c516923f6cea144c6a0581c2bc86344648aea4fe24b95442ee`
- `contracts/responses-transport-v1.md`: `9d8058678f26d9eb49b1fb9f48e962014828d508eef68baacb25ce9d908a8906`

## Current official documentation checks

- [GPT-6.1 Sol model](https://developers.openai.com/api/docs/models/gpt-6.1-sol): the selected model supports `high` effort and requires Responses for function calling. The internally available model harness was not treated as API-access evidence.
- [Function calling](https://developers.openai.com/api/docs/guides/function-calling): the strict function schema requirements match the six fixed controller schemas; reasoning items must accompany tool continuation.
- [Reasoning guide](https://developers.openai.com/api/docs/guides/reasoning): current stateless `store:false` behavior returns encrypted continuity by default. The older `include:["reasoning.encrypted_content"]` option is accepted but not required. Plaintext summaries require explicit opt-in; this adapter requests none and rejects nonempty summaries.
- [Responses API reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create): assistant phases are preserved; returned service tier identifies actual serving mode and may differ from the request. Exact returned model/tier acceptance is a deliberate narrow policy, not a claim of alias/snapshot compatibility.
- [Token counting](https://developers.openai.com/api/docs/guides/token-counting): real input accounting includes provider formatting/schema overhead. Hash-bound offline fixture counts are not a tokenizer or a proven bytes-to-tokens ceiling.

## Limits that remain explicit

The adapter has only injected offline callbacks, six fixed function tools, bounded strict parsing, encrypted opaque continuation, exact frozen initial context and transcript binding, pessimistic reservations before callback attempts, no automatic retry, no reservation refunds, and closed error messages. Unknown charging outcomes remain unknown.

There is no live authenticated sender, exact online token count, durable/shared quota ledger, restart continuity, hard callback preemption, independently enforced streaming-read cap, sandbox, or approved live-trial boundary. Returned metadata and usage are fixture assertions. Arbitrary legitimate model/workspace text is not a secret-redaction oracle. New provider output shapes or model aliases may fail closed until separately reviewed. `launch_real` remains closed.
