# Scylla Migration — Implementation Plan

## Context

`bps_clean` runs a Sobol/Morris sensitivity analysis over BPS models discovered by Simod, simulating each sampled configuration with **Prosimos**. The goal is a second engine arm — **Scylla** (`bptlab/scylla`) — so the thesis can ask whether the SA methodology is engine-independent.

`PLANNING_SCYLLA_MIGRATION.md` (repo root, untracked) already lays out the strategy, obstacles B1–B6, and a 9–11 week timeline. This plan does **not** replace it. It is the executable layer underneath it, revised against what the code actually says. Five findings from reading both codebases change the plan materially:

1. **Only `process_rows` is written to disk.** `simulate_samples.py:307-313` — task/resource/case writes are commented out. Those three row builders are dead work today. A Scylla adapter needs to produce **only the 6 process metrics**. This is the single largest scope reduction available.
2. **The pinned Scylla is unusable.** `SimuBridge/Scylla-Container/scylla` sits at `a49380b` (May 2023), **46 commits behind `origin/main`**, and does **not** contain `f9671cb` "Fix #72: Default timetables for named resource instances are ignored" — the exact `<instance timetableId>` mechanism the chosen resource strategy depends on. Build from `origin/main` (`5159b53`).
3. **B1 is worse than documented, but survivable.** `QueueManager.java:154-172` requires *every* listed resource to be simultaneously available (conjunctive AND). Measured: datamining has up to **449 resources on one task** (median 23), median slowest/fastest mean-duration ratio **2760×**. Writing profiles directly deadlocks. But `<instance name= timetableId= cost=/>` lets one `dynamicResource` carry N instances **each with its own calendar** — so per-resource *calendars* survive pooling; only per-resource *durations* collapse.
4. **Phase 1's top risk is largely retired.** Both BPMNs use only `exclusiveGateway`, `parallelGateway`, `task`, one `startEvent`, one `endEvent`. No inclusive gateways, no subprocesses, no boundary events. All natively supported.
5. **Distribution indices confirmed empirically** from `Production_train.json`, refuting SimuBridge's mapping:
   `expon [2465.17, 0.0, 11880]` = `[mean, min, max]` — SimuBridge reads `[1]`, yielding **mean 0**;
   `norm [3240, 300, 2940, 3540]` = `[mean, std, min, max]` — `[1]` is std, not variance;
   `gamma`/`lognorm` = `[mean, var, min, max]`.

**Decisions taken** (user-confirmed): Faz 1 spike first, ablation in parallel on the cluster · Resource **Option B** (pooled + per-instance calendars) · SA scope = `is_gateway`, `is_arrival_distribution`, `is_resource_calendars`, `is_resource_numbers` · Build Scylla via **Docker**.

**Outcome:** `simulate_sample(engine="scylla")` produces the same `process_rows` schema as the Prosimos arm, with translation losses measured and documented rather than hidden.

---

## Critical files

**Engine boundary (the only pipeline file that must change):**
- `backend/src/simulation_pipeline/simulation/simulate_samples.py` — `simulate_sample()` at :354-560 is the cut point. `Parallel(n_jobs=-5, backend="loky")` at :272-286 is the fan-out. `kpi_to_dict()` :629-658 defines the 5-key row payload.
- `backend/src/simulation_pipeline/run_simulation_pipeline.py` — threads the new `engine` kwarg; also has a dead `run_simulation` import at :7 to delete.

**Ground truth, read-only references:**
- `SimuBridge/Scylla-Container/scylla/samples/Kreditkarte_global_1.xml`, `p0_globalconf.xml` (named instances, `randomSeed`, `zoneOffset`), `p2_normal_sim.xml` (erlang/triangular/arbitraryFinite)
- `.../parser/SimulationConfigurationParser.java:280-418` — the **nine** supported distributions, authoritative
- `.../parser/GlobalConfigurationParser.java:97-195` — resource/instance/timetable attributes
- `.../SimulationManager.java:83-97` — programmatic entry point
- `.../simulation/QueueManager.java:142-180` — AND-semantics proof for B1
- `SimuBridge/Scylla-Container/Dockerfile` — the `mvn package -DskipTests` build recipe
- `SimuBridge/SimuBridge--Main/simodConverter/simod_converter.js` — **read as a defect catalogue only; copy nothing**

**Do not touch:** `backend/src/sensitivity_analysis/**`, `frontend/**`. Acceptance depends on these being unchanged.

---

## Phase 0 — Safety net (½ day, do first)

The plan's acceptance criterion "Prosimos behaviour preserved (regression test)" has **nothing to build on**: no `tests/`, no framework, no fixtures. Capture the baseline *before* refactoring.

