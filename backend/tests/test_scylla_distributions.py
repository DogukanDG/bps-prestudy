"""
Tests for the Simod -> Scylla distribution mapping.

The mapping is the single highest-risk part of the migration: a wrong
parameter index produces a simulation that runs happily and reports wrong
numbers. SimuBridge's converter has exactly that bug for exponentials, so
these tests pin the indices against statistical behaviour, not just shape.
"""

import random
import statistics
from xml.etree import ElementTree as ET

import pytest

from src.simulation_pipeline.simulation.scylla import distributions as D

BSIM = D.BSIM


def tag(el):
    return el.tag.split("}")[-1]


def child_text(el, name):
    return el.findtext(f"{{{BSIM}}}{name}")


def dist(name, *values):
    return {"distribution_name": name,
            "distribution_params": [{"value": v} for v in values]}


def root():
    return ET.Element("wrapper")


# --------------------------------------------------------------------------
# Native families: exact parameter mapping
# --------------------------------------------------------------------------

def test_fix_maps_to_constant():
    el = D.append_native(root(), dist("fix", 42.0))
    assert tag(el) == "constantDistribution"
    assert float(child_text(el, "constantValue")) == 42.0


def test_expon_reads_mean_from_index_zero():
    """Simod writes [mean, min, max].

    SimuBridge reads index 1, which is `min` and is almost always 0, so every
    exponential duration becomes zero. This is the regression guard for that.
    """
    el = D.append_native(root(), dist("expon", 2465.17, 0.0, 11880.0))
    assert tag(el) == "exponentialDistribution"
    assert float(child_text(el, "mean")) == pytest.approx(2465.17)


def test_norm_passes_std_through_unchanged():
    """Simod writes [mean, std, min, max] -- params[1] is already a standard
    deviation. SimuBridge treats it as a variance and square-roots it."""
    el = D.append_native(root(), dist("norm", 3240.0, 300.0, 2940.0, 3540.0))
    assert tag(el) == "normalDistribution"
    assert float(child_text(el, "mean")) == pytest.approx(3240.0)
    assert float(child_text(el, "standardDeviation")) == pytest.approx(300.0)


def test_uniform_bounds_are_absolute_not_offsets():
    """Simod writes [min, max] directly; SimuBridge computes upper = p1 + lower."""
    el = D.append_native(root(), dist("uniform", 100.0, 500.0))
    assert tag(el) == "uniformDistribution"
    assert float(child_text(el, "lower")) == pytest.approx(100.0)
    assert float(child_text(el, "upper")) == pytest.approx(500.0)


def test_unsupported_family_raises():
    with pytest.raises(ValueError):
        D.append_native(root(), dist("lognorm", 100.0, 5.0, 0.0, 1e9))


# --------------------------------------------------------------------------
# Sampling: the parameters must actually drive the draws
# --------------------------------------------------------------------------

@pytest.mark.parametrize("family,args,expected_mean", [
    ("expon", (600.0, 0.0, 1e9), 600.0),
    ("norm", (3240.0, 300.0, 0.0, 1e9), 3240.0),
    ("uniform", (100.0, 500.0), 300.0),
    ("lognorm", (6940.0, 44_007_200.0, 0.0, 1e9), 6940.0),
    ("gamma", (7890.0, 32_877_900.0, 0.0, 1e9), 7890.0),
])
def test_sample_mean_matches_declared_mean(family, args, expected_mean):
    """Every family stores its mean in params[0]; drawing from it must
    reproduce that mean. Catches index slips that shape checks miss."""
    rng = random.Random(0)
    draws = [D.sample_once(dist(family, *args), rng) for _ in range(20_000)]
    assert statistics.mean(draws) == pytest.approx(expected_mean, rel=0.05)


def test_fix_sampling_is_constant():
    rng = random.Random(0)
    d = dist("fix", 17.5)
    assert {D.sample_once(d, rng) for _ in range(100)} == {17.5}


def test_draws_are_clipped_to_recorded_bounds():
    rng = random.Random(0)
    d = dist("lognorm", 5000.0, 9_000_000.0, 1000.0, 8000.0)
    draws = D.draw_clipped(d, rng, 5000)
    assert min(draws) >= 1000.0
    assert max(draws) <= 8000.0


def test_draws_are_never_negative():
    """A normal with a wide sigma can go negative; durations cannot."""
    rng = random.Random(0)
    d = dist("norm", 100.0, 500.0, -1e9, 1e9)
    assert min(D.draw_clipped(d, rng, 5000)) >= 0.0


def test_sampling_is_reproducible_for_a_given_seed():
    a = D.draw_clipped(dist("gamma", 500.0, 250_000.0, 0.0, 1e9), random.Random(7), 500)
    b = D.draw_clipped(dist("gamma", 500.0, 250_000.0, 0.0, 1e9), random.Random(7), 500)
    assert a == b


# --------------------------------------------------------------------------
# Histogram approximation for lognorm / gamma
# --------------------------------------------------------------------------

def test_histogram_preserves_the_mean():
    rng = random.Random(0)
    d = dist("lognorm", 6940.0, 44_007_200.0, 0.0, 1e9)
    samples = D.draw_clipped(d, rng, 20_000)
    el = D.append_histogram(root(), samples, buckets=100)

    total = weighted = 0.0
    for e in el:
        v, f = float(e.get("value")), float(e.get("frequency"))
        weighted += v * f
        total += f
    assert weighted / total == pytest.approx(statistics.mean(samples), rel=0.02)


