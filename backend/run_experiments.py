"""
Batch experiment runner for the thesis sensitivity-analysis experiments.

Runs the sampling+simulation pipeline for all planned configurations of a
given phase, then automatically runs the sensitivity analysis for each
completed run. Records wall-clock durations to <dataset>_run_times.csv.

Usage (from the backend folder, using the backend venv):
    python run_experiments.py --dataset production --phase 1
    python run_experiments.py --dataset datamining --index 0
    ...
    python run_experiments.py --dataset production --smoke   # tiny test run

Resume-safe: completed runs (folder with process_kpis parquet present) are
skipped; incomplete folders are renamed aside and re-run.
"""

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd

# Backend code prints emoji; force UTF-8 so cp1252 consoles don't crash the run
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Run from the backend folder so all relative paths used by the pipeline work
BACKEND_DIR = Path(__file__).resolve().parent
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

from src.simulation_pipeline.run_simulation_pipeline import run_simulation_pipeline
from src.sensitivity_analysis.run_sensitivity_analysis import run_sensitivity_analysis

# --- Per-dataset settings ---
# The case count comes from each dataset's own pre-study, not from the thesis's
# global 3000: the pre-study is explicitly per-dataset (thesis 4.5.1).
DATASETS = {
    "production": {
        "bpmn": "models/production/Production_train.bpmn",
        "json": "models/production/Production_train.json",
        "cases": [3000],
    },
    "datamining": {
        "bpmn": "models/datamining/ConsultaDataMining201618_train.bpmn",
        "json": "models/datamining/ConsultaDataMining201618_train.json",
        "cases": [5000],
    },
}

# Filled in by main() once --dataset is known.
BPMN_PATH = JSON_PATH = None
BASE_OUTPUT = TIMES_CSV = LOG_FILE = None
CASES = None
PREFIX = None

REPLICATIONS = 1
NUM_LEVELS = 6  # Morris only

SEEDS = [100, 200, 300]

# Parameter selection for the grouped phases (1-5). The within-group phases
# (6-7) override these through their kwargs to switch grouping off and enable
# exactly one group.
GROUPED_DEFAULTS = dict(
    is_groups=True,
    is_arrival_distribution=True,
    is_arrival_calendar=True,
    is_tasks_resources=True,
    is_resource_calendars=True,
    is_resource_numbers=True,
)

GROUP_FLAGS = (
    "is_gateway",
    "is_arrival_distribution",
    "is_arrival_calendar",
    "is_tasks_resources",
    "is_resource_calendars",
    "is_resource_numbers",
)


