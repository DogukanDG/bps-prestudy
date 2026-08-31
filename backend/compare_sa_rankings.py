"""
Run the same sensitivity analysis on both engines and compare the rankings.

The thesis compares Sobol and Morris on *relative parameter rankings* rather
than absolute index values, because their scales are not comparable (4.4.2).
The same argument applies across engines: Scylla's cycle times sit well above
Prosimos's, but that is a level difference, and what the study actually claims
is about which parameters matter most.

So this asks one question: do the two engines rank the parameter groups the
same way? If they do, the level difference does not invalidate the comparison
and the four transferable dimensions are enough. If they do not, we need to
separate "the engines genuinely disagree" from "our translation broke the
model" -- and that is what would justify writing a Scylla arrival-calendar
plugin.

This is a probe, not a result: one dataset, one seed, one method, four grouped
parameters. It settles the next decision, not the thesis.

Usage:
    python compare_sa_rankings.py                     # Morris t=16, quick
    python compare_sa_rankings.py --trajectories 64
"""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import re

import pandas as pd

from src.simulation_pipeline.run_simulation_pipeline import run_simulation_pipeline
from src.sensitivity_analysis.run_sensitivity_analysis import run_sensitivity_analysis

REPO = BACKEND.parent

# The four dimensions Scylla can represent. is_arrival_calendar has no Scylla
# equivalent; is_tasks_resources collapses when durations are pooled.
DIMENSIONS = dict(
    is_gateway=True,
    is_arrival_distribution=True,
    is_arrival_calendar=False,
    is_tasks_resources=False,
    is_resource_calendars=True,
    is_resource_numbers=True,
)

DATASETS = {
    "BPIC_2012": "example_sensitivity_analysis_inputs/BPIC_2012/BPIC_2012_train",
    "BPIC_2017": "example_sensitivity_analysis_inputs/BPIC_2017/BPIC_2017_train",
}


def spearman(a, b):
    """Rank correlation, computed directly to avoid a scipy dependency here."""
    n = len(a)
    if n < 2:
        return float("nan")

    def ranks(values):
        order = sorted(range(n), key=lambda i: values[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and values[order[j + 1]] == values[order[i]]:
                j += 1
            average = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = average
            i = j + 1
        return out

    ra, rb = ranks(a), ranks(b)
    mean_a, mean_b = sum(ra) / n, sum(rb) / n
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(ra, rb))
    den = (sum((x - mean_a) ** 2 for x in ra)
           * sum((y - mean_b) ** 2 for y in rb)) ** 0.5
    return num / den if den else float("nan")


WEEK = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY",
        "SATURDAY", "SUNDAY"]
ALWAYS_ON = [{"from": d, "to": d, "beginTime": "00:00:00",
              "endTime": "23:59:59"} for d in WEEK]


def strip_arrival_calendar(json_path: Path, out_dir: Path) -> Path:
    """Copy the model with a 24/7 arrival calendar.

    Scylla has no arrival calendar, so it effectively simulates this version.
    Running Prosimos on it too removes that difference and isolates whatever
    else is driving the engines apart.
    """
    model = json.loads(json_path.read_text(encoding="utf-8"))
    model["arrival_time_calendar"] = list(ALWAYS_ON)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{json_path.stem}_no_arrival_calendar.json"
    target.write_text(json.dumps(model), encoding="utf-8")
    return target