1. Create `backend/tests/` with pytest. Add `pytest` to `requirements_cluster.txt`.
2. Golden fixture: run `run_experiments.py --dataset production --smoke` (Morris t=4, 100 cases), commit the resulting `process_chunk_*.parquet` as `tests/fixtures/`.
3. `tests/test_prosimos_regression.py` — re-run the same config, assert the process rows match the golden file exactly. This is what makes "no behavioural change" checkable.
4. **Re-enable the error chunk write** (`simulate_samples.py:312-313`). Today a failing sample vanishes silently. With a JVM engine, failure modes multiply — this is a prerequisite, not an afterthought.

---

## Phase 1 — Spike (2–3 days) — **start here**

Prove Scylla runs a real model before writing any adapter.

1. **Build the jar via Docker** (no local Maven):
   ```bash
   cd "New Paper/SimuBridge/Scylla-Container/scylla"
   git checkout origin/main            # MUST: pinned a49380b lacks Fix #72
   docker run --rm -v "$PWD":/app -w /app maven:3.9-eclipse-temurin-17 \
       mvn package -DskipTests
   ```
   Copy `target/*.jar` → `backend/engines/scylla/scylla.jar` plus `target/libs`. Java 8 is installed locally and Scylla targets 1.8, so the built jar runs here.

2. **Hand-write** one `global_config.xml` + `sim_config.xml` for `models/production/Production_train.*`, using `Kreditkarte_global_1.xml` + `p0_globalconf.xml` as templates. `processRef="proc_1684015092"`. Namespace `bsim` = `http://bsim.hpi.uni-potsdam.de/scylla/simModel`.

3. **Run headless** — `--enable-bps-logging` is required or no KPIs are produced; `--output` avoids the directory-diff race that `ScyllaApi.py` suffers from:
   ```bash
   java -jar scylla.jar --headless --enable-bps-logging \
     --config=global_config.xml --bpmn=Production_train.bpmn \
     --sim=sim_config.xml --output=out/run1
   ```
   Note `SimulationManager.java:179` throws if the output dir already exists, and uses `mkdir()` not `mkdirs()` — the parent must exist and the leaf must not.

4. **Answer three questions, write them down:**
   - Does the Simod BPMN parse? (low risk — only supported element types present)
   - What does `statslogger` emit? It has `durationTotal`, `durationInactive`, `durationResourcesIdle`, `durationWaiting`, `costs` — if these are per-case aggregates, KPI extraction may not need XES parsing at all. **This decides Phase 3's shape.**
   - Wall-clock for one 3000-case run, and heap needed.

**Exit:** one model runs clean and produces output. Numeric accuracy irrelevant here.

**In parallel, on the cluster:** the ablation experiments (E1/E2/E3) from `PLANNING_SCYLLA_MIGRATION.md` §Faz 0.1, unchanged. They need only the existing Prosimos pipeline and answer whether translation loss reorders SA rankings. Output → `docs/ablation_results.md`.

---

## Phase 2 — Adapter (3–4 weeks)

New subpackage; nothing existing is deleted.

```
backend/src/simulation_pipeline/simulation/
├── simulate_samples.py        # gains engine="prosimos"|"scylla" dispatch
├── prosimos/run_sample.py     # today's simulate_sample() body, moved verbatim
└── scylla/
    ├── distributions.py       # Prosimos dist dict -> Scylla XML element
    ├── build_global_config.py # JSON -> GC XML
    ├── build_sim_config.py    # JSON + BPMN -> SC XML
    ├── run_scylla.py          # JVM invocation
    └── parse_results.py       # Scylla output -> process_rows
```

### 2.1 `distributions.py`

Scylla supports **exactly nine** distributions (`SimulationConfigurationParser.java:283-416`); the `if/else` chain checks `arbitraryFiniteProbabilityDistribution` first. `timeUnit` is mandatory on the *containing* element (`<duration>`/`<arrivalRate>`) — a missing one is an NPE, not a validation error. Prosimos works in seconds, so emit `timeUnit="SECONDS"` throughout.

| Simod | params (verified) | Scylla |
|---|---|---|
| `fix` | `[value]` | `constantDistribution/constantValue` |
| `expon` | `[mean, min, max]` | `exponentialDistribution/mean` = **p0** |
| `norm` | `[mean, std, min, max]` | `normalDistribution/mean`=p0, `/standardDeviation`= **p1 as-is** |
| `uniform` | `[min, max]` | `uniformDistribution/lower`=p0, `/upper`= **p1 as-is** |
| `lognorm` | `[mean, var, min, max]` | discretize |
| `gamma` | `[mean, var, min, max]` | discretize |

Guard each of the last three lines against the SimuBridge bugs: **do not** read `expon[1]`, **do not** `sqrt` the normal's `p1`, **do not** compute `upper = p1 + lower`.

