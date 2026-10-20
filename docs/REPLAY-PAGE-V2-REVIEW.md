# Independent review: bounded replay pages v2

## Verdict

No blocking implementation defect found in the reviewed producer, CLI receipt or
standalone sanitizer. The observed behavior supports **bounded replay extraction
and projection**, not an authenticated service, accepted worker, heldout access,
whole-replay authenticity proof, or competitive-strength claim.

The author-confirmed stable code/test checkpoint was copied before execution.
Review covered the uncommitted changes in `feat/replay-page-v2`, based on
`d769760da47cedb4acf6c44b24497a7f29ef1fc5`. Code/test hashes were rechecked against
the author's worktree after all probes; every hash still matched. The new
contract was reviewed separately after it was written. No author files were
changed. No game, bot compile, provider/model call, credential use, network
acquisition, security-setting change or commit was performed.

## Executed evidence

Both offline Nix checks passed on **x86_64-linux**, using the repository-pinned
JDK8, Python, and each official engine profile (3.0.13 and diagnostic 2.0.3):

- Author `ReplaySummaryTest` and `ReplayQueryTest` passed for both profiles.
- 73 focused Python tests (`test_replay_feedback`, `test_replay_feedback_v2`,
  `test_replay_query`) passed in each check.
- An independent fixture runner exercised 96 combinations of one/two matches,
  explicit missing-round events, six windows, and four robot selections. It emitted **9,908
  actual Java-produced pages per profile**, using limits 1, 2, 3, 7 and 10,000.
  Each page matched the v1 full-window reference slice exactly. Global kind
  counts and per-match coverage remained identical across pages. Every page was
  then accepted by the actual v2 Python projector and compared with v1-sanitized
  reference rows. This includes pages beginning/ending inside resource pairs,
  crossing match boundaries, round zero, unknown identities, and empty/EOF cases.
- 64 independent cases per profile exercised byte/record caps, strict positive
  continuation progress, metadata injection stripping at every retained level,
  and rejection of an invalid final supplied row hidden behind a local cap.
- Five further actual Java-produced pages per profile used the 12,053-record
  fixture, including offsets 9,999, 10,000, 12,050 and exact EOF. Direct expected
  indexes confirmed late negative health targets, `DIE_EXCEPTION`, and death
  survive both producer and sanitizer beyond the old 10,000-record cap.
- The author tests additionally verified complete-stream validation outside the
  selected window and after a filled page, invalid-tail precedence over actual
  EOF rejection, identity processing before a late offset, and full retrieval
  without gaps/duplicates.
- Source `git diff --check` passed.

A pre-existing public replay was queried, never replayed as a new game. Its
SHA256 remained `62a5a7aee958455efc2a4d1dae7f0a11872d92048d845fec7a2001e75c4a59cd`.
For rounds 1–256/all robots, the producer counted **27,150** matching records.
Packaged `bcenv replay page` returned the correct seven-row slices at offsets
0, 1 and 10,000; offset 27,150 returned an empty terminal page. Out-of-range,
negative, 5,000,001 and 2,147,483,648 offsets failed. Both the summary count and
match coverage remained whole-window values on every page. Original v1 output
was byte-for-byte identical to code compiled from the base commit against the
same profile. The legacy parser rejected the persisted 3.0.13 replay with no
rows; projector unit tests enforce exact expected versions in both directions.
This review did not independently run an actual 2.0.3 replay through the 3.0.13
page CLI. It did build/run the synthetic producer/projector checks against both
profiles.

## Code and contract conclusions

1. The Java offset is counted **after** inclusive round/robot filtering; team
   deltas intentionally remain selected with a robot filter. The collector
   skips allocation before the offset and after its cap, but does not stop
   traversal/counting. Summary core-array validation precedes collection and
   actual-EOF checking. Query-only identity/ID handling also traverses all rows.
2. Offset/count ceilings are 5,000,000. Java span arithmetic casts to `long`
   before subtraction/addition; page-end arithmetic also uses `long`. Python
   validates exact integers and rejects booleans. The bounded reads reject at
   64 MiB before their integer byte counters could overflow.
