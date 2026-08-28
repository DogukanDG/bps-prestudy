"""
Simod/Prosimos duration distributions -> Scylla XML distribution elements.

This is where every translation error is isolated, so it is worth being
explicit about what the numbers mean.

Parameter order was read from the model files themselves
(`BPIC_2012_train.json`, `BPIC_2017_train.json`), not from documentation and
not from SimuBridge, whose `simod_converter.js` reads the wrong indices:

    expon   [mean, min, max]        SimuBridge reads params[1] -> mean 0
    norm    [mean, std, min, max]   SimuBridge treats params[1] as a variance
                                    and square-roots it -> sigma 300 becomes 17
    uniform [min, max]              SimuBridge computes upper = params[1] + lower
    lognorm [mean, var, min, max]
    gamma   [mean, var, min, max]
    fix     [value]

Scylla supports exactly nine distributions
(`SimulationConfigurationParser.java:283-416`); lognormal and gamma are not
among them, and together they are ~60% of the duration distributions in both
BPIC models. Those are approximated by sampling and binning into an
`arbitraryFiniteProbabilityDistribution`, which preserves the tail shape.
Moment-matching to a normal is available but is never the default: these tails
are long and a normal flattens them.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Sequence
from xml.etree import ElementTree as ET

BSIM = "http://bsim.hpi.uni-potsdam.de/scylla/simModel"

# Prosimos works in seconds throughout, so every duration and arrival rate is
# emitted with this unit. Scylla requires the attribute -- a missing timeUnit
# is a NullPointerException in the parser, not a validation error.
TIME_UNIT = "SECONDS"

# Families Scylla can represent exactly.
NATIVE_FAMILIES = frozenset({"fix", "expon", "norm", "uniform"})
# Families that have to be discretised.
APPROXIMATED_FAMILIES = frozenset({"lognorm", "gamma"})

DEFAULT_BUCKETS = 100
DEFAULT_DRAWS = 20_000


def _q(tag: str) -> str:
    return f"{{{BSIM}}}{tag}"


def values_of(dist: Dict[str, Any]) -> List[float]:
    """Unwrap Simod's [{"value": x}, ...] parameter encoding."""
    return [p["value"] for p in dist["distribution_params"]]


def bounds_of(dist: Dict[str, Any]) -> tuple[float | None, float | None]:
    """The (min, max) Simod recorded, where the family carries them.

    Prosimos rejects and redraws out-of-range values, so these are the support
    of a truncated distribution, not clipping limits. `draw_clipped` reproduces
    that; T3 measures the result against pix-framework's own sampler.
    """
    name = dist["distribution_name"]
    p = values_of(dist)
    if name == "expon":
        return p[1], p[2]
    if name in ("norm", "lognorm", "gamma"):
        return p[2], p[3]
    if name == "uniform":
        return p[0], p[1]
    return None, None


def sample_once(dist: Dict[str, Any], rng: random.Random) -> float:
    """One draw from a Simod distribution, in seconds."""
    name = dist["distribution_name"]
    p = values_of(dist)

    if name == "fix":
        return p[0]
    if name == "expon":
        mean = p[0]
        return rng.expovariate(1.0 / mean) if mean > 0 else 0.0
    if name == "norm":
        return rng.gauss(p[0], p[1])
    if name == "uniform":
        return rng.uniform(p[0], p[1])

    if name in APPROXIMATED_FAMILIES:
        mean, var = p[0], p[1]
        if mean <= 0 or var <= 0:
            return max(mean, 0.0)
        if name == "lognorm":
            mu = math.log(mean**2 / math.sqrt(var + mean**2))
            sigma = math.sqrt(math.log(1.0 + var / mean**2))
            return rng.lognormvariate(mu, sigma)
        # gamma: Simod stores (mean, variance); Python wants (shape, scale)
        return rng.gammavariate(mean**2 / var, var / mean)

    raise ValueError(f"unsupported distribution family: {name!r}")


# Give up rather than spin if the bounds make draws almost impossible.
_MAX_REJECTION_ROUNDS = 200


