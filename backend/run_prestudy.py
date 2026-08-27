"""
Pre-study: how many cases and replications does a simulation need?

Reproduces the pre-study described in the thesis (Section 4.5.1 for the
motivation, 5.1-5.1.3 for the setup and outcome).

The question it answers: at what number of simulated cases do the KPI values
stop moving, so that residual stochastic variation is negligible next to real
parameter effects?

Method, taken from the student's own user_config.json for
"BPIC_2012_5_replications_10_different_cases":

    Sobol sampling, N=32, gateways included -> 32 x (6+2) = 256 samples
    Every sample simulated at each case count in CASES
    5 replications, so 1/3/5-replication settings can all be read off afterwards
    seed 100
    KPIs: cycle time, processing time, waiting time

Sufficiency is judged from the percentage change in aggregated KPIs (mean,
median, p10, p90 across the 256 samples) between successive case counts. The
thesis states no numerical threshold -- it reads the curves -- so this script
reports the percentages and leaves the judgement explicit rather than baking in
a cutoff.

One run of the pipeline covers everything: cases_list drives the case sweep and
replication_runs=5 gives the replication sweep.

Usage (cluster):
    python run_prestudy.py --dataset production
    python run_prestudy.py --dataset production --analyse-only   # skip simulation, redo tables

Output: output/simulation_and_sensitivity_analysis_outputs/<dataset>_prestudy/
        plus prestudy_kpi_by_cases.csv and prestudy_pct_change.csv
"""

import argparse
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parent
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.simulation_pipeline.run_simulation_pipeline import run_simulation_pipeline

# Each dataset is a discovered model: a BPMN plus the Prosimos parameter JSON.
# No event log is involved -- that was only needed for SIMOD discovery, which is
# already done. Prosimos generates events from the model alone.
DATASETS = {
    "datamining": (
        "models/datamining/ConsultaDataMining201618_train.bpmn",
        "models/datamining/ConsultaDataMining201618_train.json",
    ),
    "production": (
        "models/production/Production_train.bpmn",
        "models/production/Production_train.json",
    ),
}

# Filled in by main() once --dataset is known.
BPMN_PATH = JSON_PATH = None
OUT_DIR = None

# Exactly the thesis's sweep (Section 5.1)
CASES = [50, 100, 200, 300, 500, 1000, 2000, 3000, 4000, 5000, 6000, 7000]
REPLICATIONS = 5
N_SAMPLES = 32
SEED = 100

KPIS = ["cycle_time", "processing_time", "waiting_time"]


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def simulate():
    """Run the sweep. One pipeline call covers every case count and replication."""
    log(f"Pre-study: {len(CASES)} case counts x {REPLICATIONS} replications")
    log(f"  Sobol N={N_SAMPLES} with gateways -> {N_SAMPLES * 8} samples per setting")
    log(f"  cases: {CASES}")

    t0 = time.perf_counter()
    run_simulation_pipeline(
        bpmn_path=BPMN_PATH,
        json_path=JSON_PATH,
        is_sobol=True,
        is_groups=True,
        is_gateway=True,
        is_arrival_distribution=True,
        is_arrival_calendar=True,
        is_tasks_resources=True,
        is_resource_calendars=True,
        is_resource_numbers=True,
        n_samples=N_SAMPLES,
        n_trajectories=None,
        num_levels=None,
        calc_second_order=False,
        replication_runs=REPLICATIONS,
        cases_list=CASES,
        simulation_results_folder=str(OUT_DIR),
        seed=SEED,
    )
    log(f"simulation finished in {(time.perf_counter() - t0) / 60:.1f} min")


def collect() -> pd.DataFrame:
    """
    Read every per-run chunk parquet into one long table.

    Layout written by the pipeline:
        simulation_results/sim_results_<cases>_cases/run_<k>/process_chunk_*.parquet
    """
    rows = []
    sim_root = OUT_DIR / "simulation_results"
    if not sim_root.exists():
        raise FileNotFoundError(f"no simulation_results under {OUT_DIR}")

    for case_dir in sorted(sim_root.glob("sim_results_*_cases")):
        cases = int(re.search(r"sim_results_(\d+)_cases", case_dir.name).group(1))
        for run_dir in sorted(case_dir.glob("run_*")):
            run_idx = int(run_dir.name.split("_")[1])
            frames = [pd.read_parquet(p) for p in sorted(run_dir.glob("process_chunk_*.parquet"))]
            if not frames:
                continue
            df = pd.concat(frames, ignore_index=True)
            df = df[df["metric"].isin(KPIS)][["sample_id", "metric", "avg"]]
            df["cases"] = cases
            df["run"] = run_idx
            rows.append(df)

    if not rows:
        raise RuntimeError("no chunk parquets found")
    out = pd.concat(rows, ignore_index=True)
    log(f"collected {len(out):,} rows across "
        f"{out['cases'].nunique()} case counts and {out['run'].nunique()} runs")
    return out


def aggregate(raw: pd.DataFrame, n_reps: int) -> pd.DataFrame:
    """
    Average each sample over the first `n_reps` replications, then aggregate
    across samples the way the thesis does: mean, median, p10, p90.
    """
    sub = raw[raw["run"] <= n_reps]
    per_sample = sub.groupby(["cases", "metric", "sample_id"], as_index=False)["avg"].mean()

    agg = per_sample.groupby(["cases", "metric"])["avg"].agg(
        mean="mean",
        median="median",
        p10=lambda s: np.percentile(s, 10),
        p90=lambda s: np.percentile(s, 90),
    ).reset_index()
    agg["replications"] = n_reps
    return agg


