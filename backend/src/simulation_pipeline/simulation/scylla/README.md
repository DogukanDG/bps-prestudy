# Scylla engine adapter — findings that need a decision

Three things surfaced while building the converter that are not implementation
details. Each changes how the Scylla results should be read, and two of them
were invisible in the Phase 1 spike.

Written 2026-08-28, against BPIC 2012 and BPIC 2017.

---

## 1. `waiting_time` does not mean the same thing in both engines

**The finding.** Scylla's reported waiting total exceeds its own flow time
total — 88.0M s against 71.5M s on BPIC 2012 at 3000 cases. That is impossible
for a wall-clock measure: a case cannot spend more time waiting than it exists.

**Why.** `StatisticsLogger` (`statslogger_nojar/StatisticsLogger.java:186-195`)
accumulates the enable → begin gap for *every activity instance* and sums them
per case. Activities that wait concurrently are therefore counted more than
once. Prosimos measures waiting as wall-clock time per case, so the two numbers
are not the same quantity.

```java
else if (transition == ProcessNodeTransitionType.BEGIN) {
    Long enableTimestamp = enabledTasks.get(taskInstanceIdentifier);
    if (enableTimestamp != null) {
        long duration = timestamp - enableTimestamp;
        durationWaiting += duration;        // summed across concurrent waits
```

`processing_time` is unaffected — Scylla's `effective` matches the sum of
per-activity durations (13.9M s against 13.3M s), and `cycle_time` /
`flow_time` share a definition.

**What was done.** The value is still emitted: sensitivity analysis measures how
a metric *responds* to parameter changes, not its absolute level, and this
remains a monotone measure of queueing. `check_consistency()` in
`parse_results.py` warns whenever waiting exceeds cycle time.

**What is still open.** Whether the Scylla arm reports waiting-time sensitivity
at all. Cross-engine comparison of waiting time is not defensible as things
stand. Two ways forward:

- lead with `cycle_time` only, and state the limitation; or
- recompute waiting from the XES log per case, which makes it comparable but
  gives up the "no event-log parsing needed" result from the spike.

The first is cheap and honest. The second costs implementation time and needs
its own validation. **This is a supervisor decision, not a coding one.**

---

## 2. Load weighting needed a cap to stay physical

**The finding.** Pooling resources requires knowing how much work each does.
Throughput goes as `1 / mean duration` — a faster resource finishes sooner,
becomes available again sooner, and takes on more work. Applied raw, that gives
one resource 65% of an entire pool's weight:

| BPIC 2012, first activity | value |
|---|---|
| resources in pool | 27 |
| fastest / slowest mean | 1.1 s / 4141.2 s |
| top resource share, raw `1/mean` | **65.3%** |
| top resource share, capped | **4.9%** |

**Why it is wrong.** A resource is still serial. However fast it is, it handles
one case at a time, so it cannot absorb an unbounded share of demand. Raw
`1/mean` lets a single fast resource stand in for the whole pool.

**What was done.** `MAX_WEIGHT_RATIO = 20` in `build_sim_config.py` caps the
ratio between the heaviest and lightest weight. Fast resources stay dominant;
none replaces the pool.

**What is still open.** The cap is a modelling choice with no literature behind
it — 20 was chosen because it brings the top share into the 2–10% range across
both models, not because it is derived. Its effect is measurable and should be
reported: pooled durations move by 3–86% depending on the activity.

`weighted=False` reproduces unweighted pooling, so the effect can be measured
rather than assumed. **T5 should decompose the deviation into weighting,
discretisation and calendar effects separately.** Note the Phase 1 spike pooled
unweighted, so spike numbers are not comparable to current output.

---

## 3. The dispatcher must not be a closure

**The finding.** `bpic2013_run_times.csv` records three failed runs:

```
1,bpic2013_morris_nogw_t512_seed100,...,FAILED,PicklingError: Could not pickle the task to send it to the workers.
1,bpic2013_morris_nogw_t512_seed200,...,FAILED,PicklingError: ...
1,bpic2013_morris_nogw_t512_seed300,...,FAILED,PicklingError: ...
```

**Why it matters here.** `simulate_all_samples` dispatches through
`joblib.Parallel(backend="loky")`, which pickles the worker callable to send it
to subprocesses. Selecting an engine is exactly the kind of change that invites
a closure or lambda — and either raises `PicklingError` at the top of a
multi-hour cluster run.

**What was done.** `_engine_worker()` returns a module-level function
(`simulate_sample`) or a `functools.partial` over one — never a closure.
`tests/test_engine_dispatch.py` pickles both workers and round-trips them.

**What is still open.** Nothing here, but the failures above are unrelated to
this change and are still unexplained. Worth a look before the next large
Morris run.

---

## Also worth knowing

**Prosimos results already exist for BPIC 2012 and 2017** and are used directly
as the comparison reference — the Prosimos arm does not need re-running. Copies
of the 3000-case outputs are checked in under `backend/tests/fixtures/` as
contract fixtures.

**T1 (determinism) does need Prosimos installed.** It runs both engines with all
randomness removed — fixed durations, deterministic branching, one resource, a
24/7 calendar — where they must agree exactly. Existing parquet files cannot
substitute: they come from normal configurations, not the degenerate one T1
needs. Prosimos is not installed on the development machine, so T1 runs
elsewhere.

**`--output` is silently ignored by Scylla.** The flag is parsed but never wired
to the field `SimulationManager.run()` reads, so output lands in an auto-named
`output_<timestamp>/` directory next to the global config. `run_scylla.py`
works around it by running each simulation in its own directory and discovering
what appeared.

**The Scylla build must include commit `f9671cb`** ("Fix #72: default timetables
for named resource instances are ignored"). The copy bundled with SimuBridge is
46 commits behind and predates it, and that fix covers exactly the per-instance
timetable mechanism the pooling strategy depends on.

---

## Status

| | |
|---|---|
| Converter | complete — 5 modules, `engine="scylla"` runs end to end |
| Tests | 163 passing, 6 skipped without a built jar |
| Prosimos arm | unchanged; default engine, all existing callers unaffected |
| Not yet done | T1–T5 validation, cluster scaling, the two open decisions above |
