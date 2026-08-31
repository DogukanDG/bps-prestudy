"""
T5 -- attribute the remaining gap between the engines on a real model.

T1 and T4 work on degenerate models where the engines must agree. This one runs
the real BPIC models and asks where the difference actually comes from, by
moving one thing at a time between the two arms.

The decomposition, on BPIC 2012 (averaged over repeats -- a queueing model near
saturation swings 22-80% between single runs):

    Prosimos, real model                     ~5800 s
    Prosimos, given Scylla's pooled model     ~7800 s   (+34%)
    Scylla                                   ~18100 s   (+130% on top)

So the translation we control -- pooling durations and dropping eligibility --
accounts for roughly a quarter of the gap, and is stable across case counts
(35% at 300 cases, 33% at 1000). The rest is the arrival calendar:
Prosimos honours it, Scylla has no equivalent, so the same number of cases
arrives spread over the whole week instead of packed into the 45% of it the
calendar covers. Replacing the arrival calendar with 24/7 on the Prosimos side
takes the ratio from 2.70 to 1.14.

That is the honest headline for the comparison study, and it was already known
as a scope limitation (`is_arrival_calendar` has no Scylla representation) --
what was not known is that it dominates everything else.

Needs Prosimos and a built scylla.jar; skips cleanly without either.
"""

import json
import random
import statistics
from pathlib import Path

import pytest

from src.simulation_pipeline.simulation.scylla import distributions as D
from test_t1_determinism import ALWAYS_ON, has_jar, has_prosimos, run_prosimos, run_scylla

REPO = Path(__file__).resolve().parents[2]
INPUTS = REPO / "example_sensitivity_analysis_inputs"

CASES = 300
SEED = 100

needs_both = pytest.mark.skipif(
    not (has_prosimos() and has_jar()),
    reason="T5 needs both Prosimos (python<3.12) and a built scylla.jar",
)


@pytest.fixture(scope="module")
def model():
    path = INPUTS / "BPIC_2012" / "BPIC_2012_train.json"
    if not path.exists():
        pytest.skip("BPIC 2012 model not available")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bpmn():
    return INPUTS / "BPIC_2012" / "BPIC_2012_train.bpmn"


def as_scylla_sees_it(source: dict, arrival_247: bool = False) -> dict:
    """The source model rewritten the way the Scylla converter transforms it.

    Two changes, both forced by Scylla's model rather than chosen:
      - each activity gets one pooled duration instead of one per resource
      - every resource becomes eligible for every activity (the shared pool)

    Running *this* through Prosimos isolates what the translation costs, with
    the engine held constant.
    """
    result = json.loads(json.dumps(source))
    resource_ids = [r["id"] for p in source["resource_profiles"]
                    for r in p["resource_list"]]
    task_ids = [t["task_id"] for t in source["task_resource_distribution"]]
    rng = random.Random(1)

    by_task = {t["task_id"]: t for t in source["task_resource_distribution"]}
    for task in result["task_resource_distribution"]:
        pooled = D.weighted_mixture(by_task[task["task_id"]]["resources"],
                                    None, rng, 20_000)
        mean = statistics.mean(pooled)
        task["resources"] = [
            {"resource_id": rid, "distribution_name": "fix",
             "distribution_params": [{"value": mean}]}
            for rid in resource_ids
        ]

    for profile in result["resource_profiles"]:
        for resource in profile["resource_list"]:
            resource["assignedTasks"] = task_ids

    if arrival_247:
        result["arrival_time_calendar"] = list(ALWAYS_ON)

    return result


def cycle_time(metrics):
    return metrics["cycle_time"]["avg"]


# --------------------------------------------------------------------------
# The decomposition
# --------------------------------------------------------------------------

@needs_both
def test_translation_accounts_for_only_part_of_the_gap(model, bpmn):
    """Pooling and eligibility, measured with the engine held constant.

    Both are forced by Scylla's model, so this is the part of the gap that
    would remain even with a perfect adapter.

    Averaged over repeats: a single pair of runs puts the figure anywhere
    between 22% and 80%, because a queueing model near saturation has high
    run-to-run variance. Averaged, it settles around 34% and is stable across
    case counts (measured 35% at 300 cases, 33% at 1000).
    """
    repeats = 3
    real = statistics.mean(
        cycle_time(run_prosimos(model, bpmn, CASES)) for _ in range(repeats))
    translated = statistics.mean(
        cycle_time(run_prosimos(as_scylla_sees_it(model), bpmn, CASES))
        for _ in range(repeats))

    inflation = (translated - real) / real
    assert 0.10 < inflation < 0.80, f"translation inflation {inflation:.0%}"