def run_engine(engine, dataset, trajectories, cases, seed, out_root,
               label=None, json_override=None):
    stem = REPO / DATASETS[dataset]
    folder = out_root / f"{dataset}_{label or engine}"
    if folder.exists():
        shutil.rmtree(folder)

    started = time.perf_counter()
    run_simulation_pipeline(
        bpmn_path=str(Path(f"{stem}.bpmn")),
        json_path=str(json_override or Path(f"{stem}.json")),
        is_sobol=False,
        is_groups=True,
        n_trajectories=trajectories,
        num_levels=6,
        replication_runs=1,
        cases_list=[cases],
        simulation_results_folder=str(folder),
        seed=seed,
        engine=engine,
        # Scylla ran out of memory with 8 concurrent JVMs on an 8 GB machine:
        # each tried to commit 500-630 MB and six samples died with
        # "insufficient memory for the Java Runtime Environment". Capping the
        # heap keeps them within budget. Without this the failures are silent
        # in the analysis -- SALib just returns [] when the sample matrix and
        # the output vector no longer match.
        engine_options={"heap": "1g"} if engine == "scylla" else None,
        **DIMENSIONS,
    )
    elapsed = time.perf_counter() - started

    analyse(folder)
    return folder, elapsed


def analyse(folder: Path, kpi: str = "cycle_time"):
    """Run the sensitivity analysis over what the pipeline just wrote.

    Mirrors run_experiments.run_sa_for_folder: the analysis takes an assembled
    DataFrame plus the sa_config the pipeline saved, not a folder path.
    """
    sa_dir = folder / "sensitivity_analysis_inputs"
    config_files = list(sa_dir.rglob("sa_config.json"))
    if not config_files:
        raise FileNotFoundError(f"no sa_config.json under {sa_dir}")
    sa_config = json.loads(config_files[0].read_text(encoding="utf-8"))

    frames = []
    for parquet in sa_dir.rglob("process_kpis_*.parquet"):
        df = pd.read_parquet(parquet)
        if "count" in df.columns:
            df = df.drop(columns=["count"])
        match = re.search(r"process_kpis_(\d+)_cases", parquet.stem)
        if not match:
            raise ValueError(f"cannot read case count from {parquet.name}")
        df["num_cases"] = int(match.group(1))
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"no process_kpis parquet under {sa_dir}")

    # Any lost sample makes SALib return [] instead of failing, so check the
    # count before analysing rather than discovering it in an empty result.
    kpis = pd.concat(frames, ignore_index=True)
    produced = kpis["sample_id"].nunique()
    expected = len(sa_config.get("samples") or [])
    if expected and produced != expected:
        errors = sorted((folder).rglob("errors_chunk_*.parquet"))
        detail = ""
        if errors:
            first = pd.read_parquet(errors[0])["error"].iloc[0]
            detail = " | first error: " + first[:200]
        raise RuntimeError(
            f"{folder.name}: {produced} of {expected} samples produced KPIs. "
            f"The sensitivity analysis would silently return []. {detail}"
        )

    run_sensitivity_analysis(
        kpi=kpi,
        stat_type="avg",
        process_kpis=kpis,
        sa_config=sa_config,
        output_folder=folder / "sensitivity_analysis_outputs" / f"sa_{kpi}",
    )


def read_morris(folder):
    """Pull the group -> mu_star mapping out of the analysis output."""
    results = {}
    for path in Path(folder).rglob("*.json"):
        if "sensitivity_analysis_outputs" not in str(path):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and "mu_star" in data and "names" in data:
            for name, value in zip(data["names"], data["mu_star"]):
                results[name] = float(value)
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            for row in data:
                name = row.get("name") or row.get("parameter") or row.get("group")
                value = row.get("mu_star", row.get("mu_star_value"))
                if name is not None and value is not None:
                    results[name] = float(value)
    return results


