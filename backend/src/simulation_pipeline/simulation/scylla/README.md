# Scylla engine adapter — findings that need a decision

Seven things surfaced while building and validating the converter that are not
implementation details. Each changes how the Scylla results should be read.

Three came from writing the code. Four more came from running both engines
against each other (T1, T4), and none of those were visible in the Phase 1 spike
or in any unit test — they only appear when the two engines simulate the same
model and disagree.

Written 2026-08-28, against BPIC 2012 and BPIC 2017.

**One earlier conclusion in this file was wrong and has been corrected.** After
T1, the residual divergence was attributed to calendar semantics, on the
strength of a single experiment (replacing every calendar with 24/7 closed most
of the gap). T4 tested that directly and it does not hold — see "Where the
engines still differ".

---

## What T1 found

T1 strips a model down to something both engines *must* agree on: fixed
durations, one always-available resource, a 24/7 calendar, branching forced to
1.0/0.0. Under those conditions cycle time is arithmetic, so any disagreement is
a translation bug. It passes 10/10 — after three fixes.

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
activity, so almost no contention *between* activities survived. Not a T1-only
artefact — it inflated capacity in every real run too.

**What was done.** One shared pool holds each resource exactly once
(`SHARED_POOL_ID`). Per-resource calendars still survive as named `<instance>`
elements, so `is_resource_calendars` stays meaningful.

**What it costs.** Eligibility — which resource may perform which activity — is
now lost as well. Scylla cannot express both eligibility and correct capacity,
and capacity is what queueing depends on. See finding 7: eligibility turns out
to be the main remaining source of divergence, so this trade is worth stating
explicitly in the write-up rather than burying.

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
any bucket count, even 10. The top decile is emitted sample by sample rather
than averaged, so the extreme tail survives (it was otherwise clipped from
36448 s to 3765 s), and queueing is driven by exactly those long services.

---

## What T4 found

T4 changes one variable at a time from the T1 baseline, durations fixed
throughout so any difference is structural rather than sampling noise.

| | Setup | Scylla / Prosimos cycle time |
|---|---|---|
| A | degenerate baseline (T1 conditions) | 1.00 |
| B | + real resources and their real calendars | **0.53** |
| C | real resources, all on a 24/7 calendar | 1.00 |
| D | one resource, on a real narrow calendar | 1.00 |
| E | all resources, one shared narrow calendar, full eligibility | 0.97 |

### 4. Calendar semantics is *not* the cause — the earlier conclusion was wrong

C isolates capacity, D isolates calendars, E combines them. All three agree.
Off-shift time, wrap-around ranges and multi-period calendars are handled the
same way by both engines.

The earlier attribution came from one experiment on the real model: replacing
every calendar with 24/7 moved `cycle_time` from +121% to +10%, which looked
like calendars. It was not — 24/7 also removes the *interaction* between limited
availability and restricted eligibility, and it is the eligibility half that
matters (finding 7). A single experiment with two variables moving at once was
not enough to attribute anything, and this is worth remembering for the write-up.

### 5. Scylla silently drops histogram entries that share a value

Found while chasing the residual, and larger than what was being chased.

Scylla parses entries into a `HashMap<Double, Double>` using
`entries.put(value, frequency)` (`EmpiricalDistribution.java:11`) — overwrite,
not accumulate. Two entries with the same value collapse into one, and the first
one's mass disappears with no warning.

Easy to hit: a pool of resources with identical fixed durations produces
identical bucket means. On a 38-resource BPIC 2012 activity, 100 emitted entries
became 75 and the pooled mean fell from 1095 s to **556 s**, against a model mean
of 1095 s.

**What was done.** `append_histogram` accumulates into `{value -> frequency}`
before emitting, so no two entries share a value. `processing_time` against
Prosimos improved from -53% to -21%, and is now consistent across fixed and real
durations (-20.7% / -20.9%) where it was not before.

DESMO-J itself samples `DiscreteDistEmpirical` correctly — verified directly,
mean 200.3 for 100/200/300 at equal frequency, with both raw and normalised
frequencies. The loss was entirely in Scylla's parser, and is now avoided in
what we emit rather than needing a patch to Scylla.

