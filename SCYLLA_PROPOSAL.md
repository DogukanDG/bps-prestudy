

## 1. What this is about

The sensitivity analysis pipeline currently uses **Prosimos** as its simulation engine. The proposal is to add **Scylla** as a second engine, so we can ask:

> Does the sensitivity analysis produce the same conclusions when the simulation engine changes?

If the ranking of parameters stays the same under a different engine, the methodology is engine-independent — a result worth reporting. If the ranking changes, that is also a finding, and an important one.

This document explains what the work involves, what we would lose in translation, and what I propose we commit to. I have read the source code of both Scylla and SimuBridge (the existing Simod–Scylla bridge from TUM) to ground these claims, and I cite specific files where a claim depends on them.

**Scope:** the Scylla arm covers the **BPIC 2012 and BPIC 2017** models, and all figures in this document are measured from those two. They are the smallest models we have, which keeps the simulation cost manageable, and they are the ones most commonly used in the literature, which makes the results easier to place in context.

**The Prosimos results for both models already exist**, so nothing has to be re-run on the Prosimos side. All the new work — and all the new simulation time — is on the Scylla side, with the existing results used directly as the reference.

---

## 2. Why this is not simply "swap the engine"

The pipeline calls Prosimos in one place — a single function, `simulate_sample()`. Everything after it (KPI aggregation, Sobol/Morris analysis, the frontend) depends only on the shape of what that function returns, not on which engine produced it. So the integration point is clean and small.

The difficulty is not the plumbing. It is that **Prosimos and Scylla do not model resources the same way**, and our discovered models depend heavily on the part where they differ.

### 2.1 The resource problem

Simod discovered our models in *differentiated* mode: every resource is its own profile, and each activity stores a **separate duration distribution per resource**. In BPIC 2017, one activity has 93 resources attached, each with its own duration.

Scylla works differently. An activity has **one** duration, and the resources listed under it are all **required at the same time** — not alternatives. I verified this in Scylla's source (`QueueManager.java:154-172`): the scheduler checks that every listed resource is available, and blocks if any one is missing.

So writing our models into Scylla directly would not merely be inaccurate — the process would deadlock.

Measured on the two BPIC models:

| Model | Resources | Resources per activity (median / max) | Slowest-to-fastest resource ratio (median) |
|---|---|---|---|
| BPIC 2012 | 47 | 39 / 42 | 965× |
| BPIC 2017 | 105 | 79 / 93 | 551× |

The ratio figures matter: resources performing the same activity are highly heterogeneous. Any approach that collapses them into a single "average" resource discards real structure, and we should be explicit about that in the thesis.

### 2.2 What Scylla cannot express at all

| SA dimension | Scylla equivalent | Status |
|---|---|---|
| `is_gateway` | branching probabilities | works |
| `is_arrival_distribution` | arrival rate | works, limited distribution families |
| `is_resource_calendars` | timetables | works |
| `is_resource_numbers` | resource quantity | works |
| `is_arrival_calendar` | — | **no equivalent** |
| `is_tasks_resources` | one duration per activity | **parameter space collapses** |

Four of six dimensions transfer cleanly. Two do not.

### 2.3 Distribution families

Scylla supports exactly nine distributions (`SimulationConfigurationParser.java:283-416`). Lognormal and gamma are **not** among them, and in these models they dominate: **118 of 191 duration distributions (62%) in BPIC 2012, and 258 of 433 (60%) in BPIC 2017**.

This is the largest single source of translation error, and it affects the majority of activity durations rather than a minority of edge cases.

We can represent them with Scylla's `arbitraryFiniteProbabilityDistribution` — sample the true distribution, bin it into a discrete histogram, and give Scylla the bins. This preserves the shape of the tail, unlike matching only mean and variance to a normal distribution, which would flatten it. The number of bins becomes a parameter whose effect we should measure rather than assume.

---

## 3. Proposed approach

### 3.1 Resource strategy: pool, but keep individual calendars

For each activity, group its resources into one Scylla resource pool with `quantity = N`, and declare each real resource as a named instance inside that pool with its own timetable:

```xml
<dynamicResource id="ActivityA_pool" defaultQuantity="22" defaultTimetableId="Default">
  <instance name="Resource_1" timetableId="Resource_1_calendar"/>
  <instance name="Resource_2" timetableId="Resource_2_calendar"/>
  ...
</dynamicResource>
```

This matters because it decides what we keep and what we lose:

- **Individual working calendars are preserved.** `is_resource_calendars` and `is_resource_numbers` stay meaningful — and `is_resource_calendars` is the most parameter-rich dimension in the study.
- **Per-resource durations are lost.** They collapse into one load-weighted mixture per activity. This is what makes `is_tasks_resources` untestable on the Scylla side.

