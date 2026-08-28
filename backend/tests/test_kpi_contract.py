"""
Lock the KPI row contract that the engine swap must preserve.

Nothing downstream of simulate_sample() knows which engine produced its
output -- merge_parquet_chunks, the sensitivity analysis and the frontend all
read the same seven columns. These tests pin that shape against real Prosimos
output so a Scylla arm cannot quietly change it.

Reference data: the BPIC 2012 / 2017 replication runs under
"Cases and Replications", copied into fixtures/.
"""

from pathlib import Path

import pandas as pd
import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# The contract, as built in simulate_samples.py:435-442 and kpi_to_dict():652-658
PROCESS_COLUMNS = ["sample_id", "metric", "min", "max", "avg", "total", "count"]

PROCESS_METRICS = {
    "cycle_time",
    "processing_time",
    "waiting_time",
    "idle_cycle_time",
    "idle_processing_time",
    "idle_time",
}

REFERENCES = sorted(FIXTURES.glob("prosimos_*.parquet"))


@pytest.fixture(params=REFERENCES, ids=lambda p: p.stem)
def reference(request):
    return pd.read_parquet(request.param)


def test_fixtures_exist():
    """Guard against an empty fixtures/ silently skipping every test below."""
    assert REFERENCES, f"no reference parquet files in {FIXTURES}"


def test_columns_exact(reference):
    """Column set and order are both part of the contract."""
    assert list(reference.columns) == PROCESS_COLUMNS


def test_metrics_exact(reference):
    """All six metrics present, and nothing extra."""
    assert set(reference["metric"].unique()) == PROCESS_METRICS


def test_one_row_per_metric_per_sample(reference):
    """Exactly six rows per sample -- no duplicates, no gaps."""
    counts = reference.groupby("sample_id")["metric"].count()
    assert set(counts.unique()) == {6}


def test_numeric_columns_are_numeric(reference):
    for col in ("min", "max", "avg", "total", "count"):
        assert pd.api.types.is_numeric_dtype(reference[col]), col


def test_no_missing_values(reference):
    """A Scylla arm that cannot produce a metric must say so loudly, not
    emit NaN into the sensitivity analysis."""
    assert not reference.isna().any().any()


def test_min_max_bracket_avg(reference):
    """Sanity invariant every engine must satisfy."""
    bad = reference[(reference["avg"] < reference["min"])
                    | (reference["avg"] > reference["max"])]
    assert bad.empty, f"avg outside [min, max]:\n{bad}"


def test_total_consistent_with_avg_and_count(reference):
    """total == avg * count.

    This is what makes the four aggregate columns mutually checkable: an
    engine adapter that fills them from unrelated sources fails here.

    The tolerance is 1e-4 rather than float-exact because these fixtures are
    means over five replication runs (merge_parquet_chunks averages each
    column independently), so total and avg * count drift apart slightly.
    Measured worst case here is 2.6e-5.
    """
    ratio = reference["total"] / (reference["avg"] * reference["count"])
    assert (ratio - 1.0).abs().max() < 1e-4


def test_durations_non_negative(reference):
    assert (reference[["min", "max", "avg", "total"]] >= 0).all().all()


def test_processing_time_within_cycle_time(reference):
    """Processing time is time spent working; cycle time also includes
    waiting. Per sample, mean processing cannot exceed mean cycle time.
    """
    wide = reference.pivot(index="sample_id", columns="metric", values="avg")
    bad = wide[wide["processing_time"] > wide["cycle_time"] + 1e-6]
    assert bad.empty, f"processing_time exceeds cycle_time:\n{bad}"
