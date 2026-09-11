# Historical learnings

## Central finding

The strongest recurring pattern in the reviewed Battlecode accounts is a development process: understand the particular game, build a working economy and combat policy, examine failures, and test changes against varied opponents. Successful bots frequently combine relatively compact strategic rules with sophisticated navigation, communication, and implementation techniques. Their sophistication does not by itself establish machine learning or autonomous authorship.

For bcenv, the important opportunity is to automate that development process. A language model that writes a bot is only one component. The environment must let an agent form hypotheses, preserve candidate versions, run trustworthy comparisons, inspect individual games, and deliver the artifact it actually validated. This is an inference from the evidence below, not a demonstrated recipe for autonomous tournament success.

CodeClash is a close precedent for autonomous competitive code development, and concrete Battlecode learning experiments and AI-assisted repositories exist. However, the reviewed evidence does **not establish a fully autonomous agent winning MIT's annual Battlecode competition**. AI-generated code, a learning environment, a published tournament bot, and a successful autonomous competition campaign are different achievements.

## Scope and strength of evidence

This draft covers material available through September 11, 2026: early retrospectives, season accounts from 2014–2026, source repositories, learning experiments, and recent autonomous-coding infrastructure. MIT's past-years index supplies the principal season and placement reference; the Battlecode Archive provides additional discovery leads. Neither index implies that every linked repository was audited or executed.[^1][^2]

The review prioritizes participants' own postmortems, organizer materials, research publications, and inspectable code. Described implementations were read, not reproduced. Tournament placements are contextual evidence, not controlled measurements of individual techniques. Performance numbers from authors remain self-reported. Repository snapshots are pinned where practical; mutable pages may change.

Coverage is broad rather than exhaustive. Public documentation favors successful teams and recent seasons; private bots, missing sites, unpublished failures, and videos without inspected technical content remain gaps. Older competition history is especially sparse here. Two MIT-hosted postmortems were accessible through indexed primary-page text but not direct retrieval; their entries are deliberately narrow and their source notes identify the limitation.

The word **AI** needs four separate meanings in this review:

| Category | What makes the decisions? | What would count as evidence? |
| --- | --- | --- |
| Hand-written game AI | Rules, search, state machines, scoring functions, or distributed algorithms authored by people | Strategy descriptions and bot implementation |
| Learned game policy | Parameters or policies fitted through supervision, evolution, or reinforcement learning | Training procedure, exported policy, and evaluation in the relevant engine |
| AI-assisted development | A person directs development while a model writes or analyzes parts of the bot | Documented model contributions and human involvement |
| Autonomous development | An agent owns the sustained rules-to-submission loop | Recorded campaign, bounded human intervention, exact artifacts, and competition results |

MIT's overview explains the competition format and current language support, but its general discussion of AI should not be read as a census of every learning experiment. Eligibility and submission conditions also depend on the event; an environment's ability to play matches does not establish eligibility for every tournament.[^3]

## Hand-written competition approaches

### Early experiments and the 2014–2018 champions

Greg Little's retrospective describes trying genetic parameter optimization in 2008. Even with a 16-core machine, only a few generations were practical, and new strategic ideas lay outside the parameter space being optimized. The useful lesson is not that evolution cannot work; it is that tuning a fixed strategy cannot discover an idea the representation excludes.[^4]

The 2014 winner, **that one team**, used headquarters computation and broadcast to distribute breadth-first pathfinding information. Robots could use bug navigation while that information was incomplete. Debug indicators and retained versions accompanied the algorithms. This is an early example of separating globally useful computation from cheap local action.[^5]

The 2015 winner, **the other team**, moved some pathfinding work into miners' spare bytecodes, with launchers consuming the resulting navigation information and retaining a fallback. Resupply behavior mattered alongside combat. The author also describes a specialized strategy for protected ore that never encountered its intended situation in the finals: a technically complete feature can still have little competitive value.[^6]

The 2016 winner, **future perfect**, coordinated scouts and turrets through relayed observations, cached radar information, and movement decisions about when to pack or unpack. Its README explicitly says the anti-turtle mass-charge behavior was unused in qualifiers and finals because it did not face that strategy. That distinction matters: source code containing a counter does not prove that counter caused a tournament win.[^7]

