"""
Simod parameters.json -> Scylla global configuration XML.

The global config carries resources, calendars, the random seed and the time
zone. The structural decision here is how to represent resources.

Simod discovered these models in *differentiated* mode: every resource is its
own profile with its own duration distribution per activity. Scylla cannot
express that -- an activity has one duration, and the resources listed under it
are all required *simultaneously* (`QueueManager.java:154-172` blocks unless
every listed resource is available). Writing the profiles directly would
deadlock.

So every resource goes into one shared `dynamicResource` with
`defaultQuantity = N`, each as a named `<instance>` keeping its own timetable.
What survives and what does not:

    kept   per-resource calendars, so is_resource_calendars stays meaningful
    kept   total capacity, so contention between activities is real
    lost   per-resource durations -- pooled into a weighted mixture per
           activity (build_sim_config)
    lost   eligibility, which resources may perform which activity

Pooling per activity instead was the first attempt and is wrong: a resource
that works on four activities becomes four independent instances. Capacity came
out 4.1x too high on both models (191 vs 47 on BPIC 2012, 433 vs 105 on
BPIC 2017), because 91% and 96% of their resources appear in more than one
activity. T1 caught it -- see the note on SHARED_POOL_ID below.

Requires a Scylla build that includes commit f9671cb ("Fix #72: default
timetables for named resource instances are ignored"). The copy bundled with
SimuBridge predates it and silently ignores the per-instance timetables this
strategy depends on.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence
from xml.etree import ElementTree as ET

from .distributions import BSIM

# Mandatory attributes on dynamicResource -- the parser calls Integer.valueOf /
# Double.valueOf / TimeUnit.valueOf on them without a null check.
DEFAULT_COST = "0.0"
DEFAULT_COST_TIME_UNIT = "HOURS"


def _q(tag: str) -> str:
    return f"{{{BSIM}}}{tag}"


def pool_id_for(task_id: str) -> str:
    """Pool identifier for an activity.

    Deprecated: kept only so older per-activity output can still be read. New
    configurations share one pool across all activities (see `SHARED_POOL_ID`).
    """
    return f"pool_{task_id}"


# One pool for the whole process, holding every resource once.
#
# Pooling per activity looked natural but silently multiplies capacity: a
# resource that works on four activities became four independent instances, so
# total capacity was 191 instead of 47 on BPIC 2012 and 433 instead of 105 on
# BPIC 2017 -- 4.1x in both. 91% of BPIC 2012 resources and 96% of BPIC 2017
# resources appear in more than one activity, so almost none of the contention
# between activities survived.
#
# T1 exposed it: with one resource and two concurrently-enabled activities,
# Prosimos serialised them (120 s) while Scylla ran them at once (60 s).
# A single shared pool restores the contention, at the cost of losing which
# resources are eligible for which activity -- Scylla has no way to express
# both. Eligibility already had to be given up for durations (they are pooled
# per activity); this extends the same compromise to availability, and keeps
# the capacity right, which is what queueing depends on.
SHARED_POOL_ID = "resource_pool"


def resource_calendar_map(model: Dict[str, Any]) -> Dict[str, str]:
    """resource id -> calendar id, flattened across all profiles."""
    out: Dict[str, str] = {}
    for profile in model.get("resource_profiles", []):
        for res in profile.get("resource_list", []):
            out[res["id"]] = res.get("calendar")
    return out


def resource_cost_map(model: Dict[str, Any]) -> Dict[str, float]:
    """resource id -> cost per hour."""
    out: Dict[str, float] = {}
    for profile in model.get("resource_profiles", []):
        for res in profile.get("resource_list", []):
            out[res["id"]] = float(res.get("cost_per_hour", 0.0) or 0.0)
    return out


def build_global_config(
    model: Dict[str, Any],
    seed: int,
    zone_offset: str = "+02:00",
) -> ET.Element:
    """Build the globalConfiguration element.

    `zone_offset` must agree with the offset in the simulation's start
    timestamp; Prosimos runs at +02:00 by default (`simulate_samples.py:30`).
    """
    root = ET.Element(_q("globalConfiguration"), {
        "targetNamespace": "http://www.hpi.de",
        "id": "bps_global",
    })

    # Without an explicit seed Scylla draws its own, and the same configuration
    # would give different answers on every run -- unusable for a sensitivity
    # analysis. Note SimulationManager.java:127 reads only this global seed and
    # ignores any per-simulationConfiguration randomSeed attribute.
    ET.SubElement(root, _q("randomSeed")).text = str(int(seed))
    ET.SubElement(root, _q("zoneOffset")).text = zone_offset

    calendars = {c["id"]: c for c in model.get("resource_calendars", [])}
    res_to_cal = resource_calendar_map(model)
    res_to_cost = resource_cost_map(model)

    res_data = ET.SubElement(root, _q("resourceData"))
    _append_shared_pool(res_data, model, res_to_cal, res_to_cost, calendars)

    timetables = ET.SubElement(root, _q("timetables"))
    for cal in model.get("resource_calendars", []):
        _append_timetable(timetables, cal)

    return root


def all_resource_ids(model: Dict[str, Any]) -> List[str]:
    """Every distinct resource in the model, in a stable order.

    Taken from resource_profiles rather than task_resource_distribution: the
    latter lists a resource once per activity it can perform, and counting
    those repeats is exactly the capacity inflation this pooling avoids.
    """
    seen: Dict[str, None] = {}
    for profile in model.get("resource_profiles", []):
        for res in profile.get("resource_list", []):
            seen.setdefault(res["id"], None)
    if seen:
        return list(seen)

    # Fall back to the distributions if no profiles are declared.
    for task in model["task_resource_distribution"]:
        for res in task["resources"]:
            seen.setdefault(res["resource_id"], None)
    return list(seen)


def _append_shared_pool(parent, model, res_to_cal, res_to_cost,
                        calendars) -> ET.Element:
    """One pool holding every resource once, each keeping its own calendar."""
    resource_ids = all_resource_ids(model)

    costs = [res_to_cost.get(rid, 0.0) for rid in resource_ids]
    default_cost = f"{(sum(costs) / len(costs)) if costs else 0.0:.6f}"

    el = ET.SubElement(parent, _q("dynamicResource"), {
        "id": SHARED_POOL_ID,
        "name": SHARED_POOL_ID,
        "defaultQuantity": str(len(resource_ids)),
        "defaultCost": default_cost,
        "defaultTimeUnit": DEFAULT_COST_TIME_UNIT,
    })

    for rid in resource_ids:
        attrs = {"name": f"{SHARED_POOL_ID}__{rid}"}
        cal = res_to_cal.get(rid)
        if cal in calendars:
            # Per-instance timetable: this is what survives pooling, and it is
            # why is_resource_calendars stays meaningful on the Scylla side.
            attrs["timetableId"] = cal
        cost = res_to_cost.get(rid)
        if cost is not None:
            attrs["cost"] = f"{cost:.6f}"
        ET.SubElement(el, _q("instance"), attrs)

    return el


def _append_timetable(parent, calendar) -> ET.Element:
    """One Prosimos calendar as a Scylla timetable.

    Field names line up almost exactly. Times are passed through unrounded --
    Scylla parses HH:MM:SS via LocalTime.parse. (SimuBridge rounds to whole
    hours here, which is its own limitation, not Scylla's.)
    """
    tt = ET.SubElement(parent, _q("timetable"), id=calendar["id"])
    for period in calendar.get("time_periods", []):
        ET.SubElement(tt, _q("timetableItem"), {
            "from": period["from"],
            "to": period["to"],
            "beginTime": period["beginTime"],
            "endTime": period["endTime"],
        })
    return tt


def pool_members(model: Dict[str, Any]) -> Dict[str, List[str]]:
    """activity id -> the resource ids that can perform it.

    Records the eligibility Simod discovered. Scylla cannot express it -- every
    activity draws from the one shared pool -- but it is what the duration
    mixture for each activity is built from.
    """
    return {
        task["task_id"]: [r["resource_id"] for r in task["resources"]]
        for task in model["task_resource_distribution"]
    }


def validate_global_config(root: ET.Element, model: Dict[str, Any]) -> None:
    """Fail loudly if the emitted XML lost something.

    Scylla never errors on XML it does not recognise -- it logs and skips
    (`GlobalConfigurationParser.java:207`). Silent drops are therefore the
    default failure mode, and the converter has to do its own checking.
    """
    pools = {el.get("id") for el in root.iter(_q("dynamicResource"))}
    if SHARED_POOL_ID not in pools:
        raise ValueError(f"shared resource pool {SHARED_POOL_ID!r} missing")

    # Capacity must equal the number of real resources; anything larger means
    # a resource was counted once per activity it can perform.
    pool = next(el for el in root.iter(_q("dynamicResource"))
                if el.get("id") == SHARED_POOL_ID)
    expected_capacity = len(all_resource_ids(model))
    if int(pool.get("defaultQuantity")) != expected_capacity:
        raise ValueError(
            f"pool capacity {pool.get('defaultQuantity')} does not match the "
            f"{expected_capacity} resources in the model"
        )

    declared = {tt.get("id") for tt in root.iter(_q("timetable"))}
    referenced = {
        inst.get("timetableId")
        for inst in root.iter(_q("instance"))
        if inst.get("timetableId")
    }
    dangling = referenced - declared
    if dangling:
        raise ValueError(f"instances reference undeclared timetables: {sorted(dangling)}")

    for el in root.iter(_q("dynamicResource")):
        quantity = int(el.get("defaultQuantity"))
        instances = len(el.findall(_q("instance")))
        # Scylla throws if instances exceed defaultQuantity
        # (GlobalConfigurationParser.java:119-122).
        if instances > quantity:
            raise ValueError(
                f"pool {el.get('id')} declares quantity {quantity} "
                f"but lists {instances} instances"
            )