def report(outcomes, left, right, title):
    """Print the two rankings side by side and return their correlation."""
    shared = sorted(set(outcomes[left]) & set(outcomes[right]))
    if not shared:
        print()
        print(title + ": no comparable groups")
        return None

    l = [outcomes[left][k] for k in shared]
    r = [outcomes[right][k] for k in shared]
    l_rank = {k: i + 1 for i, k in enumerate(
        sorted(shared, key=lambda k: -outcomes[left][k]))}
    r_rank = {k: i + 1 for i, k in enumerate(
        sorted(shared, key=lambda k: -outcomes[right][k]))}

    print()
    print("=" * 74)
    print(title)
    print("{:30} {:>18} {:>18}".format("group", left, right))
    for k in sorted(shared, key=lambda k: l_rank[k]):
        print("{:30} {:>18} {:>18}".format(k[:30], l_rank[k], r_rank[k]))

    rho = spearman(l, r)
    print("Spearman: {:.3f}".format(rho))
    return rho


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=DATASETS, default="BPIC_2012")
    parser.add_argument("--trajectories", type=int, default=16)
    parser.add_argument("--cases", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=100)
    parser.add_argument("--out", default="output/engine_ranking_probe")
    parser.add_argument("--no-arrival-calendar", action="store_true",
                        help="also run Prosimos with a 24/7 arrival calendar, "
                             "to isolate how much of the ranking difference "
                             "that one missing feature explains")
    args = parser.parse_args()

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    active = [k for k, v in DIMENSIONS.items() if v]
    print(f"dataset={args.dataset}  Morris t={args.trajectories}  "
          f"cases={args.cases}  seed={args.seed}")
    print(f"dimensions ({len(active)}): {', '.join(active)}")
    print(f"simulations per engine: {args.trajectories * (len(active) + 1)}\n")

    arms = [("prosimos", "prosimos", None), ("scylla", "scylla", None)]
    if args.no_arrival_calendar:
        stripped = strip_arrival_calendar(
            REPO / (DATASETS[args.dataset] + ".json"), out_root / "models")
        arms.append(("prosimos", "prosimos_no_cal", stripped))

    outcomes = {}
    for engine, label, override in arms:
        print("--- " + label + " ---", flush=True)
        folder, elapsed = run_engine(engine, args.dataset, args.trajectories,
                                     args.cases, args.seed, out_root,
                                     label=label, json_override=override)
        outcomes[label] = read_morris(folder)
        print("    {:.1f} min".format(elapsed / 60), flush=True)

    if "prosimos_no_cal" in outcomes:
        report(outcomes, "prosimos", "scylla",
               "BASELINE: both engines as-is")
        report(outcomes, "prosimos_no_cal", "scylla",
               "ARRIVAL CALENDAR REMOVED FROM PROSIMOS TOO")
        print()
        print("If the second correlation is much higher, the missing arrival")
        print("calendar is what reorders the parameters -- a Scylla plugin")
        print("would fix it. If not, the difference is engine semantics or")
        print("sampling noise.")
        return

    shared = sorted(set(outcomes["prosimos"]) & set(outcomes["scylla"]))
    if not shared:
        print("no comparable groups found in the analysis output")
        print("prosimos keys:", sorted(outcomes["prosimos"])[:8])
        print("scylla keys:  ", sorted(outcomes["scylla"])[:8])
        return

    p = [outcomes["prosimos"][k] for k in shared]
    s = [outcomes["scylla"][k] for k in shared]
    p_rank = {k: i + 1 for i, k in enumerate(sorted(shared, key=lambda k: -outcomes["prosimos"][k]))}
    s_rank = {k: i + 1 for i, k in enumerate(sorted(shared, key=lambda k: -outcomes["scylla"][k]))}

    print("=" * 74)
    print(f"{'group':32} {'prosimos':>12} {'scylla':>12} {'rank P':>7} {'rank S':>7}")
    for k in sorted(shared, key=lambda k: p_rank[k]):
        print(f"{k[:32]:32} {outcomes['prosimos'][k]:12.1f} "
              f"{outcomes['scylla'][k]:12.1f} {p_rank[k]:7} {s_rank[k]:7}")

    rho = spearman(p, s)
    print(f"\nSpearman rank correlation: {rho:.3f}  (n={len(shared)})")
    if rho > 0.8:
        print("-> rankings agree; the level difference does not reorder the "
              "parameters")
    elif rho < 0.5:
        print("-> rankings differ; needs separating engine disagreement from "
              "translation loss")
    else:
        print("-> partial agreement; look at which group moved")


if __name__ == "__main__":
    main()
