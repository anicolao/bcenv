# Galaxy 2023 replica foundation

Implementation notes, separate from the proposed experiment plan. **This change does
not provide a running replica.** It provides offline regression probes, synthetic
wire fixtures and audited integration boundaries. No queue, database, API server,
compiler or worker has been deployed; no submissions or scrimmages have been run.

## Source and runnable evidence

All historical behavior below refers to
[Galaxy 51762ec8b982132c250aa5a603978bc7147eae67](https://github.com/battlecode/galaxy/tree/51762ec8b982132c250aa5a603978bc7147eae67),
the 2023-era source baseline, not a proven production deployment revision.
`seasons/2023/replica/source-manifest.json` records SHA-256 for the audited files.
The test runner requires a separately materialized checkout and fails on byte drift:

```sh
GALAXY_SOURCE=/absolute/path/to/galaxy-source \
  python3 seasons/2023/replica/test_contracts.py -v
```

Use the release's pinned Nix Python when available. The initial builder probe ran
with the pre-existing Python interpreter, without installing dependencies. Passing
these tests is not evidence of a reproducible Django/Go dependency closure.

The tests parse hash-verified source and execute selected **unmodified historical
method bodies** with explicit in-memory doubles. They never import Django, contact
cloud services, start listeners or execute candidate code. These are method-level
contract probes, not API integration tests. In particular, the URL reverse double,
model instances, relationships, settings, logger and saves are test doubles.
The full-source hashes cover inspected Go files, but do **not** prove Go execution
or cross-language decoding. Fixtures are synthetic, not recordings of production.
The scaffold URI and map names in fixtures intentionally are non-executable placeholders.

Tests cover compile/execute payload generation, participant indexing independent
of relation iteration order, map order, storage names, request side order,
alternation, finalized states, repeated terminal reports, failure budget exhaustion,
interrupted retry behavior, and the development publisher's non-executing receipt.
The compile-rejection fixture demonstrates that task completion `OK!` can have
`accepted: false`; actual serializer validation/transactions remain an integration gate.
The retry test uses a synthetic limit of three; upstream settings use five.

## Fidelity matrix

| Surface | Historical contract | This foundation | Next proof needed |
| --- | --- | --- | --- |
| API and domain | Django/DRF submission, request, match, participant, episode, team models and permissions | Original source retained as pinned dependency; no replacement scheduler | Boot original backend; migrations and upstream API tests |
| Submission | Multipart `source_code`, package, description; cloud object upload then compile enqueue | Historical payload method tested with synthetic objects | Real upload persistence, permission checks, compile and callback |
| Requests | `P` pending, `Y` accepted, `N` rejected; acceptance snapshots active submissions into distinct matches | Actual code values and order methods tested | Database transaction, active-submission selection, rejection/cancellation, duplicate acceptance |
| Match | Participant index and submission identities, ordered maps, `alternate-order`, replay bucket/name | Actual payload method matches golden fixture | Real worker consumes payload and preserves side semantics |
| Invocation lifecycle | `NEW`, `QUE`, `RUN`, `TRY`, `OK!`, `ERR`, `CAN` | Actual finalized/update methods tested | Real DRF validation, persistence, transaction and callback auth |
| Compile result | `OK!` + `accepted: false` is bot rejection, not infrastructure retry | Synthetic report distinction tested | Java verification failure and successful binary through original recipe |
| Retry | Interrupted `TRY` does not consume failure count; other `TRY` does; terminal callbacks conflict | Historical update body tested | Duplicate delivery, restart/recovery and concurrent callback behavior |
| Queue | Pub/Sub subscriber ACKs malformed JSON, NACKs handler error, ACKs successful handler | Audited only; development NullPublisher receipt tested | Local durable transport preserving observable semantics; no claim of production Pub/Sub equivalence |
| Worker | Original Saturn Task/Runner/Finisher and Java8 recipes | Audited, hash-pinned only | Package/build actual Go code; injected local queue/storage/reporter |
| Storage | `{bucket, name}`; source.zip, binary.zip; replay path ending `.bc23` | Historical path/payload methods tested | Local private objects, path confinement, archive limits and retained artifacts |
| Reporting | HTTP POST, `invocation` with status/logs/interrupted; HTTP 409 aborts worker | Backend update method tested; Go reporter audited | Local-only reporter/auth adapter and real cross-language roundtrip |
| Engine/scaffold | Clone/pull scaffold, `update -PonSaturn=true`, verify/run, binary/replay upload | No worker execution | Pinned offline scaffold/release adaptation; alternate-order bridge |
| Ratings/brackets/Titan/email | Separate asynchronous and external services | Not implemented; must remain disabled | Explicit local behavior/deviation and no accidental external side effects |

## Exact dependency inputs and coupling

The authoritative Python input is
[`backend/environment.yml`](https://github.com/battlecode/galaxy/blob/51762ec8b982132c250aa5a603978bc7147eae67/backend/environment.yml):
Python 3.10, Django 4.1.2, DRF 3.14.0, drf-spectacular 0.24.2,
django-configurations 2.4, sortedm2m 3.1.1, django-storages 1.13.1,
SimpleJWT 5.2.2, structlog integration 4.0.1, Google auth 2.13.0,
Pub/Sub 2.13.10, Secret Manager 2.12.6, Storage 2.5.0, Scheduler 2.7.3,
Tasks 2.12.0, plus its remaining pinned packages. This is a Conda/pip environment
specification, not a Nix closure or complete transitive lock. Do not execute its
historical installers. Packaging in approved pinned Nix remains work.

[`saturn/go.mod`](https://github.com/battlecode/galaxy/blob/51762ec8b982132c250aa5a603978bc7147eae67/saturn/go.mod)
uses Go 1.18 and direct dependencies Pub/Sub 1.28.0, Secret Manager 1.9.0,
Storage 1.27.0, go-git/v5 5.5.1, mapstructure 1.5.0, zerolog 1.28.0,
x/sys 0.3.0 and Google API 0.105.0. Both go.mod and go.sum are hash-bound here.
A successful Nix Go dependency closure/build is still required.

Critical seams from the pinned source:

- `gcloud/saturn.py`: disabling cloud actions returns NullPublisher. Its successful
  future returns `none`; enqueue still advances records to `QUE`. This is not a worker.
- `SubmissionViewSet.create`: cloud-disabled mode also skips source upload entirely.
  Turning on the production flag is not a valid local-storage implementation.
- `models.py`: callback URLs hardcode `https://` plus `ALLOWED_HOSTS[0]`. A loopback
  HTTP reporter requires an explicit local URL adaptation, not just a host setting.
- `saturn/pkg/run/scaffold.go`: Compile/Execute directly construct GCS clients,
  despite the lower-level StorageClient interface. Introduce an explicit injected
  storage factory in a reviewed patch; swapping an environment variable is insufficient.
- `Scaffold.Refresh`: hard reset, clean and **pull**; Java8 Prepare runs **update**.
  Replace moving acquisition with a pinned prepared scaffold and offline distribution.
  Keep this separate from writable candidate sources and preserve upstream recipe steps.
- Saturn config uses Secret Manager/git credentials; the local path must not read
  those or fall back to application-default credentials.
- `Java8Scaffold.RunMatch` forwards `-PalternateOrder`; verify scaffold forwards that
  to the BC23 engine. Score extraction counts winner lines and can return `[0, 0]`
  with no lines: do not silently treat missing result evidence as a valid played match.
- `get_replay_url` constructs a GCS public URL; Java8 UploadReplay requests public
  upload. A private replica needs an explicit private retrieval adapter. Preserve
  object identity without making trial artifacts public.
- Report endpoints require admin permissions; GCP ID-token reporter is cloud-bound.
  Any future local authentication setup needs its own authorization/security review.
- Development uses SQLite; tournament queries use PostgreSQL-specific distinct-on
  and can return 501 on unsupported databases. SQLite is not full domain fidelity.
- Cloud Tasks handles rating/bracket work separately. Email, scanning/Titan, cloud
  scheduler and bracket integration need explicit disabled/local behaviors.

## Next bounded integration gates

1. Package historical backend and Saturn dependencies through pinned Nix. Run the
   original Django model/API tests before changing behavior. Choose PostgreSQL if
   full historical query fidelity is required; document any SQLite-limited scope.
2. Add small reviewed injection seams to the original backend/worker for local
   storage, queue, report transport and frozen release/scaffold. Do not replace
   request acceptance, match creation or worker recipes with a bespoke scheduler.
   Select a durable local queue only after testing ordering, retry, cancellation,
   replay after process interruption and recovery against this lifecycle.
3. Create synthetic episode/users/teams/maps in isolated state. Bind listeners only
   to loopback, deny production egress, and verify no GCP/default credentials or
   external actions are used. Preserve private source/binary/replay identities.
4. Exercise real multipart upload → stored source → compile → binary → callback;
   then request acceptance → actual match → replay upload → scores → terminal
   callback. Test invalid source, interrupted work, duplicates and exhausted retries.
5. Freeze those passing integration results, dependency closure and documented
   deviations before exposing an API to a competitor. These contract probes alone
   do not satisfy the replica readiness gate or authorize a fresh-agent trial.
