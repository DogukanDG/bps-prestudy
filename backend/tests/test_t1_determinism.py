"""
T1 -- determinism: the two engines must agree exactly when nothing is random.

Strip out every source of divergence -- fixed durations, deterministic
branching, one always-available resource, a 24/7 calendar -- and cycle time
becomes arithmetic. Both engines must return the same number.

This is the test that separates translation bugs from engine differences. Any
disagreement here is our converter getting something wrong, because there is
nothing left for the engines to disagree about. It would have caught all three
SimuBridge defects immediately.

Needs both a Prosimos install (Python < 3.12) and a built scylla.jar; skips
cleanly without either, so the suite still runs on a machine that has one.
"""

import json
import math
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
START_ISO = "2023-01-01T00:00:00+02:00"

# Everything open, all week. Scylla splits a wrap-around range internally, so
# per-day entries keep the two engines reading the same thing.
WEEK = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY",
        "SATURDAY", "SUNDAY"]
ALWAYS_ON = [
    {"from": day, "to": day, "beginTime": "00:00:00", "endTime": "23:59:59"}
    for day in WEEK
]


def has_prosimos():
    try:
        import prosimos.simulation_engine  # noqa: F401
        return True
    except ImportError:
        return False


def has_jar():
    from src.simulation_pipeline.simulation.scylla.run_scylla import resolve_jar
    try:
        resolve_jar()
        return True
    except FileNotFoundError:
        return False


needs_both = pytest.mark.skipif(
    not (has_prosimos() and has_jar()),
    reason="T1 needs both Prosimos (python<3.12) and a built scylla.jar",
)


def fixed(value):
    return {"distribution_name": "fix", "distribution_params": [{"value": float(value)}]}


def build_degenerate_model(
    source: dict,
    task_duration: float = 60.0,
    arrival_interval: float = 3600.0,
) -> dict:
    """Strip a real model down to something both engines must agree on.

    Keeps the process structure -- same BPMN, same activity and gateway ids --
    but removes every stochastic element:

      - all activity durations fixed
      - one resource per activity, always available
      - fixed inter-arrival time
      - branching forced to 1.0 / 0.0 on the first outgoing path

    Arrivals are slow relative to service time so nothing queues; with no
    contention, cycle time depends only on the path taken.
    """
    model = json.loads(json.dumps(source))

    model["arrival_time_distribution"] = fixed(arrival_interval)
    model["arrival_time_calendar"] = list(ALWAYS_ON)

    # One resource, one calendar, shared by every activity.
    model["resource_calendars"] = [
        {"id": "always", "name": "always", "time_periods": list(ALWAYS_ON)}
    ]
    task_ids = [t["task_id"] for t in model["task_resource_distribution"]]
    model["resource_profiles"] = [{
        "id": "solo_profile",
        "name": "solo_profile",
        "resource_list": [{
            "id": "solo",
            "name": "solo",
            "amount": 1,
            "cost_per_hour": 0,
            "calendar": "always",
            "assignedTasks": task_ids,
        }],
    }]

    for task in model["task_resource_distribution"]:
        task["resources"] = [{"resource_id": "solo", **fixed(task_duration)}]

    # Deterministic routing: first path always taken.
    for gateway in model.get("gateway_branching_probabilities", []):
        for index, branch in enumerate(gateway["probabilities"]):
            branch["value"] = 1.0 if index == 0 else 0.0

    return model