def build_runs():
    """Build the full ordered run list: (phase, folder_name, kwargs)."""
    runs = []

    def sobol(gw: bool, n: int, seed: int):
        tag = "gw" if gw else "nogw"
        return (
            f"{PREFIX}_sobol_{tag}_n{n}_seed{seed}",
            dict(
                is_sobol=True,
                is_gateway=gw,
                n_samples=n,
                n_trajectories=None,
                num_levels=None,
                seed=seed,
            ),
        )

    def morris(gw: bool, t: int, seed: int):
        tag = "gw" if gw else "nogw"
        return (
            f"{PREFIX}_morris_{tag}_t{t}_seed{seed}",
            dict(
                is_sobol=False,
                is_gateway=gw,
                n_samples=None,
                n_trajectories=t,
                num_levels=NUM_LEVELS,
                seed=seed,
            ),
        )

    def within_group(group_flag: str, label: str, t: int, seed: int):
        """
        One group opened up: is_groups=False with a single group enabled, so
        every parameter inside it gets its own sensitivity index.
        """
        flags = {k: False for k in GROUP_FLAGS}
        flags[group_flag] = True
        return (
            f"{PREFIX}_within_{label}_morris_t{t}_seed{seed}",
            dict(
                is_sobol=False,
                n_samples=None,
                n_trajectories=t,
                num_levels=NUM_LEVELS,
                seed=seed,
                is_groups=False,
                **flags,
            ),
        )

    # Phase 1: all Morris runs (Morris stability, feasible locally)
    for t in (64, 128, 256, 512):
        for s in SEEDS:
            runs.append((1, *morris(False, t, s)))

    # Phase 2: Sobol 512 (nogw then gw)
    for s in SEEDS:
        runs.append((2, *sobol(False, 512, s)))
    for s in SEEDS:
        runs.append((2, *sobol(True, 512, s)))

    # Phase 3: Sobol 1024
    for s in SEEDS:
        runs.append((3, *sobol(False, 1024, s)))
    for s in SEEDS:
        runs.append((3, *sobol(True, 1024, s)))

    # Phase 4: Sobol 2048
    for s in SEEDS:
        runs.append((4, *sobol(False, 2048, s)))
    for s in SEEDS:
        runs.append((4, *sobol(True, 2048, s)))

    # Phase 5: Sobol at N=64, matching "Step 3/sobol finding ST ratio with 64"
    # in the student's archive. At an equal simulation budget -- Sobol 64x7=448
    # against Morris 64x6=384 -- this is what justifies preferring Morris.
    # Appended last so the existing run indices stay put.
    for s in SEEDS:
        runs.append((5, *sobol(False, 64, s)))

    # Phases 6-7: within-group Morris, matching "Step 4" in the student's
    # archive (thesis Experiment 4). These run ungrouped with a single group
    # enabled, so the analysis reports one index per individual gateway or
    # calendar instead of one per group.
    # Appended last so indices 0-32 keep their meaning.

    # Phase 6 -- gateways: 8 parameters, ~3k simulations for all nine runs.
    for t in (16, 32, 64):
        for s in SEEDS:
            runs.append((6, *within_group("is_gateway", "gateways", t, s)))

    # Phase 7 -- resource calendars: same shape but 1081 parameters and ~364k
    # simulations, twice the cost of every grouped run put together. Its own
    # phase so it can be queued, throttled and cancelled independently.
    for t in (16, 32, 64):
        for s in SEEDS:
            runs.append((7, *within_group("is_resource_calendars", "rescal", t, s)))

    return runs


def log(msg: str):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def append_time_row(row: dict):
    exists = TIMES_CSV.exists()
    with open(TIMES_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "phase", "folder", "method", "gateway", "size", "seed",
                "start", "end", "duration_min", "status", "note",
            ],
        )
        if not exists:
            w.writeheader()
        w.writerow(row)


def run_is_complete(folder: Path) -> bool:
    """A run is complete when the aggregated KPI parquet exists."""
    sa_inputs = folder / "sensitivity_analysis_inputs"
    return sa_inputs.exists() and any(sa_inputs.rglob("process_kpis_*.parquet"))


def prune_samples(folder: Path):
    """
    Delete the expanded per-sample Prosimos configs once they have been consumed.

    write_all_samples_to_json_files() writes them, simulate_samples() reads them,
    and nothing afterwards needs them: the sensitivity analysis works off
    sensitivity_analysis_inputs/. They dominate the run size (1.3-4.9 GB per run
    versus ~250 KB for everything else), so keeping them exhausts the disk long
    before the run list is finished.
    """
    samples_dir = folder / "samples"
    if not samples_dir.exists():
        return
    if not run_is_complete(folder):
        log(f"    keeping samples/ (run incomplete)")
        return

    # A run can finish with a KPI parquet yet still lose individual simulations,
    # in which case SALib silently writes an empty result and the run has to be
    # repeated from the samples. Only prune once a non-empty index file exists.
    results = list((folder / "sensitivity_analysis_outputs").rglob("*_order.json"))
    if not results:
        log(f"    keeping samples/ (no sensitivity results yet)")
        return
    for r in results:
        try:
            if not json.loads(r.read_text()):
                log(f"    keeping samples/ ({r.parent.name}/{r.name} is empty -> rerun needed)")
                return
        except json.JSONDecodeError:
            log(f"    keeping samples/ ({r.name} unreadable)")
            return

    freed = sum(f.stat().st_size for f in samples_dir.rglob("*") if f.is_file())
    shutil.rmtree(samples_dir)
    log(f"    pruned samples/ ({freed / 1e9:.1f} GB freed)")


