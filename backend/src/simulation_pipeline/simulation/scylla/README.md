# Scylla engine adapter — findings that need a decision

Six things surfaced while building and validating the converter that are not
implementation details. Each changes how the Scylla results should be read.

Three came from writing the code. Three more came from actually running both
engines against each other (T1), and none of those were visible in the Phase 1
spike or in any unit test — they only appear when the two engines simulate the
same model.

Written 2026-08-28, against BPIC 2012 and BPIC 2017.

---

## What T1 found

T1 strips a model down to something both engines *must* agree on: fixed
durations, one always-available resource, a 24/7 calendar, branching forced to
1.0/0.0. Under those conditions cycle time is arithmetic, so any disagreement
is a translation bug. It now passes 10/10 — but only after three fixes.

### 1. Per-activity pooling inflated capacity 4.1x

**The finding.** T1 put one resource behind two concurrently-enabled activities.
Prosimos serialised them (120 s); Scylla ran them at once (60 s).

**Why.** Each activity got its own resource pool, so a resource working on four
activities became four independent instances:

| Model | Real resources | Capacity under per-activity pooling |
|---|---|---|
| BPIC 2012 | 47 | 191 (4.1x) |
| BPIC 2017 | 105 | 433 (4.1x) |

91% of BPIC 2012's resources and 96% of BPIC 2017's appear in more than one
activity, so almost no contention *between* activities survived. This was not a
T1-only artefact — it inflated capacity in every real run too.

**What was done.** One shared pool holds each resource exactly once
(`SHARED_POOL_ID`). Per-resource calendars still survive as named `<instance>`
elements, so `is_resource_calendars` stays meaningful.

**What it costs.** Eligibility — which resource may perform which activity — is
now lost as well. Scylla cannot express both eligibility and correct capacity,
and capacity is what queueing depends on. Eligibility was already partly gone
(durations are pooled per activity); this extends the same compromise to
availability.

### 2. Load weighting rested on a false assumption

**The finding.** The converter originally weighted pooled durations towards
faster resources, reasoning that a faster resource frees up sooner and so takes
on more work. Measured against Prosimos on BPIC 2012 (500 cases, one activity
with 42 resources):

| resource | declared mean | share of executions |
|---|---|---|
| fastest | 6.7 s | 4.8% |
| slowest | 1060.7 s | 2.7% |

Near-uniform. Prosimos allocates by availability, not by speed.

**Effect of getting it wrong.** Weighting cut pooled durations by 11–65% and
made agreement worse, not better — `processing_time` was 53% below Prosimos with
weighting, 21% below without.

**What was done.** `weighted=False` is now the default. The code stays, because
the comparison is worth reporting and because `weighted=True` is how the effect
was measured rather than assumed.

