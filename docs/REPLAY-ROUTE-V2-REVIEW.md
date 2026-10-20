# Independent durable replay-route review

Reviewed 2026-10-20 UTC. **No remaining must-fix defect found in the reviewed offline scope.** Three bounded issues found during review were corrected and independently rechecked. This verdict does not authorize deployment or establish real-game/provider authenticity.

## Exact scope and evidence

The reviewed staged route files are based on `d769760da47cedb4acf6c44b24497a7f29ef1fc5`, composed with the separately reviewed producer/projector commit `f3963b7f16857bf6aad837196b8262441ff7c1e3`. No author files were edited by this reviewer. The review did not repeat Java/projector semantic review or run an engine, real model, network provider, listener or heldout trial.

- Independently rebuilt the composed repository's pinned-Nix Python check: **286 tests passed**, 3.791 seconds, no ResourceWarnings. Log: `review_durable_replay_repository_check.log`.
- Independently authored and ran **27 additional pinned-Nix probes**, all passed in 0.530 seconds. Log: `review_durable_replay.log`; derivation `/nix/store/wacpappb7z65jdq2crh8s6ysizpn14nk-bcenv-independent-durable-replay-review.drv`.
- Both working-tree and staged diff whitespace checks passed. All eight reviewed files matched their staged bytes.

The executable probe is `review_durable_replay.py`. It takes a composed source-tree path and `review_replay_v1_registry_baseline.json`. It uses the repository's pre-existing ZIP/result/raw-row fixture helpers but defines its own provider, process races, fault injection and composed Responses exchange. Its replay bytes and completion association are deliberately fabricated synthetic fixtures.

## Findings fixed before this verdict

1. **Route expiry decoding used an incompatible int32 domain.** With supported clock `2**31`, ordinary completion produced manifest expiry `2147487248`; the store succeeded but route projection raised `FeedbackRejected`. A permitted large TTL made the same issue reachable without advancing the clock. The route now uses a bounded, duplicate-rejecting, depth-limited envelope decoder with a wider integer domain; individual game fields retain their narrow checks. Malformed manifest projection is normalized. Independent probes passed at `2**31`, `2**53+3`, and `2**60-1`, including TTL `2**49`.
2. **Unbound legacy IDs exposed existence across run scope.** A same-principal unrelated run received `replay_not_available` for a legacy job and `not_found` for a nonexistent job. The v2 store now returns `not_found` before exposing an unbound job. Bound foreign-run, unbound, foreign-owner and missing-ID cases remain closed; v1 withholding is unchanged.
3. **Boolean manifest continuation passed integer equality.** A one-entry manifest of total two accepted `next_manifest_offset=True`. This was independently reproduced while the author was tightening the same validator. Exact nullable integer validation, manifest extent/distinct-ID checks and page count/progress consistency checks are now present. Boolean continuation and malformed envelopes are rejected.

## Durable invariants exercised

- Eight independent processes concurrently initialized a nonexistent database and submitted the same request: one job and one run binding. Competing configurations produced one complete configuration and closed mismatches, with no partial extension schema.
- A binding-insert failure rolled back jobs and accepted-source payloads. Failure on the second replay registration rolled back the entire terminal result, completion marker and replay batch.
- Exact completion repeats after source expiry returned only the immutable prior result without another provider call or expiry extension. Changed owner/run, worker/generation, evidence or registration bytes were rejected. Legacy `finish` could never attach a replay afterward.
- Actual SQLite commit-then-error injection produced `storage_unavailable` with an unknown durable outcome and no provider call or implicit retry. Post-completion uncertainty was inspected through the exact-repeat path without a second provider invocation. Existing tests also exercise uncertain page publication without a compensating failure write.
- Fractional clock rollback through a legacy ledger read committed replay-attempt expiry. Restart preserved fencing; stale generations, foreign submission/view combinations and altered reservation identities could not publish.
- Accepted-source corruption during a callback prevented page release. Integrity probes use controlled corruption of a trusted fixture database; they do not claim protection against a malicious database administrator.
- Eight processes competing for one active page admitted exactly one attempt. Failed attempts retained cumulative charges across restart. Expired source rows remained charged and immutable. Identical and distinct cross-owner byte guesses failed equally at a full logical source-byte cap. A failed repeat retained the first successful fingerprint.
- Minimum 4096-byte payload configuration continued to paginate with exact positive progress to EOF. Resource exhaustion remained a closed error, never a false EOF.
- Both explicit supported profiles were exercised. A `3.0.13` policy produced bound `3.0.13` manifests/pages and rejected a `2.0.3` receipt echo without version inference.