The winning **Arbitrary Graph Restoration Fund** repository in 2017 and **Orbitary Graph** repository in 2018 preserve tournament scripts, map-wide testing workflows, and version history. These are useful process precedents. Brainstormed strategies in repository notes should not automatically be treated as the final winning policy.[^8][^9]

A counterexample to studying only champions is Stuart Johnson's 2017 account: substantial architecture work preceded a viable economy, and the competitive meta changed while planned features were being built. Its lesson for an autonomous developer is to keep a playable reference bot and shorten the interval between an idea and a real game.[^10]

### 2019: compression, formations, and the submission boundary

| Team or account | Documented approach | Transferable lesson |
| --- | --- | --- |
| **smite**, winner | Grouped resources into clusters, allowing compact cluster identifiers; coordinated economy and prophet formations, including sparse and dense lattices. | A representation chosen around the strategy can save communication bandwidth and simplify coordination.[^11] |
| **Oak's Last Disciple** | An initially strong rush-oriented approach encountered a shifting game balance and later tournament disappointment. | An early ladder result is conditional on its rules, maps, and opponents.[^12] |
| **Big Red Battlecode** | Described prophet formations and church-chain behavior exploiting the season's production and turn mechanics. | Execution order and production rules belong in strategic analysis, not just API documentation.[^13] |
| **Double J** | Developed a church spear and denser formations; also described a feature implemented in the wrong file and absent from the submission. | Validation must identify the exact submitted source and artifact, not merely a workspace believed to contain the change.[^14] |

These accounts contain reusable ideas, but church chains, prophet lattices, and resource-cluster encodings are solutions to a particular ruleset. The transferable asset is the reasoning and testing method, not a universal opening build order.

### 2020: economy, terrain, and coordinated attacks

The 2020 winner, **Java Best Waifu**, combined a terraformed economic lattice with coordinated drone and landscaper attacks. Its economy and defense supported the eventual attack rather than existing as independent subsystems. This illustrates why evaluating only combat micro can miss the source of a bot's strength.[^15]

**The High Ground** emphasized surviving early aggression while building the economy; terrain and defensive coverage affected whether a position was actually safe. **confused** described defensive construction and reinforcement but also the threat of concentrated drone attacks. A defense that works against trickling attackers may fail against coordination.[^16][^17]

**Bowl of Chowder** documents evolving strategy and bot versions as the competition progressed. The accessible **Battlegaode** account distinguishes rush, defensive, and economic styles. Together these support maintaining multiple strategic opponents rather than treating one current rival as the entire game. The Battlegaode entry rests only on indexed excerpts, not a full technical audit.[^18][^19]

### 2021: information and economic protection

| Team or account | Documented approach | Transferable lesson |
| --- | --- | --- |
| **Baby Ducks**, winner | Developed muckraker-rush and slanderer-economy approaches, with communication protecting economic units; the account discusses slower maps helping its later strategy. | Strategy selection depends on the map distribution, and defensive information can have economic value. This summary uses indexed primary-page text.[^20] |
| **Malott Fat Cats**, fourth | Used flanking behavior to send threats around contested fronts. | A local objective can distract units from the opponent's vulnerable economy.[^21] |
| **wololo** | Used distributed Bellman–Ford-style information propagation and local collective behavior. | Coordination need not require one centralized planner, but depends on what communication permits.[^22] |
| **California Roll / Chop Suey** | Described tactical adaptation and flanking through procedural behavior and match observation. | Adaptation during a match does not, by itself, mean a learned policy.[^23] |
| **3 Musketeers** | Adjusted bidding using previous outcomes and bounded estimates. | Small adaptive controllers can solve a subsystem without a general learning pipeline.[^24] |
| **Ivy Zhang** | Compressed information into flags; reported a turn-equality bug and voting-related failure. | Boundary turns and victory conditions deserve explicit regression cases.[^25] |
| **M.A.R.S.** | Combined scouting, headquarters-mediated coordination, and adaptive bidding; reported weaknesses in economy and defense and a result of 88/559. | An AI research affiliation is not evidence of neural or reinforcement learning. Use the report's own result rather than a different team result on an association page.[^26] |

### 2022–2023: shared state and navigation under constraints

**5 Musketeers** in 2022 allocated shared resources according to strategic priorities. Its report also describes a coordination assumption involving headquarters order that broke when headquarters died. A communication protocol must define membership changes and stale state, not just the normal exchange.[^27]

The 2023 reports provide several distinct navigation and information designs:

