# Scrimmage protocol scaffold v1

**Status: non-executing protocol and boundary utilities, not a deployed service.**
`services/scrimmage.py` is a Python-standard-library-only, year-neutral reference
scaffold. It does not listen on a socket, authenticate a user, run a compiler or
engine, select opponents, schedule matches, store durable state, or load heldout
bots. Passing its tests is not evidence of strength, sandboxing, a secure deployed
service, or a completed learning loop.

The module is deliberately outside `runtime/`: the existing competitor package
copies that directory, while trusted service code must be separately packaged.
The source and this contract may be public. Protecting deployed state means
independently enforcing access to the service process, private files, credentials,
opponent bytes, schedule and internal results; source-code obscurity supplies
none of those protections.

## Relationship to existing behavior

This is a proposed facade contract, not an implementation of the historical
Galaxy API or a replacement scheduler. `docs/REPLICA-2023.md` remains authoritative
about the pinned Galaxy 2023 foundation and its unmet integration gates. Any future
Galaxy integration must adapt this facade to the original backend's acceptance,
match, retry and callback lifecycle, preserving those semantics and documenting
intentional deviations. None of that integration exists here.

`contracts/artifacts-v1.md` still defines season execution artifacts. They contain
private diagnostic paths and logs and must never be serialized directly through
this public facade. A season adapter must validate complete, immutable game
outcomes before producing the small normalized result described below. Engine
process success alone is not a valid game outcome.

## Public interface proposal

The Python methods below exercise the contract in memory. The HTTP-shaped paths
are a future transport specification, **not available endpoints**. One trusted
facade instance corresponds to one authenticated owner; public IDs do not grant
access. A future transport must enforce ownership on every operation, quotas
before reading request bodies, no enumeration, and a bounded error mapping.

- `submit(archive: bytes, package: str)` corresponds to `POST /v1/submissions`.
  The only candidate-selected inputs are a source ZIP and declared Java package.
  There are no candidate-supplied host paths, opponents, maps, schedules, commands,
  environment variables, URLs, callback endpoints, resource overrides or service
  configuration. A future transport rejects unknown request fields and enforces
  body limits while streaming, before constructing a Python bytes object.
- `result(submission_id)` corresponds to `GET /v1/submissions/{submission_id}`.
  Return the latest detached public result. IDs have the opaque shape
  `s_<32 lowercase hex characters>` and are generated independently of source
  identity. Equal submissions receive different public IDs. IDs are identifiers,
  not credentials; an authentication/authorization mechanism is still required.
- `replay(submission_id)` corresponds to
  `GET /v1/submissions/{submission_id}/replay`. This version always returns
  `status: unavailable`, `reason: replay_withheld`, the schema and the submission
  ID. It never returns a raw replay, byte range, storage path, signed URL or
  opponent-derived metadata. Unknown and wrong-owner IDs are indistinguishable.
- `accepted_submission` and `record_result` are **trusted-side only**, never
  public routes. The former accesses frozen source content; the latter accepts
  one terminal result from a future trusted adapter. This does not implement
  worker delivery, retries, scheduling or authentication.

Every public response has `schema: bcenv.scrimmage.v1`. Successful intake returns
`status: accepted` and an opaque submission ID; accepted means source intake
passed, not successful compilation or scheduled/executed games. An intake
rejection also receives an ID and terminal rejected record, if quotas permit.

A transport should map bounded `QuotaExceeded` codes (`rate_limit`,
`pending_limit`, `record_limit`) to a quota response, `unknown_submission` to a
non-enumerating not-found response, and duplicate terminal reports to conflict.
Do not turn arbitrary Python exceptions or backend response bodies into public
text. No exception message from a compiler, worker or replay parser is allowed.
Transport errors, rate-limit responses, authenticating principals, request
streaming limits, request concurrency and total process-memory limits remain
unimplemented deployment responsibilities.

## Intake and content identity

`accept_source_zip` never extracts to disk and never executes candidate bytes.
The archive must use the intentionally narrow source-ZIP profile below:

- ZIP_STORED or raw-DEFLATE entries; no encryption, descriptors, ZIP64,
  multipart disks, archive/member comments, extra fields, prepended executable
  bytes, trailing junk, gaps, overlapping entries or local/central header drift.
  ZIP metadata is not accepted as a second data channel.
