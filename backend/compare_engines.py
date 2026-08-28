"""
Compare Prosimos and Scylla on the same model, and attribute the difference.

T5 in the validation plan. Runs both engines on a real model at several case
counts, then decomposes the gap between them into the three translation steps
that could cause it:

    weighting        pooled durations weighted by load vs. equal shares
    discretisation   lognorm/gamma binned into a histogram (bucket count)
    pooling itself   one duration per activity instead of one per resource

Reporting a single "engines differ by X%" number would hide which of these is
responsible, and only the third is forced by Scylla's model -- the other two
are our choices and can be tuned.

Usage:
    python compare_engines.py --dataset BPIC_2012
    python compare_engines.py --dataset BPIC_2017 --cases 100 500 --buckets 20 100 400
"""

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.simulation_pipeline.simulation.scylla import distributions as D
from src.simulation_pipeline.simulation.scylla import build_sim_config as S
from src.simulation_pipeline.simulation.scylla.run_scylla import (
    resolve_jar, simulate_sample_scylla)

REPO = BACKEND.parent
START_ISO = "2023-01-01T00:00:00+02:00"
METRICS = ("cycle_time", "processing_time", "waiting_time")

DATASETS = {
    "BPIC_2012": "example_sensitivity_analysis_inputs/BPIC_2012/BPIC_2012_train",
    "BPIC_2017": "example_sensitivity_analysis_inputs/BPIC_2017/BPIC_2017_train",
}


def load(dataset):
    stem = REPO / DATASETS[dataset]
    return (json.loads(Path(f"{stem}.json").read_text(encoding="utf-8")),
            Path(f"{stem}.bpmn"))


def run_prosimos(model, bpmn, cases, seed):
    from src.simulation_pipeline.simulation.simulate_samples import simulate_sample
    started = time.perf_counter()
    result = simulate_sample(0, model, str(bpmn), cases, START_ISO)
    if result["error"]:
        raise RuntimeError(f"prosimos: {result['error']}")
    rows = {r["metric"]: r for r in result["process_rows"]}
    return rows, time.perf_counter() - started


def run_scylla(model, bpmn, cases, seed, buckets, weighted):
    started = time.perf_counter()
    result = simulate_sample_scylla(
        sample_id=0, sample_data=model, bpmn_path=bpmn, total_cases=cases,
        start_iso=START_ISO, jar_path=resolve_jar(), seed=seed,
        buckets=buckets, weighted=weighted,
    )
    if result["error"]:
        raise RuntimeError(f"scylla: {result['error']}")
    rows = {r["metric"]: r for r in result["process_rows"]}
    return rows, time.perf_counter() - started


def relative(scylla_value, prosimos_value):
    if not prosimos_value:
        return float("nan")
    return 100.0 * (scylla_value - prosimos_value) / prosimos_value


def compare_engines(model, bpmn, cases_list, seed, buckets, weighted):
    print(f"\n{'=' * 78}\nEngine comparison "
          f"(buckets={buckets}, weighted={weighted})\n{'=' * 78}")
    print(f"{'cases':>6} {'metric':16} {'prosimos':>12} {'scylla':>12} "
          f"{'diff':>9}  {'p_secs':>7} {'s_secs':>7}")

    results = []
    for cases in cases_list:
        prosimos, p_time = run_prosimos(model, bpmn, cases, seed)
        scylla, s_time = run_scylla(model, bpmn, cases, seed, buckets, weighted)
        for metric in METRICS:
            p, s = prosimos[metric]["avg"], scylla[metric]["avg"]
            diff = relative(s, p)
            results.append({"cases": cases, "metric": metric,
                            "prosimos": p, "scylla": s, "diff_pct": diff})
            print(f"{cases:6} {metric:16} {p:12.1f} {s:12.1f} {diff:8.1f}%  "
                  f"{p_time:7.2f} {s_time:7.2f}")
    return results


