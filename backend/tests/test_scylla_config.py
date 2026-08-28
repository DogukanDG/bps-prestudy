"""
Tests for the Simod -> Scylla configuration builders.

These run against the real BPIC 2012 and 2017 models rather than toy inputs,
because the failure mode that matters is structural: Scylla logs and skips XML
it does not recognise, so a converter that drops half the model still produces
a simulation that runs and reports plausible numbers.
"""

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from src.simulation_pipeline.simulation.scylla import build_global_config as G
from src.simulation_pipeline.simulation.scylla import build_sim_config as S
from src.simulation_pipeline.simulation.scylla.distributions import BSIM

REPO = Path(__file__).resolve().parents[2]
INPUTS = REPO / "example_sensitivity_analysis_inputs"

DATASETS = ["BPIC_2012", "BPIC_2017"]
START_ISO = "2023-01-01T00:00:00+02:00"


def q(tag):
    return f"{{{BSIM}}}{tag}"


@pytest.fixture(params=DATASETS)
def dataset(request):
    name = request.param
    json_path = INPUTS / name / f"{name}_train.json"
    bpmn_path = INPUTS / name / f"{name}_train.bpmn"
    if not json_path.exists():
        pytest.skip(f"{name} model not available")
    return {
        "name": name,
        "model": json.loads(json_path.read_text(encoding="utf-8")),
        "bpmn_path": bpmn_path,
    }


@pytest.fixture
def global_root(dataset):
    return G.build_global_config(dataset["model"], seed=100)


@pytest.fixture
def sim_root(dataset):
    return S.build_sim_config(
        dataset["model"], dataset["bpmn_path"],
        total_cases=1000, start_iso=START_ISO, seed=100,
        # Small draw counts: these tests check structure, not fidelity.
        buckets=20, n_draws=500,
    )


# --------------------------------------------------------------------------
# Global config
# --------------------------------------------------------------------------

def test_seed_and_zone_are_written(global_root):
    """Without a seed Scylla draws its own and results are irreproducible."""
    assert global_root.findtext(q("randomSeed")) == "100"
    assert global_root.findtext(q("zoneOffset")) == "+02:00"


def test_one_pool_per_activity(dataset, global_root):
    pools = [el.get("id") for el in global_root.iter(q("dynamicResource"))]
    expected = [G.pool_id_for(t["task_id"])
                for t in dataset["model"]["task_resource_distribution"]]
    assert sorted(pools) == sorted(expected)


def test_pool_quantity_matches_member_count(dataset, global_root):
    """defaultQuantity is what makes the pool behave as N interchangeable
    resources rather than one."""
    by_task = {t["task_id"]: len(t["resources"])
               for t in dataset["model"]["task_resource_distribution"]}
    for el in global_root.iter(q("dynamicResource")):
        task_id = el.get("id")[len("pool_"):]
        assert int(el.get("defaultQuantity")) == by_task[task_id]


def test_every_resource_becomes_an_instance(dataset, global_root):
    for el in global_root.iter(q("dynamicResource")):
        assert len(el.findall(q("instance"))) == int(el.get("defaultQuantity"))


def test_instances_keep_their_own_calendars(dataset, global_root):
    """The point of pooling this way: per-resource calendars survive, which is
    what keeps is_resource_calendars meaningful on the Scylla side."""
    res_to_cal = G.resource_calendar_map(dataset["model"])
    declared = {c["id"] for c in dataset["model"]["resource_calendars"]}

    checked = 0
    for el in global_root.iter(q("dynamicResource")):
        pool = el.get("id")
        for inst in el.findall(q("instance")):
            rid = inst.get("name")[len(pool) + 2:]
            expected = res_to_cal.get(rid)
            if expected in declared:
                assert inst.get("timetableId") == expected
                checked += 1
    assert checked > 0


def test_all_calendars_emitted_with_full_precision(dataset, global_root):
    """Times pass through unrounded -- HH:MM:SS, not whole hours."""
    model_cals = {c["id"]: c for c in dataset["model"]["resource_calendars"]}
    emitted = {tt.get("id"): tt for tt in global_root.iter(q("timetable"))}
    assert set(emitted) == set(model_cals)

    for cal_id, cal in model_cals.items():
        items = emitted[cal_id].findall(q("timetableItem"))
        assert len(items) == len(cal["time_periods"])
        for item, period in zip(items, cal["time_periods"]):
            assert item.get("beginTime") == period["beginTime"]
            assert item.get("endTime") == period["endTime"]
            assert item.get("from") == period["from"]


def test_global_config_validates(dataset, global_root):
    G.validate_global_config(global_root, dataset["model"])


def test_validation_catches_a_dropped_pool(dataset, global_root):
    victim = next(global_root.iter(q("dynamicResource")))
    global_root.find(q("resourceData")).remove(victim)
    with pytest.raises(ValueError, match="missing"):
        G.validate_global_config(global_root, dataset["model"])


