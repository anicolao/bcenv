# Offline Responses transport v1

`services/responses_transport.py` implements `ModelTransport.next_turn` for the
fixed six-tool controller. This is a concrete request/response **codec and
admission harness tested with inert fixtures**, not an authenticated provider
integration or runnable autonomous competitor. It contains no HTTP sender, SDK,
credential lookup, network implementation, filesystem operation, subprocess,
account provisioning, or service launch. Both injected callbacks are offline
fixtures in this version. `services.controller.launch_real` still always raises
`real_launch_unsupported`; adding this adapter does not open that gate.

## Provider assumptions checked on 20 October 2026

The official model page documents `gpt-6.1-sol`, Responses function calling, and
`reasoning.effort: high`. Those constants are pinned by the adapter. Completed responses must report the exact same model identifier and actual
`service_tier: default`; missing or differing provenance is rejected. No guessed
dated snapshots or alias expansions are accepted. Public model
documentation does not establish this user's API access, billing, credentials,
rate limits, or equivalence to an internally hosted agent.
[Model](https://developers.openai.com/api/docs/models/gpt-6.1-sol)

The Responses function interface uses `type: function`, a name, parameters, and
`strict: true`. All object fields are required and additional properties are
forbidden. `parallel_tool_calls: false` asks for at most one function call; the
adapter also rejects a larger batch before returning any calls.
[Function calling](https://developers.openai.com/api/docs/guides/function-calling)

The request is nonstreaming, foreground, stateless (`store: false`), Standard
service tier (`service_tier: default`), and disables automatic truncation. Its
`max_output_tokens` covers generated reasoning as well as visible output. No
built-in tool, namespace, custom tool, deferred registration, configuration update,
remote MCP server, previous response ID, conversation ID, attachment, URL input,
or model-selected endpoint is configured.
[Responses reference](https://developers.openai.com/api/reference/resources/responses/methods/create)

The API reference and guides establish the assumed protocol, not a successful
live request. No provider call or paid token was used for this implementation.
Schema/feature acceptance, entitlement and alias stability still require a
separately authorized smoke test. Unknown response variants fail closed rather
than adding tools or silently switching models.

## Immutable context and strict application registry

Construction captures the exact canonical `tool_schemas(controller_limits)`
bytes from the existing controller, adding only the Responses `type` and `strict`
fields. Every `TurnRequest` must contain the exact six names and those same schema
bytes. A host must pass matching `ControllerLimits` to both controller and adapter.
The candidate cannot register a function or change its schema. `workspace_exec`
remains only a typed request for the separately required sandbox; this module
does not execute argv or supply a sandbox.

The initial context is copied as a `FrozenContext` and its framing hash is checked.
Its system bytes round-trip unchanged through UTF-8 into `instructions`; its task
bytes round-trip unchanged into one user message. No instructions, wrappers,
examples, delimiters, prompt normalization, or secret configuration are appended.
JSON wire escaping changes representation only, not the decoded UTF-8 content.
No later request can replace either initial byte string.

Each successful nonfinal response must contain exactly one allowed function call.
It maps to the existing `ToolCall(call_id, name, arguments_json)`. Function JSON
must be an object, without duplicate keys, nonfinite numbers or invalid Unicode.
The existing controller still performs all path, handle, type, field and semantic
checks; strict provider schemas are not the permission boundary. Malformed calls,
unknown names or a second call reject the whole response before controller-side
effects. Call IDs and provider output item IDs cannot be reused after a successful turn.
Failed attempts do not advance either identity registry.

The next controller request must advance exactly one turn and supply the previous
transcript byte-for-byte, the exact normalized call reply the controller records,
and exactly one matching result envelope. Only its result becomes a
`function_call_output`; it cannot inject a new system role or configuration field.
All tools and fixed generation options are reconstructed from trusted constants.
The adapter has no context-compaction or history-truncation fallback.

## Bounded opaque provider continuation

Stateless Responses can return encrypted reasoning continuity; the reasoning
guide describes replaying that opaque data together with calls and results.
Assistant messages can carry a phase that distinguishes intermediate commentary
from a final answer. The adapter preserves phase for accepted assistant messages
and replays bounded encrypted continuity without interpreting or decrypting it.
It neither requests nor accepts plaintext reasoning or nonempty reasoning
summaries. A missing encrypted continuation for a returned reasoning item fails
closed. No special `include` option is necessary under the currently documented
stateless behavior.
[Reasoning continuity](https://developers.openai.com/api/docs/guides/reasoning),
[Assistant phase](https://developers.openai.com/api/docs/guides/deployment-checklist)

Only the following output item projections are supported:

- `function_call`: id, call ID, name, arguments, optional completed status
- `reasoning`: id, type, empty summary, bounded nonempty encrypted content; optional
  null/empty plaintext content and null/completed status are checked and discarded
- `message`: completed assistant role, id, optional original phase, and bounded
  `output_text` parts with empty annotations; empty/null logprobs are discarded

Additional fields inside those items are refused. Built-in tool results,
configuration updates, plaintext reasoning, nonempty annotations, logprobs,
refusals and unknown item kinds are not passed through. Ordinary commentary may
accompany a call and is preserved only for the provider's conversation state.
A final-answer message accompanying a call is rejected. Exactly one nonempty
non-commentary message, optionally preceded by commentary, without calls becomes
`TurnReply.final_text`. A message after the final answer, a second final answer,
or an empty or commentary-only response is an error; the adapter does not generate an extra
request just to continue it.

The continuation is serialized bytes held by the trusted adapter. It is absent
from `TurnReply`, controller tool feedback and the audit snapshot. It is not
written to logs or disk. Requests delivered to the trusted fixture naturally
contain the continuation; a future trusted sender must not expose raw requests
as candidate/public logs. This projection is intentionally a narrow supported
subset, not arbitrary replay of provider JSON.

## Reservations, observed usage, and explicit retry

Default ceilings are illustrative offline policy, **not approved real-trial
spending or evidence of accurate tokenization**:

| Dimension | Ceiling |
| --- | ---: |
| Total attempts, including explicitly repeated failed attempts | 20 |
| Serialized complete request | 64 KiB |
| Total serialized request bytes over all attempts | 1,280 KiB |
| Response bytes per attempt | 256 KiB |
| Total reserved response bytes | 5 MiB |
| Input-token reservation per attempt | 16,000 |
| Generated-token reservation per attempt | 8,000 |
| Total reserved input/generated tokens | 320,000 / 160,000 |
| Output items / content parts per message | 16 / 16 |
| Text bytes per message | 16 KiB |
| Opaque encrypted content per reasoning item | 64 KiB |
| Serialized provider state / controller transcript, independently | 128 KiB each |
| Soft time per admitted attempt | 30 seconds |

An attempt reserves its complete input ceiling, generated-token ceiling and
response-byte ceiling **before either injected callback**. It also records the
complete request's byte length and SHA-256. No counter failure, malformed reply,
provider rejection, timeout, missing usage, or cache hit refunds a reservation.
The unused part of a successful reservation is also retained. This deliberate
pessimism prevents retry storms and unknown charging outcomes from replenishing
budget. Reservations can stop a run sooner than actual consumption requires.

`OfflineTokenCounter.count_input(body)` receives the exact complete serialized
request, including immutable instructions, task, six schemas, conversation,
opaque continuation and tool results. It returns `OfflineInputCount` tied to that
body's SHA-256. Wrong-body counts, booleans, negative counts and counts above the
input ceiling fail before exchange. A fixture count is an arbitrary test assertion,
**not an estimate, a real token count, or a proved bytes-to-tokens bound**. There
is no characters-per-token heuristic and no tokenizer dependency.

For a future live gate, use the authorized provider input-count API on the complete
admitted request or another independently validated exact provider-compatible
counter. The official API includes formatting and schema overhead that a local
text-only tokenizer may miss. This implementation does not contact that endpoint.
[Token counting](https://developers.openai.com/api/docs/guides/token-counting)

An online counting implementation must not be slipped into this offline callback:
counting requests and their retries would be additional provider operations and
must be included in the approved durable request/time/cost budget. As written,
there are at most 20 offline counting invocations and at most 20 offline exchange
invocations, often fewer. The 20-attempt fixture envelope is not a claim that a
future online count-plus-inference flow makes only 20 HTTP requests.

`OfflineExchange.respond(ResponsesRequest)` gets immutable request bytes plus the
attempt number, response-byte cap and remaining attempt time after counting.
That duration is a declaration for the callback, not enforced preemption. It returns one UTF-8
JSON byte string or raises. There is no automatic retry, SDK retry, polling,
backoff, alternate endpoint or model fallback. The existing controller terminates
on a transport exception. A trusted caller can explicitly repeat the identical
failed `TurnRequest` on the same transport; this starts another fully reserved
attempt. Changing the request during such a retry is rejected. Successful turns
cannot be repeated, and a final response closes the transport.

Audit entries distinguish `not_sent` (exchange not invoked), `unknown` (exchange
invoked but no validated usage), and `reported` (validated numeric usage).
They expose only attempt/hash/byte counts, ceilings and numeric usage, never raw
responses, error messages, encrypted state, IDs or text. Usage validates integer
input/output/total and optional cached/reasoning subtotals. Missing usage stops a
completed response; invalid totals or usage over either ceiling stop dispatch.
Valid usage on an incomplete response is recorded even though the response is
not executable. Observed usage never lowers reservations. Cache-write pricing,
provider billing, rate limits, taxes and account funding are not calculated here.
Numeric reports are not receipts or an all-in dollar spending guarantee.

A process-local lock rejects concurrent/reentrant calls on one instance. Clock
samples must be finite numeric values within ±2^53 seconds, excluding booleans,
and cannot regress;
clock exceptions become a fixed error. Deadline checks occur after callbacks
return and cannot preempt them. State and
reservations are not durable or shared across instances/processes. Starting a new
adapter loses their continuity; this is explicitly unsuitable for restart-safe
billing or trial authorization. A production quota ledger must reserve durably
before any count/send, account for crash recovery and unknown charges, and prevent
multiple workers from allocating the same budget.

## Response and error handling

The complete response is byte-capped before JSON parsing; duplicate keys,
nonfinite numbers, excessive nesting, invalid UTF-8 and lone surrogates fail.
Top-level metadata outside the explicitly consumed status/object/model/service-tier/output/usage/
error/incomplete-details projection is ignored and never re-emitted. All of it
still counts toward the raw response cap. Accepted output item fields have the
narrow allowlists above. No arbitrary nested provider field becomes a tool result.

Only `completed` output can be dispatched. Incomplete, cancelled, queued,
in-progress, unknown or failed statuses stop, including a truncated response that
already contains a plausible function call. Partial text is not returned as final.
Provider error codes map only known rate-limit and server-error identifiers to
fixed local errors; every other provider error becomes `provider_error`. Raw error
messages and callback exception text never cross the controller boundary. The
controller continues to expose only its existing `transport_failure` outcome.

This is not a redaction oracle for ordinary model output or workspace text. Those
are allowed content under their existing limits; secrets must never be supplied
to the model, fixture, candidate or incorrectly isolated tool adapter.

## What still blocks a real launch

1. Explicit user authorization for model access, cost, counting requests and an
   approved supported execution environment; secure credential provisioning outside
   candidate state. No such access is tested here.
2. A separately reviewed sender: fixed official HTTPS destination, certificate
   verification, no candidate URL/model/header selection, no implicit redirects or
   retries, bounded streaming reads and independently enforced connect/read/total
   deadlines. No inactive generic HTTP code is included to accidentally enable it.
3. Durable quota/retry/unknown-outcome accounting and an authorized exact count of
   each complete request. The current byte cap bounds serialized input size, not
   undisclosed model token overhead. No pricing guarantee follows from fixture counts.
4. Independently accepted sandbox, broker, cgroup/resource controls, filesystem/
   network isolation, cleanup and recovery. Neither Python callbacks nor declared
   timeouts can preempt a blocked callback, prevent an oversized allocation inside
   one, or sandbox hostile candidate code. A future receiver must enforce the cap
   while reading, rather than buffer an unlimited response then call this parser.
5. Authenticated protocol/schema smoke testing, then a bounded real integration
   trial; both remain unperformed. No competitiveness claim follows from unit tests.

## Verification

`tests/test_responses_transport.py` supplies all counts, outputs and failures
locally. It covers exact context/registry mapping, controller integration,
stateless opaque continuity, phase handling, cross-turn item identity, returned
model/tier provenance, transcript binding, candidate option
injection, malformed/duplicate/multiple/unknown calls, invalid statuses,
truncation/exhaustion, oversized/error fields, response allowlists, numeric usage,
no-refund reservations, unknown outcomes, explicit retry admission, callback
failures, concurrency, soft deadlines and the closed real-launch gate.

The repository's pinned Nix `checks.x86_64-linux.python-unit` check discovers this
suite together with all existing Python tests. No additional dependencies or
installers are introduced.
