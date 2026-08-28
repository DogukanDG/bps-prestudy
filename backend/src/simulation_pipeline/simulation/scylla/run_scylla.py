"""
Invoke Scylla on a generated configuration and collect the KPI rows.

One simulation per subprocess. A long-lived JVM driving `SimulationManager`
directly would remove the ~0.5 s startup, but the spike measured a 3000-case
run at 1.58 s wall including that startup -- faster than the Prosimos arm --
so the added complexity is not justified yet. Phase 4 revisits it with a
measurement rather than a guess.

Two Scylla behaviours the caller cannot avoid:

  - `--output` is parsed but never wired to the field `SimulationManager.run()`
    reads, so the flag is silently ignored and output lands next to the global
    config in an auto-named `output_<timestamp>/` directory. Working directories
    are therefore per-run and the produced directory is discovered afterwards.
  - `--enable-bps-logging` is required. Without it Scylla collects no node
    information and writes no KPI file at all.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Sequence
from xml.etree import ElementTree as ET

from . import distributions as D
from .build_global_config import build_global_config, validate_global_config
from .build_sim_config import build_sim_config, read_bpmn, validate_sim_config
from .parse_results import parse_process_rows

# Scylla's own output directories are named with this prefix.
_OUTPUT_PREFIX = "output_"

DEFAULT_TIMEOUT_S = 900


class ScyllaError(RuntimeError):
    """Scylla failed to run, or ran without producing usable output."""


def write_xml(root: ET.Element, path: Path) -> Path:
    ET.register_namespace("bsim", D.BSIM)
    ET.indent(root, space="  ")
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode"),
        encoding="utf-8",
    )
    return path


def write_configs(
    work_dir: Path,
    model: Dict[str, Any],
    bpmn_path: str | Path,
    total_cases: int,
    start_iso: str,
    seed: int,
    buckets: int = D.DEFAULT_BUCKETS,
    n_draws: int = D.DEFAULT_DRAWS,
    weighted: bool = False,
) -> Dict[str, Path]:
    """Generate and validate both config files for one sample.

    Validation runs before Scylla is invoked: it skips XML it does not
    understand rather than failing, so anything dropped here would surface as
    plausible-looking wrong numbers rather than an error.
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    global_root = build_global_config(model, seed=seed)
    validate_global_config(global_root, model)

    sim_root = build_sim_config(
        model, bpmn_path, total_cases=total_cases, start_iso=start_iso,
        seed=seed, buckets=buckets, n_draws=n_draws, weighted=weighted,
    )
    validate_sim_config(sim_root, model, read_bpmn(bpmn_path))

    return {
        "global_config": write_xml(global_root, work_dir / "global_config.xml"),
        "sim_config": write_xml(sim_root, work_dir / "sim_config.xml"),
        "bpmn": Path(shutil.copy(bpmn_path, work_dir / "model.bpmn")),
    }


def resolve_java(explicit: str | None = None) -> str:
    """Pick the java binary.

    Scylla's current main targets Java 11 (`pom.xml`: source/target 11), so an
    older JVM on PATH fails with UnsupportedClassVersionError at startup. JAVA_BIN
    overrides; otherwise a JDK inside the active conda environment is preferred
    over whatever PATH resolves to, since that is where the pinned one lives.
    """
    import os
    import sys

    if explicit:
        return explicit
    if os.environ.get("JAVA_BIN"):
        return os.environ["JAVA_BIN"]

    prefix = Path(sys.prefix)
    for candidate in (prefix / "Library" / "bin" / "java.exe",   # conda, Windows
                      prefix / "bin" / "java"):                  # conda, POSIX
        if candidate.is_file():
            return str(candidate)
    return "java"