def draw_clipped(dist: Dict[str, Any], rng: random.Random, n: int) -> List[float]:
    """n draws, truncated to the recorded bounds the way Prosimos truncates.

    Prosimos *rejects and redraws* out-of-range values
    (`probability_distributions.evaluate_distribution_function`, and
    `DurationDistribution.generate_sample` in pix-framework), so the result is
    the conditional distribution given the bounds. Clipping instead -- pinning
    out-of-range draws to the nearest bound -- piles mass onto the endpoints and
    is a materially different distribution.

    It matters here because the bounds are tight. On the representative
    BPIC 2012 gamma, 39% of raw draws fall outside [4500, 17820]: 33% below and
    6% above. Clipping gave a mean of 8157 s against Prosimos's 9134 s, an 11%
    understatement that no amount of bucket resolution would have fixed.

    The name is kept for compatibility; the behaviour is rejection sampling.
    """
    lo, hi = bounds_of(dist)
    out: List[float] = []

    if lo is None:
        for _ in range(n):
            out.append(max(sample_once(dist, rng), 0.0))
        return out

    rounds = 0
    while len(out) < n and rounds < _MAX_REJECTION_ROUNDS:
        for _ in range(n - len(out)):
            v = sample_once(dist, rng)
            if v >= 0.0 and lo <= v <= hi:
                out.append(v)
        rounds += 1

    if len(out) < n:
        # Degenerate bounds (or a distribution that cannot reach them): fall
        # back to clipping so the caller still gets a usable sample, rather
        # than looping forever or returning short.
        while len(out) < n:
            out.append(min(max(sample_once(dist, rng), lo), hi))

    return out


def weighted_mixture(
    resources: Sequence[Dict[str, Any]],
    weights: Sequence[float] | None,
    rng: random.Random,
    n_draws: int = DEFAULT_DRAWS,
) -> List[float]:
    """Collapse a task's per-resource distributions into one sample set.

    Scylla gives an activity a single duration, so the per-resource
    distributions Simod discovered have to be pooled. Draws are allocated in
    proportion to `weights` (how much work each resource actually does), which
    matters because the resources are highly heterogeneous -- the median
    slowest-to-fastest ratio is 965x in BPIC 2012 and 551x in BPIC 2017.

    Weighting is the point: in Prosimos a fast resource finishes sooner, becomes
    available again, and so takes a disproportionate share of the work. An
    unweighted pool overstates the mean. Pass weights=None only to reproduce
    that unweighted behaviour deliberately.
    """
    if not resources:
        raise ValueError("cannot build a mixture from zero resources")

    if weights is None:
        weights = [1.0] * len(resources)
    total = float(sum(weights))
    if total <= 0:
        weights = [1.0] * len(resources)
        total = float(len(resources))

    samples: List[float] = []
    for res, w in zip(resources, weights):
        share = max(1, round(n_draws * w / total))
        samples.extend(draw_clipped(res, rng, share))
    return samples