def attribute_pooling(model, cases, seed, buckets):
    """How much of the gap is weighting, and how much is discretisation?

    Compares pooled activity durations as written into the XML, not simulated
    output, so each effect is isolated from queueing.
    """
    print(f"\n{'=' * 78}\nWhere the difference comes from\n{'=' * 78}")

    import random
    print(f"\n{'activity':14} {'n':>4} {'unweighted':>11} {'weighted':>11} "
          f"{'effect':>9}")
    weighting_effects = []
    for task in model["task_resource_distribution"]:
        weights = S.resource_weights(task, random.Random(seed))
        unweighted = statistics.mean(
            D.weighted_mixture(task["resources"], None, random.Random(1), 8000))
        weighted = statistics.mean(
            D.weighted_mixture(task["resources"], weights, random.Random(1), 8000))
        effect = relative(weighted, unweighted)
        weighting_effects.append(abs(effect))
        print(f"{task['task_id'][5:15]:14} {len(task['resources']):4} "
              f"{unweighted:11.1f} {weighted:11.1f} {effect:8.1f}%")

    print(f"\n  weighting moves pooled durations by "
          f"{min(weighting_effects):.0f}-{max(weighting_effects):.0f}% "
          f"(median {statistics.median(weighting_effects):.0f}%)")

    # Discretisation: does the bucket count change the pooled mean?
    print(f"\n{'buckets':>8} {'mean pooled duration':>22} {'vs 400 buckets':>16}")
    import random as rnd
    task = max(model["task_resource_distribution"],
               key=lambda t: len(t["resources"]))
    samples = D.weighted_mixture(
        task["resources"], S.resource_weights(task, rnd.Random(seed)),
        rnd.Random(1), 20000)

    def histogram_mean(n_buckets):
        from xml.etree import ElementTree as ET
        el = D.append_histogram(ET.Element("w"), samples, buckets=n_buckets)
        num = den = 0.0
        for entry in el:
            value, freq = float(entry.get("value")), float(entry.get("frequency"))
            num += value * freq
            den += freq
        return num / den

    reference = histogram_mean(400)
    for n_buckets in (10, 20, 50, 100, 200, 400):
        mean = histogram_mean(n_buckets)
        print(f"{n_buckets:8} {mean:22.1f} {relative(mean, reference):15.2f}%")

    families = {}
    for task in model["task_resource_distribution"]:
        for res in task["resources"]:
            families[res["distribution_name"]] = \
                families.get(res["distribution_name"], 0) + 1
    approximated = sum(v for k, v in families.items()
                       if k in D.APPROXIMATED_FAMILIES)
    total = sum(families.values())
    print(f"\n  {approximated}/{total} duration distributions "
          f"({100 * approximated / total:.0f}%) need discretisation: "
          f"{ {k: v for k, v in families.items() if k in D.APPROXIMATED_FAMILIES} }")


def compare_bucket_counts(model, bpmn, cases, seed, bucket_list):
    """Does the bucket count change the simulated result, not just the input?"""
    print(f"\n{'=' * 78}\nBucket count sensitivity ({cases} cases)\n{'=' * 78}")
    prosimos, _ = run_prosimos(model, bpmn, cases, seed)
    print(f"{'buckets':>8} {'cycle_time':>12} {'vs prosimos':>13}")
    for buckets in bucket_list:
        scylla, _ = run_scylla(model, bpmn, cases, seed, buckets, True)
        value = scylla["cycle_time"]["avg"]
        print(f"{buckets:8} {value:12.1f} {relative(value, prosimos['cycle_time']['avg']):12.1f}%")
    print(f"\n  prosimos cycle_time: {prosimos['cycle_time']['avg']:.1f}")


def compare_weighting(model, bpmn, cases, seed, buckets):
    """Weighted against unweighted pooling, end to end."""
    print(f"\n{'=' * 78}\nWeighting, simulated ({cases} cases)\n{'=' * 78}")
    prosimos, _ = run_prosimos(model, bpmn, cases, seed)
    weighted, _ = run_scylla(model, bpmn, cases, seed, buckets, True)
    unweighted, _ = run_scylla(model, bpmn, cases, seed, buckets, False)

    print(f"{'metric':16} {'prosimos':>12} {'weighted':>12} {'unweighted':>12}")
    for metric in METRICS:
        print(f"{metric:16} {prosimos[metric]['avg']:12.1f} "
              f"{weighted[metric]['avg']:12.1f} {unweighted[metric]['avg']:12.1f}")

    p = prosimos["cycle_time"]["avg"]
    print(f"\n  weighted   vs prosimos: {relative(weighted['cycle_time']['avg'], p):+.1f}%")
    print(f"  unweighted vs prosimos: {relative(unweighted['cycle_time']['avg'], p):+.1f}%")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=DATASETS, default="BPIC_2012")
    parser.add_argument("--cases", type=int, nargs="+", default=[100, 500, 1000])
    parser.add_argument("--buckets", type=int, nargs="+",
                        default=[10, 50, 100, 400])
    parser.add_argument("--seed", type=int, default=100)
    args = parser.parse_args()

    model, bpmn = load(args.dataset)
    print(f"dataset: {args.dataset}  "
          f"activities={len(model['task_resource_distribution'])}  "
          f"gateways={len(model['gateway_branching_probabilities'])}")

    compare_engines(model, bpmn, args.cases, args.seed,
                    buckets=D.DEFAULT_BUCKETS, weighted=False)
    attribute_pooling(model, args.cases[-1], args.seed, D.DEFAULT_BUCKETS)
    compare_bucket_counts(model, bpmn, args.cases[-1], args.seed, args.buckets)
    compare_weighting(model, bpmn, args.cases[-1], args.seed, D.DEFAULT_BUCKETS)


if __name__ == "__main__":
    main()
