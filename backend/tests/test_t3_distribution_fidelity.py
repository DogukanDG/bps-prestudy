"""
T3 -- distribution fidelity: how much does discretisation cost?

Scylla supports nine distributions; lognormal and gamma are not among them and
are ~60% of the duration distributions in both BPIC models. They are
approximated by sampling and binning into an
`arbitraryFiniteProbabilityDistribution`, and this file measures what that
approximation loses.

The reference is `pix_framework`'s own sampler -- the same code Prosimos draws
from -- rather than our reimplementation, so the comparison is against what the
Prosimos arm actually simulates.

Distance is the 1-Wasserstein (earth mover's) distance, reported relative to
the reference mean so thresholds are scale-free. It is the right measure here
because it is sensitive to the whole distribution, not just its moments: a
discretisation can match mean and variance exactly while destroying the tail,
and queueing is driven by the tail.

Every threshold below is an assertion, not a print, so a regression in the
discretisation fails the suite rather than needing someone to read a number.
"""

import json
import random
import statistics
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from src.simulation_pipeline.simulation.scylla import distributions as D

REPO = Path(__file__).resolve().parents[2]
INPUTS = REPO / "example_sensitivity_analysis_inputs"

SAMPLE_SIZE = 20_000
SEED = 42

pix = pytest.importorskip("pix_framework.statistics.distribution",
                          reason="T3 compares against pix-framework's sampler")


def reference_sample(distribution: dict, size: int = SAMPLE_SIZE) -> list:
    """Draw from pix-framework, i.e. exactly what Prosimos would simulate."""
    return pix.DurationDistribution.from_dict(distribution).generate_sample(size)


def scylla_sample(distribution: dict, buckets: int, size: int = SAMPLE_SIZE) -> list:
    """Draw what Scylla would, by expanding the histogram we emit for it.

    Scylla backs `arbitraryFiniteProbabilityDistribution` with
    DiscreteDistEmpirical, a genuinely discrete distribution over the emitted
    values, so expanding each entry by its frequency reproduces the population
    Scylla samples from.
    """
    rng = random.Random(SEED)
    element = ET.Element("wrapper")
    D.append_distribution(element, distribution, rng, buckets=buckets,
                          n_draws=size)

    emitted = element[0]
    if emitted.tag.endswith("arbitraryFiniteProbabilityDistribution"):
        expanded = []
        for entry in emitted:
            expanded.extend([float(entry.get("value"))] * int(entry.get("frequency")))
        return expanded

    # Natively supported family: Scylla samples the real distribution, so the
    # reference sampler is the honest stand-in.
    return reference_sample(distribution, size)


def wasserstein(a, b) -> float:
    """1-Wasserstein distance between two samples, via their quantiles."""
    a, b = sorted(a), sorted(b)
    n = min(len(a), len(b), 2000)

    def quantiles(values):
        return [values[min(int((i + 0.5) / n * len(values)), len(values) - 1)]
                for i in range(n)]

    qa, qb = quantiles(a), quantiles(b)
    return sum(abs(x - y) for x, y in zip(qa, qb)) / n


def relative_distance(reference, candidate) -> float:
    """Wasserstein distance as a fraction of the reference mean."""
    mean = statistics.mean(reference)
    return wasserstein(reference, candidate) / mean if mean else float("nan")


def percentile(values, p):
    ordered = sorted(values)
    return ordered[min(int(p * len(ordered)), len(ordered) - 1)]


# Representative parameters, taken from the real models rather than invented.
FAMILIES = {
    "fix": dict(name="fix", mean=600.0),
    "expon": dict(name="expon", mean=2465.17, minimum=0.0, maximum=11880.0),
    "norm": dict(name="norm", mean=3240.0, std=300.0, minimum=2940.0,
                 maximum=3540.0),
    "uniform": dict(name="uniform", minimum=0.0, maximum=6180.0),
    "lognorm": dict(name="lognorm", mean=6940.0, var=44_007_200.0, minimum=2100.0,
                    maximum=16320.0),
    "gamma": dict(name="gamma", mean=7890.0, var=32_877_900.0, minimum=4500.0,
                  maximum=17820.0),
}


def as_simod_json(**kwargs) -> dict:
    return pix.DurationDistribution(**kwargs).to_prosimos_distribution()


@pytest.fixture(scope="module", params=sorted(FAMILIES))
def family(request):
    name = request.param
    return name, as_simod_json(**FAMILIES[name])


# --------------------------------------------------------------------------
# Per-family fidelity at the default bucket count
# --------------------------------------------------------------------------

def test_mean_is_preserved(family):
    """Equal-frequency buckets carry the mean of their contents, so the sample
    mean should survive discretisation essentially exactly.

    The 5% tolerance is sampling noise, not discretisation error: the two sides
    are independent draws. Heavily truncated exponentials move ~1.6% between
    successive 20k samples on their own.
    """
    name, distribution = family
    reference = reference_sample(distribution)
    candidate = scylla_sample(distribution, D.DEFAULT_BUCKETS)
    assert statistics.mean(candidate) == pytest.approx(
        statistics.mean(reference), rel=0.05), name


