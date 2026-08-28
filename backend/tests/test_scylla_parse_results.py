"""
Tests for reading Scylla's statistics output back into process_rows.

Run against the real spike output committed under spike/, so the parser is
checked against XML Scylla actually produced rather than a hand-written mock.
"""

import math
from pathlib import Path

import pytest

from src.simulation_pipeline.simulation.scylla import parse_results as P

REPO = Path(__file__).resolve().parents[2]
SPIKE = REPO / "spike"

RUNS = [
    (SPIKE / "bpic2012" / "out_3000", 3000),
    (SPIKE / "bpic2012" / "out_1000", 1000),
    (SPIKE / "bpic2017" / "out_3000", 3000),
]
AVAILABLE = [(d, n) for d, n in RUNS if d.exists()]


@pytest.fixture(params=AVAILABLE, ids=lambda p: f"{p[0].parent.name}_{p[1]}")
def run(request):
    return request.param


def test_spike_output_is_available():
    assert AVAILABLE, f"no Scylla output found under {SPIKE}"


def test_finds_the_stats_file(run):
    path = P.find_stats_file(run[0])
    assert path.name.endswith(P.STATS_SUFFIX)


def test_missing_output_dir_explains_the_likely_cause(tmp_path):
    """The usual reason is a run without --enable-bps-logging."""
    with pytest.raises(FileNotFoundError, match="enable-bps-logging"):
        P.find_stats_file(tmp_path)


def test_emits_the_full_six_metric_contract(run):
    rows = P.parse_process_rows(run[0], sample_id=7)
    assert [r["metric"] for r in rows] == list(P.PROCESS_METRICS)
    assert all(r["sample_id"] == 7 for r in rows)
    assert all(set(r) == {"sample_id", "metric", "min", "max", "avg", "total", "count"}
               for r in rows)


def test_mapped_metrics_have_real_numbers(run):
    rows = {r["metric"]: r for r in P.parse_process_rows(run[0], 0)}
    for metric in ("cycle_time", "processing_time", "waiting_time"):
        for field in ("min", "max", "avg", "total", "count"):
            value = rows[metric][field]
            assert not math.isnan(value), f"{metric}.{field}"
            assert value >= 0


def test_unmapped_metrics_are_nan_not_zero(run):
    """Emitting 0.0 would silently feed a wrong number into the sensitivity
    analysis; NaN makes the gap visible."""
    rows = {r["metric"]: r for r in P.parse_process_rows(run[0], 0)}
    for metric in P.UNMAPPED_METRICS:
        assert all(math.isnan(rows[metric][f])
                   for f in ("min", "max", "avg", "total", "count"))


def test_case_count_is_recovered_from_total_and_avg(run):
    """Scylla reports no count field; total / avg must reproduce it exactly."""
    output_dir, expected = run
    rows = {r["metric"]: r for r in P.parse_process_rows(output_dir, 0)}
    assert rows["cycle_time"]["count"] == expected


def test_expected_cases_mismatch_is_an_error(run):
    """A case count that does not match what was requested means Scylla did
    not read the configuration as written."""
    with pytest.raises(ValueError, match="not read as written"):
        P.parse_process_rows(run[0], 0, expected_cases=run[1] + 1)


def test_expected_cases_match_passes(run):
    P.parse_process_rows(run[0], 0, expected_cases=run[1])


def test_total_equals_avg_times_count(run):
    """The invariant the KPI contract test asserts for the Prosimos arm."""
    for row in P.parse_process_rows(run[0], 0):
        if math.isnan(row["avg"]):
            continue
        assert row["total"] == pytest.approx(row["avg"] * row["count"], rel=1e-6)


def test_avg_lies_between_min_and_max(run):
    for row in P.parse_process_rows(run[0], 0):
        if math.isnan(row["avg"]):
            continue
        assert row["min"] <= row["avg"] <= row["max"], row["metric"]


