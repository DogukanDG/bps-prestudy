"""
T4 -- calendars and capacity: locate what actually makes the engines diverge.

T1 shows the two engines agree exactly when nothing is random. On a real model
they do not, and the residual has to be attributed to something before the
comparison study can say anything honest about it.

The method is one controlled variable at a time, all durations fixed so that any
difference is structural rather than sampling noise:

    A  degenerate baseline (T1 conditions)          engines agree
    B  + real resources and their real calendars    engines diverge
    C  real resources, all on a 24/7 calendar       engines agree
    D  one resource, on a real narrow calendar      engines agree
    E  all resources, one shared narrow calendar,   engines agree
       every activity eligible for every resource

C and D isolate capacity and calendars separately; both agree. E adds them
together and still agrees. The only thing left in B that E does not have is
**eligibility** -- which resources may perform which activity -- and that is
exactly what the shared pool gives up.

So the divergence is not calendar semantics, which was the standing hypothesis.
It is that Scylla lets every activity draw on all 47 resources while Prosimos
restricts each to its own subset, as little as 4% of them.

Needs Prosimos and a built scylla.jar; skips cleanly without either.
"""

import json
from pathlib import Path

import pytest

from test_t1_determinism import (ALWAYS_ON, build_degenerate_model, fixed,
                                 has_jar, has_prosimos, run_prosimos, run_scylla)

REPO = Path(__file__).resolve().parents[2]
MODEL_DIR = REPO / "example_sensitivity_analysis_inputs" / "BPIC_2012"

CASES = 200
TASK_DURATION = 600.0
ARRIVAL_INTERVAL = 1800.0     # busy enough to queue, so capacity matters

WEEKDAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]
OFFICE_HOURS = [
    {"from": d, "to": d, "beginTime": "09:00:00", "endTime": "17:00:00"}
    for d in WEEKDAYS
]

needs_both = pytest.mark.skipif(
    not (has_prosimos() and has_jar()),
    reason="T4 needs both Prosimos (python<3.12) and a built scylla.jar",
)


@pytest.fixture(scope="module")
def source():
    path = MODEL_DIR / "BPIC_2012_train.json"
    if not path.exists():
        pytest.skip("BPIC 2012 model not available")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bpmn():
    return MODEL_DIR / "BPIC_2012_train.bpmn"


def baseline(source):
    return build_degenerate_model(source, task_duration=TASK_DURATION,
                                  arrival_interval=ARRIVAL_INTERVAL)


def all_resource_ids(source):
    return [r["id"] for p in source["resource_profiles"] for r in p["resource_list"]]


def ratio(model, bpmn):
    """Scylla cycle time / Prosimos cycle time."""
    prosimos = run_prosimos(model, bpmn, CASES)["cycle_time"]["avg"]
    scylla = run_scylla(model, bpmn, CASES)["cycle_time"]["avg"]
    assert prosimos > 0
    return scylla / prosimos


# --------------------------------------------------------------------------
# The controlled sequence
# --------------------------------------------------------------------------

@needs_both
def test_a_baseline_agrees(source, bpmn):
    """T1 conditions, restated here so the sequence starts from a known point."""
    assert ratio(baseline(source), bpmn) == pytest.approx(1.0, rel=0.02)


@needs_both
def test_b_real_resources_and_calendars_diverge(source, bpmn):
    """Everything real except the durations. This is where the gap appears."""
    model = baseline(source)
    model["resource_calendars"] = json.loads(json.dumps(source["resource_calendars"]))
    model["resource_profiles"] = json.loads(json.dumps(source["resource_profiles"]))
    by_task = {t["task_id"]: t for t in source["task_resource_distribution"]}
    for task in model["task_resource_distribution"]:
        task["resources"] = [
            {"resource_id": r["resource_id"], **fixed(TASK_DURATION)}
            for r in by_task[task["task_id"]]["resources"]
        ]

    # Scylla finishes materially sooner; measured ~0.53.
    assert ratio(model, bpmn) < 0.8