def pct_change(agg: pd.DataFrame) -> pd.DataFrame:
    """Relative change of each aggregate between successive case counts."""
    out = []
    for (metric, reps), grp in agg.groupby(["metric", "replications"]):
        grp = grp.sort_values("cases").reset_index(drop=True)
        for stat in ("mean", "median", "p10", "p90"):
            prev = grp[stat].shift(1)
            change = (grp[stat] - prev).abs() / prev.abs() * 100
            for i, row in grp.iterrows():
                if i == 0:
                    continue
                out.append({
                    "metric": metric,
                    "replications": reps,
                    "from_cases": int(grp.loc[i - 1, "cases"]),
                    "to_cases": int(row["cases"]),
                    "statistic": stat,
                    "pct_change": round(float(change[i]), 3),
                })
    return pd.DataFrame(out)


def analyse():
    raw = collect()

    aggs = pd.concat([aggregate(raw, r) for r in (1, 3, 5)], ignore_index=True)
    changes = pct_change(aggs)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    aggs.to_csv(OUT_DIR / "prestudy_kpi_by_cases.csv", index=False)
    changes.to_csv(OUT_DIR / "prestudy_pct_change.csv", index=False)

    log(f"wrote prestudy_kpi_by_cases.csv ({len(aggs)} rows)")
    log(f"wrote prestudy_pct_change.csv ({len(changes)} rows)")

    # A readable summary: mean-statistic change per step, 5 replications
    print("\n=== percentage change of the mean, 5 replications ===")
    view = changes[(changes["replications"] == 5) & (changes["statistic"] == "mean")]
    pivot = view.pivot(index="to_cases", columns="metric", values="pct_change")
    print(pivot.to_string())

    print("\n=== does adding replications change the picture? "
          "(mean statistic, cycle time) ===")
    view = changes[(changes["metric"] == "cycle_time") & (changes["statistic"] == "mean")]
    pivot = view.pivot(index="to_cases", columns="replications", values="pct_change")
    print(pivot.to_string())

    print("\nThe thesis sets no numerical threshold; it reads these curves. State"
          "\nwhichever cutoff you apply explicitly in the write-up.")


def merge_into(final: Path, partial: Path):
    """Move newly simulated case folders into an existing pre-study directory."""
    src = partial / "simulation_results"
    dst = final / "simulation_results"
    dst.mkdir(parents=True, exist_ok=True)
    moved = []
    for case_dir in sorted(src.glob("sim_results_*_cases")):
        target = dst / case_dir.name
        if target.exists():
            log(f"    {case_dir.name} already present, replacing")
            shutil.rmtree(target)
        shutil.move(str(case_dir), str(target))
        moved.append(case_dir.name)
    shutil.rmtree(partial, ignore_errors=True)
    log(f"merged {len(moved)} case folders into {final.name}: {', '.join(moved)}")


def main():
    global BPMN_PATH, JSON_PATH, OUT_DIR

    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=sorted(DATASETS), default="production",
                    help="which discovered model to run the pre-study on")
    ap.add_argument("--analyse-only", action="store_true",
                    help="skip simulation, rebuild the tables from existing output")
    ap.add_argument("--fresh", action="store_true",
                    help="delete any existing pre-study output and start over")
    ap.add_argument("--cases", type=int, nargs="+", metavar="N",
                    help="override the case sweep, e.g. --cases 4000 5000 6000 7000")
    args = ap.parse_args()

    global CASES
    if args.cases:
        CASES = args.cases

    bpmn_rel, json_rel = DATASETS[args.dataset]
    BPMN_PATH = str(BACKEND_DIR / bpmn_rel)
    JSON_PATH = str(BACKEND_DIR / json_rel)
    OUT_DIR = (BACKEND_DIR / "output/simulation_and_sensitivity_analysis_outputs"
               / f"{args.dataset}_prestudy")

    log(f"dataset: {args.dataset}")
    log(f"  bpmn: {bpmn_rel}")
    log(f"  json: {json_rel}")
    log(f"  out : {OUT_DIR.name}")

    for f in (BPMN_PATH, JSON_PATH):
        if not Path(f).exists():
            log(f"FATAL: missing input {f}")
            sys.exit(1)

    if args.analyse_only:
        analyse()
        return

    # run_simulation_pipeline refuses to write into a folder that already
    # exists, so the directory must not be created ahead of it.
    if OUT_DIR.exists():
        if args.fresh:
            log(f"removing existing {OUT_DIR.name}")
            shutil.rmtree(OUT_DIR)
            simulate()
        else:
            # Adding case counts to a sweep that already ran: simulate into a
            # scratch folder, then move the new sim_results_*_cases directories
            # across. Anything already present is left alone.
            log(f"{OUT_DIR.name} exists -- adding cases {CASES} to it")
            final = OUT_DIR
            globals()["OUT_DIR"] = OUT_DIR.with_name(OUT_DIR.name + "_partial")
            if OUT_DIR.exists():
                shutil.rmtree(OUT_DIR)
            simulate()
            merge_into(final, OUT_DIR)
            globals()["OUT_DIR"] = final
    else:
        simulate()

    analyse()


if __name__ == "__main__":
    main()