def test_histogram_respects_bucket_count():
    rng = random.Random(0)
    samples = D.draw_clipped(dist("gamma", 500.0, 250_000.0, 0.0, 1e9), rng, 20_000)
    assert len(D.append_histogram(root(), samples, buckets=20)) <= 20
    assert len(D.append_histogram(root(), samples, buckets=200)) <= 200


def test_histogram_of_identical_values_collapses_to_one_entry():
    el = D.append_histogram(root(), [5.0] * 100)
    assert len(el) == 1
    assert float(el[0].get("value")) == pytest.approx(5.0)


def test_histogram_entries_are_non_negative():
    rng = random.Random(0)
    samples = D.draw_clipped(dist("norm", 100.0, 400.0, 0.0, 1e9), rng, 5000)
    el = D.append_histogram(root(), samples)
    assert all(float(e.get("value")) >= 0 for e in el)


def test_histogram_rejects_empty_input():
    with pytest.raises(ValueError):
        D.append_histogram(root(), [])


# --------------------------------------------------------------------------
# Pooling: the load-weighted mixture
# --------------------------------------------------------------------------

def test_weighting_pulls_the_mixture_toward_the_busier_resource():
    """The whole point of weighting. A fast resource that does most of the
    work must dominate the pooled duration; unweighted pooling overstates it.
    """
    fast, slow = dist("fix", 100.0), dist("fix", 10_000.0)
    rng = random.Random(0)

    balanced = statistics.mean(D.weighted_mixture([fast, slow], [1, 1], rng, 4000))
    fast_heavy = statistics.mean(D.weighted_mixture([fast, slow], [9, 1], rng, 4000))

    assert fast_heavy < balanced
    assert fast_heavy == pytest.approx(1090.0, rel=0.1)   # 0.9*100 + 0.1*10000
    assert balanced == pytest.approx(5050.0, rel=0.1)


def test_none_weights_mean_equal_shares():
    fast, slow = dist("fix", 100.0), dist("fix", 10_000.0)
    rng = random.Random(0)
    equal = statistics.mean(D.weighted_mixture([fast, slow], [1, 1], rng, 4000))
    unweighted = statistics.mean(D.weighted_mixture([fast, slow], None, rng, 4000))
    assert unweighted == pytest.approx(equal, rel=0.05)


def test_zero_weights_fall_back_to_equal_shares():
    """A task whose resources all report zero load must not divide by zero."""
    rng = random.Random(0)
    out = D.weighted_mixture([dist("fix", 1.0), dist("fix", 3.0)], [0, 0], rng, 1000)
    assert statistics.mean(out) == pytest.approx(2.0, rel=0.05)


def test_mixture_rejects_empty_resource_list():
    with pytest.raises(ValueError):
        D.weighted_mixture([], None, random.Random(0))


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

def test_single_native_resource_is_passed_through_exactly():
    """No needless discretisation when Scylla can represent it exactly."""
    el = D.append_pooled_duration(
        root(), [dist("expon", 600.0, 0.0, 1e9)], [1.0], random.Random(0))
    assert tag(el) == "exponentialDistribution"
    assert float(child_text(el, "mean")) == pytest.approx(600.0)


def test_single_unsupported_resource_is_discretised():
    el = D.append_pooled_duration(
        root(), [dist("lognorm", 5000.0, 9e6, 0.0, 1e9)], [1.0], random.Random(0))
    assert tag(el) == "arbitraryFiniteProbabilityDistribution"


def test_multiple_resources_are_always_discretised():
    """Even when every family is native, a pool has no single exact form."""
    el = D.append_pooled_duration(
        root(),
        [dist("fix", 100.0), dist("fix", 200.0)],
        [1.0, 1.0],
        random.Random(0),
    )
    assert tag(el) == "arbitraryFiniteProbabilityDistribution"


def test_append_distribution_routes_by_family():
    rng = random.Random(0)
    assert tag(D.append_distribution(root(), dist("fix", 1.0), rng)) \
        == "constantDistribution"
    assert tag(D.append_distribution(root(), dist("gamma", 500.0, 250_000.0, 0.0, 1e9), rng)) \
        == "arbitraryFiniteProbabilityDistribution"


# --------------------------------------------------------------------------
# Real model data
# --------------------------------------------------------------------------

def test_every_family_in_the_real_models_is_handled():
    """Guard against a family appearing in the data that the mapping forgot."""
    import json
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    known = D.NATIVE_FAMILIES | D.APPROXIMATED_FAMILIES
    checked = 0

    for name in ("BPIC_2012", "BPIC_2017"):
        path = repo / "example_sensitivity_analysis_inputs" / name / f"{name}_train.json"
        if not path.exists():
            continue
        model = json.loads(path.read_text(encoding="utf-8"))
        for task in model["task_resource_distribution"]:
            for res in task["resources"]:
                assert res["distribution_name"] in known, res["distribution_name"]
                checked += 1
        assert model["arrival_time_distribution"]["distribution_name"] in known

    assert checked > 0, "no model files found to check"
