"""
Tests for the Scylla resource-duration plugin.

Scylla gives an activity a single duration regardless of who performs it. Simod
discovers one distribution per resource per activity -- up to 42 on BPIC 2012,
fastest to slowest over a hundredfold apart -- so the converter had been pooling
them into one. The plugin restores the per-resource distributions.

It needed a small core change as well: getDistributionSample() took only a node
id, and the TaskBeginEvent plugin hook fires after the duration is already
sampled. TaskBeginEvent now passes the assigned resources to a new overload,
which falls back to the node-level distribution when no plugin registered any.

Needs a scylla.jar built from a tree containing the plugin; skips without one.
"""

import collections
import datetime as dt
import json
import statistics
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from src.simulation_pipeline.simulation.scylla import build_sim_config as S
from src.simulation_pipeline.simulation.scylla.distributions import BSIM
from test_t1_determinism import has_jar, has_prosimos

REPO = Path(__file__).resolve().parents[2]
MODEL_DIR = REPO / "example_sensitivity_analysis_inputs" / "BPIC_2012"
START_ISO = "2023-01-01T00:00:00+02:00"

needs_jar = pytest.mark.skipif(not has_jar(), reason="needs a built scylla.jar")


def q(tag):
    return f"{{{BSIM}}}{tag}"


@pytest.fixture(scope="module")
def model():
    path = MODEL_DIR / "BPIC_2012_train.json"
    if not path.exists():
        pytest.skip("BPIC 2012 model not available")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bpmn():
    return MODEL_DIR / "BPIC_2012_train.bpmn"


def build(model, bpmn, **kwargs):
    return S.build_sim_config(model, bpmn, total_cases=100, start_iso=START_ISO,
                              seed=1, buckets=20, n_draws=500, **kwargs)


# --------------------------------------------------------------------------
# The emitted XML
# --------------------------------------------------------------------------

def test_one_entry_per_resource(model, bpmn):
    sim = build(model, bpmn).find(q("simulationConfiguration"))
    by_task = {t["task_id"]: t for t in model["task_resource_distribution"]}

    for task in sim.findall(q("task")):
        block = task.find(q("resourceDurations"))
        assert block is not None, task.get("id")
        assert len(block.findall(q("resourceDuration"))) == \
            len(by_task[task.get("id")]["resources"])


def test_entries_name_their_resource_and_unit(model, bpmn):
    """A missing timeUnit is a NullPointerException inside Scylla."""
    sim = build(model, bpmn).find(q("simulationConfiguration"))
    for task in sim.findall(q("task")):
        for item in task.find(q("resourceDurations")):
            assert item.get("resourceId")
            assert item.get("timeUnit") == "SECONDS"
            assert len(item) == 1, "each entry holds exactly one distribution"


def test_pooled_duration_is_kept_as_a_fallback(model, bpmn):
    """A Scylla build without the plugin ignores resourceDurations and uses
    this, reproducing the previous behaviour rather than failing."""
    sim = build(model, bpmn).find(q("simulationConfiguration"))
    for task in sim.findall(q("task")):
        duration = task.find(q("duration"))
        assert duration is not None
        assert duration.get("timeUnit") == "SECONDS"
        assert len(duration) == 1


def test_per_resource_durations_can_be_switched_off(model, bpmn):
    sim = build(model, bpmn, resource_durations=False).find(q("simulationConfiguration"))
    for task in sim.findall(q("task")):
        assert task.find(q("resourceDurations")) is None


def test_emitted_distributions_match_the_model(model, bpmn):
    """Each entry carries that resource's own distribution, not the pooled one."""
    sim = build(model, bpmn).find(q("simulationConfiguration"))
    by_task = {t["task_id"]: t for t in model["task_resource_distribution"]}

    for task in sim.findall(q("task")):
        declared = {r["resource_id"]: r
                    for r in by_task[task.get("id")]["resources"]}
        for item in task.find(q("resourceDurations")):
            source = declared[item.get("resourceId")]
            emitted = item[0].tag.split("}")[-1]
            if source["distribution_name"] == "fix":
                assert emitted == "constantDistribution"
                assert float(item[0][0].text) == pytest.approx(
                    source["distribution_params"][0]["value"])


# --------------------------------------------------------------------------
# What Scylla does with it
# --------------------------------------------------------------------------

def run_scylla(model, bpmn, cases, tmp_path, **kwargs):
    from src.simulation_pipeline.simulation.scylla import run_scylla as R

    original = S.build_sim_config
    if kwargs:
        S.build_sim_config = lambda *a, **k: original(*a, **{**k, **kwargs})
        R.build_sim_config = S.build_sim_config
    try:
        result = R.simulate_sample_scylla(
            sample_id=0, sample_data=model, bpmn_path=bpmn, total_cases=cases,
            start_iso=START_ISO, jar_path=R.resolve_jar(), seed=5,
            heap="1g", keep_output=tmp_path,
        )
    finally:
        S.build_sim_config = original
        R.build_sim_config = original
    assert result["error"] is None, result["error"]
    return result, Path(tmp_path) / "sample_00000"