- **Gone Fishin'**, runner-up, used stack-based bug navigation, map memory and symmetry inference. Combat units could gather information along useful routes rather than spending economic units solely on scouting.[^28]
- **4 Musketeers**, third, represented the map through sectors to reduce communication cost, optimized local pathfinding for bytecodes, and used automated match testing. Its rare headquarters-placement bug shows why a wide aggregate score does not replace targeted cases.[^29]
- **Don't @ Me** combined local and shared knowledge, inferred symmetric objectives, and used navigation with a fallback. Its account also records experiments with specialized unit behavior.[^30]
- **no thoughts head empty** paired local Bellman–Ford search with bug navigation to escape local minima and account for currents; the authors wanted better automated parallel comparisons.[^31]

These approaches should not be collapsed into “use BFS.” The search horizon, terrain mechanics, dynamic congestion, communication representation, and fallback policy all change the engineering problem.

### 2024: combat scoring and objective reliability

**Cout for Clout**, the high-school winner, describes micro that compares candidate moves by priorities, coordinated flag handling, and specialized responsibilities. Its reported combined ladder position is not an overall championship title. The account also illustrates deliberate reuse of earlier navigation and micro ideas.[^32]

The **muskellunge** postmortem is valuable because it records failure modes: a flag carrier could become trapped, friendly traffic mattered, and increased healing or feature complexity did not reliably improve results. Testing against older versions of oneself missed problems that outside opponents exposed.[^33]

### 2025: state machines, generated code, and economic discipline

**Just Woke Up**, winner, describes explicit soldier states and remembered return locations so units can resume interrupted tasks. Its workflow combined automated matches with replay inspection. It also replaced an expensive spatial structure with cheaper grid-based bookkeeping. This supports measuring both behavioral quality and execution cost.[^34]

**confused**, runner-up, prioritized tower economy and paint efficiency, with scored exploration and local navigation backed by bug navigation. The report emphasizes avoiding wasted time and resources. A bot can become stronger by doing fewer unproductive actions rather than by adding a more elaborate planner.[^35]

**Om Nom**, third, used bit-oriented pathfinding and generated Java through templates to meet runtime constraints. Its economic reasoning included reconstructing information from income and exploiting tower rebuilding mechanics. The reported bytecode costs are author measurements, not independently reproduced benchmarks. Code generation here means programmatic templates, not evidence of LLM authorship.[^36]

**SPAARK**, the high-school winner, stresses scrimmages and replay analysis, discusses changing strategies, and warns through experience about late broken changes. Its account also makes clear that matchup strength can be nontransitive: beating a bot that beats another bot is not a guarantee of beating the latter.[^37]

**The Kragle** organizes behavior through goals with start, execution, and stop conditions. It also describes navigation refinements and the risks of rewriting systems. This is a useful modularity pattern for experimentable bot code, not an architectural requirement for every season.[^38]

### 2026: stronger experiment infrastructure, persistent tactical gaps

**Generalized Stroke's Theorem**, runner-up, separates state updates from behavior selection and optimizes communication and combat choices for a game with movement, orientation, and throwing. Its report also describes bytecode pressure and difficulty validating navigation changes near the deadline. A generic nine-move micro interface would have been too narrow for this season.[^39]

**food** argues for a small set of diagnostic maps that make tactical failures visible, followed by broader validation. This resolves an apparent tension: narrow cases are useful for debugging, but repeated success on them does not establish general strength. Aggregate results can conceal a simple, repeatable failure.[^40]

**3MiceWalkIntoABar** describes state machines, compact navigation state, and the distributed runner Nudge. The author reports that better testing raised benchmark win rate from below 50% to around 70%, and describes hundreds of games within minutes. Those are self-reported workflow results with a particular comparison set, not a general performance promise. The author explicitly says the bot code was not AI-written; this is infrastructure precedent rather than an autonomous-agent result.[^41]

**Lorem Ipsum** records an abandoned AI-assisted parameter tuner: it was too slow and did not produce useful improvements. The team still relied on targeted scenarios, broader matches, and scrimmages. The existence of generated optimization code is therefore especially weak evidence that learning improved a competitor.[^42]

## Learning and AI-assisted development

### Concrete experiments, with their limits

