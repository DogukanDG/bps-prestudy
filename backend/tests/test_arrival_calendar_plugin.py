"""
Tests for the Scylla arrival-calendar plugin.

Stock Scylla schedules arrivals from an inter-arrival distribution alone, with
no notion of when arrivals are possible. The models carry an arrival calendar --
75 hours a week on BPIC 2012, about 45% -- and ignoring it releases the same
cases across the whole week, so they arrive when no resource is working and
queue until the next shift. That was the single largest source of divergence
from Prosimos.

The plugin (scylla/src/main/java/.../plugin/arrivalcalendar/) defers a case that
would arrive outside the calendar to the next open window. These tests check the
converter emits the element and that Scylla, built with the plugin, honours it.

Needs a scylla.jar built from a tree containing the plugin; skips without one.
"""

import collections
import datetime as dt
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from src.simulation_pipeline.simulation.scylla import build_sim_config as S
from src.simulation_pipeline.simulation.scylla.distributions import BSIM
from test_t1_determinism import has_jar, has_prosimos

REPO = Path(__file__).resolve().parents[2]
MODEL_DIR = REPO / "example_sensitivity_analysis_inputs" / "BPIC_2012"
START_ISO = "2023-01-01T00:00:00+02:00"

needs_jar = pytest.mark.skipif(not has_jar(), reason="needs a built scylla.jar")


def q(tag):
    return f"{{{BSIM}}}{tag}"


@pytest.fixture(scope="module")
def model():
    path = MODEL_DIR / "BPIC_2012_train.json"
    if not path.exists():
        pytest.skip("BPIC 2012 model not available")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bpmn():
    return MODEL_DIR / "BPIC_2012_train.bpmn"


def build(model, bpmn, **kwargs):
    return S.build_sim_config(model, bpmn, total_cases=100, start_iso=START_ISO,
                              seed=1, buckets=20, n_draws=500, **kwargs)


# --------------------------------------------------------------------------
# The emitted XML
# --------------------------------------------------------------------------

def test_calendar_is_emitted_by_default(model, bpmn):
    start = build(model, bpmn).find(q("simulationConfiguration")).find(q("startEvent"))
    calendar = start.find(q("arrivalCalendar"))
    assert calendar is not None
    assert len(calendar.findall(q("timetableItem"))) == \
        len(model["arrival_time_calendar"])


def test_calendar_windows_match_the_model(model, bpmn):
    """Passed through unrounded -- Scylla parses HH:MM:SS via LocalTime."""
    start = build(model, bpmn).find(q("simulationConfiguration")).find(q("startEvent"))
    items = start.find(q("arrivalCalendar")).findall(q("timetableItem"))

    for item, period in zip(items, model["arrival_time_calendar"]):
        assert item.get("from") == period["from"]
        assert item.get("to") == period["to"]
        assert item.get("beginTime") == period["beginTime"]
        assert item.get("endTime") == period["endTime"]


def test_calendar_can_be_switched_off(model, bpmn):
    """Kept so the with/without comparison stays reproducible."""
    start = (build(model, bpmn, arrival_calendar=False)
             .find(q("simulationConfiguration")).find(q("startEvent")))
    assert start.find(q("arrivalCalendar")) is None


def test_arrival_rate_is_still_emitted(model, bpmn):
    """The calendar restricts *when*; the distribution still sets how often.
    A build without the plugin ignores the calendar and behaves as before."""
    start = build(model, bpmn).find(q("simulationConfiguration")).find(q("startEvent"))
    rate = start.find(q("arrivalRate"))
    assert rate is not None
    assert rate.get("timeUnit") == "SECONDS"
    assert len(rate) == 1


# --------------------------------------------------------------------------
# What Scylla actually does with it
# --------------------------------------------------------------------------

def run_scylla(model, bpmn, cases, tmp_path, arrival_calendar=True):
    from src.simulation_pipeline.simulation.scylla import run_scylla as R

    original = S.build_sim_config
    if not arrival_calendar:
        S.build_sim_config = lambda *a, **k: original(
            *a, **{**k, "arrival_calendar": False})
        R.build_sim_config = S.build_sim_config
    try:
        result = R.simulate_sample_scylla(
            sample_id=0, sample_data=model, bpmn_path=bpmn, total_cases=cases,
            start_iso=START_ISO, jar_path=R.resolve_jar(), seed=7,
            heap="1g", keep_output=tmp_path,
        )
    finally:
        S.build_sim_config = original
        R.build_sim_config = original
    assert result["error"] is None, result["error"]
    return result, Path(tmp_path) / "sample_00000"


