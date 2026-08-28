# Phase 1 Spike — run Scylla once, end to end

Goal: answer three questions before writing the real adapter.

1. Does Scylla accept the BPMN that Simod produced?
2. Which KPIs does Scylla report directly, and which must we recompute?
3. **How long does one simulation take?** ← the number that decides the schedule

This is throwaway code. It hard-codes the pooling strategy, skips validation,
and ignores replication. The real adapter replaces it.

## Status (2026-08-28)

All three questions are answered, on `bpic2012`, with the current code in
this folder — `./run_spike.sh build && ./run_spike.sh bench bpic2012`
reproduces it end to end with no further changes needed:

1. BPMN parses — yes.
2. KPIs come out directly in `<dataset>_resourceutilization.xml` — yes, see
   the mapping table under Question 2 below. No XES parsing needed for the
   metrics Prosimos reports.
3. 3000 cases in 1.58 s, beating Prosimos's 2.6–3.1 s reference — full
   sensitivity-analysis matrix is feasible on timing grounds.

Not yet run: `bpic2017` (same commands, untested dataset) and the `cost`
field, which is `0.0` everywhere because the converter doesn't carry cost
data through yet.

## Prerequisites

- Docker (for building Scylla — no local Maven needed)
- Java 8 or newer (to run the jar; Scylla targets 1.8)
- Python 3.9+ (standard library only, no packages needed)

Check:

```bash
docker --version
java -version
python3 --version
```

macOS ships `python3` but not a bare `python` command, and `run_spike.sh`
calls `python`. Either alias it or put a shim on `PATH`, e.g.:
`printf '#!/bin/sh\nexec python3 "$@"\n' > /opt/homebrew/bin/python && chmod +x /opt/homebrew/bin/python`
(a plain symlink doesn't work — `/usr/bin/python3` resolves its real binary
by argv[0], and a symlink named `python` breaks that lookup).

## Setup

```bash
git clone -b spike/scylla-phase1 https://github.com/DogukanDG/bps-prestudy.git
cd bps-prestudy/spike

# Scylla source. NOT the SimuBridge submodule copy: that one is 46 commits
# behind and is missing Fix #72, which breaks the per-instance timetables
# this spike depends on.
git clone https://github.com/bptlab/scylla.git ../../scylla
export SCYLLA_SRC=../../scylla
```

## Run

```bash
chmod +x run_spike.sh

# 1. build scylla.jar (once, ~15-40 min including the Maven image pull)
./run_spike.sh build

# 2. one simulation, small
./run_spike.sh run bpic2012 100

# 3. timing sweep -> bpic2012_timing.csv
./run_spike.sh bench bpic2012
```

On Windows use Git Bash. If `bc` is missing, timings still print from `date`.

`build` runs `mvn clean` and `mvn package` as two separate invocations in the
same container: the local jars (`desmoj`, `openxes`, `ospex`, in `lib/`) only
get installed into the local Maven repo via `install-file` goals bound to the
pom's `clean` phase, so a single `mvn package` can't resolve them.

## What to look for

**Question 1 — does the BPMN parse?** ✅ answered — yes.
Low risk: both BPIC models use only element types Scylla supports (exclusive
gateways, parallel gateways, tasks, one start and one end event). If it fails,
the error names the element. Verified 2026-08-28 on `bpic2012`: model parsed
(`tasks=6 gateways=11`) and the output XES trace count matched the requested
case count exactly at 100/500/1000/3000 cases (see Known trap below).

**Question 2 — which KPIs come out?** ✅ answered — read directly, no XES parsing.

`de.hpi.bpt.scylla.plugin.statslogger_nojar.StatisticsLogger` (plugin name
`KPI`, listed in `plugins_list`) is always on — nothing to enable — and writes
`<dataset>/out_<cases>/global_config_resourceutilization.xml`. Its internal
field names (`durationTotal`, `durationInactive`, `durationResourcesIdle`,
`durationWaiting`, `costs`) don't appear verbatim in the XML; they're written
under different tag names:

| XML tag (per-process, in `<time>`) | Scylla internal field | Prosimos equivalent |
|---|---|---|
| `flow_time` | `durationTotal` | `cycle_time` |
| `effective` | `durationTotal - durationInactive` | `processing_time` |
| `waiting` | `durationWaiting` | `waiting_time` |
| `off_timetable` | `durationResourcesIdle` | idle/resource-paused time |
| `cost` (sibling of `<time>`) | `costs` | cost |

