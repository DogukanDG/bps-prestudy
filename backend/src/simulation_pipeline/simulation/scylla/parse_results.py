"""
Scylla output -> the `process_rows` schema the pipeline already speaks.

Scylla's statslogger plugin (`statslogger_nojar`, always on, no configuration)
writes `<global config name>_resourceutilization.xml` next to the other output.
Its process-level `<time>` block carries the aggregates we need, so the XES
event log does not have to be parsed at all:

    <time>
      <flow_time>     min max median Q1 Q3 avg total   -> cycle_time
      <effective>     ...                              -> processing_time
      <waiting>       ...                              -> waiting_time
      <off_timetable> ...                              -> resource-paused time

Mapping to Prosimos, and what it costs:

    cycle_time      = flow_time       start to end, wall clock -- same definition
    processing_time = effective       time actually worked; verified against the
                                      sum of per-activity durations
    waiting_time    = waiting         NOT the same definition, see below

`waiting` is summed per activity instance, not over the case: StatisticsLogger
(:186-195) accumulates every enable -> begin gap, so activities that wait
concurrently are counted more than once. On BPIC 2012 at 3000 cases this makes
the reported waiting total (88.0M s) exceed the flow time total (71.5M s),
which is impossible for a wall-clock measure. Prosimos's waiting_time is
wall-clock per case.

It is emitted anyway, because the sensitivity analysis compares how a metric
*responds* to parameter changes rather than its absolute level, and this is
still a monotone measure of queueing. But it is not comparable to the Prosimos
number case for case, and `check_consistency()` flags runs where the two
definitions diverge most. Sensitivity results for the Scylla arm should lead
with cycle_time.

Prosimos's three idle_* metrics are calendar-aware in a way Scylla does not
reproduce: `idle_time` counts time a case sat while its resource was
off-shift, and `idle_cycle_time` / `idle_processing_time` are cycle and
processing time with those pauses folded back in. Scylla reports
`off_timetable`, which is the closest analogue but is not defined identically.
Rather than pass off an approximation as the real metric, those three are
emitted as NaN and the Scylla arm's sensitivity analysis runs on the three
metrics that do map. This is a documented scope limit, not an oversight; the
proposal records it and the T1 test is where the three mapped metrics get
checked against Prosimos directly.

There is no `count` field in the XML, so it is recovered as total / avg, which
reproduces the case count exactly (verified against the per-instance list).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List
from xml.etree import ElementTree as ET

# Scylla's process-level tag -> the Prosimos metric name it stands for.
METRIC_FROM_TAG = {
    "flow_time": "cycle_time",
    "effective": "processing_time",
    "waiting": "waiting_time",
}

# Reported with NaN: Prosimos defines these against resource calendars in a way
# Scylla's off_timetable does not match. See the module docstring.
UNMAPPED_METRICS = ("idle_cycle_time", "idle_processing_time", "idle_time")

# The full six-metric contract, in the order simulate_sample() emits it.
PROCESS_METRICS = (
    "cycle_time",
    "processing_time",
    "waiting_time",
    "idle_cycle_time",
    "idle_processing_time",
    "idle_time",
)

STATS_SUFFIX = "_resourceutilization.xml"


def find_stats_file(output_dir: str | Path) -> Path:
    """Locate the statslogger output inside a Scylla run directory.

    The name is derived from the global config's filename, so it is matched by
    suffix rather than assumed.
    """
    output_dir = Path(output_dir)
    matches = sorted(output_dir.rglob(f"*{STATS_SUFFIX}"))
    if not matches:
        raise FileNotFoundError(
            f"no *{STATS_SUFFIX} under {output_dir}. Scylla writes it only with "
            f"--enable-bps-logging; without that flag no KPIs are collected."
        )
    return matches[0]


def _stat_block(element: ET.Element) -> Dict[str, float]:
    """Read one <flow_time>/<effective>/... block into min/max/avg/total/count."""
    def num(name: str) -> float:
        text = element.findtext(name)
        if text is None:
            raise ValueError(f"<{element.tag}> has no <{name}>")
        return float(text)

    avg = num("avg")
    total = num("total")
    # Scylla reports no count; total / avg recovers it exactly.
    count = round(total / avg) if avg else 0.0

    return {
        "min": num("min"),
        "max": num("max"),
        "avg": avg,
        "total": total,
        "count": float(count),
    }


def parse_process_rows(
    output_dir: str | Path,
    sample_id: int,
    expected_cases: int | None = None,
) -> List[Dict[str, Any]]:
    """Build the six process_rows for one simulation run.

    `expected_cases` is checked when given: Scylla skips XML it does not
    understand instead of failing, so a converter mistake shows up as a case
    count that does not match what was requested.
    """
    stats_path = find_stats_file(output_dir)
    root = ET.parse(stats_path).getroot()

    process = root.find(".//process")
    if process is None:
        raise ValueError(f"no <process> element in {stats_path}")

    time_block = process.find("time")
    if time_block is None:
        raise ValueError(f"<process> has no <time> block in {stats_path}")

    rows: List[Dict[str, Any]] = []
    stats_by_metric: Dict[str, Dict[str, float]] = {}

    for tag, metric in METRIC_FROM_TAG.items():
        element = time_block.find(tag)
        if element is None:
            raise ValueError(f"<time> has no <{tag}> in {stats_path}")
        stats_by_metric[metric] = _stat_block(element)

    if expected_cases is not None:
        found = stats_by_metric["cycle_time"]["count"]
        if int(found) != int(expected_cases):
            raise ValueError(
                f"Scylla simulated {int(found)} cases but {expected_cases} were "
                f"requested — the configuration was probably not read as written "
                f"({stats_path})"
            )

    for metric in PROCESS_METRICS:
        if metric in stats_by_metric:
            rows.append({"sample_id": sample_id, "metric": metric,
                         **stats_by_metric[metric]})
        else:
            rows.append({
                "sample_id": sample_id, "metric": metric,
                "min": math.nan, "max": math.nan, "avg": math.nan,
                "total": math.nan, "count": math.nan,
            })

    return rows


def parse_activity_stats(output_dir: str | Path) -> Dict[str, Dict[str, float]]:
    """Per-activity durations, keyed by BPMN activity id.

    Not part of the pipeline contract -- nothing downstream reads task rows --
    but this is how a run gets checked against the model it came from, which is
    the only way to catch Scylla having silently skipped part of the config.
    """
    root = ET.parse(find_stats_file(output_dir)).getroot()
    out: Dict[str, Dict[str, float]] = {}
    for activity in root.iter("activity"):
        activity_id = activity.findtext("id")
        duration = activity.find("time/duration")
        if activity_id and duration is not None:
            out[activity_id] = _stat_block(duration)
    return out


def check_consistency(rows: List[Dict[str, Any]]) -> List[str]:
    """Sanity-check parsed rows, returning human-readable warnings.

    Returns rather than raises: none of these mean the run failed, but each
    marks a place where Scylla's numbers should not be read as Prosimos's.
    """
    by_metric = {r["metric"]: r for r in rows}
    warnings: List[str] = []

    cycle = by_metric.get("cycle_time", {})
    processing = by_metric.get("processing_time", {})
    waiting = by_metric.get("waiting_time", {})

    # Must hold under any definition: work done cannot exceed elapsed time.
    if processing.get("avg", 0) > cycle.get("avg", 0) + 1e-6:
        warnings.append(
            f"processing_time avg ({processing['avg']:.1f}) exceeds cycle_time avg "
            f"({cycle['avg']:.1f}) — the run is inconsistent, not just differently "
            f"defined"
        )

    # Expected: Scylla sums waiting per activity instance, so concurrent waits
    # double-count. Worth reporting because it sizes the definitional gap.
    if waiting.get("avg", 0) > cycle.get("avg", 0) + 1e-6:
        warnings.append(
            f"waiting_time avg ({waiting['avg']:.1f}) exceeds cycle_time avg "
            f"({cycle['avg']:.1f}) — expected: Scylla sums waiting per activity, "
            f"Prosimos measures it per case. Not comparable across engines."
        )

    for row in rows:
        if row["metric"] in UNMAPPED_METRICS:
            continue
        for field in ("min", "max", "avg", "total"):
            if row[field] < 0:
                warnings.append(f"{row['metric']}.{field} is negative ({row[field]})")
        if not (row["min"] - 1e-6 <= row["avg"] <= row["max"] + 1e-6):
            warnings.append(
                f"{row['metric']} avg {row['avg']:.1f} outside "
                f"[{row['min']:.1f}, {row['max']:.1f}]"
            )

    return warnings