def test_validation_catches_a_dangling_timetable_reference(dataset, global_root):
    inst = next(global_root.iter(q("instance")))
    inst.set("timetableId", "no_such_calendar")
    with pytest.raises(ValueError, match="undeclared"):
        G.validate_global_config(global_root, dataset["model"])


# --------------------------------------------------------------------------
# Simulation config
# --------------------------------------------------------------------------

def test_process_ref_matches_bpmn(dataset, sim_root):
    bpmn = S.read_bpmn(dataset["bpmn_path"])
    sim = sim_root.find(q("simulationConfiguration"))
    assert sim.get("processRef") == bpmn["process_id"]


def test_case_count_is_not_clamped(dataset):
    """SimuBridge caps processInstances at 5000; the SA runs need the real
    number, and the ceiling is measured rather than assumed."""
    root = S.build_sim_config(
        dataset["model"], dataset["bpmn_path"],
        total_cases=7000, start_iso=START_ISO, seed=1, buckets=10, n_draws=200,
    )
    sim = root.find(q("simulationConfiguration"))
    assert sim.get("processInstances") == "7000"


def test_every_activity_is_written(dataset, sim_root):
    sim = sim_root.find(q("simulationConfiguration"))
    written = {el.get("id") for el in sim.findall(q("task"))}
    expected = {t["task_id"] for t in dataset["model"]["task_resource_distribution"]}
    assert written == expected


def test_every_activity_has_a_duration_with_a_time_unit(dataset, sim_root):
    """A missing timeUnit is a NullPointerException inside Scylla, not a
    readable error."""
    sim = sim_root.find(q("simulationConfiguration"))
    for el in sim.findall(q("task")):
        duration = el.find(q("duration"))
        assert duration is not None
        assert duration.get("timeUnit") == "SECONDS"
        assert len(duration) == 1


def test_activities_reference_their_own_pool(dataset, sim_root):
    sim = sim_root.find(q("simulationConfiguration"))
    for el in sim.findall(q("task")):
        ref = el.find(q("resources")).find(q("resource"))
        assert ref.get("id") == G.pool_id_for(el.get("id"))
        assert ref.get("amount") == "1"


def test_exclusive_gateways_are_written_with_their_branches(dataset, sim_root):
    sim = sim_root.find(q("simulationConfiguration"))
    bpmn = S.read_bpmn(dataset["bpmn_path"])
    written = {el.get("id") for el in sim.findall(q("exclusiveGateway"))}

    expected = {
        g["gateway_id"] for g in dataset["model"]["gateway_branching_probabilities"]
        if bpmn["gateway_types"].get(g["gateway_id"], "exclusiveGateway")
        == "exclusiveGateway"
    }
    assert written == expected

    by_id = {g["gateway_id"]: g
             for g in dataset["model"]["gateway_branching_probabilities"]}
    for el in sim.findall(q("exclusiveGateway")):
        flows = el.findall(q("outgoingSequenceFlow"))
        assert len(flows) == len(by_id[el.get("id")]["probabilities"])


def test_parallel_gateways_get_no_probabilities(dataset, sim_root):
    """Scylla takes branching probabilities only for exclusive/inclusive
    gateways; a parallel gateway with them would be wrong."""
    sim = sim_root.find(q("simulationConfiguration"))
    assert sim.findall(q("parallelGateway")) == []


def test_start_event_carries_an_arrival_rate(dataset, sim_root):
    """Mandatory: without it the Scylla parser throws."""
    sim = sim_root.find(q("simulationConfiguration"))
    starts = sim.findall(q("startEvent"))
    assert len(starts) >= 1
    rate = starts[0].find(q("arrivalRate"))
    assert rate is not None
    assert rate.get("timeUnit") == "SECONDS"
    assert len(rate) == 1


def test_sim_config_validates(dataset, sim_root):
    S.validate_sim_config(sim_root, dataset["model"], S.read_bpmn(dataset["bpmn_path"]))


def test_validation_catches_a_dropped_activity(dataset, sim_root):
    sim = sim_root.find(q("simulationConfiguration"))
    sim.remove(sim.find(q("task")))
    with pytest.raises(ValueError, match="task mismatch"):
        S.validate_sim_config(sim_root, dataset["model"],
                              S.read_bpmn(dataset["bpmn_path"]))


def test_validation_catches_a_wrong_process_ref(dataset, sim_root):
    sim_root.find(q("simulationConfiguration")).set("processRef", "wrong")
    with pytest.raises(ValueError, match="processRef"):
        S.validate_sim_config(sim_root, dataset["model"],
                              S.read_bpmn(dataset["bpmn_path"]))


# --------------------------------------------------------------------------
# Weighting
# --------------------------------------------------------------------------