def run_scylla(
    jar_path: str | Path,
    configs: Dict[str, Path],
    work_dir: Path,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    java_bin: str | None = None,
    heap: str | None = None,
) -> Path:
    """Run one simulation; return the directory Scylla wrote."""
    java_bin = resolve_java(java_bin)
    before = {p for p in work_dir.iterdir()
              if p.is_dir() and p.name.startswith(_OUTPUT_PREFIX)}

    cmd = [java_bin]
    if heap:
        cmd.append(f"-Xmx{heap}")
    cmd += [
        "-jar", str(jar_path),
        "--headless",
        # Without this no KPIs are collected at all.
        "--enable-bps-logging",
        f"--config={configs['global_config']}",
        f"--bpmn={configs['bpmn']}",
        f"--sim={configs['sim_config']}",
    ]

    try:
        result = subprocess.run(
            cmd, cwd=str(work_dir), capture_output=True, text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise ScyllaError(f"Scylla timed out after {timeout_s}s") from exc

    if result.returncode != 0:
        raise ScyllaError(
            f"Scylla exited {result.returncode}\n"
            f"stderr: {(result.stderr or '').strip()[-2000:]}\n"
            f"stdout: {(result.stdout or '').strip()[-1000:]}"
        )

    after = {p for p in work_dir.iterdir()
             if p.is_dir() and p.name.startswith(_OUTPUT_PREFIX)}
    produced = after - before
    if not produced:
        raise ScyllaError(
            "Scylla produced no output directory. It exited cleanly, so the "
            "configuration was probably parsed but empty.\n"
            f"stdout: {(result.stdout or '').strip()[-1000:]}"
        )
    return sorted(produced)[-1]


def simulate_sample_scylla(
    sample_id: int,
    sample_data: Dict[str, Any],
    bpmn_path: str | Path,
    total_cases: int,
    start_iso: str,
    jar_path: str | Path,
    seed: int | None = None,
    buckets: int = D.DEFAULT_BUCKETS,
    n_draws: int = D.DEFAULT_DRAWS,
    weighted: bool = False,
    keep_output: str | Path | None = None,
    heap: str | None = None,
    java_bin: str | None = None,
) -> Dict[str, Any]:
    """Simulate one sampled configuration with Scylla.

    Mirrors the Prosimos `simulate_sample()` contract exactly: same arguments
    plus the engine's own, same return shape, and every exception captured into
    the "error" field so one bad sample cannot abort a chunk.

    Only `process_rows` is populated. The task, resource and case row builders
    exist on the Prosimos side but nothing reads them -- `simulate_all_samples`
    writes only the process chunk to disk -- so producing them here would be
    dead work.
    """
    # Derived so replications differ but stay reproducible. Scylla reads only
    # the global config's seed (SimulationManager.java:127), so it has to be
    # baked into the file rather than passed per simulation.
    effective_seed = seed if seed is not None else (sample_id + 1) * 7919

    work_dir = Path(tempfile.mkdtemp(prefix=f"scylla_s{sample_id}_"))
    try:
        configs = write_configs(
            work_dir, sample_data, bpmn_path, total_cases, start_iso,
            effective_seed, buckets, n_draws, weighted,
        )
        output_dir = run_scylla(jar_path, configs, work_dir, heap=heap,
                                java_bin=java_bin)

        rows = parse_process_rows(
            output_dir, sample_id=sample_id, expected_cases=total_cases,
        )

        if keep_output:
            destination = Path(keep_output) / f"sample_{sample_id:05d}"
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(output_dir, destination)

        return {
            "sample_id": sample_id,
            "process_rows": rows,
            "task_rows": [],
            "resource_rows": [],
            "case_rows": [],
            "error": None,
        }

    except Exception as exc:  # noqa: BLE001 -- mirrors the Prosimos arm
        return {
            "sample_id": sample_id,
            "process_rows": [],
            "task_rows": [],
            "resource_rows": [],
            "case_rows": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


_JAR_HELP = (
    "Build it with spike/run_spike.sh build, or set SCYLLA_JAR. It must come "
    "from a commit that includes f9671cb (Fix #72) -- the copy bundled with "
    "SimuBridge does not -- and must be compiled for the local JVM: building "
    "with a newer JDK than the one running it gives UnsupportedClassVersionError."
)


def resolve_jar(explicit: str | Path | None = None) -> Path:
    """Find scylla.jar: an explicit path, SCYLLA_JAR, or the spike build.

    An explicit path that does not exist is an error rather than a reason to
    fall back -- silently running a different jar than the caller asked for
    would make results untraceable.
    """
    import os

    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(f"scylla.jar not found at {path}. {_JAR_HELP}")
        return path

    candidates = []
    if os.environ.get("SCYLLA_JAR"):
        candidates.append(Path(os.environ["SCYLLA_JAR"]))
    candidates.append(Path(__file__).resolve().parents[5] / "spike" / "scylla.jar")

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(f"scylla.jar not found. {_JAR_HELP}")