def test_distribution_shape_is_close(family):
    """Wasserstein distance under 5% of the mean at the default bucket count."""
    name, distribution = family
    reference = reference_sample(distribution)
    candidate = scylla_sample(distribution, D.DEFAULT_BUCKETS)
    assert relative_distance(reference, candidate) < 0.05, name


def test_median_and_upper_quantiles_are_close(family):
    """Moments can match while the body drifts; check the quantiles directly."""
    name, distribution = family
    reference = reference_sample(distribution)
    candidate = scylla_sample(distribution, D.DEFAULT_BUCKETS)
    for p in (0.5, 0.9, 0.99):
        assert percentile(candidate, p) == pytest.approx(
            percentile(reference, p), rel=0.15), f"{name} p{int(p * 100)}"


def test_extreme_tail_is_not_clipped(family):
    """Queueing is driven by the longest services, so the maximum must survive.

    Averaging inside a bucket would flatten it -- which is why the top decile is
    emitted sample by sample.
    """
    name, distribution = family
    reference = reference_sample(distribution)
    candidate = scylla_sample(distribution, D.DEFAULT_BUCKETS)
    assert max(candidate) >= max(reference) * 0.9, name


def test_bounds_are_respected(family):
    """Simod records min/max per distribution; discretisation must stay inside."""
    name, distribution = family
    params = D.values_of(distribution)
    low, high = D.bounds_of(distribution)
    if low is None:
        pytest.skip(f"{name} carries no bounds")

    candidate = scylla_sample(distribution, D.DEFAULT_BUCKETS)
    assert min(candidate) >= low - 1e-6, name
    assert max(candidate) <= high + 1e-6, name


def test_no_negative_durations(family):
    name, distribution = family
    assert min(scylla_sample(distribution, D.DEFAULT_BUCKETS)) >= 0.0, name


# --------------------------------------------------------------------------
# How much does the bucket count matter?
# --------------------------------------------------------------------------

@pytest.mark.parametrize("buckets", [10, 20, 50, 100, 200, 400])
def test_fidelity_across_bucket_counts(buckets):
    """The approximated families at several bucket counts.

    Recorded because the bucket count is a free parameter of the method: if
    fidelity depended sharply on it, the choice would need justifying in the
    write-up and the sensitivity results would inherit that uncertainty.
    """
    for name in ("lognorm", "gamma"):
        distribution = as_simod_json(**FAMILIES[name])
        reference = reference_sample(distribution)
        candidate = scylla_sample(distribution, buckets)
        assert relative_distance(reference, candidate) < 0.10, \
            f"{name} at {buckets} buckets"


def test_more_buckets_do_not_make_it_worse():
    """Monotone-ish improvement, allowing for sampling noise. A method whose
    error grew with resolution would mean something is wrong with the binning.
    """
    distribution = as_simod_json(**FAMILIES["lognorm"])
    reference = reference_sample(distribution)
    coarse = relative_distance(reference, scylla_sample(distribution, 10))
    fine = relative_distance(reference, scylla_sample(distribution, 400))
    assert fine <= coarse + 0.02


def test_natively_supported_families_are_not_discretised():
    """No approximation error at all where Scylla can represent the family."""
    for name in ("fix", "expon", "norm", "uniform"):
        element = ET.Element("wrapper")
        D.append_distribution(element, as_simod_json(**FAMILIES[name]),
                              random.Random(SEED))
        tag = element[0].tag.split("}")[-1]
        assert tag != "arbitraryFiniteProbabilityDistribution", name


# --------------------------------------------------------------------------
# Against the real models
# --------------------------------------------------------------------------

