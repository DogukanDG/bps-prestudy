"""
Tests for the Scylla runner.

Config generation and error handling are checked everywhere. The end-to-end
test needs a built scylla.jar and skips without one, so the suite stays green
on a machine that has not run spike/run_spike.sh build.
"""

import json
import math
import os
import shutil
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from src.simulation_pipeline.simulation.scylla import run_scylla as R
from src.simulation_pipeline.simulation.scylla.distributions import BSIM

REPO = Path(__file__).resolve().parents[2]
INPUTS = REPO / "example_sensitivity_analysis_inputs"
START_ISO = "2023-01-01T00:00:00+02:00"


def q(tag):
    return f"{{{BSIM}}}{tag}"


def find_jar():
    try:
        return R.resolve_jar()
    except FileNotFoundError:
        return None


JAR = find_jar()
needs_jar = pytest.mark.skipif(
    JAR is None,
    reason="scylla.jar not built; run spike/run_spike.sh build or set SCYLLA_JAR",
)


@pytest.fixture
def model():
    path = INPUTS / "BPIC_2012" / "BPIC_2012_train.json"
    if not path.exists():
        pytest.skip("BPIC 2012 model not available")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def bpmn():
    return INPUTS / "BPIC_2012" / "BPIC_2012_train.bpmn"


# --------------------------------------------------------------------------
# Config generation
# --------------------------------------------------------------------------

def test_write_configs_produces_all_three_files(tmp_path, model, bpmn):
    configs = R.write_configs(tmp_path, model, bpmn, total_cases=100,
                              start_iso=START_ISO, seed=1,
                              buckets=10, n_draws=200)
    assert set(configs) == {"global_config", "sim_config", "bpmn"}
    for path in configs.values():
        assert path.is_file() and path.stat().st_size > 0


def test_written_xml_declares_the_bsim_namespace(tmp_path, model, bpmn):
    """Scylla inherits the root namespace, so it must be declared."""
    configs = R.write_configs(tmp_path, model, bpmn, total_cases=100,
                              start_iso=START_ISO, seed=1,
                              buckets=10, n_draws=200)
    text = configs["global_config"].read_text(encoding="utf-8")
    assert BSIM in text
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>')


def test_written_xml_is_parseable_and_complete(tmp_path, model, bpmn):
    configs = R.write_configs(tmp_path, model, bpmn, total_cases=250,
                              start_iso=START_ISO, seed=1,
                              buckets=10, n_draws=200)

    global_root = ET.parse(configs["global_config"]).getroot()
    assert global_root.findtext(q("randomSeed")) == "1"

    sim = ET.parse(configs["sim_config"]).getroot().find(q("simulationConfiguration"))
    assert sim.get("processInstances") == "250"
    assert len(sim.findall(q("task"))) == len(model["task_resource_distribution"])


def test_same_seed_writes_identical_configs(tmp_path, model, bpmn):
    """Reproducibility: a sensitivity analysis cannot use an engine whose
    output moves between identical runs."""
    kw = dict(total_cases=100, start_iso=START_ISO, seed=99,
              buckets=10, n_draws=200)
    a = R.write_configs(tmp_path / "a", model, bpmn, **kw)
    b = R.write_configs(tmp_path / "b", model, bpmn, **kw)
    assert a["global_config"].read_text() == b["global_config"].read_text()
    assert a["sim_config"].read_text() == b["sim_config"].read_text()


def test_invalid_model_is_rejected_before_scylla_runs(tmp_path, model, bpmn):
    """Validation happens up front, because Scylla would accept this silently."""
    broken = json.loads(json.dumps(model))
    broken["task_resource_distribution"][0]["resources"] = []
    with pytest.raises(Exception):
        R.write_configs(tmp_path, broken, bpmn, total_cases=100,
                        start_iso=START_ISO, seed=1, buckets=10, n_draws=200)


# --------------------------------------------------------------------------
# Error handling
# --------------------------------------------------------------------------

def test_missing_jar_message_names_the_version_requirement():
    with pytest.raises(FileNotFoundError, match="f9671cb"):
        R.resolve_jar("/nonexistent/scylla.jar")