**Worth noting for the write-up.** This is the second assumption in this project
that measurement overturned (the first being SimuBridge's parameter indices).
Both were plausible and both were wrong.

### 3. Equal-width histogram buckets destroyed the mean

**The finding.** lognormal and gamma have no Scylla equivalent and are ~60% of
the duration distributions in both models, so they are discretised into an
`arbitraryFiniteProbabilityDistribution`. With equal-width buckets, on the
largest BPIC 2012 activity:

- maximum is 218x the median, so the range is set by outliers
- only 32 of 100 buckets ended up occupied
- the mean was overstated by 16%

**What was done.** Buckets are now equal-*frequency*, each carrying the mean of
its contents, which makes the sample mean exact by construction — 0.00% error at
any bucket count, even 10.

Averaging inside a bucket then clipped the extreme tail (36448 s down to
3765 s), and queueing is driven by exactly those long services, so the top decile
is emitted sample by sample instead. Both the mean and the maximum now survive.

---

## What building the converter found

### 4. `waiting_time` does not mean the same thing in both engines

**The finding.** Scylla's reported waiting total exceeds its own flow time total
— 88.0M s against 71.5M s on BPIC 2012 at 3000 cases. Impossible for a
wall-clock measure: a case cannot spend more time waiting than it exists.

**Why.** `StatisticsLogger` (`statslogger_nojar/StatisticsLogger.java:186-195`)
accumulates the enable → begin gap for *every activity instance* and sums them
per case, so activities that wait concurrently are counted more than once.
Prosimos measures waiting as wall-clock time per case.

```java
else if (transition == ProcessNodeTransitionType.BEGIN) {
    Long enableTimestamp = enabledTasks.get(taskInstanceIdentifier);
    if (enableTimestamp != null) {
        long duration = timestamp - enableTimestamp;
        durationWaiting += duration;        // summed across concurrent waits
```

`processing_time` is unaffected — Scylla's `effective` matches the sum of
per-activity durations (13.9M s against 13.3M s) — and `cycle_time` /
`flow_time` share a definition.

T1 sharpened this: even in a model where nothing can queue, Prosimos reports
60 s of waiting, and spacing arrivals ten times further apart leaves it
unchanged. So its waiting figure is structural too, just structured differently.

**What was done.** The value is still emitted — sensitivity analysis measures
how a metric *responds* to parameter changes, not its absolute level, and this
is still a monotone measure of queueing. `check_consistency()` warns whenever
waiting exceeds cycle time.

**Still open.** Whether the Scylla arm reports waiting-time sensitivity at all.
Cross-engine comparison of waiting time is not defensible as things stand:

- lead with `cycle_time` only, and state the limitation; or
- recompute waiting per case from the XES log, which makes it comparable but
  gives up the "no event-log parsing needed" result from the spike.

The first is cheap and honest. **A supervisor decision, not a coding one.**

### 5. The dispatcher must not be a closure

`bpic2013_run_times.csv` records three runs lost this way:

```
1,bpic2013_morris_nogw_t512_seed100,...,FAILED,PicklingError: Could not pickle the task to send it to the workers.
```

`simulate_all_samples` dispatches through `joblib.Parallel(backend="loky")`,
which pickles the worker callable. Selecting an engine invites a closure, and a
closure raises `PicklingError` at the top of a multi-hour cluster run.
`_engine_worker()` returns a module-level function or a `functools.partial`
over one, and a test pickles both workers to keep it that way.

Those earlier BPIC 2013 failures remain unexplained and are worth a look before
the next large Morris run.

### 6. Two Scylla quirks worth knowing before debugging anything

**`--output` is silently ignored.** Parsed, but never wired to the field
`SimulationManager.run()` reads, so output lands in an auto-named
`output_<timestamp>/` directory next to the global config. `run_scylla.py` runs
each simulation in its own directory and discovers what appeared.

**Scylla never fails on XML it does not recognise** — it logs and skips
(`SimulationConfigurationParser.java:245-252`). "It ran" is never proof it read
what was written, which is why both builders validate their own output and
`parse_process_rows` checks the simulated case count against what was requested.

---

## Where the engines still differ, and why it is not a bug

On the real BPIC 2012 model at 500 cases, `cycle_time` is about 121% above
Prosimos. Replacing every calendar with 24/7 brings that to **+10%**.

So the remaining gap is almost entirely **calendar semantics**, not translation.
The two engines handle off-shift time differently, and characterising that is
T4's job. The converter is not the suspect here — T1 rules it out under
controlled conditions, and the 24/7 result localises what is left.

Attribution as it stands:

| Source | Size | Ours to fix? |
|---|---|---|
| Calendar semantics | ~110 pp of the 121% | No — engine difference, characterise in T4 |
| Discretisation | ~0% on the mean | No longer material after fix 3 |
| Pooling (durations + eligibility) | included in the residual +10% | Forced by Scylla's model |
| Weighting | was 11–65%, now 0 | Fixed by turning it off |

---

## Environment

Both engines run on this machine now. Prosimos needs Python < 3.12, and current
Scylla needs Java 11 (`pom.xml` targets 11 — an older JVM on PATH fails with
`UnsupportedClassVersionError`).

```bash
conda create -n bps python=3.11
conda activate bps
pip install -r backend/requirements_cluster.txt
conda install -c conda-forge openjdk=11
```

`run_scylla.resolve_java()` prefers the JDK inside the active conda environment;
`JAVA_BIN` overrides it.

The Scylla build **must include commit `f9671cb`** ("Fix #72: default timetables
for named resource instances are ignored") — the copy bundled with SimuBridge is
46 commits behind and predates it, and that fix covers exactly the per-instance
timetable mechanism the pooling strategy depends on. Build from `origin/main`:

```bash
docker run --rm -v "$PWD":/app -w /app maven:3.8-openjdk-11 \
  sh -c "mvn -q clean && mvn -q package -DskipTests"
```

(Two invocations: the local jars in `lib/` are installed by `install-file` goals
bound to the `clean` phase, so a single `mvn package` cannot resolve them.)

**Prosimos results for BPIC 2012 and 2017 already exist** and serve directly as
the comparison reference — the Prosimos arm does not need re-running. The
3000-case outputs are checked in under `backend/tests/fixtures/`.

---

## Status

| | |
|---|---|
| Converter | complete — 5 modules, `engine="scylla"` runs end to end |
| T1 determinism | **passing 10/10** — engines agree exactly when nothing is random |
| Tests | 185 passing, none skipped |
| Prosimos arm | unchanged; default engine, existing callers unaffected |
| Next | T4 (calendars) — the one unexplained source of divergence |
| Open decision | waiting-time reporting (finding 4) |

`compare_engines.py` runs both engines on a real model and attributes the gap to
weighting, discretisation and pooling separately.