def arrival_times(output_dir):
    """First event timestamp of each trace."""
    root = ET.parse(output_dir / "model.xes").getroot()
    times = []
    for trace in root.findall(".//{*}trace"):
        events = trace.findall("{*}event")
        if not events:
            continue
        for field in events[0].findall("{*}date"):
            if field.get("key") == "time:timestamp":
                times.append(dt.datetime.fromisoformat(field.get("value")))
    return times


def open_hours(model):
    """Hours the calendar covers, in the timezone the XES log reports."""
    hours = set()
    for period in model["arrival_time_calendar"]:
        begin = int(period["beginTime"][:2])
        end = int(period["endTime"][:2])
        # The log is written at +01:00 while the model's start time is +02:00,
        # so a model hour appears one hour earlier in the log.
        hours.update((h - 1) % 24 for h in range(begin, end + 1))
    return hours


@needs_jar
def test_arrivals_fall_inside_the_calendar(model, bpmn, tmp_path):
    """The point of the plugin. Without it, arrivals spread over all 24 hours."""
    _, output = run_scylla(model, bpmn, 300, tmp_path)
    observed = {t.hour for t in arrival_times(output)}
    assert observed, "no arrivals found in the log"
    assert observed <= open_hours(model), (
        f"arrivals outside the calendar: {sorted(observed - open_hours(model))}"
    )


@needs_jar
def test_no_cases_are_lost(model, bpmn, tmp_path):
    """Deferring, not dropping: the case count must still match what was asked.

    This is what keeps runs comparable across engines -- Prosimos compresses the
    same number of cases into the open hours rather than discarding any.
    """
    _, output = run_scylla(model, bpmn, 300, tmp_path)
    assert len(arrival_times(output)) == 300


@needs_jar
def test_without_the_calendar_arrivals_spread_over_the_week(model, bpmn, tmp_path):
    """The behaviour being corrected, pinned so the comparison stays honest."""
    _, output = run_scylla(model, bpmn, 300, tmp_path, arrival_calendar=False)
    observed = {t.hour for t in arrival_times(output)}
    assert not observed <= open_hours(model), (
        "arrivals stayed inside the calendar even with the element removed -- "
        "the comparison would be meaningless"
    )


@needs_jar
def test_deferral_clusters_arrivals_at_window_openings(model, bpmn, tmp_path):
    """A known and intended side effect, recorded rather than hidden.

    Cases drawn for closed hours pile up at the next opening, so the realised
    inter-arrival distribution is not exactly the configured one. Prosimos
    compresses arrivals differently, so this is a residual difference between
    the engines even with the plugin in place.
    """
    _, output = run_scylla(model, bpmn, 300, tmp_path)
    times = arrival_times(output)
    counts = collections.Counter(t.hour for t in times)
    busiest = max(counts.values())
    assert busiest > 2 * (len(times) / max(len(counts), 1)), (
        "expected a pile-up at window openings; if this stops holding the "
        "deferral semantics have changed"
    )


@needs_jar
@pytest.mark.skipif(not has_prosimos(), reason="needs Prosimos for the comparison")
def test_plugin_narrows_the_gap_to_prosimos(model, bpmn, tmp_path):
    """The measurement that justifies the plugin.

    On BPIC 2012 at 500 cases the cycle-time ratio against Prosimos was about
    2.3x without the calendar and 1.6x with it. The assertion is deliberately
    loose -- the point is the direction and that it is substantial, not the
    exact number, which moves with the seed.
    """
    from test_t1_determinism import run_prosimos

    prosimos = run_prosimos(model, bpmn, 500)["cycle_time"]["avg"]
    with_calendar, _ = run_scylla(model, bpmn, 500, tmp_path / "on")
    without, _ = run_scylla(model, bpmn, 500, tmp_path / "off",
                            arrival_calendar=False)

    ratio_on = with_calendar["process_rows"][0]["avg"] / prosimos
    ratio_off = without["process_rows"][0]["avg"] / prosimos

    assert ratio_on < ratio_off, (
        f"the plugin did not narrow the gap: {ratio_off:.2f} -> {ratio_on:.2f}"
    )
    assert ratio_on < ratio_off * 0.85
