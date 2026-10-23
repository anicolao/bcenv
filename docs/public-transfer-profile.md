# Optional public transfer profile (prospective)

`public-transfer-v1` is a separately named opt-in profile. Existing
`resumable-v1` and legacy profiles retain their defaults and schedules. This
increment is code/pure-test preparation, not a new trial or runtime qualification.

## One optional learning intervention

The author may request `transfer` with a completed ordinary `submission_id` and
an unused ordinary source-bound `note_id`/`note_sha256`. The service revalidates
that original's committed manifest, copies its exact source/build/package, checks
the copied build identity, and runs the fixed transfer schedule without rebuilding.
No new source bytes are accepted by this operation. The same single batch executor,
maximum two pending batches, absolute deadline and cancellation token apply.
Replay access may still overlap background batch work.

At most two optional eight-cell transfer batches are available: two public
opponents × two transfer maps × both sides. Each admission atomically consumes one
of the existing 24 version/batch slots and reserves eight of the existing 192
training-game budget. The locked final validation remains 16 games, hence maximum
208 total. Transfer cells are recorded when admitted but are not debited twice.
Status separately reports reserved transfer games and physically admitted cells;
reservation does not assert a process was launched or a game completed. With both
optional slots used at most 22 ordinary batches remain; unused slots do not reduce
the ordinary 24. Existing six-hour clock, 72 notes, 450 replay queries, 2,000 reads,
2,200 mailbox requests and 1 GiB service-storage detection remain unchanged.

A source/package plus transfer-schedule effect can be admitted only once across
QUEUED, RUNNING, COMPLETE and UNKNOWN, even under a new delivery ID or an alias
ordinary submission. Concurrent duplicate checks and all reservations are one SQL
transaction. UNKNOWN work remains unscored, not refunded or retried; existing
conservative UNKNOWN-source restrictions also remain. No forced usage, full-budget
requirement, perfect-test rule or new strategy-specific acceptance condition exists.

Transfer batches cannot become finalists, original source submissions, or inputs
to further transfer operations. Their responses and replay references retain the
original ordinary ID, normalized source/package, compact verified build-identity
digest, transfer schedule and effect digests. Original full manifests remain
retained evidence. Phase closure, artifact verification and epoch fencing apply.
Recovery copies only committed own evidence; it is not turnkey native-task recovery.

## Public map provenance and deterministic choice

`services/public_transfer_maps.py` reads bounded FlatBuffer dimensional metadata
without invoking the engine. Its parser follows pinned official GameMap/Vec and
GameMapIO sources. A complete bundle's basename and byte inventory must equal the
map resources in the pinned official engine JAR. Invalid, unreadable or missing
maps, duplicate basenames, symlinks and missing public exclusions abort preparation.
This is dimension/schema validation, not proof of full engine legality.

Only declared ordinary-training and final-validation maps are excluded. Width and
height ranges use the entire pinned corpus before exclusions. The first map
maximizes its minimum normalized Manhattan distance from the two ordinary maps;
the second includes the first selected map among anchors. Arithmetic is exact
rational; zero-range dimensions contribute zero; ties use Unicode-codepoint
basename order without case folding. No private membership, outcomes, competitor
corpus or strategy enters the rule. Full proof bytes, parser identity, corpus hashes,
selection and exact schedule are fixed before any runtime plan.

Configuration adds `transfer_games`, its canonical JSON SHA256
`transfer_schedule_sha256`, and `transfer_selection` containing the absolute proof
path and SHA256. The complete proof/corpus is revalidated outside SQL, while its
hash and actual schedule input bytes bind the authority. The metadata-only local
preparation selected **Contraction.map23** then **BatSignal.map23** from 103 maps.
That demonstrates geometric distinctness, not difficulty or generalization.

## Interpretation and gates

Transfer feedback is learning evidence, never hidden or finalist validation. Keep
ordinary and transfer blocks separate; unequal map exposure cannot be pooled into
one leaderboard or treated as a matched score change. Optional uptake, later
source revisions and explicitly cited feedback are descriptive process measures;
a single fresh run cannot distinguish motivation, utility and model variability
causally. Frozen final validation and any separately sealed evaluator remain
unchanged; private outcomes must not adapt this public rule.

A fresh trial needs separately reviewed candidate documentation/client adaptation,
fixed schedule/proof export, public parity plan, exact-code CI and normal launch
gates. This code increment authorizes none of those executions. Physical orphan
CPU containment, engine in-flight cancellation and native-context durability are
not established by pure/mocked tests or SQL fencing.
