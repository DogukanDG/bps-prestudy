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

So resources are pooled per activity: one `dynamicResource` with
`defaultQuantity = N`, holding each real resource as a named `<instance>` that
keeps its own timetable. That preserves per-resource *calendars* -- which is
what keeps `is_resource_calendars` and `is_resource_numbers` meaningful -- and
sacrifices only per-resource *durations*, which collapse into the weighted
mixture built in build_sim_config.

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
    """Pool identifier for an activity. Kept deterministic and collision-free:
    the full task id is used, since Scylla only requires uniqueness."""
    return f"pool_{task_id}"


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
    for task in model["task_resource_distribution"]:
        _append_pool(res_data, task, res_to_cal, res_to_cost, calendars)

    timetables = ET.SubElement(root, _q("timetables"))
    for cal in model.get("resource_calendars", []):
        _append_timetable(timetables, cal)

    return root


def _append_pool(parent, task, res_to_cal, res_to_cost, calendars) -> ET.Element:
    """One resource pool for one activity, with its members as instances."""
    resource_ids = [r["resource_id"] for r in task["resources"]]
    pool = pool_id_for(task["task_id"])

    costs = [res_to_cost.get(rid, 0.0) for rid in resource_ids]
    default_cost = f"{(sum(costs) / len(costs)) if costs else 0.0:.6f}"

    el = ET.SubElement(parent, _q("dynamicResource"), {
        "id": pool,
        "name": pool,
        "defaultQuantity": str(len(resource_ids)),
        "defaultCost": default_cost,
        "defaultTimeUnit": DEFAULT_COST_TIME_UNIT,
    })

    for rid in resource_ids:
        attrs = {"name": f"{pool}__{rid}"}
        cal = res_to_cal.get(rid)
        if cal in calendars:
            # Per-instance timetable: this is what survives pooling.
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
    """activity id -> the resource ids pooled under it.

    Exposed so build_sim_config and the validation pass can check that what
    was written to the global config is what the simulation config references.
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
    expected = {pool_id_for(t["task_id"]) for t in model["task_resource_distribution"]}
    missing = expected - pools
    if missing:
        raise ValueError(f"resource pools missing from global config: {sorted(missing)}")

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