def test_bad_jar_is_reported_as_an_error(tmp_path, model, bpmn):
    configs = R.write_configs(tmp_path, model, bpmn, total_cases=10,
                              start_iso=START_ISO, seed=1,
                              buckets=10, n_draws=100)
    fake = tmp_path / "not_really.jar"
    fake.write_text("not a jar")
    with pytest.raises(R.ScyllaError):
        R.run_scylla(fake, configs, tmp_path, timeout_s=60)


def test_failures_are_captured_not_raised(model, bpmn):
    """simulate_sample_scylla mirrors the Prosimos contract: one bad sample
    returns an error string rather than aborting the whole chunk."""
    result = R.simulate_sample_scylla(
        sample_id=3, sample_data=model, bpmn_path=bpmn,
        total_cases=10, start_iso=START_ISO,
        jar_path="/nonexistent/scylla.jar",
    )
    assert result["sample_id"] == 3
    assert result["error"]
    assert result["process_rows"] == []


def test_error_result_has_the_full_contract_shape(model, bpmn):
    result = R.simulate_sample_scylla(
        sample_id=0, sample_data=model, bpmn_path=bpmn,
        total_cases=10, start_iso=START_ISO, jar_path="/nonexistent.jar",
    )
    assert set(result) == {"sample_id", "process_rows", "task_rows",
                           "resource_rows", "case_rows", "error"}


# --------------------------------------------------------------------------
# End to end -- needs a built jar
# --------------------------------------------------------------------------

@needs_jar
def test_end_to_end_returns_the_contract(model, bpmn):
    result = R.simulate_sample_scylla(
        sample_id=42, sample_data=model, bpmn_path=bpmn,
        total_cases=200, start_iso=START_ISO, jar_path=JAR,
        buckets=50, n_draws=4000, seed=100,
    )
    assert result["error"] is None, result["error"]
    assert result["sample_id"] == 42

    rows = result["process_rows"]
    assert len(rows) == 6
    assert [r["metric"] for r in rows] == [
        "cycle_time", "processing_time", "waiting_time",
        "idle_cycle_time", "idle_processing_time", "idle_time",
    ]

    by_metric = {r["metric"]: r for r in rows}
    assert by_metric["cycle_time"]["count"] == 200
    assert by_metric["cycle_time"]["avg"] > 0
    assert math.isnan(by_metric["idle_time"]["avg"])


@needs_jar
def test_end_to_end_is_reproducible(model, bpmn):
    kw = dict(sample_data=model, bpmn_path=bpmn, total_cases=100,
              start_iso=START_ISO, jar_path=JAR, buckets=30,
              n_draws=2000, seed=7)
    a = R.simulate_sample_scylla(sample_id=1, **kw)
    b = R.simulate_sample_scylla(sample_id=1, **kw)
    assert a["error"] is None and b["error"] is None
    assert [r["avg"] for r in a["process_rows"][:3]] \
        == [r["avg"] for r in b["process_rows"][:3]]


@needs_jar
def test_end_to_end_output_can_be_kept(tmp_path, model, bpmn):
    result = R.simulate_sample_scylla(
        sample_id=5, sample_data=model, bpmn_path=bpmn,
        total_cases=100, start_iso=START_ISO, jar_path=JAR,
        buckets=30, n_draws=2000, keep_output=tmp_path,
    )
    assert result["error"] is None
    kept = tmp_path / "sample_00005"
    assert kept.is_dir()
    assert any(kept.rglob("*_resourceutilization.xml"))


@needs_jar
def test_temporary_working_directories_are_cleaned_up(model, bpmn):
    """178k simulations must not leave 178k directories behind."""
    import tempfile
    before = set(Path(tempfile.gettempdir()).glob("scylla_s*"))
    R.simulate_sample_scylla(
        sample_id=9, sample_data=model, bpmn_path=bpmn,
        total_cases=50, start_iso=START_ISO, jar_path=JAR,
        buckets=20, n_draws=1000,
    )
    after = set(Path(tempfile.gettempdir()).glob("scylla_s*"))
    assert after <= before
