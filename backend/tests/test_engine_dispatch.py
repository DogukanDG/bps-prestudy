"""
Tests for engine selection in simulate_samples.

The dispatch itself is small, but it sits on the pipeline's hot path and two
things about it can break a whole cluster run:

  - defaulting: every existing caller omits `engine`, so the default must stay
    Prosimos and the Prosimos worker must be the untouched original;
  - picklability: joblib's loky backend sends the worker to subprocesses, and a
    closure or lambda raises PicklingError. The BPIC 2013 Morris t=512 runs
    died exactly that way (see bpic2013_run_times.csv), losing three runs.
"""

import pickle

import pytest

from src.simulation_pipeline.simulation import simulate_samples as SS


def test_default_engine_is_prosimos():
    """Existing callers pass no engine at all; they must keep working."""
    assert SS._engine_worker("prosimos", None) is SS.simulate_sample
    assert SS._engine_worker(None, None) is SS.simulate_sample


def test_engine_name_is_case_insensitive():
    assert SS._engine_worker("PROSIMOS", None) is SS.simulate_sample


def test_unknown_engine_fails_immediately():
    """Better a clear error up front than a cluster job that dies per sample."""
    with pytest.raises(ValueError, match="unknown engine"):
        SS._engine_worker("bimp", None)


def test_prosimos_worker_is_picklable():
    assert pickle.loads(pickle.dumps(SS._engine_worker("prosimos", None))) \
        is SS.simulate_sample


def test_scylla_worker_is_picklable():
    """The regression guard for the PicklingError that killed the t=512 runs."""
    scylla = pytest.importorskip(
        "src.simulation_pipeline.simulation.scylla.run_scylla")
    try:
        worker = SS._engine_worker("scylla", {"jar_path": None})
    except FileNotFoundError:
        pytest.skip("scylla.jar not built")

    restored = pickle.loads(pickle.dumps(worker))
    assert restored.func is scylla.simulate_sample_scylla
    assert restored.keywords["jar_path"] == worker.keywords["jar_path"]


def test_scylla_engine_reports_a_missing_jar_once_up_front():
    """Resolved during dispatch, not inside each worker, so a missing jar
    fails before thousands of simulations are queued."""
    with pytest.raises(FileNotFoundError):
        SS._engine_worker("scylla", {"jar_path": "/nonexistent/scylla.jar"})


def test_scylla_options_are_bound_to_the_worker():
    try:
        worker = SS._engine_worker(
            "scylla", {"jar_path": None, "buckets": 42, "weighted": False})
    except FileNotFoundError:
        pytest.skip("scylla.jar not built")
    assert worker.keywords["buckets"] == 42
    assert worker.keywords["weighted"] is False


def test_engine_options_are_not_mutated(monkeypatch):
    """The caller's dict is reused across case counts; popping jar_path out of
    it in place would break the second call."""
    import src.simulation_pipeline.simulation.scylla.run_scylla as R
    monkeypatch.setattr(R, "resolve_jar", lambda p=None: "/fake/scylla.jar")

    options = {"jar_path": "/fake/scylla.jar", "buckets": 10}
    SS._engine_worker("scylla", options)
    assert options == {"jar_path": "/fake/scylla.jar", "buckets": 10}


def test_both_engines_declare_the_same_call_signature():
    """simulate_all_samples calls the worker with fixed keyword arguments, so
    the two engines have to accept the same ones."""
    import inspect

    scylla = pytest.importorskip(
        "src.simulation_pipeline.simulation.scylla.run_scylla")

    required = {"sample_id", "sample_data", "bpmn_path", "total_cases", "start_iso"}
    for fn in (SS.simulate_sample, scylla.simulate_sample_scylla):
        assert required <= set(inspect.signature(fn).parameters), fn.__name__


def test_engine_parameters_are_threaded_through_the_public_api():
    """simulate_samples -> simulate_all_samples must both accept and forward
    the engine, or selecting one silently does nothing."""
    import inspect

    for fn in (SS.simulate_samples, SS.simulate_all_samples):
        params = inspect.signature(fn).parameters
        assert "engine" in params, fn.__name__
        assert "engine_options" in params, fn.__name__
        assert params["engine"].default == "prosimos", fn.__name__

    source = inspect.getsource(SS.simulate_samples)
    assert "engine=engine" in source, "simulate_samples does not forward engine"
    assert "engine_options=engine_options" in source