**lognorm/gamma → `arbitraryFiniteProbabilityDistribution`:** sample N draws from the scipy distribution fitted to `(mean, var)`, clipped to `[min, max]`; histogram into K buckets; emit `<entry value="{center}" frequency="{count}"/>`. The parser normalizes frequencies itself, so raw counts are fine. Backing type is `DiscreteDistEmpirical` (`SimulationUtils.java:304-313`) — a genuinely discrete distribution over bucket centres, so K controls fidelity directly. Default K=100, configurable, measured in T3. Moment-matching to `normal` stays available as an explicitly-labelled fallback, never the default — these tails are long.

### 2.2 `build_global_config.py` — resource Option B

Per task-pool, emit one `<bsim:dynamicResource>` with `defaultQuantity` = number of resources in the pool, and one `<bsim:instance name= timetableId=/>` per real resource. Per-resource **calendars survive**; per-resource **durations** collapse into a single load-weighted mixture (built in `build_sim_config`). This is what keeps `is_resource_calendars` and `is_resource_numbers` meaningful.

- `resource_calendars[].time_periods` → `<timetable><timetableItem from to beginTime endTime/>`. Field names already match; `LocalTime.parse` accepts `HH:MM:SS` directly. **Do not round to whole hours** — that is SimuBridge's `timeToNumber()` defect, not a Scylla limitation.
- `<zoneOffset>` = `+02:00`, matching `start_iso` (`simulate_samples.py:30`).
- `<randomSeed>` **must** be written. It exists in both places — as a `<bsim:randomSeed>` element here and as a `randomSeed=` attribute on `simulationConfiguration` — but `SimulationManager.java:127` reads only the **global** one (`// XXX each simulation configuration may have its own seed` is an acknowledged TODO). **Consequence: batching many sim configs into one JVM call cannot give them independent seeds.** Derive the seed from `(sample_id, run_idx)` and run one config per invocation until measurement proves batching safe.
- `defaultQuantity`, `defaultCost`, `defaultTimeUnit` are all mandatory attributes — unguarded `valueOf` calls in the parser.

### 2.3 `build_sim_config.py`

- `processRef` from the BPMN `<process id=...>` via `lxml` (`proc_1684015092` / `proc_1131316523`).
- `processInstances` = `total_cases`. **Do not copy SimuBridge's `Math.min(..., 5000)` clamp** — write the real value and test the ceiling empirically.
- `<startEvent><arrivalRate>` ← `arrival_time_distribution`. At least one `startEvent` with an `arrivalRate` is mandatory or the parser throws.
- `<task><duration>` ← load-weighted mixture over the pool's resources; `<resources><resource id amount="1"/></resources>` referencing the pool. Every `resource id` must exist in the global config or it is a validation error.
- `<exclusiveGateway>` / `<parallelGateway>` ← `gateway_branching_probabilities`; tag name from the BPMN element type. Exclusive-gateway probabilities must sum to ≤1 and match the outgoing-flow count.

**Validation is on us:** Scylla logs and skips unknown elements rather than failing (`SimulationConfigurationParser.java:245-252`), so silent drops are the default failure mode. Add an assertion pass that every task, gateway and resource we intended to write is present in the emitted XML.

### 2.4 `run_scylla.py`

Start with subprocess-per-sample, `--output` to a fresh unique dir, capture **both** stdout and stderr and check the exit code — `ScyllaApi.py` does none of this, which is why Java stack traces are invisible there. Keep `joblib` fan-out; drop `n_jobs` from `-5` to roughly the core count divided by JVM heap footprint, measured in Phase 4.

Defer the warm-JVM harness (JPype/Py4J against `SimulationManager`) to Phase 4, and only if measurement justifies it. Note `run()` calls `cleanup()`, so sequential reuse in one JVM is safe but **concurrent** reuse is not — `DateTimeUtils.setZoneId`, `Experiment.setEpsilon` and the plugin loader are global static state.

### 2.5 `parse_results.py`

Produce **only** `process_rows` — 6 metrics × `{min,max,avg,total,count}`. Return `[]` for task/resource/case rows; nothing reads them.

`cycle_time`, `processing_time`, `waiting_time` are recoverable. `idle_cycle_time`, `idle_processing_time`, `idle_time` are Prosimos-specific calendar-aware metrics; re-implement from `prosimos/simulation_stats_calculator.py`, or — if Phase 1 shows the cost is high — emit `None` and run the Scylla-arm SA on cycle time only. **Decide this at the end of Phase 1, not later.**

**Exit:** `engine="scylla"` runs end-to-end and writes `process_kpis_*.parquet` with the Prosimos arm's schema.

---

## Phase 3 — Validation (1.5–2 weeks)

Sequential; each depends on the last. Output → `docs/translation_fidelity.md`.