@needs_both
def test_arrival_calendar_dominates_the_remainder(model, bpmn):
    """The engines nearly agree once the arrival calendar is neutralised.

    Prosimos honours `arrival_time_calendar`; Scylla has no equivalent, so the
    same cases arrive spread over the whole week rather than packed into the
    45% of it the calendar covers. This is the single largest source of
    divergence -- larger than pooling, eligibility and discretisation together.
    """
    translated = as_scylla_sees_it(model)
    with_calendar = cycle_time(run_prosimos(translated, bpmn, CASES))
    without_calendar = cycle_time(
        run_prosimos(as_scylla_sees_it(model, arrival_247=True), bpmn, CASES))
    scylla = cycle_time(run_scylla(translated, bpmn, CASES))

    ratio_with = scylla / with_calendar
    ratio_without = scylla / without_calendar

    # Neutralising it must close most of the gap.
    assert ratio_without < 1.4, f"ratio without arrival calendar {ratio_without:.2f}"
    assert ratio_without < ratio_with / 1.5, (
        f"arrival calendar explains too little: {ratio_with:.2f} -> "
        f"{ratio_without:.2f}"
    )


@needs_both
def test_engines_agree_closely_once_every_known_difference_is_removed(model, bpmn):
    """Same model, same durations, no arrival calendar: within ~40%.

    Not exact -- the metric definitions still differ (see the adapter README) --
    but far from the 2.7x on the untouched model. What is left here is what a
    perfect converter could not fix.
    """
    neutral = as_scylla_sees_it(model, arrival_247=True)
    prosimos = cycle_time(run_prosimos(neutral, bpmn, CASES))
    scylla = cycle_time(run_scylla(neutral, bpmn, CASES))
    assert 0.7 < scylla / prosimos < 1.4


# --------------------------------------------------------------------------
# Facts the decomposition rests on
# --------------------------------------------------------------------------

@pytest.mark.parametrize("dataset", ["BPIC_2012", "BPIC_2017"])
def test_arrival_calendars_cover_only_part_of_the_week(dataset):
    """Why the arrival calendar matters so much: it roughly doubles arrival
    density relative to Scylla spreading the same cases over 168 hours."""
    path = INPUTS / dataset / f"{dataset}_train.json"
    if not path.exists():
        pytest.skip(f"{dataset} not available")
    model = json.loads(path.read_text(encoding="utf-8"))

    hours = 0.0
    for period in model["arrival_time_calendar"]:
        begin = [int(x) for x in period["beginTime"].split(":")]
        end = [int(x) for x in period["endTime"].split(":")]
        hours += ((end[0] * 3600 + end[1] * 60 + end[2])
                  - (begin[0] * 3600 + begin[1] * 60 + begin[2])) / 3600

    coverage = hours / 168
    assert 0.3 < coverage < 0.7, f"{dataset} coverage {coverage:.0%}"


@pytest.mark.parametrize("dataset,expected_parallel", [
    ("BPIC_2012", 2),
    ("BPIC_2017", 0),
])
def test_concurrency_differs_between_the_datasets(dataset, expected_parallel):
    """The processing_time definition gap only bites where activities overlap.

    BPIC 2017 has no parallel gateways at all, so `sum of durations` and
    `wall-clock busy time` coincide there and the definitional difference is
    inert. BPIC 2012 has two. Worth pinning, because it means the two datasets
    are not interchangeable when reporting that metric -- and Production, which
    has 35, would be affected far more if it is ever added.
    """
    path = INPUTS / dataset / f"{dataset}_train.bpmn"
    if not path.exists():
        pytest.skip(f"{dataset} not available")
    text = path.read_text(encoding="utf-8")
    assert text.count("<parallelGateway") == expected_parallel