---

## What building the converter found

### 6. Two metrics do not mean the same thing in both engines

**`waiting_time`.** Scylla's reported waiting total exceeds its own flow time
total — 88.0M s against 71.5M s on BPIC 2012 at 3000 cases. Impossible for a
wall-clock measure. `StatisticsLogger` (`statslogger_nojar/StatisticsLogger.java:186-195`)
accumulates the enable → begin gap for *every activity instance* and sums them
per case, so activities waiting concurrently are counted more than once.
Prosimos measures waiting as wall-clock time per case.

T1 sharpened this: even where nothing can queue, Prosimos reports 60 s of
waiting, unchanged when arrivals are spaced ten times further apart. Its figure
is structural too, just structured differently.

**`processing_time`.** The same shape of gap, in the other direction. Scylla's
`effective` is `durationTotal - durationInactive`: wall-clock time during which
*at least one* activity was running, so concurrent activities count once.
Prosimos sums activity durations. Measured on BPIC 2012 at 500 cases with fixed
durations: 2055 s/case against 4726 s/case. This is what the residual -21%
after finding 5 is.

**What was done.** Both are emitted; `check_consistency()` warns when waiting
exceeds cycle time.

**Still open.** Whether the Scylla arm reports either metric's sensitivity.
`cycle_time` shares a definition across both engines and is safe. For the other
two, either:

- lead with `cycle_time` only, and state the limitation; or
- recompute both per case from the XES log, which makes them comparable but
  gives up the "no event-log parsing needed" result from the spike.

The first is cheap and honest. **A supervisor decision, not a coding one.**

### 7. The dispatcher must not be a closure

`bpic2013_run_times.csv` records three runs lost this way:

```
1,bpic2013_morris_nogw_t512_seed100,...,FAILED,PicklingError: Could not pickle the task to send it to the workers.
```

`simulate_all_samples` dispatches through `joblib.Parallel(backend="loky")`,
which pickles the worker callable. Selecting an engine invites a closure, and a
closure raises `PicklingError` at the top of a multi-hour cluster run.
`_engine_worker()` returns a module-level function or a `functools.partial` over
one, and a test pickles both workers to keep it that way.

Those earlier BPIC 2013 failures remain unexplained and are worth a look before
the next large Morris run.

### 8. Two Scylla quirks worth knowing before debugging anything

**`--output` is silently ignored.** Parsed, but never wired to the field
`SimulationManager.run()` reads, so output lands in an auto-named
`output_<timestamp>/` directory next to the global config. `run_scylla.py` runs
each simulation in its own directory and discovers what appeared.

**Scylla never fails on XML it does not recognise** — it logs and skips
(`SimulationConfigurationParser.java:245-252`). "It ran" is never proof it read
what was written, which is why both builders validate their own output and
`parse_process_rows` checks the simulated case count against what was requested.

---

## Where the engines still differ

Attribution as it now stands, on BPIC 2012:

| Source | Size | Ours to fix? |
|---|---|---|
| **Eligibility** (shared pool ignores which resource may do which activity) | dominant | No — Scylla cannot express it alongside correct capacity |
| `processing_time` / `waiting_time` definitions | -21% on processing | No — different measures, not different results |
| Calendar semantics | none detectable | Ruled out by T4 (C, D, E) |
| Discretisation | ~0% on the mean | Fixed (findings 3, 5) |
| Weighting | was 11-65% | Fixed by turning it off (finding 2) |

Eligibility is the honest headline. Prosimos restricts each activity to the
resources Simod found performing it; the shared pool lets any resource do
anything:

| activity | eligible resources | share of the 47 |
|---|---|---|
| 667876f6-e | 2 | 4% |
| a7e0a1e4-5 | 27 | 57% |
| 265a48ad-6 | 38 | 81% |
| cc450863-8 | 40 | 85% |
| 347ce5f3-c | 42 | 89% |
| 194e4a28-5 | 42 | 89% |