- Nonempty UTF-8, NUL-free `.java` files only; no directory entries, binaries,
  JARs, hidden files, symlinks, devices, sockets or FIFOs. No default package.
- Paths must be exactly under the declared package path, with optional Java
  subpackages. Every path component uses the conservative ASCII identifier
  subset `[A-Za-z_][A-Za-z0-9_]*`; Java keywords/literals, `_` and Windows
  reserved device names are rejected. This intentionally rejects some names
  otherwise valid in Java, including Unicode and `$` identifiers.
- No path normalization: absolute paths, drives, backslashes, traversal, empty
  components, `.`/`..`, colons, whitespace, unexpected suffixes and NUL-truncated
  names are rejected. Duplicate names and case-colliding files **or directory
  prefixes** are rejected. Limits apply before decompression.
- The central directory is prewalked with a hard record cap before ZIP-library
  allocation; a forged small end-record count cannot trigger unbounded record
  parsing. Local sizes/names/flags/methods/CRC match the central directory. Actual decoded
  bytes must match declared sizes and CRC. DEFLATE output is explicitly bounded
  to declared size plus one and must consume exactly one complete stream.
  An underdeclared output length cannot cause accepted truncated source.

This is archive/layout/text validation. It is not a Java parser, package-declaration
checker, compiler, bytecode verifier or execution sandbox. A future isolated
season worker must validate those properties. A file called `.java` is still
untrusted code.

Accepted content is a frozen tuple of frozen `(path, bytes)` entries, sorted by
path. Its internal content address is SHA-256 of canonical UTF-8 JSON (sorted
keys, compact separators) containing `schema: bcenv.scrimmage-source.v1`, declared
package, and sorted `files` entries with path, byte size and SHA-256 of exact
source bytes. ZIP order, timestamps and compression do not change the identity;
source paths, package or bytes do. Original ZIP metadata is discarded. The facade
does not expose hashes or sources through public responses.

Immutability here means ordinary API callers cannot mutate accepted bytes or
result snapshots. It is not a tamper-proof object store, signature, access-control
mechanism or defense against a caller that can modify the Python process or its
files. A real trusted service must own append-only content-addressed storage
outside agent access and bind source/build/result provenance to it.

## Public states and result allowlist

An initial accepted record may transition once to exactly one terminal state:

- `rejected`: source/compile rejection. Closed reasons are `invalid_archive`,
  `archive_limit`, `invalid_package`, `invalid_source`, `compile_rejected`.
- `infrastructure_failure`: no reliable complete result. Closed reasons are
  `worker_unavailable`, `resource_limit`, `invalid_evidence`,
  `boundary_unverified`, `internal_error`.
- `complete`: a validated aggregate of complete games. Public `summary` contains
  only integer `games`, `wins`, `draws`, `losses`; the last three sum to games,
  games is positive, and every count is within the game cap. Booleans, floats,
  negative numbers and missing/contradictory evidence fail closed.

An intake rejection is immediately terminal. A second terminal report is refused;
retained records are never overwritten. A complete report requires the trusted
adapter's `validated_complete: true` and well-formed counts. This flag is a
trusted-adapter assertion, not independent evidence validation. It is not a
public request field. The scaffold does not invent winners or treat missing
results, partial replays, invalid evidence, crashes or timeouts as losses.

`public_result` builds fresh dictionaries from an allowlist at every level. It
never copies arbitrary text, paths, commands, logs, stack traces, map names,
opponent names/hashes, schedule ordering, replay links or unknown nested fields.
Invalid normalized reports become `infrastructure_failure: invalid_evidence`.
Unknown rejection/failure text is replaced with a fixed safe reason. Aggregate
results remain intentional feedback and can themselves reveal information over
repeated trials; quotas limit access but do not establish a privacy guarantee.

## Resource and rate quotas

Defaults implemented in the in-memory scaffold:

