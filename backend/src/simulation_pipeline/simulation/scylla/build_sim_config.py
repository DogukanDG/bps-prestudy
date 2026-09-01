"""
Simod parameters.json + BPMN -> Scylla simulation configuration XML.

Carries activity durations, gateway branching, the arrival rate and the case
count. Resources live in the global config; this file only references the pools
that build_global_config created.

Two Scylla behaviours shape the code:

  - Unknown elements are logged and skipped, never rejected
    (`SimulationConfigurationParser.java:245-252`). A typo produces a
    simulation that runs and reports wrong numbers, so validate_sim_config()
    checks the emitted tree rather than trusting it.
  - At least one startEvent carrying an arrivalRate is mandatory
    (`SimulationConfigurationParser.java:98-110`); without it the parser throws.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Dict, List, Sequence
from xml.etree import ElementTree as ET

from . import distributions as D
from .build_global_config import SHARED_POOL_ID

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"

# Scylla reads branching probabilities for these; parallel gateways carry none.
PROBABILISTIC_GATEWAYS = {"exclusiveGateway", "inclusiveGateway"}


def _q(tag: str) -> str:
    return f"{{{D.BSIM}}}{tag}"


def _b(tag: str) -> str:
    return f"{{{BPMN_NS}}}{tag}"


def read_bpmn(bpmn_path: str | Path) -> Dict[str, Any]:
    """Pull the few things the simulation config needs out of the BPMN.

    Returns the process id, the gateway element types (so each gateway gets the
    right Scylla tag) and the start event ids.
    """
    root = ET.parse(str(bpmn_path)).getroot()
    process = root.find(_b("process"))
    if process is None:
        raise ValueError(f"no <process> element in {bpmn_path}")

    gateway_types: Dict[str, str] = {}
    for tag in ("exclusiveGateway", "inclusiveGateway", "parallelGateway",
                "eventBasedGateway"):
        for el in process.findall(_b(tag)):
            gateway_types[el.get("id")] = tag

    start_events = [el.get("id") for el in process.findall(_b("startEvent"))]
    if not start_events:
        raise ValueError(f"no <startEvent> in {bpmn_path}")

    return {
        "process_id": process.get("id"),
        "gateway_types": gateway_types,
        "start_events": start_events,
    }


# Retained only so `weighted=True` still means something specific if anyone
# re-enables it; the default is off. See resource_weights().
MAX_WEIGHT_RATIO = 20.0


def resource_weights(
    task: Dict[str, Any],
    rng: random.Random,
    max_ratio: float = MAX_WEIGHT_RATIO,
) -> List[float]:
    """Throughput-proportional weights: 1 / mean duration, ratio-capped.

    **Not used by default, because the assumption behind it is false.**

    The reasoning was that a faster resource finishes sooner, frees up sooner,
    and so takes on more work -- meaning a pooled duration should be weighted
    towards the fast end. Measured against Prosimos on BPIC 2012 (500 cases,
    one activity with 42 resources), it is not what happens:

        fastest resource (mean    6.7 s) -> 4.8% of executions
        slowest resource (mean 1060.7 s) -> 2.7% of executions

    Near-uniform. Prosimos allocates by availability, not by speed, so the
    correct pooled duration is the *unweighted* mixture. Weighting cut pooled
    durations by 11-65% and made agreement with Prosimos worse, not better.

    Kept because the comparison is worth reporting, and because `weighted=True`
    is how the effect was measured rather than assumed.
    """
    means = [D.values_of(res)[0] for res in task["resources"]]
    positive = [m for m in means if m and m > 0]
    if not positive:
        return [1.0] * len(means)

    fastest = min(positive)
    raw = [1.0 / (m if m and m > 0 else fastest) for m in means]

    ceiling = min(raw) * max_ratio
    return [min(w, ceiling) for w in raw]


def build_sim_config(
    model: Dict[str, Any],
    bpmn_path: str | Path,
    total_cases: int,
    start_iso: str,
    seed: int,
    buckets: int = D.DEFAULT_BUCKETS,
    n_draws: int = D.DEFAULT_DRAWS,
    weighted: bool = False,
    arrival_calendar: bool = True,
) -> ET.Element:
    """Build the definitions/simulationConfiguration tree.

    `weighted=True` weights the pooled duration towards faster resources. Off by
    default: measured against Prosimos, resource selection is near-uniform, so
    weighting moves the pooled duration away from what Prosimos produces. See
    resource_weights().
    """
    bpmn = read_bpmn(bpmn_path)
    rng = random.Random(seed)

    root = ET.Element(_q("definitions"), {"targetNamespace": "http://www.hpi.de"})
    sim = ET.SubElement(root, _q("simulationConfiguration"), {
        "id": "bps_sim",
        "processRef": bpmn["process_id"],
        # The real case count. SimuBridge clamps this to 5000; we do not, and
        # the ceiling is tested empirically instead.
        "processInstances": str(int(total_cases)),
        "startDateTime": start_iso,
    })

    for task in model["task_resource_distribution"]:
        _append_task(sim, task, rng, buckets, n_draws, weighted)

    for gateway in model.get("gateway_branching_probabilities", []):
        _append_gateway(sim, gateway, bpmn["gateway_types"])

    _append_start_event(
        sim, bpmn["start_events"][0], model["arrival_time_distribution"],
        rng, buckets, n_draws,
        arrival_calendar=model.get("arrival_time_calendar") if arrival_calendar else None,
    )

    return root


def _append_task(parent, task, rng, buckets, n_draws, weighted) -> ET.Element:
    el = ET.SubElement(parent, _q("task"), id=task["task_id"])

    duration = ET.SubElement(el, _q("duration"), timeUnit=D.TIME_UNIT)
    weights = resource_weights(task, rng) if weighted else None
    D.append_pooled_duration(duration, task["resources"], weights, rng,
                             buckets, n_draws)

    # One unit of the single shared pool. amount="1" means "any one resource",
    # which is the alternative-resource semantics Prosimos has and Scylla's
    # multi-resource lists do not. Sharing one pool across all activities is
    # what preserves contention between them -- see build_global_config.
    resources = ET.SubElement(el, _q("resources"))
    ET.SubElement(resources, _q("resource"), {
        "id": SHARED_POOL_ID,
        "amount": "1",
    })
    return el


def _append_gateway(parent, gateway, gateway_types) -> ET.Element | None:
    gid = gateway["gateway_id"]
    kind = gateway_types.get(gid, "exclusiveGateway")
    if kind not in PROBABILISTIC_GATEWAYS:
        # Parallel and event-based gateways take no branching probabilities.
        return None

    el = ET.SubElement(parent, _q(kind), id=gid)
    for branch in gateway["probabilities"]:
        flow = ET.SubElement(el, _q("outgoingSequenceFlow"), id=branch["path_id"])
        ET.SubElement(flow, _q("branchingProbability")).text = f"{branch['value']:.6f}"
    return el


def _append_start_event(parent, start_id, arrival, rng, buckets, n_draws,
                       arrival_calendar=None) -> ET.Element:
    el = ET.SubElement(parent, _q("startEvent"), id=start_id)
    rate = ET.SubElement(el, _q("arrivalRate"), timeUnit=D.TIME_UNIT)
    D.append_distribution(rate, arrival, rng, buckets, n_draws)

    # Read by our arrivalCalendar plugin, which defers a case that would arrive
    # outside these windows to the next open one. Stock Scylla ignores the
    # element and releases cases across the whole week, which is the behaviour
    # this exists to correct -- so a run without the plugin still works, just
    # with the original discrepancy.
    if arrival_calendar:
        cal = ET.SubElement(el, _q("arrivalCalendar"))
        for period in arrival_calendar:
            ET.SubElement(cal, _q("timetableItem"), {
                "from": period["from"],
                "to": period["to"],
                "beginTime": period["beginTime"],
                "endTime": period["endTime"],
            })
    return el


def validate_sim_config(root: ET.Element, model: Dict[str, Any],
                        bpmn: Dict[str, Any]) -> None:
    """Fail loudly if the emitted XML lost something.

    Scylla skips what it does not recognise, so "it ran" is never proof that it
    read what was written.
    """
    sim = root.find(_q("simulationConfiguration"))
    if sim is None:
        raise ValueError("no simulationConfiguration element")

    if sim.get("processRef") != bpmn["process_id"]:
        raise ValueError(
            f"processRef {sim.get('processRef')!r} does not match the BPMN "
            f"process id {bpmn['process_id']!r}"
        )

    written_tasks = {el.get("id") for el in sim.findall(_q("task"))}
    expected_tasks = {t["task_id"] for t in model["task_resource_distribution"]}
    if written_tasks != expected_tasks:
        raise ValueError(
            f"task mismatch; missing={sorted(expected_tasks - written_tasks)} "
            f"unexpected={sorted(written_tasks - expected_tasks)}"
        )

    for el in sim.findall(_q("task")):
        duration = el.find(_q("duration"))
        if duration is None or len(duration) == 0:
            raise ValueError(f"task {el.get('id')} has no duration distribution")
        if duration.get("timeUnit") is None:
            # A missing timeUnit is an NPE inside Scylla, not a clean error.
            raise ValueError(f"task {el.get('id')} duration has no timeUnit")

    for kind in PROBABILISTIC_GATEWAYS:
        for el in sim.findall(_q(kind)):
            total = sum(
                float(f.findtext(_q("branchingProbability")))
                for f in el.findall(_q("outgoingSequenceFlow"))
            )
            # Scylla validates exclusive-gateway probabilities sum to (0, 1].
            if kind == "exclusiveGateway" and not 0.0 < total <= 1.0 + 1e-6:
                raise ValueError(
                    f"gateway {el.get('id')} probabilities sum to {total}"
                )

    starts = sim.findall(_q("startEvent"))
    if not starts:
        raise ValueError("no startEvent -- Scylla requires at least one")
    if not any(s.find(_q("arrivalRate")) is not None for s in starts):
        raise ValueError("no startEvent carries an arrivalRate")