def run_sa_for_folder(folder: Path, kpis: list[str]):
    """Replicates the /sensitivity-analysis endpoint for a finished run."""
    sa_dir = folder / "sensitivity_analysis_inputs"
    config_files = list(sa_dir.rglob("sa_config.json"))
    if not config_files:
        raise FileNotFoundError(f"No sa_config.json in {sa_dir}")
    with open(config_files[0], "r") as f:
        sa_config = json.load(f)

    frames = []
    for pfile in sa_dir.rglob("process_kpis_*.parquet"):
        df = pd.read_parquet(pfile)
        if "count" in df.columns:
            df = df.drop(columns=["count"])
        match = re.search(r"process_kpis_(\d+)_cases", pfile.stem)
        if not match:
            raise ValueError(f"Could not extract num_cases from {pfile.name}")
        df["num_cases"] = int(match.group(1))
        frames.append(df)
    process_kpis = pd.concat(frames, ignore_index=True)

    for kpi in kpis:
        out = folder / "sensitivity_analysis_outputs" / f"sa_{kpi}"
        if out.exists():
            log(f"    SA sa_{kpi} already exists, skipping")
            continue
        run_sensitivity_analysis(
            kpi=kpi,
            stat_type="avg",
            process_kpis=process_kpis,
            sa_config=sa_config,
            output_folder=out,
        )
        log(f"    SA done: {out.name}")


def execute_run(phase: int, name: str, kw: dict, cases=None, replications=None) -> str:
    folder = BASE_OUTPUT / name
    cases = cases if cases is not None else CASES
    replications = replications if replications is not None else REPLICATIONS

    if folder.exists():
        if run_is_complete(folder):
            log(f"SKIP (complete): {name}")
            # Still make sure SA outputs exist
            try:
                run_sa_for_folder(folder, sa_kpis_for(name))
                prune_samples(folder)
            except Exception as e:
                log(f"    SA error on skip-path for {name}: {e}")
            return "skipped"
        stash = folder.with_name(f"{name}_incomplete_{datetime.now().strftime('%H%M%S')}")
        log(f"INCOMPLETE folder found, moving aside -> {stash.name}")
        shutil.move(str(folder), str(stash))

    log(f"START {name} (phase {phase})")
    start = datetime.now()
    t0 = time.perf_counter()
    status, note = "success", ""
    try:
        # Phases 1-5 analyse all five groups together; the within-group phases
        # override these to switch grouping off and enable a single group.
        pipeline_kw = {**GROUPED_DEFAULTS, **kw}
        run_simulation_pipeline(
            bpmn_path=BPMN_PATH,
            json_path=JSON_PATH,
            calc_second_order=False,
            replication_runs=replications,
            cases_list=cases,
            simulation_results_folder=str(folder),
            **pipeline_kw,
        )
    except Exception as e:
        status = "FAILED"
        note = f"{type(e).__name__}: {e}"
        log(f"FAILED {name}: {note}")
        log(traceback.format_exc())

    dur_min = (time.perf_counter() - t0) / 60.0
    end = datetime.now()

    m = re.match(
        rf"{PREFIX}_(?:(sobol|morris)_(gw|nogw)|within_(\w+)_(morris))"
        r"_[nt](\d+)_seed(\d+)",
        name,
    )
    append_time_row({
        "phase": phase,
        "folder": name,
        # groups: 1/2 for the grouped runs, 3/4 for the within-group ones
        "method": (m.group(1) or m.group(4)) if m else "",
        "gateway": (m.group(2) or f"within-{m.group(3)}") if m else "",
        "size": m.group(5) if m else "",
        "seed": m.group(6) if m else "",
        "start": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end": end.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_min": f"{dur_min:.1f}",
        "status": status,
        "note": note,
    })
    log(f"END {name}: {status} ({dur_min:.1f} min)")

    if status == "success":
        try:
            run_sa_for_folder(BASE_OUTPUT / name, sa_kpis_for(name))
            prune_samples(BASE_OUTPUT / name)
        except Exception as e:
            log(f"    SA error for {name}: {e}")
            log(traceback.format_exc())

    return status