def append_histogram(parent: ET.Element, samples: Sequence[float],
                     buckets: int = DEFAULT_BUCKETS) -> ET.Element:
    """Emit samples as an arbitraryFiniteProbabilityDistribution.

    Scylla backs this with DiscreteDistEmpirical (`SimulationUtils.java:304`),
    a genuinely discrete distribution over the values given -- so the emitted
    values are the only durations that can ever occur.

    Buckets are equal-*frequency* (quantile), not equal-width. These durations
    have very long tails: on the largest BPIC 2012 activity the maximum is 218x
    the median, so equal-width bucketing spends its whole range on outliers.
    Measured there, equal-width bucketing left only 32 of 100 buckets occupied
    and overstated the mean by 16%; equal-frequency reproduces it to well under
    1% at the same bucket count. Each bucket instead carries the mean of the
    samples inside it, which makes the overall mean exact by construction.

    Frequencies are normalised by the parser
    (`SimulationConfigurationParser.java:292`), so raw counts are fine.
    """
    if not samples:
        raise ValueError("cannot build a histogram from zero samples")

    el = ET.SubElement(parent, _q("arbitraryFiniteProbabilityDistribution"))

    ordered = sorted(samples)
    if ordered[-1] - ordered[0] < 1e-9:
        ET.SubElement(el, _q("entry"), value=f"{ordered[0]:.6f}", frequency="1")
        return el

    n = len(ordered)
    buckets = max(1, min(buckets, n))

    # Averaging inside a bucket loses the extreme tail: the largest sample gets
    # blended into its bucket's mean, and on the largest BPIC 2012 activity that
    # pulled the maximum from 36448 s down to 3765 s at 100 buckets. Queueing is
    # driven by exactly those long services, so the top tail is emitted sample
    # by sample instead of averaged.
    tail = min(max(buckets // 10, 1), n)
    body, extremes = ordered[: n - tail], ordered[n - tail:]

    # Accumulate into {value -> frequency} before emitting. Scylla parses these
    # into a HashMap<Double, Double> with entries.put(value, frequency)
    # (`EmpiricalDistribution.java:11`), which *overwrites* rather than adds, so
    # two entries sharing a value silently collapse to one and the mass of the
    # first is lost. That is easy to hit: a pool of resources with identical
    # fixed durations produces identical bucket means. Measured on a 38-resource
    # BPIC 2012 activity, 100 emitted entries became 75 and the mean fell from
    # 1095 s to 556 s. Merging here keeps the total mass intact.
    merged: Dict[float, int] = {}

    def add(value: float, count: int) -> None:
        key = round(value, 6)          # the precision the XML is written at
        merged[key] = merged.get(key, 0) + count

    if body:
        body_buckets = max(1, buckets - tail)
        edges = [round(i * len(body) / body_buckets)
                 for i in range(body_buckets + 1)]
        for start, stop in zip(edges[:-1], edges[1:]):
            if stop <= start:
                continue
            group = body[start:stop]
            add(sum(group) / len(group), len(group))

    for value in extremes:
        add(value, 1)

    for value, count in sorted(merged.items()):
        ET.SubElement(el, _q("entry"), value=f"{value:.6f}", frequency=str(count))

    return el


def append_native(parent: ET.Element, dist: Dict[str, Any]) -> ET.Element:
    """Emit one of the four families Scylla represents exactly.

    Every index here is the one the Simod files actually use; see the module
    docstring for what SimuBridge gets wrong.
    """
    name = dist["distribution_name"]
    p = values_of(dist)

    if name == "fix":
        el = ET.SubElement(parent, _q("constantDistribution"))
        ET.SubElement(el, _q("constantValue")).text = f"{p[0]:.6f}"
    elif name == "expon":
        el = ET.SubElement(parent, _q("exponentialDistribution"))
        ET.SubElement(el, _q("mean")).text = f"{p[0]:.6f}"
    elif name == "norm":
        el = ET.SubElement(parent, _q("normalDistribution"))
        ET.SubElement(el, _q("mean")).text = f"{p[0]:.6f}"
        ET.SubElement(el, _q("standardDeviation")).text = f"{p[1]:.6f}"
    elif name == "uniform":
        el = ET.SubElement(parent, _q("uniformDistribution"))
        ET.SubElement(el, _q("lower")).text = f"{p[0]:.6f}"
        ET.SubElement(el, _q("upper")).text = f"{p[1]:.6f}"
    else:
        raise ValueError(f"{name!r} is not natively supported by Scylla")
    return el


def append_distribution(
    parent: ET.Element,
    dist: Dict[str, Any],
    rng: random.Random,
    buckets: int = DEFAULT_BUCKETS,
    n_draws: int = DEFAULT_DRAWS,
) -> ET.Element:
    """Emit a single Simod distribution, choosing native or discretised."""
    if dist["distribution_name"] in NATIVE_FAMILIES:
        return append_native(parent, dist)
    return append_histogram(parent, draw_clipped(dist, rng, n_draws), buckets)


def append_pooled_duration(
    parent: ET.Element,
    resources: Sequence[Dict[str, Any]],
    weights: Sequence[float] | None,
    rng: random.Random,
    buckets: int = DEFAULT_BUCKETS,
    n_draws: int = DEFAULT_DRAWS,
) -> ET.Element:
    """Emit the duration for a task whose resources have been pooled.

    A single resource in a natively supported family is passed through exactly;
    anything else goes through the weighted mixture and is discretised.
    """
    if len(resources) == 1 and resources[0]["distribution_name"] in NATIVE_FAMILIES:
        return append_native(parent, resources[0])
    return append_histogram(
        parent, weighted_mixture(resources, weights, rng, n_draws), buckets
    )