def observed_durations(output_dir, activity_name):
    root = ET.parse(output_dir / "model.xes").getroot()
    durations = []
    for trace in root.findall(".//{*}trace"):
        started = None
        for event in trace.findall("{*}event"):
            fields = {f.get("key"): f.get("value") for f in list(event)}
            if fields.get("concept:name") != activity_name:
                continue
            stamp = fields.get("time:timestamp")
            if not stamp:
                continue
            moment = dt.datetime.fromisoformat(stamp)
            if fields.get("lifecycle:transition") == "start":
                started = moment
            elif fields.get("lifecycle:transition") == "complete" and started:
                durations.append((moment - started).total_seconds())
                started = None
    return durations


def bimodal_model(source, fast=60.0, slow=6000.0):
    """Alternate every resource between two very different fixed durations.

    Pooling would produce a spread of values between them; per-resource
    selection can only ever produce these two.
    """
    model = json.loads(json.dumps(source))
    for task in model["task_resource_distribution"]:
        for index, resource in enumerate(task["resources"]):
            resource["distribution_name"] = "fix"
            resource["distribution_params"] = [
                {"value": fast if index % 2 == 0 else slow}]
    return model


@needs_jar
def test_durations_follow_the_assigned_resource(model, bpmn, tmp_path):
    """The core check.

    With every resource on one of two fixed durations, the observed durations
    must be those two values. A pooled distribution would produce intermediate
    ones. Durations that span a calendar boundary are longer in wall-clock terms,
    so the assertion is on the dominant share rather than on every observation.
    """
    _, output = run_scylla(bimodal_model(model), bpmn, 300, tmp_path)
    durations = observed_durations(output, "W_Completeren aanvraag")
    assert durations, "activity never ran"

    exact = [d for d in durations if round(d) in (60, 6000)]
    assert len(exact) / len(durations) > 0.85, (
        f"only {len(exact)}/{len(durations)} durations were one of the two "
        f"configured values -- durations do not follow the resource"
    )


@needs_jar
def test_pooling_would_not_produce_that(model, bpmn, tmp_path):
    """The contrast: with the per-resource block removed, the same model gives
    a spread of intermediate durations."""
    _, output = run_scylla(bimodal_model(model), bpmn, 300, tmp_path,
                           resource_durations=False)
    durations = observed_durations(output, "W_Completeren aanvraag")
    assert durations

    exact = [d for d in durations if round(d) in (60, 6000)]
    assert len(exact) / len(durations) < 0.85, (
        "pooled durations reproduced the two exact values, so the comparison "
        "above proves nothing"
    )


@needs_jar
def test_fast_resources_take_disproportionate_work(model, bpmn, tmp_path):
    """A consequence worth pinning, because it is the opposite of Prosimos.

    Scylla assigns whichever pool instance is free, so a fast resource finishes
    sooner, becomes free sooner and takes on more work. The realised mean
    duration therefore sits below the arithmetic mean of the resources' means.

    Prosimos does not do this -- measured there, resource selection is
    near-uniform (fastest 4.8% of executions against slowest 2.7%), which is why
    the converter's load weighting was turned off. The same assumption that was
    false for Prosimos is true for Scylla.
    """
    _, output = run_scylla(model, bpmn, 500, tmp_path)
    durations = observed_durations(output, "W_Completeren aanvraag")
    assert durations

    task = max(model["task_resource_distribution"],
               key=lambda t: len(t["resources"]))
    declared = [r["distribution_params"][0]["value"] for r in task["resources"]]

    assert statistics.median(durations) < statistics.median(sorted(declared)), (
        "realised durations are not below the declared median, so fast "
        "resources are not being favoured -- the note in the README is stale"
    )


@needs_jar
@pytest.mark.skipif(not has_prosimos(), reason="needs Prosimos for the comparison")
def test_effect_on_the_gap_to_prosimos_is_recorded(model, bpmn, tmp_path):
    """Measured, not assumed: this plugin moves the gap the wrong way.

    On BPIC 2012 at 500 cases, mean cycle time against Prosimos went from 0.84x
    with the arrival calendar alone to 0.47x with per-resource durations added.
    Restoring the per-resource spread was expected to close the remaining gap;
    combined with Scylla's availability-based assignment it widens it instead.

    Asserted loosely and in the direction actually observed, so that a future
    change which fixes it fails here and forces the note to be updated.
    """
    from test_t1_determinism import run_prosimos

    prosimos = run_prosimos(model, bpmn, 500)["cycle_time"]["avg"]
    with_durations, _ = run_scylla(model, bpmn, 500, tmp_path / "on")
    without, _ = run_scylla(model, bpmn, 500, tmp_path / "off",
                            resource_durations=False)

    ratio_on = with_durations["process_rows"][0]["avg"] / prosimos
    ratio_off = without["process_rows"][0]["avg"] / prosimos

    assert ratio_on < ratio_off, (
        f"per-resource durations no longer lower cycle time "
        f"({ratio_off:.2f} -> {ratio_on:.2f}); the README note needs revisiting"
    )