## Controller and Responses protocol audit

Default and tightened v1 registry bytes were compared against a baseline captured from `d769760`; both were identical. The default v1 controller still returned `replay_withheld` when composed with the new v2 adapter. The v2 registry remains an explicit host selection with the same six tool names. Both v1/v2 registry mismatches were rejected by the offline transport before any exchange.

Every Responses tool was verified as a flat function definition with `strict: true`, a closed object, and every property required. Inactive operation fields are nullable; runtime parsing separately rejects inactive non-null fields, unknown keys, malformed IDs and booleans used as integers. This matches the documented strict-schema requirements and flat Responses shape in the [official OpenAI function-calling guide](https://developers.openai.com/api/docs/guides/function-calling), checked on the review date. No live API conformance call was made.

The v2 complete result-envelope ceiling remained 16384 bytes even when trusted controller settings requested a larger limit. An escaping-heavy result was replaced by the bounded failure envelope. Aggregate admission succeeded at the exact summed output length and failed one byte below it without appending an over-budget result. V1's configured behavior was preserved.

### Composed offline retrieval proof

The independent executable drove the actual `OfflineResponsesTransport`, `CapturingSandboxAdapter`, immutable source store, durable submit/completion route, v2 dispatcher and sanitized replay projection. It used all six tools and reopened the durable ledger/store between pages. Frozen initial context and the exact selected registry stayed unchanged on every exchange.

Measured result:

- 11 tool calls; 5 replay pages; all 196 synthetic fixture records retrieved once in order
- Maximum complete tool-result envelope: **11476 bytes**
- Aggregate complete tool-result envelopes: **50406 bytes**, below the default 131072-byte budget
- EOF only after all records; no private fixture marker, raw replay bytes, source digest or producer/projector identity in released results

This is a fabricated offline association and deterministic provider proof. It does not extend the separately reviewed public replay producer proof into evidence of a real service execution boundary.

## Reproduction and hashes

Workspace command for the independent probes:

```sh
/workspace/scratch/1523a3f802eb/nix-canonical-root/nix-root build \
  --impure --file /workspace/shared/review_durable_replay.nix \
  --option sandbox-paths '' --no-link -L
```

The Nix expression overlays only the six route-owned service files on exact producer commit `f3963b7`; it obtains Python from the repository's pinned nixpkgs. For a published composed checkout, the executable is portable:

```sh
python review_durable_replay.py /path/to/composed-checkout \
  review_replay_v1_registry_baseline.json
```

Full file hashes and staged Git blob identities are in `bcenv-durable-replay-review-sources.json` (SHA-256 `c3f525d43a985c419b4a019594455039f7bea023ed58a3f83bf395a3ef57ab12`). Reviewed production SHA-256 values:

| File | SHA-256 |
| --- | --- |
| `services/ledger.py` | `a42d57e2e7dcad7ad40bbe76295d58a9830ec7eb283998490545b2491ee9bd3e` |
| `services/controller.py` | `cddf84fca4ae18bc25296f64cdec0fe0bd18c1242fbbdfafe925c0641feac2bf` |
| `services/responses_transport.py` | `75552cad3465bf7a4cc5cb700989d1824e9c05f28a72a876c4dde3e91ac185bd` |
| `services/replay_store.py` | `c143d79f54253b8ef36402b5f1e911b91e6b8876d445eb67c0d0c6f27fea1129` |
| `services/replay_route.py` | `2763d59771e5b8a71a566687a2031e998fb28b99e32c4206a92e8ddb7f708f81` |
| `services/replay_protocol.py` | `09eeab010910c7b41a0b473e37c05c189db734f755079f28fe9b923b15387486` |

The earlier `COMPETITIVE-INTEGRATION-SOURCES.json` is a historical integration baseline, not a current hash manifest for these changed controller/ledger/transport bytes or the three new replay modules.

## Gates still open

Publication still requires the parent's clean composition, full x86_64 flake check and final competitor-package inspection excluding all ten trusted service modules. Those final-tree checks are not claimed by this staged review.

Deployment still lacks authentication/principal binding; independently tested filesystem/network/tool isolation; physical process admission, timeout and teardown; validated immutable real-engine evidence bound to outcomes; measured/controlled producer execution; real RPC resource bounds; disk/WAL lifecycle; supported live inference transport and approved trial budgets; and an approved opponent/replay release policy. The callback precheck race and lack of preemption remain explicit. SQLite recovery/expiry does not stop workers or reclaim their OS resources. No real-launch or heldout gate was opened.