@needs_both
def test_c_capacity_alone_agrees(source, bpmn):
    """All 47 resources, but every one always available.

    Rules out capacity per se: pooling the resources is not what breaks it.
    """
    model = baseline(source)
    model["resource_calendars"] = [
        {"id": "always", "name": "always", "time_periods": list(ALWAYS_ON)}
    ]
    model["resource_profiles"] = json.loads(json.dumps(source["resource_profiles"]))
    for profile in model["resource_profiles"]:
        for res in profile["resource_list"]:
            res["calendar"] = "always"
    by_task = {t["task_id"]: t for t in source["task_resource_distribution"]}
    for task in model["task_resource_distribution"]:
        task["resources"] = [
            {"resource_id": r["resource_id"], **fixed(TASK_DURATION)}
            for r in by_task[task["task_id"]]["resources"]
        ]

    assert ratio(model, bpmn) == pytest.approx(1.0, rel=0.02)


@needs_both
def test_d_calendars_alone_agree(source, bpmn):
    """One resource on a real, narrow calendar.

    Rules out calendar semantics: off-shift time is handled the same way by
    both engines, including the wrap-around and multi-period cases in the real
    calendars. This was the standing hypothesis for the divergence, and it is
    wrong.
    """
    model = baseline(source)
    model["resource_calendars"] = json.loads(json.dumps(source["resource_calendars"]))
    model["resource_profiles"][0]["resource_list"][0]["calendar"] = \
        source["resource_calendars"][0]["id"]

    assert ratio(model, bpmn) == pytest.approx(1.0, rel=0.02)


@needs_both
def test_e_capacity_and_calendars_together_agree(source, bpmn):
    """All 47 resources on one shared narrow calendar, every activity eligible
    for every resource.

    Capacity and calendars combined, with eligibility removed. The engines
    agree, so their interaction is not the cause either -- which leaves
    eligibility as the only remaining difference from B.
    """
    calendar = next(c for c in source["resource_calendars"]
                    if c["id"] == "Undifferentiated_calendar")
    ids = all_resource_ids(source)
    task_ids = [t["task_id"] for t in source["task_resource_distribution"]]

    model = baseline(source)
    model["resource_calendars"] = [json.loads(json.dumps(calendar))]
    model["resource_profiles"] = [{
        "id": "P", "name": "P",
        "resource_list": [{
            "id": rid, "name": rid, "amount": 1, "cost_per_hour": 0,
            "calendar": calendar["id"], "assignedTasks": task_ids,
        } for rid in ids],
    }]
    for task in model["task_resource_distribution"]:
        task["resources"] = [{"resource_id": rid, **fixed(TASK_DURATION)}
                             for rid in ids]

    assert ratio(model, bpmn) == pytest.approx(1.0, rel=0.05)


# --------------------------------------------------------------------------
# What the sequence points at
# --------------------------------------------------------------------------

def test_eligibility_is_genuinely_restrictive(source):
    """The shared pool lets every activity use every resource. Prosimos does
    not, and the restriction is severe enough to matter -- one BPIC 2012
    activity can use 2 of 47 resources.

    Pure bookkeeping, so it runs without either engine.
    """
    total = len(all_resource_ids(source))
    shares = [len(t["resources"]) / total
              for t in source["task_resource_distribution"]]

    assert min(shares) < 0.10, "no activity is meaningfully restricted"
    assert max(shares) < 1.0, "every activity can already use every resource"


def test_calendars_are_nearly_homogeneous(source):
    """Rules out a second explanation for B: it is not that resources have
    wildly different calendars. 45 of 47 share one.
    """
    from src.simulation_pipeline.simulation.scylla.build_global_config import (
        resource_calendar_map)

    counts = {}
    for calendar in resource_calendar_map(source).values():
        counts[calendar] = counts.get(calendar, 0) + 1

    dominant = max(counts.values())
    assert dominant / sum(counts.values()) > 0.9