One caveat worth recording: giving Prosimos full eligibility too did *not* close
the gap on the real model (+118% became +140%), so eligibility is not a simple
additive term. It interacts with capacity and with the metric definitions above.
Quantifying it properly is a T5 job, and it belongs in the limitations section
either way — this is a structural difference between the two simulation models,
not a converter defect.

---

## The two plugins, and what they changed

Samira's decision (2026-09-01) was to keep the differentiated Simod output and
extend Scylla instead of pooling the model to fit it. Both plugins are written
and measured.

Measured on BPIC 2012 at 500 cases, mean cycle time:

| | cycle time | vs Prosimos |
|---|---|---|
| Prosimos | ~7000 | — |
| Scylla, stock | 15357 | 2.24x |
| + arrival calendar plugin | 6115 | 0.84x |
| + resource durations plugin | 3397 | **0.47x** |

### The arrival calendar plugin did what T5 predicted

It removed the dominant term. Cases now arrive only during the hours the model's
calendar covers, instead of spreading over all 168.

Two things came out of building it. First, a real defect in Scylla:
`DateTimeUtils.getTimeTableIndexWithinOrNext` ranks candidate windows using
`getNextOrSameZonedDateTime`, which can select a window whose start already
passed earlier the same day. That yields a negative duration and lets arrivals
through outside the calendar -- seen as arrivals at 21:00 and 22:00 against a
calendar closing at 20:00. The plugin computes the delay itself, walking
forward. **The same helper is used elsewhere in Scylla, so resource calendars
may be affected too** -- worth reporting to Leon.

Second, deferring rather than dropping clusters arrivals at window openings, so
the realised inter-arrival distribution is not exactly the configured one. That
is a deliberate choice: it keeps the case count equal to `processInstances`,
which is what Prosimos does.

### The resource duration plugin moved the gap the wrong way

Restoring per-resource durations was expected to close the remaining gap. It
widened it, from 0.84x to 0.47x, and the reason is a genuine engine difference
rather than a bug.

Scylla assigns whichever pool instance is free. A fast resource finishes sooner,
becomes available sooner, and so takes on disproportionately more work. On
`W_Completeren aanvraag` the declared resource means run 6.7 s to 1060.7 s with a
median of 343.8 s; the realised median is 182 s. Fast resources are doing most
of the work, so the activity runs faster than the resource population implies.

**This is the assumption the converter's load weighting was built on, and which
was measured false for Prosimos.** There, selection is near-uniform -- fastest
resource 4.8% of executions, slowest 2.7% -- which is why `weighted=False` is
the default (finding 2). The same assumption turns out to be true for Scylla.
The two engines allocate work differently, and no amount of translation fixes
that.

So the plugin is correct and does what it was asked to do -- durations now
follow the assigned resource, verified with a bimodal test model. What it
exposes is that per-resource durations and availability-based assignment
interact, and Prosimos and Scylla differ in the second.

### What this means for the comparison

The gap is no longer dominated by something missing from Scylla. It is dominated
by how the two engines choose a resource, which is a modelling difference to
report rather than a defect to fix.

Open: whether to keep the resource-duration plugin enabled. Enabled, the model
is faithful to what Simod discovered but the engines diverge more. Disabled,
pooling hides the difference and the numbers agree more closely for the wrong
reason. The first is more honest; the second is easier to compare. Worth a
decision before the comparison study, and worth stating in the write-up either
way.

## Environment

Both engines run on this machine. Prosimos needs Python < 3.12, and current
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
| T1 determinism | passing 10/10 — engines agree exactly when nothing is random |
| T4 calendars | passing 7/7 — calendars ruled out, eligibility identified |
| Tests | 195 passing, none skipped |
| Prosimos arm | unchanged; default engine, existing callers unaffected |
| Arrival calendar plugin | done — 2.24x to 0.84x against Prosimos |
| Resource duration plugin | done — but widens the gap to 0.47x, see above |
| Next | decide whether to keep resource durations on; then the SA comparison |
| Open decision | whether to report waiting_time and processing_time sensitivity |
| Open decision | resource-duration plugin on (faithful) or off (comparable) |

`compare_engines.py` runs both engines on a real model and attributes the gap to
weighting, discretisation and pooling separately.