The alternative — re-running Simod in pooled discovery mode — would fit Scylla more naturally, but would produce *different models* from the ones already used, so the existing Prosimos results would no longer serve as a reference. Keeping the existing models and accepting the documented loss is the better trade.

### 3.2 Scope: four dimensions, not six

I propose the Scylla arm runs `is_gateway`, `is_arrival_distribution`, `is_resource_calendars`, and `is_resource_numbers`, and that we exclude the other two with a stated justification:

- **`is_arrival_calendar`** — Scylla has no concept of an arrival calendar. It could be added as a Scylla plugin (there is an existing plugin, `eventArrivalRate`, whose structure we could follow), but that is Java engine development rather than thesis work, and the time is better spent on validation.
- **`is_tasks_resources`** — the parameter space does not survive pooling, as explained above.

This is the decision that most affects what the comparison can claim.

### 3.3 Framing the contribution

Two possible claims:

- **(a)** "Two engines produce the same SA results on the same model." This requires near-lossless translation, which §2 shows is not achievable.
- **(b)** "The SA methodology can be applied with a second engine, and here is what transfers, what does not, and how much the differences move the results."

**(b)** is the realistic target. It is achievable, and the translation losses become a documented result rather than a hidden threat to validity.

The comparison itself is straightforward, because the Prosimos results for BPIC 2012 and 2017 already exist. Nothing needs to be re-run on the Prosimos side — the new work is entirely on the Scylla side, and the existing results serve directly as the reference.

The main question the comparison answers:

| Result | Interpretation |
|---|---|
| Parameter ranking preserved | The methodology is robust to the choice of engine — the strongest available finding |
| Ranking changes | The translation losses matter, and we report which of them drive the difference |

---

## 4. On reusing SimuBridge

SimuBridge (Bein et al., BPM 2023) is the existing open-source Simod-to-Scylla bridge, developed at TUM. I studied it closely and propose we **adopt its architecture but not its conversion code**.

Its structure — a pivot data model feeding separate global-config and simulation-config XML writers — is sound and worth following. Its numeric layer is not. Reading the code against our own model files, I found three concrete defects:

1. **Exponential distributions become zero.** It reads parameter index `[1]` as the mean (`simod_converter.js:65`). Checked against our model files, Simod writes exponential parameters as `[mean, min, max]` — so index `[1]` is `min`, which is `0.0`. Every exponential duration would become zero. This affects 42 distributions in BPIC 2012 and 95 in BPIC 2017, and would fail silently: the simulation runs and produces plausible-looking output.
2. **Normal distributions get the wrong spread.** It treats the standard deviation as a variance and then takes its square root, turning σ = 300 into σ ≈ 17.
3. **Calendars are rounded to whole hours** (`timeToNumber()`), so `09:30` becomes `09:00`. This is SimuBridge's own limitation — Scylla itself accepts second-level precision.

These are understandable in a demonstration tool where a user inspects results visually, but not acceptable where the numbers are the contribution. I would write the distribution mapping from Simod's actual parameter schema and verify it with a test that compares both engines under conditions where they must agree exactly.

I also suggest we report these issues to the SimuBridge authors. The group is at TUM and Luise Pufahl — Scylla's author — is among SimuBridge's authors, so there is a direct channel.

**Version note:** the Scylla version bundled with SimuBridge is from May 2023 and is 46 commits behind current, missing a 2024 fix (`Fix #72`) to exactly the named-resource-timetable mechanism our approach depends on. We should build from the current main branch.

---

## 5. Computational cost

The full experiment matrix is 42 runs per dataset, and the number of simulations grows with the number of parameters — Morris and Sobol both require runs proportional to the parameter count. In the largest configuration of the existing study this reaches several hundred thousand simulations.

To be clear: **this cost already exists in the Prosimos arm.** It comes from the sensitivity analysis design, not from Scylla. What changes is the cost *per simulation*.

Scylla runs on the JVM. Starting a JVM process per simulation would add 1–2 seconds each, which alone is 100–200 hours. This is solvable: Scylla exposes a programmatic entry point (`SimulationManager`), so one long-lived JVM process can run many simulations in sequence and pay the startup cost once.

The genuine unknown is how fast Scylla simulates our models relative to Prosimos. **This has not been measured yet, and I propose measuring it before anything else**, because it determines which phases are feasible at all. If the largest phase turns out to be too expensive, excluding it from the Scylla arm is a reasonable and defensible decision — the grouped phases are far smaller and remain comfortable.

One point in our favour here: both BPIC models are relatively compact — 6 and 7 activities, 11 gateways each, and only 3 and 6 distinct resource calendars respectively. They are considerably lighter than the alternatives, which is part of why I suggest running the Scylla arm on these two.