| Resource | Limit |
| --- | --- |
| Compressed archive | 1,048,576 bytes |
| Source files | 128 |
| Single uncompressed source file | 524,288 bytes |
| Total uncompressed source | 2,097,152 bytes |
| Per-file expansion ratio | 100 times compressed size (minimum denominator 1 byte) |
| Source path | 240 ASCII bytes |
| Package/subpackage depth | 16 components |
| Submission attempts | 20 per rolling 3,600 seconds |
| Result and replay lookups combined | 120 per rolling 60 seconds |
| Accepted unfinished records | 4 |
| All retained records | 64 |
| Games in one public aggregate | 128 |
| Serialized public result | 4,096 bytes |

The source byte limits accommodate generated Java observed in public historical
BC23 packages (a 269,818-byte file and a 1,057,245-byte package), without changing
archive, file-count or expansion caps. These are bounded compatibility defaults,
not assertions of the original competition upload limits.

Malformed attempts consume submission-rate budget; missing-ID reads consume read
budget. Submission and read accounting use a monotonic clock. State changes and
quota checks share a process-local lock. Terminal records continue to count toward
the retention cap; there is no silent eviction or quota reset API. The maximum
retained source payload with defaults is 128 MiB plus bounded Python/container
metadata. Request bytes held by the caller and concurrent callers are additional
memory and require host-level bounding.

These are **single-instance, process-local quotas only**. Restart loses records
and resets rate limits. Multiple instances do not share accounting. There is no
durable service, cross-process idempotency, TTL/recovery protocol or production
rate limiter. A future deployment must enforce durable per-owner and global
budgets, concurrency, compiler/runtime CPU/memory/time/process/output limits,
network isolation and replay/storage retention before execution is allowed. The
scaffold accepts no client override of these trusted configuration values.

## Replay/learning gap and future publication boundary

Withheld replay is deliberate and **blocks the full submit → evaluate → inspect
replay → revise learning goal**. Aggregate scores alone do not close that gap.
There is no claim of useful tactical replay feedback in this implementation.

A future version must define a season-specific sanitized replay byte schema and
an independently tested export pipeline. Its minimum requirements are:

1. Bind the replay to the immutable submission, validated complete game evidence
   and a versioned release policy, with an opaque owner-scoped replay ID.
2. Publish only bounded, sanitized bytes generated in service-owned storage.
   Specify media type/schema, total byte cap, query/range/event caps and truncation
   semantics. Never return a filesystem path, worker URL, raw storage object or
   original replay bytes as a fallback.
3. Remove opponent identities/source/binary hashes, internal paths, raw logs,
   schedule metadata and opaque extension blobs; validate nested fields and all
   attacker-controlled strings. Test adversarial replay payloads, metadata and
   parser failures. Document unavoidable tactical-information release explicitly.
4. Authorize each retrieval, enforce durable quotas, and fail closed when
   sanitization or completeness cannot be established. Unknown or unauthorized
   handles must not leak object existence.

No such exporter or schema is implemented here, and no raw replay should be wired
into this interface in its place.

## Heldout gate: policy is not enforcement

`require_heldout_attestation` **always raises `BoundaryUnverified` in this version**.
`BoundaryAttestation` is a reserved typed future integration shape only. Even an
all-true, apparently fresh attestation with a well-formed evidence digest cannot
open the gate. There is no enabled flag, trusted callback escape hatch or default
allow path. Real heldout use is unsupported.

The library cannot constrain agent tools, shell access, filesystem reads/writes,
network egress or a shared host's access to a private corpus. Prompts, hidden
filenames, content hashes and claims of isolation do not enforce that boundary.
A future implementation must independently validate the actual tool surface,
filesystem/process isolation and network boundary; protect service state from
agent mutation; verify deployment-bound, current attestation evidence; and make
all real heldout execution pass that verified gate. Replacing the permanently
closed hook requires reviewed deployment work and adversarial boundary tests.

## Verification

Run synthetic, offline probes without engine access or third-party dependencies:

```sh
python3 -m unittest discover -s tests -p test_scrimmage.py -v
```

These probes cover malformed archives and expansion/metadata attacks, normalized
immutable identities, internal-field leakage, public state distinctions,
terminal-result immutability, concurrent process-local quotas, package-input
separation, unavailable replays, and the
permanently closed heldout gate. They do not prove deployment isolation,
historical Galaxy fidelity, secure authentication, durable execution or bot
strength. The repository's existing full Python test discovery includes them.