| Project | What the inspected material establishes | What it does not establish |
| --- | --- | --- |
| **StrategyChooser**, repository created 2014 | A neural strategy selector with a GUI for human labeling of maps, backpropagation, and weight output. | An autonomous developer or a documented official tournament result.[^43] |
| **FightMicroGA**, repository created 2015 | Genetic and particle-swarm optimization code for small neural combat controllers, evaluated through a custom simulation and scripted opponents. | That the custom simulator matches the official engine, or that an exported policy achieved a particular placement.[^44] |
| **Salisbury REU project**, 2018 | A poster proposing recurrent models over logs and convolutional models over visual data, with preliminary training results. | A strong playing agent; competitive gameplay remains future work in the poster.[^45] |
| **Re;Battlecode 2022** | A Gym-style environment design with simplified actions and automatic handling of some combat and mining behavior. | Full season fidelity or a demonstrated policy transferred into official competition.[^46] |
| **cattleboys**, 2023 | A postmortem describing procedural strategies, dispatch, and shared-array communication. | Its introductory “machine-learning” label alone does not establish a training procedure or learned policy.[^47] |
| **Ratbot**, 2026 | A repository crediting Claude Sonnet 4.5 as an AI pair-programming partner, with development notes and testing material. | Independent ownership of the entire campaign or a verified tournament placement.[^48] |

These examples rule out a simple claim that Battlecode has never attracted machine learning. They also show why bcenv should demand explicit evidence at the boundaries: training to policy export, policy export to official engine, and local performance to tournament outcome.

### CodeClash: the closest autonomous-development precedent

CodeClash frames competition as repeated code editing, execution, and feedback. Its agents use development tools and retain a changing codebase across rounds. The paper discusses failure patterns such as poorly grounded fixes and accumulating code complexity. However, its main evaluation covers six arenas, and **Battlecode is not one of those six**. Battlecode appears in Appendix B.1. The paper's aggregate results therefore cannot be quoted as Battlecode performance.[^49]

The later training-arena announcement explicitly includes MIT Battlecode 2023, 2024, and 2025. This establishes available arena support and a proposed training setting; it does not establish a successfully trained annual competition entrant.[^50]

The inspected adapters are valuable starting points, but illustrate what bcenv must verify. The 2023/2024 images use Java scaffolds, while the 2025 adapter uses a Python scaffold. Setup invokes update commands, which would need freezing for reproducible comparisons. The 2025 implementation validates an expected file/function shape and parses selected stdout text for outcomes; short output, ties, and missing scores need stronger treatment before relying on the adapter as an evaluator. These are code-inspection findings, not reproduced failures or an exploit demonstration.[^51]

The practical conclusion is to reuse the competitive coding-loop idea and evaluate existing adapter code against explicit contracts. “Supports Battlecode” is not sufficient evidence of current official-engine fidelity, complete outcome accounting, or tournament-ready submission handling.

### Adjacent projects that should not be mistaken for MIT results

**Cambridge Battlecode 2026** is a separate competition. Ismail Fateen describes using Claude to translate the 2023 4 Musketeers navigation algorithm into that competition's Python API while people directed strategy. The **muteki** repository similarly documents AI-generated methods and self-reports a finalist placement. These are useful examples of AI-assisted adaptation across APIs, not MIT annual competition results or fully autonomous campaigns.[^52][^53]

**Metta AI's cogame-battlecode** exposes a constrained interface: an agent supplies a sealed doctrine for a prewritten bot chassis in a deterministic Nim port. Its README documents parity machinery and accepted divergences. That is a potentially useful policy-search surface, but is different from independently writing an arbitrary legal bot for the official engine. The project's parity claims were not reproduced in this review.[^54]

**ECLAIR Robotics' battlecode-gym** implements a small territory game with its own grid and shooting rules. Despite its name, the inspected environment is not evidence of a faithful MIT season adapter. Similar names must not become evidence of successful transfer.[^55]

## What the history implies for bcenv

### Make experiments explainable, not just plentiful

Nudge is a concrete distributed-runner precedent, with workers and reports containing win-rate comparisons and uncertainty estimates. It suggests useful separation between scheduling and game execution. More throughput is valuable only if the compared artifacts, opponents, maps, and outcome accounting remain stable.[^56]

An agent should be able to move from a failed aggregate comparison to a particular replay, unit, turn, and observable error. It should distinguish “the strategy loses to an early rush” from “the bot spent its turn budget before moving” and “the runner failed to produce a result.” Those explanations imply different next experiments.

