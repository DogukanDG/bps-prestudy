"""
Spike converter: Simod parameters.json + BPMN -> Scylla global/sim config XML.

Deliberately minimal. Its only job is to answer the Phase 1 questions:
  1. does Scylla accept a BPMN produced by Simod?
  2. which KPIs does Scylla report directly?
  3. how long does one simulation take?

Not the real adapter. It hard-codes the pooling strategy, ignores replication,
and skips validation. Everything it does is documented in SCYLLA_PROPOSAL.md;
the real implementation replaces it.

Usage:
    python build_spike_config.py --dataset bpic2012 --cases 3000
"""

import argparse
import json
import math
import random
from pathlib import Path
from xml.etree import ElementTree as ET

BSIM = "http://bsim.hpi.uni-potsdam.de/scylla/simModel"
BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

DATASETS = {
    "bpic2012": (
        "example_sensitivity_analysis_inputs/BPIC_2012/BPIC_2012_train.bpmn",
        "example_sensitivity_analysis_inputs/BPIC_2012/BPIC_2012_train.json",
    ),
    "bpic2017": (
        "example_sensitivity_analysis_inputs/BPIC_2017/BPIC_2017_train.bpmn",
        "example_sensitivity_analysis_inputs/BPIC_2017/BPIC_2017_train.json",
    ),
}

# Prosimos writes everything in seconds; Scylla needs an explicit unit.
TIME_UNIT = "SECONDS"

# Buckets for the discretised lognorm/gamma approximation (proposal section 2.3).
N_BUCKETS = 100
N_DRAWS = 20000


def q(tag):
    return f"{{{BSIM}}}{tag}"


def params(dist):
    return [p["value"] for p in dist["distribution_params"]]


def sample_family(name, p, rng):
    """Draw one value from the Simod distribution. Parameter order verified
    against the model JSON files, NOT from SimuBridge (see proposal section 4)."""
    if name == "fix":
        return p[0]
    if name == "expon":
        # [mean, min, max]
        return rng.expovariate(1.0 / p[0]) if p[0] > 0 else 0.0
    if name == "norm":
        # [mean, std, min, max]
        return rng.gauss(p[0], p[1])
    if name == "uniform":
        # [min, max]
        return rng.uniform(p[0], p[1])
    if name in ("lognorm", "gamma"):
        # [mean, var, min, max]
        mean, var = p[0], p[1]
        if mean <= 0 or var <= 0:
            return max(mean, 0.0)
        if name == "lognorm":
            mu = math.log(mean**2 / math.sqrt(var + mean**2))
            sigma = math.sqrt(math.log(1 + var / mean**2))
            return rng.lognormvariate(mu, sigma)
        shape = mean**2 / var
        scale = var / mean
        return rng.gammavariate(shape, scale)
    raise ValueError(f"unsupported distribution: {name}")


def clip_bounds(name, p):
    """min/max, where Simod records them."""
    if name in ("expon",):
        return p[1], p[2]
    if name in ("norm", "lognorm", "gamma"):
        return p[2], p[3]
    if name == "uniform":
        return p[0], p[1]
    return None, None