def test_processing_time_within_cycle_time(run):
    """Work done cannot exceed elapsed time under any definition."""
    rows = {r["metric"]: r for r in P.parse_process_rows(run[0], 0)}
    assert rows["processing_time"]["avg"] <= rows["cycle_time"]["avg"] + 1e-6


def test_waiting_time_definition_gap_is_reported(run):
    """Scylla sums waiting per activity instance, so concurrent waits are
    counted more than once and the total can exceed flow time. That is a real
    definitional difference from Prosimos, and it must be surfaced rather than
    silently carried into a cross-engine comparison.
    """
    rows = P.parse_process_rows(run[0], 0)
    by_metric = {r["metric"]: r for r in rows}
    warnings = P.check_consistency(rows)

    if by_metric["waiting_time"]["avg"] > by_metric["cycle_time"]["avg"]:
        assert any("waiting_time" in w for w in warnings)


def test_consistency_check_passes_on_sane_rows():
    rows = [
        {"sample_id": 0, "metric": "cycle_time", "min": 1.0, "max": 10.0,
         "avg": 5.0, "total": 50.0, "count": 10.0},
        {"sample_id": 0, "metric": "processing_time", "min": 1.0, "max": 4.0,
         "avg": 2.0, "total": 20.0, "count": 10.0},
        {"sample_id": 0, "metric": "waiting_time", "min": 0.0, "max": 6.0,
         "avg": 3.0, "total": 30.0, "count": 10.0},
    ]
    assert P.check_consistency(rows) == []


def test_consistency_check_catches_impossible_processing_time():
    rows = [
        {"sample_id": 0, "metric": "cycle_time", "min": 1.0, "max": 10.0,
         "avg": 5.0, "total": 50.0, "count": 10.0},
        {"sample_id": 0, "metric": "processing_time", "min": 1.0, "max": 90.0,
         "avg": 50.0, "total": 500.0, "count": 10.0},
    ]
    warnings = P.check_consistency(rows)
    assert any("inconsistent" in w for w in warnings)


def test_consistency_check_catches_avg_outside_range():
    rows = [
        {"sample_id": 0, "metric": "cycle_time", "min": 10.0, "max": 20.0,
         "avg": 99.0, "total": 990.0, "count": 10.0},
    ]
    assert any("outside" in w for w in P.check_consistency(rows))


def test_activity_stats_match_the_model(run):
    """The check that catches Scylla having silently skipped part of the
    configuration: every activity we wrote must appear in the output."""
    import json

    dataset = "BPIC_2012" if "bpic2012" in str(run[0]) else "BPIC_2017"
    model_path = (REPO / "example_sensitivity_analysis_inputs" / dataset
                  / f"{dataset}_train.json")
    if not model_path.exists():
        pytest.skip(f"{dataset} model not available")

    model = json.loads(model_path.read_text(encoding="utf-8"))
    expected = {t["task_id"] for t in model["task_resource_distribution"]}
    found = set(P.parse_activity_stats(run[0]))
    assert expected <= found, f"activities missing from output: {expected - found}"


def test_activity_durations_are_plausible(run):
    """Durations should land near the model's own means. Wildly different
    numbers would mean the durations were not read as written."""
    import json
    import statistics

    dataset = "BPIC_2012" if "bpic2012" in str(run[0]) else "BPIC_2017"
    model_path = (REPO / "example_sensitivity_analysis_inputs" / dataset
                  / f"{dataset}_train.json")
    if not model_path.exists():
        pytest.skip(f"{dataset} model not available")

    model = json.loads(model_path.read_text(encoding="utf-8"))
    stats = P.parse_activity_stats(run[0])

    for task in model["task_resource_distribution"]:
        observed = stats.get(task["task_id"])
        if observed is None:
            continue
        means = [r["distribution_params"][0]["value"] for r in task["resources"]]
        slowest = max(means) if means else 0
        # Generous: the spike pooled unweighted, and pooling of any kind moves
        # the mean. The point is to catch zeros and absurdities, not to pin
        # fidelity -- that is what the T3/T5 tests are for.
        assert 0 < observed["avg"] <= max(slowest * 2, 1.0), task["task_id"]
