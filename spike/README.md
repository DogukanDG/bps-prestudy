# Phase 1 Spike — run Scylla once, end to end

Goal: answer three questions before writing the real adapter.

1. Does Scylla accept the BPMN that Simod produced?
2. Which KPIs does Scylla report directly, and which must we recompute?
3. **How long does one simulation take?** ← the number that decides the schedule

This is throwaway code. It hard-codes the pooling strategy, skips validation,
and ignores replication. The real adapter replaces it.

## Prerequisites

- Docker (for building Scylla — no local Maven needed)
- Java 8 or newer (to run the jar; Scylla targets 1.8)
- Python 3.9+ (standard library only, no packages needed)

Check:

```bash
docker --version
java -version
python --version
```

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

## What to look for

**Question 1 — does the BPMN parse?**
Low risk: both BPIC models use only element types Scylla supports (exclusive
gateways, parallel gateways, tasks, one start and one end event). If it fails,
the error names the element.

**Question 2 — which KPIs come out?**
Look in the output folder. Scylla's `statslogger` plugin reports
`durationTotal`, `durationInactive`, `durationResourcesIdle`, `durationWaiting`
and `costs`. The question is whether these match Prosimos's definitions of
`cycle_time`, `waiting_time`, `processing_time` and the three `idle_*` metrics.

- If they match → read the numbers directly, no XES parsing needed
- If not → recompute from the XES event log

This decides how much work result parsing is.

**Question 3 — how fast?**
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

## Known trap

Scylla does **not** fail on XML it does not recognise — it logs and skips
(`SimulationConfigurationParser.java:245-252`). So "it ran" is not proof that
it read what we wrote. Check that the case count and activity names in the
output match the model before trusting any timing.

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