def mixture_samples(resources, rng, n=N_DRAWS):
    """Pool a task's per-resource distributions into one sample set.

    Unweighted: the spike only needs a plausible duration, not the final
    aggregation rule. Load weighting is a Phase 2 decision.
    """
    out = []
    per = max(1, n // max(1, len(resources)))
    for r in resources:
        name = r["distribution_name"]
        p = params(r)
        lo, hi = clip_bounds(name, p)
        for _ in range(per):
            v = sample_family(name, p, rng)
            if lo is not None:
                v = min(max(v, lo), hi)
            out.append(max(v, 0.0))
    return out


def histogram_element(parent, samples):
    """Emit arbitraryFiniteProbabilityDistribution over bucket centres.

    Scylla normalises frequencies itself (SimulationConfigurationParser.java:292),
    so raw counts are fine.
    """
    lo, hi = min(samples), max(samples)
    el = ET.SubElement(parent, q("arbitraryFiniteProbabilityDistribution"))
    if hi - lo < 1e-9:
        ET.SubElement(el, q("entry"), value=f"{lo:.6f}", frequency="1")
        return el
    width = (hi - lo) / N_BUCKETS
    counts = [0] * N_BUCKETS
    for v in samples:
        i = min(int((v - lo) / width), N_BUCKETS - 1)
        counts[i] += 1
    for i, c in enumerate(counts):
        if c:
            centre = lo + width * (i + 0.5)
            ET.SubElement(el, q("entry"), value=f"{centre:.6f}", frequency=str(c))
    return el


def simple_distribution(parent, name, p):
    """Direct mapping for the families Scylla supports natively."""
    if name == "fix":
        el = ET.SubElement(parent, q("constantDistribution"))
        ET.SubElement(el, q("constantValue")).text = f"{p[0]:.6f}"
    elif name == "expon":
        el = ET.SubElement(parent, q("exponentialDistribution"))
        ET.SubElement(el, q("mean")).text = f"{p[0]:.6f}"       # p0, not p1
    elif name == "norm":
        el = ET.SubElement(parent, q("normalDistribution"))
        ET.SubElement(el, q("mean")).text = f"{p[0]:.6f}"
        ET.SubElement(el, q("standardDeviation")).text = f"{p[1]:.6f}"  # already std
    elif name == "uniform":
        el = ET.SubElement(parent, q("uniformDistribution"))
        ET.SubElement(el, q("lower")).text = f"{p[0]:.6f}"
        ET.SubElement(el, q("upper")).text = f"{p[1]:.6f}"      # not p1+p0
    else:
        raise ValueError(name)
    return el


def build_global_config(model, seed, out_path):
    """Resource pooling with per-instance calendars (proposal section 3.1)."""
    root = ET.Element(q("globalConfiguration"), {
        "targetNamespace": "http://www.hpi.de",
        "id": "spike_global",
    })
    ET.SubElement(root, q("randomSeed")).text = str(seed)
    ET.SubElement(root, q("zoneOffset")).text = "+02:00"

    # One pool per task, holding that task's resources as named instances.
    res_data = ET.SubElement(root, q("resourceData"))
    calendars = {c["id"]: c for c in model["resource_calendars"]}
    res_to_cal = {}
    for prof in model["resource_profiles"]:
        for r in prof["resource_list"]:
            res_to_cal[r["id"]] = r["calendar"]

    pools = {}
    for task in model["task_resource_distribution"]:
        pool_id = f"pool_{task['task_id']}"
        ids = [r["resource_id"] for r in task["resources"]]
        pools[task["task_id"]] = pool_id
        dr = ET.SubElement(res_data, q("dynamicResource"), {
            "id": pool_id,
            "name": pool_id,
            "defaultQuantity": str(len(ids)),
            "defaultCost": "0.0",
            "defaultTimeUnit": "HOURS",
        })
        for rid in ids:
            attrs = {"name": f"{pool_id}__{rid}"}
            cal = res_to_cal.get(rid)
            if cal in calendars:
                attrs["timetableId"] = cal
            ET.SubElement(dr, q("instance"), attrs)

    tts = ET.SubElement(root, q("timetables"))
    for cal in model["resource_calendars"]:
        tt = ET.SubElement(tts, q("timetable"), id=cal["id"])
        for tp in cal["time_periods"]:
            # No rounding: Scylla parses HH:MM:SS directly (proposal section 4).
            ET.SubElement(tt, q("timetableItem"), {
                "from": tp["from"], "to": tp["to"],
                "beginTime": tp["beginTime"], "endTime": tp["endTime"],
            })

    write_xml(root, out_path)
    return pools


def build_sim_config(model, bpmn_path, pools, cases, start_iso, seed, out_path):
    process_id, gateway_types = read_bpmn(bpmn_path)
    rng = random.Random(seed)

    root = ET.Element(q("definitions"), {"targetNamespace": "http://www.hpi.de"})
    sim = ET.SubElement(root, q("simulationConfiguration"), {
        "id": "spike_sim",
        "processRef": process_id,
        "processInstances": str(cases),
        "startDateTime": start_iso,
    })

    for task in model["task_resource_distribution"]:
        tid = task["task_id"]
        t_el = ET.SubElement(sim, q("task"), id=tid)
        dur = ET.SubElement(t_el, q("duration"), timeUnit=TIME_UNIT)

        names = {r["distribution_name"] for r in task["resources"]}
        if len(task["resources"]) == 1 and names <= {"fix", "expon", "norm", "uniform"}:
            r = task["resources"][0]
            simple_distribution(dur, r["distribution_name"], params(r))
        else:
            # Pooled and/or unsupported family -> discretise the mixture.
            histogram_element(dur, mixture_samples(task["resources"], rng))

        res = ET.SubElement(t_el, q("resources"))
        ET.SubElement(res, q("resource"), id=pools[tid], amount="1")

    # Gateways: tag name follows the BPMN element type.
    for gw in model["gateway_branching_probabilities"]:
        gid = gw["gateway_id"]
        kind = gateway_types.get(gid, "exclusiveGateway")
        if kind != "exclusiveGateway":
            continue  # parallel gateways carry no probabilities
        g_el = ET.SubElement(sim, q(kind), id=gid)
        for pr in gw["probabilities"]:
            f = ET.SubElement(g_el, q("outgoingSequenceFlow"), id=pr["path_id"])
            ET.SubElement(f, q("branchingProbability")).text = f"{pr['value']:.6f}"

    # Start event + arrival rate (mandatory, else the parser throws).
    start_id = find_start_event(bpmn_path)
    se = ET.SubElement(sim, q("startEvent"), id=start_id)
    ar = ET.SubElement(se, q("arrivalRate"), timeUnit=TIME_UNIT)
    ad = model["arrival_time_distribution"]
    if ad["distribution_name"] in ("fix", "expon", "norm", "uniform"):
        simple_distribution(ar, ad["distribution_name"], params(ad))
    else:
        p = params(ad)
        lo, hi = clip_bounds(ad["distribution_name"], p)
        draws = []
        for _ in range(N_DRAWS):
            v = sample_family(ad["distribution_name"], p, rng)
            if lo is not None:
                v = min(max(v, lo), hi)
            draws.append(max(v, 0.0))
        histogram_element(ar, draws)

    write_xml(root, out_path)


def read_bpmn(path):
    tree = ET.parse(path)
    root = tree.getroot()
    proc = root.find(f"{{{BPMN_NS}}}process")
    types = {}
    for tag in ("exclusiveGateway", "parallelGateway", "inclusiveGateway"):
        for el in proc.findall(f"{{{BPMN_NS}}}{tag}"):
            types[el.get("id")] = tag
    return proc.get("id"), types


def find_start_event(path):
    root = ET.parse(path).getroot()
    proc = root.find(f"{{{BPMN_NS}}}process")
    return proc.find(f"{{{BPMN_NS}}}startEvent").get("id")


def write_xml(root, path):
    ET.register_namespace("bsim", BSIM)
    ET.indent(root, space="  ")
    Path(path).write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode"),
        encoding="utf-8",
    )
    print(f"wrote {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=DATASETS, default="bpic2012")
    ap.add_argument("--cases", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=100)
    ap.add_argument("--start", default="2023-01-01T00:00:00+02:00")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    bpmn_rel, json_rel = DATASETS[a.dataset]
    bpmn = REPO / bpmn_rel
    model = json.loads((REPO / json_rel).read_text(encoding="utf-8"))

    out = Path(a.out) if a.out else HERE / a.dataset
    out.mkdir(parents=True, exist_ok=True)

    pools = build_global_config(model, a.seed, out / "global_config.xml")
    build_sim_config(model, bpmn, pools, a.cases, a.start, a.seed,
                     out / "sim_config.xml")

    # Scylla resolves --bpmn by path; copy it next to the configs for convenience.
    (out / "model.bpmn").write_text(bpmn.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"\nready: {out}")
    print(f"  tasks={len(model['task_resource_distribution'])} "
          f"gateways={len(model['gateway_branching_probabilities'])} "
          f"cases={a.cases}")


if __name__ == "__main__":
    main()