A sensible evaluation ladder is diagnostic scenarios, a diverse development suite, and a protected final comparison. This is a proposed synthesis, not a protocol demonstrated optimal by the historical accounts. Diagnostic maps should accelerate understanding without quietly becoming the only definition of success.

### Preserve season-specific mechanics behind stable tooling

The XSquare guide describes how bytecode constraints, communication, navigation, and testing shape Java Battlecode. These are recurring concerns, but exact APIs and costs change. The 2024 specification changelog documents substantial within-season adjustments, showing that even a season label is insufficient to identify an experimental environment.[^57][^58]

The stable bcenv interface should concern development operations—build, run, inspect, compare, package—while a season integration owns rules and engine details. It should not force every game into one permanent action space, resource model, or communication protocol. Cross-season memory should carry hypotheses and provenance rather than assume that an old trick remains legal or useful.

### Treat representation and search space as choices

Historical bots compress locations into sectors, share partial navigation fields, generate code, and use compact tactical scoring. Learning experiments search over strategy labels, network weights, or a fixed doctrine. Each representation makes some discoveries cheap and others impossible.

bcenv should allow an agent to modify strategy and implementation, not only tune a fixed set of parameters. It should also support restricted tuning when justified: a small stable subsystem may be cheaper to optimize than repeatedly rewrite. A learned policy should remain an option to test, not a premise that every competitive solution must satisfy.

### Keep the evaluator independent of the candidate

Custom simulators and parsed logs are useful development aids, but official-engine execution must remain the reference for competitive claims. A candidate must not be able to edit the scoring logic, opponent artifacts, or trusted engine installation that evaluates it. Simulator divergence, missing results, and candidate crashes must be visible rather than silently filtered out.

The current official engine contains the client and replay schema, and the scaffold defines concrete build and run integration points. These are more appropriate anchors for season support than a guessed generic game interface.[^59][^60]

### Measure autonomy separately from playing strength

A human-assisted finalist and an autonomous agent producing a mediocre legal bot answer different questions. Record human strategic advice, manual fixes, model configuration, elapsed time, compute expenditure, and the exact submitted artifact. Distinguish agent development cost from the bot's per-turn execution cost.

Historical materials are useful knowledge for the real competition objective. They also create a research distinction: an agent given winning code for a known season is being tested on adaptation with prior solutions, not clean discovery of an unfamiliar game. A transfer study should specify its information boundary, and should not claim pretrained models are uncontaminated merely because the local workspace excludes old solutions.

## Unresolved questions and research gaps

The public accounts do not establish which techniques causally improve autonomous development under a fixed budget. They rarely provide matched comparisons, full failed-experiment histories, or exact cost accounting. A tournament placement combines implementation quality, strategy, opponent matchups, and the competition's map distribution.

The most consequential missing evidence is a reproducible end-to-end autonomous MIT campaign: rules intake, candidate generation, sustained iteration, submission, official results, and documented human intervention. Other useful gaps are official-engine evaluations of the older learned controllers, reproduced parity tests for substitute simulators, and controlled comparisons between code editing and restricted parameter search.

This document should grow by adding primary evidence and correcting claims when stronger sources appear. An additional repository is most useful when it resolves one of those questions; a larger list alone does not strengthen the conclusions. [INITIAL_DESIGN_SKETCH.md](INITIAL_DESIGN_SKETCH.md) translates the current evidence into a proposed environment architecture.

## Sources