@pytest.mark.parametrize("dataset", ["BPIC_2012", "BPIC_2017"])
def test_every_real_distribution_is_faithful(dataset):
    """Sweep every duration distribution in the model, not just representative
    parameters. Catches families whose real parameters are degenerate --
    near-zero shape, bounds that cut most of the mass -- which invented test
    parameters would miss.

    Thresholds are on the *distribution* of errors rather than the worst case.
    A handful of the real distributions are extremely skewed (coefficient of
    variation up to 7.7, gamma shape as low as 0.015: median zero, 90% of the
    mass under 1.5 s, maximum near 2800 s). For those the relative Wasserstein
    distance is large while the mean is nearly exact, because the measure is
    dominated by a sparse tail. That is a property of the distribution, not a
    defect in the discretisation, so the median and p90 are what is asserted.

    Measured 2026-08-28: BPIC 2012 median 0.033 / p90 0.093 / max 0.210 over 118
    distributions; BPIC 2017 median 0.030 / p90 0.061 / max 0.363 over 258.
    """
    path = INPUTS / dataset / f"{dataset}_train.json"
    if not path.exists():
        pytest.skip(f"{dataset} not available")
    model = json.loads(path.read_text(encoding="utf-8"))

    distances = []
    for task in model["task_resource_distribution"]:
        for resource in task["resources"]:
            if resource["distribution_name"] not in D.APPROXIMATED_FAMILIES:
                continue
            reference = reference_sample(resource, 4000)
            if len(reference) < 100 or statistics.mean(reference) <= 0:
                continue
            candidate = scylla_sample(resource, D.DEFAULT_BUCKETS, 4000)
            distances.append(relative_distance(reference, candidate))

    assert len(distances) > 50, "too few approximated distributions to judge"
    distances.sort()
    median = distances[len(distances) // 2]
    p90 = distances[int(0.9 * len(distances))]

    assert median < 0.06, f"median relative distance {median:.3f}"
    assert p90 < 0.15, f"p90 relative distance {p90:.3f}"


@pytest.mark.parametrize("dataset", ["BPIC_2012", "BPIC_2017"])
def test_means_of_real_distributions_are_accurate(dataset):
    """The mean survives even where the shape measure does not.

    This is the check that matters for the KPIs: cycle time aggregates
    durations, so a faithful mean per distribution is what keeps the simulation
    honest even when a sparse tail moves the Wasserstein distance.
    """
    path = INPUTS / dataset / f"{dataset}_train.json"
    if not path.exists():
        pytest.skip(f"{dataset} not available")
    model = json.loads(path.read_text(encoding="utf-8"))

    errors = []
    for task in model["task_resource_distribution"]:
        for resource in task["resources"]:
            if resource["distribution_name"] not in D.APPROXIMATED_FAMILIES:
                continue
            reference = reference_sample(resource, 4000)
            if len(reference) < 100 or statistics.mean(reference) <= 0:
                continue
            candidate = scylla_sample(resource, D.DEFAULT_BUCKETS, 4000)
            reference_mean = statistics.mean(reference)
            errors.append(abs(statistics.mean(candidate) - reference_mean)
                          / reference_mean)

    errors.sort()
    median = errors[len(errors) // 2]
    p90 = errors[int(0.9 * len(errors))]
    assert median < 0.05, f"median mean-error {median:.3f}"
    assert p90 < 0.20, f"p90 mean-error {p90:.3f}"


@pytest.mark.parametrize("dataset", ["BPIC_2012", "BPIC_2017"])
def test_pooled_activity_durations_are_faithful(dataset):
    """The mixture actually written per activity, against the mixture of the
    reference samplers. This is what the simulation really uses -- the
    per-distribution check above does not cover the pooling step.

    Averaged over several seeds rather than taken from one. A pool containing
    very skewed members carries real sampling noise: on the worst BPIC 2017
    activity (26 resources) the single-seed error ranges 3-15% across seeds,
    so a one-seed assertion would be flaky rather than informative.
    """
    path = INPUTS / dataset / f"{dataset}_train.json"
    if not path.exists():
        pytest.skip(f"{dataset} not available")
    model = json.loads(path.read_text(encoding="utf-8"))

    trials = 5
    for task in model["task_resource_distribution"]:
        resources = task["resources"]
        per_resource = max(1, 8000 // len(resources))

        # Average the reference too: it is an independent draw and carries the
        # same sampling noise as the candidate, so pinning one against a single
        # draw of the other is flaky rather than informative.
        reference_means = []
        for _ in range(trials):
            reference = []
            for resource in resources:
                reference.extend(reference_sample(resource, per_resource))
            reference_means.append(statistics.mean(reference))
        reference_mean = statistics.mean(reference_means)

        means = []
        for trial in range(trials):
            element = ET.Element("wrapper")
            D.append_pooled_duration(element, resources, None,
                                     random.Random(SEED + trial),
                                     buckets=D.DEFAULT_BUCKETS, n_draws=8000)
            emitted = element[0]
            if not emitted.tag.endswith("arbitraryFiniteProbabilityDistribution"):
                break
            expanded = []
            for entry in emitted:
                expanded.extend([float(entry.get("value"))]
                                * int(entry.get("frequency")))
            means.append(statistics.mean(expanded))

        if not means:
            continue
        assert statistics.mean(means) == pytest.approx(
            reference_mean, rel=0.10), task["task_id"]


@pytest.mark.parametrize("dataset", ["BPIC_2012", "BPIC_2017"])
def test_emitted_entries_never_share_a_value(dataset):
    """Scylla's parser overwrites on duplicate values rather than accumulating
    (`EmpiricalDistribution.java:11`), so a collision silently discards mass.
    Checked here across every real activity, not just synthetic input.
    """
    path = INPUTS / dataset / f"{dataset}_train.json"
    if not path.exists():
        pytest.skip(f"{dataset} not available")
    model = json.loads(path.read_text(encoding="utf-8"))

    for task in model["task_resource_distribution"]:
        element = ET.Element("wrapper")
        D.append_pooled_duration(element, task["resources"], None,
                                 random.Random(SEED),
                                 buckets=D.DEFAULT_BUCKETS, n_draws=8000)
        values = [e.get("value") for e in element[0]]
        assert len(values) == len(set(values)), task["task_id"]