def sa_kpis_for(name: str) -> list[str]:
    """
    Which KPIs to analyse, matching what the student's archive holds per run type.

      Sobol without gateways, N>=512  cycle + waiting + processing
      Morris without gateways         cycle + waiting
      Sobol N=64 (Morris comparison)  cycle + waiting
      anything with gateways          cycle only

    The large Sobol runs feed the KPI-comparison experiment, which is why they
    alone carry processing time. Gateway runs were only ever analysed on cycle
    time.
    """
    if "within_" in name:
        return ["cycle_time", "waiting_time"]
    if "_gw_" in name:
        return ["cycle_time"]
    if "sobol_nogw" in name and "_n64_" not in name:
        return ["cycle_time", "waiting_time", "processing_time"]
    return ["cycle_time", "waiting_time"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=sorted(DATASETS), required=True,
                    help="which discovered model to run")
    ap.add_argument("--phase", type=int, choices=[1, 2, 3, 4, 5, 6, 7], help="run all runs of this phase")
    ap.add_argument("--index", type=int,
                    help="run only run number INDEX (0-based) of the whole list; "
                         "one Slurm array task per run")
    ap.add_argument("--list", action="store_true", help="print the numbered run list and exit")
    ap.add_argument("--smoke", action="store_true", help="tiny end-to-end test run")
    args = ap.parse_args()

    global BPMN_PATH, JSON_PATH, BASE_OUTPUT, TIMES_CSV, LOG_FILE, CASES, PREFIX
    cfg = DATASETS[args.dataset]
    PREFIX = args.dataset
    BPMN_PATH = str(BACKEND_DIR / cfg["bpmn"])
    JSON_PATH = str(BACKEND_DIR / cfg["json"])
    CASES = cfg["cases"]
    BASE_OUTPUT = BACKEND_DIR / "output/simulation_and_sensitivity_analysis_outputs"
    TIMES_CSV = BACKEND_DIR / f"{args.dataset}_run_times.csv"
    LOG_FILE = BACKEND_DIR / f"{args.dataset}_batch_log.txt"

    if args.list:
        for i, (phase, name, _) in enumerate(build_runs()):
            marker = "done" if run_is_complete(BASE_OUTPUT / name) else "    "
            print(f"{i:3d}  phase {phase}  [{marker}]  {name}")
        sys.exit(0)

    for p in (BPMN_PATH, JSON_PATH):
        if not Path(p).exists():
            log(f"FATAL: input file missing: {p}")
            sys.exit(1)

    if args.smoke:
        log("=== SMOKE TEST: morris t4, 100 cases ===")
        name = f"{PREFIX}_SMOKETEST_morris_t4"
        folder = BASE_OUTPUT / name
        if folder.exists():
            shutil.rmtree(folder)
        status = execute_run(
            0, name,
            dict(is_sobol=False, is_gateway=False, n_samples=None,
                 n_trajectories=4, num_levels=NUM_LEVELS, seed=100),
            cases=[100],
        )
        log(f"=== SMOKE TEST RESULT: {status} ===")
        sys.exit(0 if status == "success" else 1)

    if args.index is not None:
        all_runs = build_runs()
        if not 0 <= args.index < len(all_runs):
            ap.error(f"--index must be between 0 and {len(all_runs) - 1}")
        phase, name, kw = all_runs[args.index]
        log(f"=== RUN {args.index}: {name} (phase {phase}) ===")
        status = execute_run(phase, name, kw)
        log(f"=== RUN {args.index} {status} ===")
        sys.exit(1 if status == "FAILED" else 0)

    if args.phase is None:
        ap.error("provide --phase 1..5, --index N, --list, or --smoke")

    runs = [r for r in build_runs() if r[0] == args.phase]
    log(f"=== PHASE {args.phase}: {len(runs)} runs ===")
    results = {}
    for phase, name, kw in runs:
        results[name] = execute_run(phase, name, kw)

    log(f"=== PHASE {args.phase} FINISHED ===")
    for name, status in results.items():
        log(f"  {status:8s} {name}")
    failed = [n for n, s in results.items() if s == "FAILED"]
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