[^1]: MIT Battlecode. [Past Years](https://battlecode.org/past.html). accessed September 11, 2026. Organizer index for season identities, reported placements, and postmortems; not a technical account of every listed winner.

[^2]: Justin Ottesen / Battlecode Archive. [Battlecode Archive repositories](https://github.com/battlecode-archive). accessed September 11, 2026. Discovery index, not evidence that all repositories were executed or audited.

[^3]: MIT Battlecode. [About: format, eligibility, languages, and AI FAQ](https://battlecode.org/about.html). 2026 season; accessed September 11, 2026.

[^4]: Greg Little. [battlecode](https://realgl.blogspot.com/2013/08/battlecode.html). August 2013 retrospective.

[^5]: Greg McGlynn. [that one team: Battlecode 2014 repository README](https://github.com/TheDuck314/battlecode2014). 2014 season.

[^6]: Greg McGlynn. [the other team: Battlecode 2015 repository README](https://github.com/TheDuck314/battlecode2015/tree/845e52bec1a7cb9f3f7f19a3415b5635d44a2fa6). 2015 season.

[^7]: Greg McGlynn and Luchang Jin. [future perfect: Battlecode 2016 repository README](https://github.com/TheDuck314/battlecode2016). 2016 season.

[^8]: Arbitrary Graph Restoration Fund. [Battlecode 2017 repository README](https://github.com/HalfVoxel/battlecode2017/tree/a1401ad156a9504300e56c928b58b515efbb1066). 2017 season.

[^9]: Orbitary Graph. [Battlecode bot for 2018: repository README](https://github.com/HalfVoxel/battlecode2018/tree/a3cf779d3936d930e8c9a5ab2626bc94cb6cbb90). 2018 season.

[^10]: Stuart Johnson. [The Lessons I Learned From MIT’s Battlecode 2017 Competition](https://medium.com/@thestuart/the-lessons-i-learned-from-mits-battlecode-2017-competition-post-mortem-570acdc6c1a2). February 4, 2017.

[^11]: Vivek Myers, Mihir Patel, Nikhil Sardana, and Vinjai Vale. [smite: Battlecode 2019 postmortem](https://battlecode.org/assets/files/postmortem-2019-smite.pdf). February 1, 2019.

[^12]: Ivan Geffner. [Battlecode 2019 Post-Mortem: Oak’s Last Disciple](https://battlecode.org/assets/files/postmortem-2019-oak.pdf). 2019 season.

[^13]: Big Red Battlecode. [Battlecode 2019 postmortem](https://battlecode.org/assets/files/postmortem-2019-big-red-battlecode.pdf). 2019 season.

[^14]: Double J. [Battlecode 2019 Postmortem](https://github.com/programjames/BC19Bot/blob/master/Battlecode%202019%20Postmortem/Battlecode%202019%20Postmortem.md). 2019 season.

[^15]: Ivan Geffner, Java Best Waifu. [Battlecode 2020 Postmortem](https://battlecode.org/assets/files/postmortem-2020-java-best-waifu.pdf). 2020 season.

[^16]: Eli Lifland, Aaron Ho, and Alex Hoganson. [The High Ground: Battlecode 2020 Postmortem](https://battlecode.org/assets/files/postmortem-2020-the-high-ground.pdf). 2020 season.

[^17]: confused. [Battlecode 2020 Postmortem](https://battlecode.org/assets/files/postmortem-2020-confused.pdf). 2020 season.

[^18]: Stone Tao. [Battlecode 2020 Postmortem: Bowl of Chowder](https://stonet2000.github.io/battlecode/2020/). 2020 season.

[^19]: Battlegaode. [Battlecode 2020 Postmortem](https://web.mit.edu/agrebe/www/battlecode/20/index.html). 2020 season. Direct retrieval failed; only indexed primary-page excerpts support the narrow summary here.

[^20]: Josh Brunner, Anthony Grebe, Jason Ye, and Wesley Zhang. [Battlecode 2021 Postmortem: Baby Ducks](https://web.mit.edu/agrebe/www/battlecode/21/index.html). 2021 season. Direct retrieval failed; indexed primary-page text provides the strategy and tournament account.

[^21]: Ivan Geffner. [Malott Fat Cats: Battlecode 2021 Postmortem](https://battlecode.org/assets/files/postmortem-2021-malott-fat-cats.pdf). 2021 season.

[^22]: Isaac Liao. [Battlecode 2021 Postmortem: wololo](https://battlecode.org/assets/files/postmortem-2021-wololo.pdf). 2021 season.

[^23]: Stone Tao. [Battlecode 2021 Postmortem](https://blog.stoneztao.com/posts/bc21/). February 21, 2021.

[^24]: Winston Cheung, Maxwell Jones, David Lyons, and Bharath Sreenivas. [3 Musketeers: Battlecode 2021 Post Mortem](https://battlecode.org/assets/files/postmortem-2021-musketeers.pdf). January 2021.

[^25]: Ivy Zhang. [Winning Elections Without Voting](https://ivyzhang.me/bc21-postmortem). February 1, 2021.

[^26]: Dik van Genuchten, Koen Ligthart, Max de Louw, and Gijs Pennings. [Battlecode 2021 — M.A.R.S.](https://serpentine.ai/wp-content/battlecode_2021_mars_paper.pdf). 2021 competition; cover prints 2020. The report’s own result is 88/559; do not substitute the association’s aggregate project-page placement.

[^27]: David Lyons, 5 Musketeers. [Battlecode 2022 Strategy Guide](https://battlecode.org/assets/files/postmortem-2022-5-musketeers.pdf). February 2022.

[^28]: Carl Guo, Ray Wang, and Yuxuan Chen, Gone Fishin’. [BattleCode 2023 Strategy Report / Postmortem](https://battlecode.org/assets/files/postmortem-2023-gone-fishin.pdf). March 7, 2023.

[^29]: Winston Cheung, Maxwell Jones, David Lyons, and Bharath Sreenivas. [4 Musketeers: Battlecode 2023 Strategy Guide](https://battlecode.org/assets/files/postmortem-2023-4-musketeers.pdf). February 2023.

[^30]: George Zhang, Henry Liao, Parum Misri, and Ray Guo. [Battlecode 2023 Postmortem: Don’t @ Me](https://battlecode.org/assets/files/postmortem-2023-dont-at-me.pdf). 2023 season.

[^31]: no thoughts head empty. [Battlecode 2023 Postmortem](https://battlecode.org/assets/files/postmortem-2023-no-thoughts.pdf). 2023 season.

[^32]: Daniel Qiu, Johnny Liu, Max Wang, and David Wei. [Cout for Clout: Battlecode 2024 Strategy Guide](https://battlecode.org/assets/files/postmortem-2024-cout-for-clout.pdf). February 2024.

[^33]: David Teather. [Battlecode 2024: muskellunge postmortem](https://dteather.com/blogs/battlecode24/). 2024 season.

[^34]: Tim Gubskiy and Andy Nguyen. [Just Woke Up: BattleCode 2025 Postmortem](https://battlecode.org/assets/files/postmortem-2025-just-woke-up.pdf). 2025 season; publication date unstated.

[^35]: Michael Hahn (skipiano). [Battlecode 2025 Postmortem: confused](https://battlecode.org/assets/files/postmortem-2025-confused.pdf). 2025 season.

[^36]: Cyril Sharma and Egor Gagushin. [Money Is All You Need: Battlecode 2025 Postmortem](https://battlecode.org/assets/files/postmortem-2025-om-nom.pdf). February 2025.

[^37]: SPAARK. [Battlecode 2025 Postmortem](https://battlecode.org/assets/files/postmortem-2025-spaark.pdf). 2025 season.

[^38]: Justin Ottesen, Andrew Bank, and Matt Voynovich. [The Kragle: Battlecode 25 Postmortem](https://battlecode.org/assets/files/postmortem-2025-the-kragle.pdf). updated February 5, 2025.

[^39]: Urav Tanna, Seth Lifland, and Andre Mao. [Generalized Stroke’s Theorem: Battlecode 2026 Postmortem](https://battlecode.org/assets/files/postmortem-2026-generalized-strokes-theorem.pdf). March 7, 2026.

[^40]: Alex Thummalapalli. [Battlecode 2026 Postmortem: food](https://www.alext.app/Battlecode_Postmortem_2026.pdf). 2026 season. Final Thoughts, printed pp. 29–30; PDF pp. 30–31.

[^41]: Liam Hanrahan. [Battlecode 2026: 3MiceWalkIntoABar](https://www.outercloud.dev/blogs/battlecode-2026/). February 4, 2026.

[^42]: Lorem Ipsum. [Battlecode 2026 Postmortem](https://battlecode.org/assets/files/postmortem-2026-lorem-ipsum.pdf). 2026 season. §10.1 describes the abandoned vibecoded tuner.

[^43]: joshatron. [StrategyChooser: README and Main.java](https://github.com/joshatron/StrategyChooser/tree/d25363c1b598e52a433e695876a93c5a703d5e37). repository created December 2014.

[^44]: TheSimpleSoldier. [FightMicroGA: README, Simulation/Main.java, and PSO.java](https://github.com/TheSimpleSoldier/FightMicroGA/tree/3f9db63fa23c3403c5ba20db0ed4710e37494a40). repository created November 2015.

[^45]: Hieu Nguyen and Ian Schwartz; mentor Joseph Anderson. [Developing an AI Framework to Play Games Without Knowing the Rules](https://faculty.salisbury.edu/~ealu/reu/Projects_File/2018/Hieu%20Nguyen1%20and%20Ian%20Schwartz_final%20poster.pdf). 2018 REU poster.

[^46]: jxiong21029. [Re;Battlecode 2022: environment and design specification v0](https://github.com/jxiong21029/Re_Battlecode22/tree/d41f3e4e77bce92fdc6f55983f4c75491b9368a9). repository created December 2022.

[^47]: Elian Rieza. [MIT Battlecode 2023: An unofficial Postmortem by cattleboys](https://elianrieza.dev/posts/mit-battlecode-2023-unofficial-postmortem/). page dated February 8, 2023. Terminology in the introduction does not establish model training; the technical description is procedural.

[^48]: quanticsoul4772. [Battlecode 2026 — Ratbot](https://github.com/quanticsoul4772/battlecode2026/tree/5217c0ae8df222562d14ac57927984403cd37b1d). 2026 season. README credits Claude Sonnet 4.5 as an AI pair-programming partner.

[^49]: John Yang, Kilian Lieret, Joyce Yang, Carlos E. Jimenez, Ofir Press, Ludwig Schmidt, and Diyi Yang. [CodeClash: Benchmarking Goal-Oriented Software Engineering](https://arxiv.org/html/2511.00839v1). arXiv:2511.00839v1, November 2, 2025. §§2–5, Table 1, Appendix B.1. Battlecode is described in the appendix but absent from the six-arena main evaluation.

[^50]: Muhtasham Oblokulov, Aryan Siddiqui, and John Yang. [Introducing Training Arenas](https://codeclash.ai/insights/20251231_train_split/). January 7, 2026. Use the displayed publication date, not the date embedded in the URL.

[^51]: CodeClash contributors. [Battlecode arena adapters and Dockerfiles](https://github.com/CodeClash-ai/CodeClash/tree/f0694c64ecf6abfca2bc867bad2de9333fef5be8/codeclash/arenas). snapshot accessed September 11, 2026. Inspected battlecode23, battlecode24, and battlecode25; implementation inspection, not a reproduced benchmark.

[^52]: Ismail Fateen. [Cambridge Battlecode Postmortem](https://ismailfateen.me/blog/cambc_postmortem). May 14, 2026. Cambridge’s separate competition, not MIT’s annual tournament.

[^53]: muteki contributors. [Cambridge Battlecode 2026 finalist bot](https://github.com/lxorb/muteki/tree/79328f3119c1ad84ff898464368e4ccb8e9f8c1c). 2026 competition. README self-reports 5th/6th and documents an AI-generated method naming convention.

[^54]: Metta AI. [cogame-battlecode](https://github.com/Metta-AI/cogame-battlecode/tree/173878c9d3b2e580f904a5d45d69424f4f043bd0). repository created September 3, 2026; snapshot accessed September 11. README describes a Nim port and sealed doctrine interface, despite repository description referring to the real MIT engine. Parity claims not independently reproduced.

[^55]: ECLAIR Robotics. [battlecode-gym: TerritoryBattleMultiEnv](https://github.com/ECLAIR-Robotics/battlecode-gym/tree/796edd187a55741a2436d08b2d5de4fb6040f88e). repository created July 2022.

[^56]: Outer Cloud Studio. [Nudge: distributed game runner](https://github.com/outercloudstudio/nudge). 2026; accessed September 11, 2026.

[^57]: Ivan Geffner (XSquare). [A Guide to Battlecode](https://battlecode.org/assets/files/battlecode-guide-xsquare.pdf). undated; examples through 2023. Especially §§3–6; assumes the Java bytecode engine.

[^58]: MIT Battlecode. [Battlecode 2024 specifications and changelog, v3.0.5](https://releases.battlecode.org/specs/battlecode24/3.0.5/specs.md.html). February 1, 2024.

[^59]: MIT Battlecode. [Battlecode 2026 engine, client, and replay schema](https://github.com/battlecode/battlecode26/tree/103abf6b67a2cf544e6344dddef9318af9ae9193). snapshot accessed September 11, 2026.

[^60]: MIT Battlecode. [Battlecode 2026 Java scaffold](https://github.com/battlecode/battlecode26-scaffold/tree/f69e2ab872a0061c9d4a684aa1dd798a0829da85). snapshot accessed September 11, 2026.
