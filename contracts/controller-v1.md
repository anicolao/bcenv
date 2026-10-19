# Fixed-tool controller scaffold v1

`services/controller.py` is an **offline, synthetic protocol harness**. It has no
provider SDK, inference request, authentication, listener, filesystem operation,
subprocess or network implementation. `launch_real` always raises
`real_launch_unsupported`, including when passed enabled flags or claimed
attestations. This is not an accepted trial launcher, completed learning system,
secure sandbox, historical Galaxy replica or source of bot-strength evidence.

## Transport and frozen initial context

A trusted future provider adapter implements `ModelTransport.next_turn` over
`TurnRequest` and `TurnReply`. The request carries exact frozen initial system
and task UTF-8 bytes, immutable fixed tool specifications, bounded prior messages
and a turn number. No additional prompt/context is silently inserted. The context
SHA-256 covers a versioned domain prefix and length-framed exact bytes; whitespace
or newline changes change identity. The transport must preserve those bytes when
mapping to the selected provider's supported structured-tool API.

`tool_schemas()` returns immutable, provider-neutral JSON-schema descriptions.
They are not an implemented provider integration or an assurance that a particular
SDK/request shape is supported. The six fixed function names are:

- `workspace_read`: safe relative file path, integer byte offset and capped limit
- `workspace_write`: safe relative file path and capped UTF-8 content
- `workspace_exec`: bounded string-array argv, workspace-relative cwd and capped
  timeout, passed only as a typed `SandboxAdapter.exec` request
- `scrimmage_submit`: run-issued opaque source-artifact handle and Java package
- `scrimmage_result`: run-issued opaque submission ID
- `scrimmage_replay`: run-issued opaque submission ID; replay remains withheld

All schemas require their exact fields and reject extra fields. Dispatch validates
again, including duplicate JSON keys, types, lengths, path components and handles.
Unknown native/host/browser/network tool names are rejected. Text returned by a
model does not itself execute actions. Call IDs must be unique; malformed/duplicate
IDs reject the whole turn before any adapter side effect.

## Sandbox adapter is a required external boundary

Arbitrary candidate tests, interpreters and shell programs may appear in argv.
They are opaque sandbox inputs, never executed by this module. No model field
selects a host launcher, mount, inherited environment, network capability, sandbox
configuration or resource-limit override. The trusted adapter owns those choices
and must enforce them independently, including against deliberately hostile argv.

The lexical file roots are `src`, `scratch`, `docs`, `artifacts`, `self_play`.
File writes are limited to `src` and `scratch`; files require a leaf component.
Exec cwd allows `.` or one of those roots itself, plus safe descendants, so a
fresh workspace need not already contain a nested directory. Paths reject
absolute/backslash/traversal/empty components, hidden components, non-ASCII names,
reserved device names and trailing dots. These checks do **not** prevent symlink,
process, mount or network escapes. There is no real filesystem implementation.
The future sandbox must enforce immutable reference/controller files and allowed
writes even when a shell or arbitrary candidate code is running.

Trusted adapters and transport objects are host configuration, not model inputs.
Synthetic callbacks can prove dispatcher behavior, not process isolation. A
future real launch needs an implemented, supported provider transport and an
independently tested tool/filesystem/network boundary; no prompt or boolean flag
can stand in for that proof. Trusted service code is outside the competitor
`runtime/` package and no deployed private state exists in these source files.

## Scrimmage and feedback

The controller tracks artifact and submission handles issued within its current
run; guessed or prior-run handles are refused before adapter access. A real
service must still enforce authenticated ownership. Typed scrimmage responses
pass through `public_result` in `services/scrimmage.py`; nested private metadata
and arbitrary error text are not forwarded. Complete evidence is still a trusted
adapter assertion, not independently validated game execution.

Raw replay data is never forwarded, even if an adapter returns it by mistake.
Replay responds `unavailable: replay_withheld`, so tactical replay diagnosis and
the complete learning loop remain unmet. See `contracts/scrimmage-v1.md` for the
future sanitized-replay publication requirements and permanently closed heldout
gate. Sandbox stdout/stderr and file bytes are allowed workspace content, not a
secret-redaction mechanism; an incorrectly isolated adapter can leak information.

## Synthetic budgets and stopping

Default budgets are test-harness limits, **not approved real-trial budgets**:

- 16 model turns; 4 tool calls per turn; 64 tool calls total; 30-second soft run deadline
- 64 KiB initial context; 1 MiB per call JSON; 4 MiB per model reply
- 512 KiB per write; 8 KiB per read; maximum read offset 1 MiB
- 64 argv strings, 4 KiB each, 16 KiB combined; exec timeout at most 10 seconds
- Adapter request declares 256 MiB exec memory and 16 KiB output ceilings
- Public tool envelopes at most 16 KiB; all tool outputs at most 128 KiB

Exec stdout/stderr are clipped before serialization; an envelope still exceeding
its byte cap becomes a fixed bounded error. If even that error envelope cannot
fit the configured cap, the loop stops without appending an oversized result. Adapter/transport exceptions are
mapped to closed errors without exception text. Invalid model replies, turn/call
budgets, output limits, transport failures or deadlines end the loop. `complete`
means the model ended its synthetic conversation, not that a bot task succeeded.

Wall time is checked before and after every callback. The harness cannot preempt
a blocked callback, and its declared exec limits are not OS enforcement. Real
adapters must independently bound blocking RPCs, CPU, memory, processes, runtime,
storage and output. The harness has no durable transcript/quota store, recovery,
restart continuity, retry scheduler or cross-process enforcement.

## Tests

`python3 -m unittest discover -s tests -p test_controller.py -v` uses inert
transport, sandbox and scrimmage doubles. It checks typed mediation, frozen
context, strict schemas, forbidden tools/fields/paths, arbitrary sandbox argv
without host execution, run-scoped handles, result/replay leakage, malformed
batches, source/output budgets, deadlines and permanently blocked real launch.
Passing those tests does not validate a provider integration or deployed boundary.