@pytest.fixture(scope="module")
def degenerate():
    path = REPO / "example_sensitivity_analysis_inputs" / "BPIC_2012" / "BPIC_2012_train.json"
    if not path.exists():
        pytest.skip("BPIC 2012 model not available")
    return build_degenerate_model(json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def bpmn():
    return REPO / "example_sensitivity_analysis_inputs" / "BPIC_2012" / "BPIC_2012_train.bpmn"


# --------------------------------------------------------------------------
# The degenerate model itself
# --------------------------------------------------------------------------

def test_degenerate_model_has_no_randomness_left(degenerate):
    """If this fails the comparison below proves nothing."""
    for task in degenerate["task_resource_distribution"]:
        assert len(task["resources"]) == 1
        assert task["resources"][0]["distribution_name"] == "fix"

    assert degenerate["arrival_time_distribution"]["distribution_name"] == "fix"

    for gateway in degenerate["gateway_branching_probabilities"]:
        values = sorted(b["value"] for b in gateway["probabilities"])
        assert values[-1] == 1.0
        assert all(v == 0.0 for v in values[:-1])


def test_degenerate_model_keeps_the_process_structure(degenerate):
    source = json.loads(
        (REPO / "example_sensitivity_analysis_inputs" / "BPIC_2012"
         / "BPIC_2012_train.json").read_text(encoding="utf-8"))
    assert ({t["task_id"] for t in degenerate["task_resource_distribution"]}
            == {t["task_id"] for t in source["task_resource_distribution"]})
    assert ({g["gateway_id"] for g in degenerate["gateway_branching_probabilities"]}
            == {g["gateway_id"] for g in source["gateway_branching_probabilities"]})


def test_degenerate_model_converts_without_approximation(degenerate, bpmn):
    """Every duration is `fix`, so nothing should be discretised -- a histogram
    here would mean the pooling path ran when it should not have."""
    from src.simulation_pipeline.simulation.scylla import build_sim_config as S
    from src.simulation_pipeline.simulation.scylla.distributions import BSIM

    root = S.build_sim_config(degenerate, bpmn, total_cases=10,
                              start_iso=START_ISO, seed=1)
    sim = root.find(f"{{{BSIM}}}simulationConfiguration")
    for task in sim.findall(f"{{{BSIM}}}task"):
        duration = task.find(f"{{{BSIM}}}duration")
        assert duration[0].tag == f"{{{BSIM}}}constantDistribution", \
            f"{task.get('id')} was approximated"


# --------------------------------------------------------------------------
# The comparison
# --------------------------------------------------------------------------

def run_prosimos(model, bpmn_path, cases):
    from src.simulation_pipeline.simulation.simulate_samples import simulate_sample
    result = simulate_sample(0, model, str(bpmn_path), cases, START_ISO)
    assert result["error"] is None, result["error"]
    return {r["metric"]: r for r in result["process_rows"]}


def run_scylla(model, bpmn_path, cases):
    from src.simulation_pipeline.simulation.scylla.run_scylla import (
        resolve_jar, simulate_sample_scylla)
    result = simulate_sample_scylla(
        sample_id=0, sample_data=model, bpmn_path=bpmn_path,
        total_cases=cases, start_iso=START_ISO, jar_path=resolve_jar(), seed=1,
    )
    assert result["error"] is None, result["error"]
    return {r["metric"]: r for r in result["process_rows"]}


@needs_both
@pytest.mark.parametrize("cases", [50, 200])
def test_both_engines_simulate_the_requested_cases(degenerate, bpmn, cases):
    assert run_prosimos(degenerate, bpmn, cases)["cycle_time"]["count"] == cases
    assert run_scylla(degenerate, bpmn, cases)["cycle_time"]["count"] == cases


@needs_both
def test_cycle_time_agrees_between_engines(degenerate, bpmn):
    """The core of T1.

    With fixed durations, deterministic routing and no contention, every case
    follows the same path and takes the same time. Both engines must report the
    same cycle time; a mismatch is a translation bug.
    """
    cases = 200
    prosimos = run_prosimos(degenerate, bpmn, cases)
    scylla = run_scylla(degenerate, bpmn, cases)

    p_avg = prosimos["cycle_time"]["avg"]
    s_avg = scylla["cycle_time"]["avg"]
    assert s_avg == pytest.approx(p_avg, rel=0.01), (
        f"cycle_time differs: prosimos={p_avg:.1f}s scylla={s_avg:.1f}s "
        f"(ratio {s_avg / p_avg if p_avg else float('nan'):.3f})"
    )


@needs_both
def test_processing_time_agrees_between_engines(degenerate, bpmn):
    """Sum of fixed activity durations along one path -- pure arithmetic."""
    cases = 200
    p_avg = run_prosimos(degenerate, bpmn, cases)["processing_time"]["avg"]
    s_avg = run_scylla(degenerate, bpmn, cases)["processing_time"]["avg"]
    assert s_avg == pytest.approx(p_avg, rel=0.01), (
        f"processing_time differs: prosimos={p_avg:.1f}s scylla={s_avg:.1f}s"
    )


@pytest.mark.skipif(not has_prosimos(), reason="needs Prosimos")
def test_reported_waiting_is_not_contention(degenerate, bpmn):
    """Prosimos reports 60 s of waiting here even though nothing queues.

    Spacing arrivals ten times further apart leaves it unchanged, so it is
    structural -- an enable-to-start gap counted per activity -- not queueing.
    Worth pinning: it means a non-zero waiting_time in the degenerate model is
    expected, and the two engines' waiting figures are not comparable even
    under T1 conditions (see the module README on the definitional gap).
    """
    tight = run_prosimos(build_degenerate_model(
        json.loads((REPO / "example_sensitivity_analysis_inputs" / "BPIC_2012"
                    / "BPIC_2012_train.json").read_text(encoding="utf-8")),
        arrival_interval=3600.0), bpmn, 100)
    sparse = run_prosimos(build_degenerate_model(
        json.loads((REPO / "example_sensitivity_analysis_inputs" / "BPIC_2012"
                    / "BPIC_2012_train.json").read_text(encoding="utf-8")),
        arrival_interval=36000.0), bpmn, 100)

    assert tight["waiting_time"]["avg"] == pytest.approx(
        sparse["waiting_time"]["avg"], rel=0.01)
    # And cycle time is unaffected by arrival spacing -- no contention.
    assert tight["cycle_time"]["avg"] == pytest.approx(
        sparse["cycle_time"]["avg"], rel=0.01)


@needs_both
def test_scylla_is_reproducible_across_runs(degenerate, bpmn):
    """A fixed seed must give identical output; a sensitivity analysis cannot
    use an engine that drifts between runs."""
    a = run_scylla(degenerate, bpmn, 100)
    b = run_scylla(degenerate, bpmn, 100)
    assert a["cycle_time"]["avg"] == b["cycle_time"]["avg"]
    assert a["cycle_time"]["total"] == b["cycle_time"]["total"]


@needs_both
def test_all_cases_are_identical_within_a_run(degenerate, bpmn):
    """Deterministic model, so min == max: every case takes the same time.
    This is what proves the routing and durations really are fixed."""
    for name, metrics in (("prosimos", run_prosimos(degenerate, bpmn, 100)),
                          ("scylla", run_scylla(degenerate, bpmn, 100))):
        cycle = metrics["cycle_time"]
        spread = cycle["max"] - cycle["min"]
        assert spread == pytest.approx(0.0, abs=max(cycle["avg"] * 0.02, 1.0)), \
            f"{name}: cycle time varies between cases ({cycle['min']}..{cycle['max']})"