Same breakdown is repeated per-activity (`<activities><activity>`) and
per-resource (`<resources><resource>`, in-use/available/workload). Verified
2026-08-28 on `bpic2012` at 100/500/1000/3000 cases — the file is produced
every run.

`global_config_batchactivitystats.txt` is a **different** plugin (batch
activities) and says duration/cost live in "the statslogger plug-in output" —
that's a pointer to the file above, not a sign statslogger is missing. It's
empty here because the BPIC 2012 conversion defines no batch activities.

One gap: `cost` is `0.0` everywhere, because `build_spike_config.py` writes
every resource with `defaultCost="0.0"` (already listed under "Not done here"
below) — not a Scylla limitation, but real adapter needs to carry cost data
through if it's wanted.

**Question 3 — how fast?** ✅ answered — Scylla is faster than Prosimos.
`bpic2012_timing.csv`. The reference to beat is Prosimos at roughly
**2.6–3.1 s per simulation** at 3000 cases (measured from the BPIC 2013 runs in
`bpic2013_run_times.csv`).

Rough consequences at full scale:

| Scylla vs Prosimos | Verdict |
|---|---|
| about the same or faster | full matrix is comfortable |
| 2–5× slower | fine; total still measured in days |
| >10× slower | narrow the scope — drop the largest Sobol runs |

Note the JVM adds 1–2 s of startup per process. Subtract it when comparing
per-simulation cost: at full scale a long-lived JVM removes it entirely.

Measured 2026-08-28 on `bpic2012` (includes JVM startup each run):

| cases | wall time |
|---|---|
| 100 | 0.48 s |
| 500 | 0.64 s |
| 1000 | 0.93 s |
| 3000 | 1.58 s |

3000 cases at 1.58 s beats Prosimos's 2.6–3.1 s reference even with per-process
JVM startup included — "about the same or faster" tier, full matrix is
comfortable. `bpic2017` hasn't been benchmarked yet; same command, different
dataset: `./run_spike.sh bench bpic2017`.

## Known traps

Scylla does **not** fail on XML it does not recognise — it logs and skips
(`SimulationConfigurationParser.java:245-252`). So "it ran" is not proof that
it read what we wrote. Check that the case count and activity names in the
output match the model before trusting any timing.

`Scylla.java` parses `--output=<path>` but never wires it into the field
`SimulationManager.run()` actually checks, so the flag is silently ignored —
output always lands next to `global_config.xml` as an autogenerated
`output_<timestamp>/` folder, not at the path you asked for. `run_spike.sh`
works around this (it diffs the folder before/after the run and moves the new
`output_*` dir to `out_<cases>/`); calling `scylla.jar` directly, expect the
timestamped name instead.

## Files

| File | What |
|---|---|
| `build_spike_config.py` | Simod JSON + BPMN → Scylla global/sim config XML |
| `run_spike.sh` | build / run / bench |
| `<dataset>/global_config.xml` | resources, pooled with per-instance calendars |
| `<dataset>/sim_config.xml` | durations, gateways, arrival rate |
| `<dataset>_timing.csv` | the answer to question 3 |

## What the converter does

Only what the spike needs — the real version is specified in
`SCYLLA_PROPOSAL.md`.

- **Resources:** one pool per activity, `defaultQuantity = N`, each real
  resource a named `<instance>` carrying its own timetable. Scylla requires all
  listed resources simultaneously (`QueueManager.java:154-172`), so writing the
  raw profiles would deadlock.
- **Durations:** `fix`, `expon`, `norm`, `uniform` map directly. Everything
  else — and every pooled activity — is sampled and emitted as a 100-bucket
  `arbitraryFiniteProbabilityDistribution`. Lognormal and gamma have no Scylla
  equivalent and are ~62% of BPIC 2012's duration distributions.
- **Parameter order** is taken from the model files, not from SimuBridge, whose
  mapping reads the wrong indices (see proposal §4).
- **Calendars** are passed through unrounded; Scylla parses `HH:MM:SS`.
- **Not done here:** load-weighted mixing, error handling, per-sample seeding.

## Report back

- `bpic2012_timing.csv`
- The list of output files Scylla produced
- Any error text, verbatim