3. The projector requires the host's exact window/robot/offset/limit and expected
   rules version, validates every supplied row before truncation, and treats
   unknown boundary records as unknown rather than declaring them absent.
4. Continuation is `offset + actually released rows`, not the source page end.
   Byte truncation retains whole rows and all coverage; a nonterminal response
   cannot silently return zero progress. Exact EOF is distinct from a complete
   selected window and from absent requested rounds.
5. The rebuilt allowlists strip source paths, map/package/name metadata,
   hashes, logs and injected arbitrary strings. Raw gameplay fields remain by
   design; this is not anonymity or fog-of-war protection. V1 implementation
   remains unchanged and v1/v2 formats cannot be silently interchanged.

No required claim correction was found in `contracts/replay-feedback-v2.md`.
Its explicit limits are important: shape checks are not source authenticity;
the host must establish owner/run/job binding, immutable replay identity,
parser/process integrity, per-view producer/projector version binding, aggregate
budgets and outer-envelope byte bounds. Paging reparses the bounded complete
source on each request, so CPU/time/concurrency limits remain a host obligation.
No aggregate flake check was run: existing aggregate checks include fixture
games. No aarch64 execution or service/controller integration is covered here.

## Exact reviewed hashes

| File | SHA256 |
| --- | --- |
| `seasons/2023/replay/ReplayQuery.java` | `dc3edbe7af68353dfaa4a18e22a0a00a87cb4e1381dd0a4cea1d4e72e412ebbe` |
| `seasons/2023/replay/ReplayQueryTest.java` | `631d9cee3336c0c7ccdbd53b2fed2c05cd5464c91f7ff2f3c5dd25b324324515` |
| `seasons/2023/adapter.py` | `356a42cc9429c75bdf485a752ff633eb82e4353d297027102bad2f6168fc53b7` |
| `services/replay_feedback.py` (unchanged v1) | `bd68a1c4a768a07cc90f7d0cd65017053d2d542fb9fc48f9fcc9b59d235be865` |
| `services/replay_feedback_v2.py` | `d1864505c392a520f69f38aa9f865be940c300bc884fce5c506045d92695f927` |
| `tests/test_replay_feedback_v2.py` | `29af75bcbdf416cb46d6e8e928eafd6c8a9dcbec2b6bd8b9184592bbcf451abf` |
| `tests/test_replay_query.py` | `6c66852859694c167f9d09f16b8b247bfcd36c2c852d8b8d2537be2a469cdd89` |
| `contracts/replay-feedback-v2.md` | `2a913c3fb20ba4ae40b3eec6cc5fa58e9a7b6a8d6eec7d960db0d82ff1a74e82` |

## Reproduction and artifacts

Review harness, captured code/test snapshot, copied public replay, source hash
manifest, initial harness-failure logs and passing results are under:
`/workspace/shared/bc23-replay-page-review/`.

```sh
/workspace/scratch/1523a3f802eb/nix-canonical-root/nix-root \
  --option sandbox-paths '' build --offline --impure --no-link --print-out-paths \
  -f /workspace/shared/bc23-replay-page-review/review.nix final diagnostic
```

Sandbox remains enabled. This uses already acquired pinned closures and performs
no fetch. Result paths:

- `/nix/store/qf9ivv2yqzrfr1scd9k4slfrlgdb2vb2-bcenv-independent-page-review-3.0.13`
- `/nix/store/a6xplg0d3mqqcxdbxg0lasxc8q8ia7iq-bcenv-independent-page-review-2.0.3`

The selected Nix store is rooted beneath
`/workspace/scratch/1523a3f802eb/nix-canonical-root/root`; convenient result copies
are also in `results/3.0.13/` and `results/2.0.3/`. Each contains Java and Python
logs, machine-readable independent-probe counts and replay-query receipts.
The review harness initially needed two corrections: copying a Nix store input
to its Java public-class filename, and accounting for the encoded byte-limit
field's changing digit width in a test threshold. Neither was an implementation
failure; final results above come from the corrected, preserved harness.