Since the simulations themselves dominate the schedule, this measurement decides the timeline far more than the implementation work does.

---

## 6. Plan and sequencing

Starting with a short feasibility spike rather than with implementation:

| Step | What | Development | Compute |
|---|---|---|---|
| **1. Spike** | Build Scylla, hand-write configuration files for one model, run it | 2–3 days | — |
| **2. Scope decision** | Confirm §3.1–3.3 against what the spike measures | — | — |
| **3. Converter** | Simod JSON → Scylla XML, with a verified distribution mapping | ~1 week | — |
| **4. Validation** | Agreement tests between the two engines | 4–5 days | short runs |
| **5. Scaling** | Long-lived JVM, cluster configuration | 2–3 days | benchmark runs |
| **6. Scylla runs** | Run the SA configurations on the Scylla side | 1–2 days | **the dominant cost** |
| **7. Comparison** | Compare against the existing Prosimos results | 2–3 days | — |

Roughly **3–4 weeks of development effort**, plus simulation time on the Scylla side only.

The two columns are separated deliberately, because they behave differently. The development work is bounded and largely mechanical: the conversion layer is a well-specified mapping from one file format to another, and the validation tests are small. The compute is not bounded by effort at all — it is wall-clock time on the cluster, and no amount of implementation speed shortens it.

**This is why step 6 carries the real risk, not step 3.** Until we know how fast Scylla simulates these models, that step cannot be estimated honestly. The number is unknown today and is measured in step 1.

**The spike answers three questions cheaply**, and all later estimates depend on them:

1. Does Scylla accept the BPMN files Simod produced? (Low risk — I checked both BPIC models, and they use only element types Scylla supports: exclusive gateways, parallel gateways, tasks, and single start/end events. No inclusive gateways or subprocesses.)
2. Which KPIs does Scylla report directly, and which must we compute ourselves? Scylla reports total, waiting, and idle durations; whether these match Prosimos's definitions determines how much work KPI extraction is.
3. How long does one simulation take? This decides §5.

### On validation

One test is worth treating as non-negotiable. Configure both engines so that all randomness is removed — fixed durations, deterministic branching, a single resource, a 24/7 calendar. Under those conditions the two engines **must** produce identical cycle times. If they do not, we have a translation bug, not an engine difference.

This costs about half a day and would have caught all three SimuBridge defects immediately. Without it, a silent error like the zero-mean exponential could survive into the final results.

---

## 7. Summary of the open decisions

Four points this plan takes a position on, each of which changes what the study can claim:

1. **Scope (§3.2)** — `is_arrival_calendar` and `is_tasks_resources` are excluded, because Scylla cannot represent them. Four of six dimensions remain.
2. **Framing (§3.3)** — the target is an applicability study with documented translation losses, not a strict engine-equivalence claim.
3. **Resource strategy (§3.1)** — keep the existing differentiated models and accept the pooling loss, rather than re-discovering pooled models that would no longer match the existing Prosimos results.
4. **Sequencing (§6)** — measure Scylla's speed with a short spike before committing to the full implementation.

---

## Appendix: sources for the claims

Claims about engine behaviour are taken from source code rather than documentation, since the Scylla wiki is incomplete.

| Claim | Source |
|---|---|
| Scylla requires all listed resources simultaneously | `scylla/simulation/QueueManager.java:154-172` |
| Nine supported distributions; no lognormal or gamma | `scylla/parser/SimulationConfigurationParser.java:283-416` |
| Named instances can carry individual timetables | `scylla/parser/GlobalConfigurationParser.java:116-165`; sample `p0_globalconf.xml` |
| Programmatic entry point exists | `scylla/SimulationManager.java:83-97` |
| Bundled Scylla lacks the 2024 timetable fix | Scylla git history, commit `f9671cb`; submodule pinned at `a49380b` |
| SimuBridge exponential index defect | `SimuBridge--Main/simodConverter/simod_converter.js:65` |
| Simod parameter ordering | Read directly from the model JSON files |
| Resource heterogeneity and distribution counts | Computed from `BPIC_2012_train.json` and `BPIC_2017_train.json` |
| Simulation counts in the largest phase | `backend/run_experiments.py:186-192` |

Related literature: Bein et al., *SimuBridge*, BPM 2023 Demos (CEUR-WS Vol-3469); Pufahl, Wong & Weske, *Design of an Extensible BPMN Process Simulator*, BPM Workshops 2018; López-Pintado & Dumas, *Business Process Simulation with Differentiated Resources: Does it Make a Difference?*, BPM 2022 (relevant to the pooled-versus-differentiated resource question in §3.1).