def test_weights_are_inverse_to_duration(dataset):
    """Fast resources take on more work, so they weigh more."""
    import random
    task = max(dataset["model"]["task_resource_distribution"],
               key=lambda t: len(t["resources"]))
    weights = S.resource_weights(task, random.Random(0))
    means = [r["distribution_params"][0]["value"] for r in task["resources"]]

    pairs = [(m, w) for m, w in zip(means, weights) if m > 0]
    if len(pairs) >= 2:
        slowest = max(pairs, key=lambda p: p[0])
        fastest = min(pairs, key=lambda p: p[0])
        assert fastest[1] > slowest[1]


def test_weighting_changes_the_pooled_duration(dataset):
    """If weighting made no difference there would be no reason to do it --
    and the spike's unweighted output would already have been correct."""
    kw = dict(total_cases=100, start_iso=START_ISO, seed=100,
              buckets=50, n_draws=4000)
    weighted = S.build_sim_config(dataset["model"], dataset["bpmn_path"],
                                  weighted=True, **kw)
    unweighted = S.build_sim_config(dataset["model"], dataset["bpmn_path"],
                                    weighted=False, **kw)

    def pooled_means(root):
        out = {}
        for el in root.find(q("simulationConfiguration")).findall(q("task")):
            hist = el.find(q("duration")).find(
                q("arbitraryFiniteProbabilityDistribution"))
            if hist is None:
                continue
            num = den = 0.0
            for e in hist:
                v, f = float(e.get("value")), float(e.get("frequency"))
                num += v * f
                den += f
            out[el.get("id")] = num / den
        return out

    w, u = pooled_means(weighted), pooled_means(unweighted)
    shared = set(w) & set(u)
    assert shared, "no pooled activities to compare"
    assert any(abs(w[k] - u[k]) / max(u[k], 1e-9) > 0.01 for k in shared)


def test_weighted_pool_is_not_slower_than_unweighted(dataset):
    """Load weighting favours fast resources, so the pooled mean should move
    down, not up."""
    kw = dict(total_cases=100, start_iso=START_ISO, seed=100,
              buckets=50, n_draws=4000)
    import random
    rng = random.Random(0)
    from src.simulation_pipeline.simulation.scylla import distributions as D

    task = max(dataset["model"]["task_resource_distribution"],
               key=lambda t: len(t["resources"]))
    weights = S.resource_weights(task, rng)

    import statistics
    weighted = statistics.mean(
        D.weighted_mixture(task["resources"], weights, random.Random(1), 8000))
    unweighted = statistics.mean(
        D.weighted_mixture(task["resources"], None, random.Random(1), 8000))
    assert weighted <= unweighted * 1.05


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------

def test_same_seed_gives_identical_xml(dataset):
    kw = dict(total_cases=500, start_iso=START_ISO, buckets=30, n_draws=1000)
    a = S.build_sim_config(dataset["model"], dataset["bpmn_path"], seed=42, **kw)
    b = S.build_sim_config(dataset["model"], dataset["bpmn_path"], seed=42, **kw)
    assert ET.tostring(a) == ET.tostring(b)


def test_different_seed_gives_different_xml(dataset):
    kw = dict(total_cases=500, start_iso=START_ISO, buckets=30, n_draws=1000)
    a = S.build_sim_config(dataset["model"], dataset["bpmn_path"], seed=1, **kw)
    b = S.build_sim_config(dataset["model"], dataset["bpmn_path"], seed=2, **kw)
    assert ET.tostring(a) != ET.tostring(b)


def test_no_single_resource_dominates_a_large_pool(dataset):
    """A resource is still serial no matter how fast it is.

    Raw 1/mean weighting lets one very fast resource stand in for the whole
    pool -- in BPIC 2012 a 1.1 s resource next to a 4141 s one took 65% of the
    weight. The cap keeps fast resources dominant without that.
    """
    import random
    for task in dataset["model"]["task_resource_distribution"]:
        n = len(task["resources"])
        if n < 10:
            continue
        weights = S.resource_weights(task, random.Random(0))
        assert max(weights) / sum(weights) < 0.25, task["task_id"]


def test_weight_ratio_is_capped(dataset):
    import random
    for task in dataset["model"]["task_resource_distribution"]:
        weights = S.resource_weights(task, random.Random(0))
        assert max(weights) / min(weights) <= S.MAX_WEIGHT_RATIO + 1e-9


def test_zero_duration_resource_does_not_blow_up_the_weights():
    """Simod emits fix-0.0 durations; they must not become infinite weight."""
    import random
    task = {"task_id": "t", "resources": [
        {"distribution_name": "fix", "distribution_params": [{"value": 0.0}]},
        {"distribution_name": "fix", "distribution_params": [{"value": 100.0}]},
    ]}
    weights = S.resource_weights(task, random.Random(0))
    assert all(w > 0 and w < float("inf") for w in weights)
    assert max(weights) / min(weights) <= S.MAX_WEIGHT_RATIO + 1e-9