- **T1 Determinism** — all distributions `fix`, gateway probabilities 0/1, single resource, 24/7 calendar. Both engines must agree **exactly**. Any gap is a translation bug, not engine semantics.
- **T2 Analytic** — one activity, constant duration, unbounded resources, known arrival rate. Cycle time is hand-computable.
- **T3 Distribution fidelity** — 10 000 draws per family from each side, Wasserstein distance. Sweep K ∈ {20, 50, 100, 200} for lognorm/gamma and report the fidelity/cost curve. Verify no negative durations.
- **T4 Calendar** — single resource, Mon–Fri 09:00–17:00, heavy arrivals. Compare utilization and waiting.
- **T5 Full model** — Production + datamining at 100/500/1000 cases. Decompose deviation into: distribution discretization, resource pooling, missing arrival calendar.

---

## Phase 4 — Scale (1 week)

Phase 7 of `run_experiments.py` alone is **~364k simulations** (1081 parameters); the full matrix is 42 runs across 7 phases. At 1–2 s JVM startup each, process-per-sample costs 100–200 h in startup alone.

1. Measure single-run wall-clock and heap from Phase 1; extrapolate; compute the Prosimos ratio.
2. If the ratio is unacceptable, build the warm-JVM harness: a small Java class reading `(gc, sc, out)` triples from stdin, driving `SimulationManager` sequentially, one process per core. Respect the concurrency caveat above.
3. Disable the `xeslogger` plugin (drop the line from `src/main/resources/META-INF/plugins/plugins_list` and rebuild) if `statslogger` alone suffices — XES logs are far larger than the parquet output.
4. Update `server_computing/slurm/run_array.sh`: neither SLURM script currently loads a JDK or sets `-Xmx`. Reconcile `-n 32` with `n_jobs=-5`, which counts node cores rather than the allocation.

---

## Phase 5 — Comparison (2 weeks)

Run the four in-scope dimensions on both engines. Compare Sobol/Morris indices by **Spearman rank correlation** first, magnitudes second: if ranking survives, the methodology is engine-independent — the strongest available finding. Check the realized Scylla deviation against the E3 ablation prediction; a correct prediction is itself a result.

Scope limitations to state plainly in the write-up: `is_arrival_calendar` has no Scylla representation and is excluded; `is_tasks_resources` is excluded because pooling collapses its parameter space. Both are consequences of Scylla's model, documented in `docs/scope_decisions.md`.

---

## Verification

```bash
# Phase 0 — baseline must stay green after every later phase
cd backend && python -m pytest tests/ -v

# Phase 1 — spike
java -jar engines/scylla/scylla.jar --headless --enable-bps-logging \
  --config=spike/global_config.xml --bpmn=models/production/Production_train.bpmn \
  --sim=spike/sim_config.xml --output=spike/out
#   -> exit 0, output dir non-empty, KPI/XES files present

# Phase 2 — parity smoke on both engines
python run_experiments.py --dataset production --smoke                    # prosimos
python run_experiments.py --dataset production --smoke --engine scylla     # scylla
#   -> both write process_kpis_*.parquet with identical columns and row counts

# Phase 3
python -m pytest tests/test_translation_fidelity.py -v
```

**Acceptance:**
- [ ] Prosimos regression test green — behaviour bit-identical to the golden fixture
- [ ] `engine="scylla"` yields the same `process_rows` schema
- [ ] T1 determinism passes exactly
- [ ] T3 fidelity measured per family, K-sweep reported
- [ ] Same seed reproduces the same result twice
- [ ] Error chunks are written (no more silent sample loss)
- [ ] `docs/ablation_results.md`, `docs/translation_fidelity.md`, `docs/scope_decisions.md` exist
- [ ] Zero diff in `backend/src/sensitivity_analysis/**` and `frontend/**`

---

## Risks

| Risk | Mitigation |
|---|---|
| Global-only random seed blocks batching | Confirmed at `SimulationManager.java:127`. Assume one JVM call per sample; treat batching as an optimization to be proven, not assumed |
| `idle_*` not reproducible from Scylla output | Decide at end of Phase 1; fallback is cycle-time-only SA on the Scylla arm |
| JVM startup dominates at 364k sims | Warm-JVM harness in Phase 4; measure before building |
| Discretization K perturbs SA results | T3 sweeps K and reports the effect |
| Wrong Scylla version silently reintroduces bug #72 | Pin `origin/main`; record the commit hash in `docs/scope_decisions.md` |
| Silent XML drops | Post-generation assertion pass in `build_*_config.py` |

## Out of scope

SimuBridge's `simod_converter.js` code (architecture yes, code no — the distribution mapping is wrong). Extending Scylla for batching / case attributes / prioritisation. An `is_arrival_calendar` plugin. `is_tasks_resources` on the Scylla arm.

Worth doing regardless of this migration: `pix-framework` is pinned nowhere, arriving transitively via `prosimos`/`simod` — a real reproducibility gap, since it is the authority on distribution parameter order.
