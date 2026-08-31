from src.simulation_pipeline.convert_samples.write_converted_samples import joblib_tqdm
from typing import Dict, Any, Iterable, List
from joblib import Parallel, delayed
from pathlib import Path
import pandas as pd
import numpy as np
import tempfile
import shutil
import json
import time
import glob
import gc
import os
import re


def simulate_samples(
    is_sobol: bool,
    is_groups: bool,
    problem: Dict[str, Any],
    bpmn_path: str,
    cases_list: list[int],
    replication_runs: int,
    simulation_results_folder: str,
    samples: list | None = None,
    calc_second_order: bool | None = None,
    num_levels: int | None = None,
    seed: int | None = None,
    start_iso="2023-01-01T00:00:00+02:00",
    disk_format="parquet",  # or "csv"
    engine: str = "prosimos",
    engine_options: Dict[str, Any] | None = None,
):
    """
    Run Prosimos simulations for all sampled JSON chunks and prepare
    inputs for sensitivity analysis.

    The function:
      1. Discovers all sample chunk JSON files under
         `<simulation_results_folder>/samples/`.
      2. For each value in `cases_list`, simulates every sample (with
         `replication_runs` replication runs) and stores raw KPIs under
         `simulation_results/`.
      3. Merges per-run KPI chunks and computes averaged KPIs using
         `merge_parquet_chunks`.
      4. Builds and saves `sa_config.json` with the information needed
         for Sobol or Morris sensitivity analysis.

    Parameters
    ----------
    is_sobol : bool
        If True, configure SA for Sobol (global) analysis; otherwise
        for Morris (local) analysis.
    is_groups : bool
        If True, configure visualization for between groups; otherwise
        for within group.
    problem : dict
        SALib-style problem definition with at least "num_vars" and "names".
    bpmn_path : str
        Path to the BPMN model used in Prosimos simulations.
    cases_list : list[int]
        List of total case counts to simulate (e.g. [100, 500, 1000]).
    replication_runs : int
        Number of replication runs per `total_cases`.
    simulation_results_folder : str
        Root folder where samples, simulation outputs and SA inputs live.
    samples : list | None, optional
        Design matrix used for Morris analysis (stored in `sa_config.json`
        when `is_sobol` is False).
    calc_second_order : bool | None, optional
        Whether second-order Sobol indices are computed (stored only when
        `is_sobol` is True).
    num_levels : int | None, optional
        Number of levels in Morris design (stored only when `is_sobol` is False).
    seed : int | None, optional
        Random seed used during sampling (stored in SA config for Morris).
    start_iso : str, optional
        ISO timestamp from which Prosimos starts case arrivals.
    disk_format : str, optional
        File format for KPI chunks, "parquet" (default) or "csv".

    Returns
    -------
    None
        Results and SA configuration are written to disk under
        `simulation_results_folder`.
    """

    # --- 1) Find chunk JSON files ---
    input_folder = os.path.join(simulation_results_folder, "samples")

    chunk_files = sorted(glob.glob(os.path.join(input_folder, "*.json")))
    if not chunk_files:
        raise FileNotFoundError("❌ No chunk files found in the expected samples folder.")

    print(f"Found {len(chunk_files)} chunks.\n")
    print("First/last:", chunk_files[0], "…", chunk_files[-1], "\n")

    # --- 2) Resolve paths for BPMN + OUT_DIR ---
    output_folder = os.path.join(simulation_results_folder, "simulation_results")
    os.makedirs(output_folder, exist_ok=True)

    OUT_DIR = Path(output_folder)
    BPMN_PATH = Path(bpmn_path)

    # --- 3) Check problem dict sanity ---
    try:
        assert isinstance(problem, dict) and "num_vars" in problem and "names" in problem
    except Exception:
        raise RuntimeError(
            "Please define `problem = {'num_vars': D, 'names': names, 'bounds': [[0,1]]*D, 'groups': groups}` before running."
        )

    # --- 4) Run for each TOTAL_CASES ---
    for total_cases in cases_list:
        simulate_all_samples(
            total_cases=total_cases,
            bpmn_path=BPMN_PATH,
            start_iso=start_iso,
            base_out_dir=OUT_DIR,
            problem=problem,
            chunk_files=chunk_files,
            replication_runs=replication_runs,
            disk_format=disk_format,
            engine=engine,
            engine_options=engine_options,
        )

    # --- 5) Merge and average out KPIs to single files and save to sensitivity analysis folder ---
    sensitivity_analyis_input_folder = os.path.join(simulation_results_folder, "sensitivity_analysis_inputs")
    os.makedirs(sensitivity_analyis_input_folder, exist_ok=True)

    SA_INPUT_DIR = Path(sensitivity_analyis_input_folder)

    merge_parquet_chunks(is_sobol, cases_list, read_dir=OUT_DIR, write_dir=SA_INPUT_DIR)

    # --- 6) Create and save sensitivity-analysis config dictionary ---
    # Rule: If is_sobol -> samples, num_levels, seed, conf_level = None
    #       If not sobol -> calc_second_order = None

    def _make_json_safe(x):
        """Convert numpy arrays, numpy ints, etc. into JSON serializable objects."""
        if isinstance(x, np.ndarray):
            return x.tolist()
        if isinstance(x, (np.integer, np.floating)):
            return float(x) if isinstance(x, np.floating) else int(x)
        if isinstance(x, dict):
            return {k: _make_json_safe(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [_make_json_safe(v) for v in x]
        return x

    if is_sobol:
        sa_config = {
            "is_sobol": is_sobol,
            "is_groups": is_groups,
            "cases_list": cases_list,
            "problem": problem,
            "calc_second_order": calc_second_order,
            "samples": None,
            "num_levels": None,
            "seed": seed,
            "conf_level": None,
        }
    else:
        sa_config = {
            "is_sobol": is_sobol,
            "is_groups": is_groups,
            "cases_list": cases_list,
            "problem": problem,
            "calc_second_order": None,
            "samples": _make_json_safe(samples),
            "num_levels": num_levels,
            "seed": seed,
            "conf_level": 0.95,
        }

    # Save dictionary
    sa_config_path = SA_INPUT_DIR / "sa_config.json"
    with open(sa_config_path, "w") as f:
        json.dump(sa_config, f, indent=2)

    print(f"SA configuration saved to: {sa_config_path}\n")


def _n_jobs_for(engine: str, engine_options: Dict[str, Any] | None) -> int:
    """How many samples to run at once.

    Prosimos runs in-process and is memory-light, so the historical -5 ("every
    core but four") is right for it. Scylla is a JVM per sample: with eight
    concurrent JVMs on an 8 GB machine the runs die with "insufficient memory
    for the Java Runtime Environment", and the failures are easy to miss --
    SALib silently returns [] when the sample matrix no longer matches the
    output vector. So the Scylla arm is sized by memory rather than by cores.
    """
    options = engine_options or {}
    if options.get("n_jobs") is not None:
        return int(options["n_jobs"])
    if (engine or "prosimos").lower() != "scylla":
        return -5

    import os
    heap = str(options.get("heap") or "1g").lower()
    gb = (float(heap[:-1]) if heap.endswith("g")
          else float(heap[:-1]) / 1024 if heap.endswith("m")
          else 1.0)

    total_gb = 8.0
    try:
        import shutil  # noqa: F401
        if hasattr(os, "sysconf") and "SC_PAGE_SIZE" in os.sysconf_names:
            total_gb = (os.sysconf("SC_PAGE_SIZE")
                        * os.sysconf("SC_PHYS_PAGES")) / 1024**3
    except Exception:
        pass

    # Leave roughly a third of RAM for the OS and the parent process.
    by_memory = max(1, int((total_gb * 0.6) / max(gb, 0.25)))
    by_cores = max(1, (os.cpu_count() or 4) - 4)
    return min(by_memory, by_cores)


def _engine_worker(engine: str, engine_options: Dict[str, Any] | None):
    """Return the per-sample worker for `engine`.

    joblib's loky backend pickles the callable to send it to worker processes,
    so this must be a module-level function or a functools.partial over one --
    a closure or lambda raises PicklingError, which is how the earlier BPIC
    2013 runs failed.
    """
    engine = (engine or "prosimos").lower()

    if engine == "prosimos":
        return simulate_sample

    if engine == "scylla":
        from functools import partial

        from .scylla.run_scylla import resolve_jar, simulate_sample_scylla

        options = dict(engine_options or {})
        # Resolved once here rather than in every worker, so a missing jar
        # fails immediately instead of once per sample.
        options["jar_path"] = resolve_jar(options.pop("jar_path", None))
        return partial(simulate_sample_scylla, **options)

    raise ValueError(
        f"unknown engine {engine!r}; expected 'prosimos' or 'scylla'"
    )


def simulate_all_samples(
    total_cases: int,
    bpmn_path: str | Path,
    start_iso: str,
    base_out_dir: str | Path,
    problem: Dict[str, Any],
    chunk_files: Iterable[str | Path],
    replication_runs: int,
    disk_format: str = "parquet",
    engine: str = "prosimos",
    engine_options: Dict[str, Any] | None = None,
) -> None:
    """
    Simulate all sample-chunk JSON files for a given case volume and
    store detailed KPIs per run.

    For each `total_cases` value this function:
      - Creates a folder `sim_results_<total_cases>_cases` under `base_out_dir`.
      - Saves the SA `problem` definition as problem.json.
      - Repeats simulations `replication_runs` times (run_1, run_2, ...).
      - For each run, iterates over all chunk JSON files, simulating every
        sample in parallel via `simulate_sample`.
      - Writes per-chunk KPI tables (process, tasks, resources, cases, errors)
        in the selected `disk_format`.

    Parameters
    ----------
    total_cases : int
        Number of cases to simulate in each Prosimos run.
    bpmn_path : str | Path
        Path to the BPMN model.
    start_iso : str
        ISO timestamp for the start of the simulation.
    base_out_dir : str | Path
        Root folder where per-case simulation result folders are created.
    problem : dict
        SA problem dictionary, stored for traceability in each case folder.
    chunk_files : Iterable[str | Path]
        List of sample JSON chunk files produced by the sampling step.
    replication_runs : int
        Number of replication runs (run_1, run_2, ...).
    disk_format : str, optional
        Output format for KPI chunks, "parquet" (default) or "csv".
    engine : str, optional
        Simulation engine, "prosimos" (default) or "scylla". Both produce the
        same `process_rows` schema; the Scylla arm leaves the three idle_*
        metrics as NaN because Prosimos defines them against resource calendars
        in a way Scylla does not reproduce.
    engine_options : dict, optional
        Engine-specific settings. For "scylla": jar_path, buckets, n_draws,
        weighted, heap, keep_output. Ignored by "prosimos".

    Returns
    -------
    None
        All outputs are written under `<base_out_dir>/sim_results_<total_cases>_cases/`.
    """
    run_one = _engine_worker(engine, engine_options)
    base_out_dir = Path(base_out_dir)
    chunk_files = [Path(cf) for cf in chunk_files]

    root_dir = base_out_dir / f"sim_results_{total_cases}_cases"
    root_dir.mkdir(parents=True, exist_ok=True)

    # Save / overwrite problem.json
    (root_dir / "problem.json").write_text(
        json.dumps(problem, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    for run_idx in range(1, replication_runs + 1):
        start_time = time.perf_counter()
        results_dir = root_dir / f"run_{run_idx}"
        results_dir.mkdir(parents=True, exist_ok=True)

        total_proc = total_task = total_res = total_case = 0

        for i, chunk_file in enumerate(chunk_files, start=1):
            with open(chunk_file, "r", encoding="utf-8") as f:
                all_samples = json.load(f)

            sample_ids = sorted(all_samples.keys(), key=lambda x: int(x))

            print(
                f"▶️ Running chunk {i}/{len(chunk_files)} — "
                f"{chunk_file} ({len(sample_ids)} samples) — "
                f"TOTAL_CASES={total_cases} — RUN={run_idx}"
            )

            # Per-chunk progress bar
            with joblib_tqdm(
                total=len(sample_ids),
                desc=(
                    f"Simulating chunk {i}/{len(chunk_files)} "
                    f"@ {total_cases} cases (run {run_idx})"
                ),
                position=1,
                leave=False,
            ):
                outs = Parallel(
                    n_jobs=_n_jobs_for(engine, engine_options),
                    backend="loky",
                    prefer="processes",
                    verbose=0,
                )(
                    delayed(run_one)(
                        sample_id=int(sample_id),
                        sample_data=all_samples[sample_id],
                        bpmn_path=bpmn_path,
                        total_cases=total_cases,
                        start_iso=start_iso,
                    )
                    for sample_id in sample_ids
                )

            # ---- aggregate this chunk & write to disk ----
            err_rows = [
                {"sample_id": o["sample_id"], "error": o["error"]}
                for o in outs
                if o["error"]
            ]

            proc_rows = [r for o in outs for r in o["process_rows"]]
            task_rows = [r for o in outs for r in o["task_rows"]]
            res_rows = [r for o in outs for r in o["resource_rows"]]
            case_rows = [r for o in outs for r in o["case_rows"]]

            df_proc = pd.DataFrame(proc_rows)
            df_task = pd.DataFrame(task_rows)
            df_res = pd.DataFrame(res_rows)
            df_case = pd.DataFrame(case_rows)
            df_err = pd.DataFrame(err_rows)

            # write to disk for this run & chunk
            write_dataframe_chunk(df_proc, "process", i, results_dir, disk_format=disk_format)
            # Tasks, resources and cases stay off to save disk -- nothing reads them.
            # write_dataframe_chunk(df_task, "tasks", i, results_dir, disk_format=disk_format)
            # write_dataframe_chunk(df_res, "resources", i, results_dir, disk_format=disk_format)
            # write_dataframe_chunk(df_case, "cases", i, results_dir, disk_format=disk_format)

            # Errors are different: simulate_sample() swallows every exception and
            # returns empty row lists, so without this a failed sample is invisible
            # -- the parquet is just silently short. Costs nothing when nothing fails.
            if not df_err.empty:
                write_dataframe_chunk(df_err, "errors", i, results_dir, disk_format=disk_format)
                print(
                    f"⚠️  {len(df_err)} sample(s) failed in chunk {i} "
                    f"(run {run_idx}, {total_cases} cases) — see errors_chunk_*"
                )

            total_proc += len(df_proc)
            total_task += len(df_task)
            total_res += len(df_res)
            total_case += len(df_case)

            print(f"✅ Finished chunk {i}/{len(chunk_files)} → [{results_dir.name}]")
            print(
                f" process_chunk_{i:05d}.{disk_format}: +{len(df_proc)} rows\n"
                f" Totals so far → process:{total_proc:,} "
                f"tasks:{total_task:,} resources:{total_res:,} cases:{total_case:,}\n"
            )

            # free memory
            del (
                all_samples,
                outs,
                df_proc,
                df_task,
                df_res,
                df_case,
                df_err,
                proc_rows,
                task_rows,
                res_rows,
                case_rows,
            )
            gc.collect()

        total_time = time.perf_counter() - start_time
        print(
            f"⏱ TOTAL_CASES={total_cases} RUN={run_idx} "
            f"finished in {total_time/60:.2f} min ({total_time:.1f} s)\n"
        )
        print(f"All chunks written to: {results_dir}\n")

    print(f"All runs complete for TOTAL_CASES={total_cases}. Root: {root_dir}\n")


# ---- worker: simulate ONE sample and return row lists ----
def simulate_sample(
    sample_id: int,
    sample_data: Dict[str, Any],
    bpmn_path: str | Path,
    total_cases: int,
    start_iso: str,
) -> Dict[str, Any]:
    """
    Simulate a single sampled configuration with Prosimos and return
    all collected KPI rows.

    The function writes `sample_data` to a temporary JSON file, runs
    `_simulate_samples`, then reshapes the resulting KPIs into row lists
    for process, tasks, resources and cases. Any exception is caught and
    returned via the "error" field.

    Parameters
    ----------
    sample_id : int
        Index of the sample in the design matrix.
    sample_data : dict
        Simulation configuration JSON for this sample.
    bpmn_path : str | Path
        Path to the BPMN model.
    total_cases : int
        Number of cases to simulate.
    start_iso : str
        ISO timestamp at which the simulation starts.

    Returns
    -------
    dict
        {
            "sample_id": int,
            "process_rows": list[dict],
            "task_rows": list[dict],
            "resource_rows": list[dict],
            "case_rows": list[dict],
            "error": str | None
        }
        If an error occurs, all row lists are empty and "error"
        contains the exception message.
    """

    tmp_path = None

    try:
        # Imported here rather than at module scope so the Scylla arm does not
        # require Prosimos to be installed. Import cost is negligible next to a
        # simulation, and joblib workers import the module once each anyway.
        from prosimos.simulation_engine import run_simulation as _simulate_samples

        # Write this sample's JSON config to a temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp:
            json.dump(sample_data, tmp, ensure_ascii=False, indent=2)
            tmp_path = Path(tmp.name)

        res = _simulate_samples(
            bpmn_path=str(bpmn_path),
            json_path=str(tmp_path),
            total_cases=total_cases,
            starting_at=start_iso,
            stat_out_path=None,
            log_out_path=None,
        )

        # Basic shape check
        if not (
            isinstance(res, tuple)
            and len(res) == 2
            and isinstance(res[0], (list, tuple))
            and len(res[0]) >= 5
        ):
            raise RuntimeError(
                f"Unexpected return shape from _simulate_samples for sample {sample_id}"
            )

        process_kpi, task_kpi, resource_kpi, started, ended = res[0][:5]
        log_info = res[1]

        bpmn_graph = getattr(getattr(log_info, "sim_setup", None), "bpmn_graph", None)
        element_info = getattr(bpmn_graph, "element_info", {}) if bpmn_graph else {}

        # ---------- process-level rows ----------
        proc_rows = [
            {"sample_id": sample_id, "metric": "cycle_time",           **kpi_to_dict(process_kpi.cycle_time)},
            {"sample_id": sample_id, "metric": "processing_time",      **kpi_to_dict(process_kpi.processing_time)},
            {"sample_id": sample_id, "metric": "waiting_time",         **kpi_to_dict(process_kpi.waiting_time)},
            {"sample_id": sample_id, "metric": "idle_cycle_time",      **kpi_to_dict(process_kpi.idle_cycle_time)},
            {"sample_id": sample_id, "metric": "idle_processing_time", **kpi_to_dict(process_kpi.idle_processing_time)},
            {"sample_id": sample_id, "metric": "idle_time",            **kpi_to_dict(process_kpi.idle_time)},
        ]

        # ---------- task-level rows ----------
        task_rows: List[Dict[str, Any]] = []
        for task_id, kmap in task_kpi.items():
            task_name = None
            if element_info and task_id in element_info:
                try:
                    task_name = element_info[task_id].name
                except Exception:
                    task_name = None

            task_rows.append(
                {
                    "sample_id": sample_id,
                    "task_id": task_id,
                    "task_name": task_name,
                    "count": getattr(getattr(kmap, "cycle_time", None), "count", None),

                    "duration_min":   getattr(getattr(kmap, "duration", None), "min", None),
                    "duration_max":   getattr(getattr(kmap, "duration", None), "max", None),
                    "duration_avg":   getattr(getattr(kmap, "duration", None), "avg", None),
                    "duration_total": getattr(getattr(kmap, "duration", None), "total", None),

                    "waiting_min":    getattr(getattr(kmap, "waiting_time", None), "min", None),
                    "waiting_max":    getattr(getattr(kmap, "waiting_time", None), "max", None),
                    "waiting_avg":    getattr(getattr(kmap, "waiting_time", None), "avg", None),
                    "waiting_total":  getattr(getattr(kmap, "waiting_time", None), "total", None),

                    "processing_min":   getattr(getattr(kmap, "processing_time", None), "min", None),
                    "processing_max":   getattr(getattr(kmap, "processing_time", None), "max", None),
                    "processing_avg":   getattr(getattr(kmap, "processing_time", None), "avg", None),
                    "processing_total": getattr(getattr(kmap, "processing_time", None), "total", None),

                    "cycle_min":      getattr(getattr(kmap, "cycle_time", None), "min", None),
                    "cycle_max":      getattr(getattr(kmap, "cycle_time", None), "max", None),
                    "cycle_avg":      getattr(getattr(kmap, "cycle_time", None), "avg", None),
                    "cycle_total":    getattr(getattr(kmap, "cycle_time", None), "total", None),

                    "idle_min":       getattr(getattr(kmap, "idle_time", None), "min", None),
                    "idle_max":       getattr(getattr(kmap, "idle_time", None), "max", None),
                    "idle_avg":       getattr(getattr(kmap, "idle_time", None), "avg", None),
                    "idle_total":     getattr(getattr(kmap, "idle_time", None), "total", None),

                    "idle_cycle_min":    getattr(getattr(kmap, "idle_cycle_time", None), "min", None),
                    "idle_cycle_max":    getattr(getattr(kmap, "idle_cycle_time", None), "max", None),
                    "idle_cycle_avg":    getattr(getattr(kmap, "idle_cycle_time", None), "avg", None),
                    "idle_cycle_total":  getattr(getattr(kmap, "idle_cycle_time", None), "total", None),

                    "idle_proc_min":     getattr(getattr(kmap, "idle_processing_time", None), "min", None),
                    "idle_proc_max":     getattr(getattr(kmap, "idle_processing_time", None), "max", None),
                    "idle_proc_avg":     getattr(getattr(kmap, "idle_processing_time", None), "avg", None),
                    "idle_proc_total":   getattr(getattr(kmap, "idle_processing_time", None), "total", None),

                    "cost_min":       getattr(getattr(kmap, "cost", None), "min", None),
                    "cost_max":       getattr(getattr(kmap, "cost", None), "max", None),
                    "cost_avg":       getattr(getattr(kmap, "cost", None), "avg", None),
                    "cost_total":     getattr(getattr(kmap, "cost", None), "total", None),
                }
            )

        # ---------- resource-level rows ----------
        resource_rows: List[Dict[str, Any]] = []
        for rid, rinfo in resource_kpi.items():
            resource_rows.append(
                {
                    "sample_id": sample_id,
                    "resource_id": rid,
                    "resource_name": getattr(
                        getattr(rinfo, "r_profile", None), "resource_name", None
                    ),
                    "tasks_allocated": getattr(rinfo, "task_allocated", None),
                    "worked_time_s": getattr(rinfo, "worked_time", None),
                    "available_time_s": getattr(rinfo, "available_time", None),
                    "utilization": getattr(rinfo, "utilization", None),
                }
            )

        # ---------- case rows ----------
        case_rows: List[Dict[str, Any]] = []
        for tr in getattr(log_info, "trace_list", []):
            try:
                dur_s = (tr.completed_at - tr.started_at).total_seconds()
            except Exception:
                dur_s = None
            case_rows.append(
                {
                    "sample_id": sample_id,
                    "case_id": getattr(tr, "p_case", None),
                    "started_at": getattr(tr, "started_at", None),
                    "completed_at": getattr(tr, "completed_at", None),
                    "cycle_time_s": dur_s,
                }
            )

        return {
            "sample_id": sample_id,
            "process_rows": proc_rows,
            "task_rows": task_rows,
            "resource_rows": resource_rows,
            "case_rows": case_rows,
            "error": None,
        }

    except Exception as e:
        return {
            "sample_id": sample_id,
            "process_rows": [],
            "task_rows": [],
            "resource_rows": [],
            "case_rows": [],
            "error": str(e),
        }
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


def write_dataframe_chunk(
    simulated_chunk: pd.DataFrame,
    metric_type: str,
    chunk_idx: int,
    results_dir: Path,
    disk_format: str,
) -> Path:
    """
    Write a single KPI DataFrame chunk to disk in parquet or CSV format.

    Files are named as:
        `<metric_type>_chunk_<chunk_idx>.parquet` or `.csv`
    and stored in `results_dir`. Empty DataFrames still produce an
    empty file so schemas remain consistent.

    Parameters
    ----------
    simulated_chunk : pd.DataFrame
        DataFrame to persist for this chunk (may be empty).
    metric_type : str
        Logical type of the data, e.g. "process", "tasks",
        "resources", or "cases".
    chunk_idx : int
        Chunk index used for zero-padded numbering in the filename.
    results_dir : Path
        Directory where the file will be written.
    disk_format : str
        Output format: "parquet" or "csv".

    Returns
    -------
    Path | None
        Path to the written file if successful. May return None in
        the non-empty case for historical/backwards-compatibility.
    """

    ext = "parquet" if disk_format == "parquet" else "csv"
    out = results_dir / f"{metric_type}_chunk_{chunk_idx:05d}.{ext}"

    if simulated_chunk.empty:
        # Still write an empty file so schemas stay consistent
        if disk_format == "parquet":
            try:
                simulated_chunk.to_parquet(out, index=False)
            except Exception as e:
                raise RuntimeError(
                    f"Parquet write failed for {metric_type} (install pyarrow or use CSV): {e}"
                )
        else:
            simulated_chunk.to_csv(out, index=False)
        return out

    # Non-empty simulated_chunk
    if disk_format == "parquet":
        try:
            simulated_chunk.to_parquet(out, index=False)
        except Exception as e:
            raise RuntimeError(
                f"Parquet write failed for {metric_type} (install pyarrow or use CSV): {e}"
            )
    else:
        simulated_chunk.to_csv(out, index=False)

    return None


def kpi_to_dict(kpi) -> dict:
    """
    Convert a KPI-like object into a plain dictionary.

    The function safely reads the usual KPI attributes using
    `getattr`, returning None when an attribute is missing.

    Parameters
    ----------
    kpi : object
        Object that may expose attributes: min, max, avg, total, count.

    Returns
    -------
    dict
        {
            "min":   kpi.min   or None,
            "max":   kpi.max   or None,
            "avg":   kpi.avg   or None,
            "total": kpi.total or None,
            "count": kpi.count or None,
        }
    """
    return {
        "min": getattr(kpi, "min", None),
        "max": getattr(kpi, "max", None),
        "avg": getattr(kpi, "avg", None),
        "total": getattr(kpi, "total", None),
        "count": getattr(kpi, "count", None),
    }


def merge_parquet_chunks(is_sobol: bool, cases_list: list[int], read_dir: str | Path, write_dir: str | Path) -> None:
    """
    Merge per-run KPI chunks into averaged KPI tables for each case size.

    For every `<read_dir>/sim_results_<cases>_cases` folder this function:
      - Collects all `run_*` subfolders.
      - Reads process/tasks/resources/cases chunk files (parquet or CSV).
      - Concatenates chunks across runs.
      - Aggregates KPIs by averaging over runs using:
          * Cases:     group by (sample_id, case_id) → mean(cycle_time_s)
          * Process:   group by (sample_id, metric) → mean of
                       [min, max, avg, total, count]
          * Resources: group by (sample_id, resource_name) → mean of
                       [tasks_allocated, available_time_s, utilization]
          * Tasks:     group by (sample_id, task_name) → mean of the
                       task KPI columns.
      - Writes the aggregated results as parquet files into
        `<write_dir>/run_avg_<cases>_cases/`.

    Parameters
    ----------
    is_sobol : bool
        Currently unused in merging logic; kept for symmetry with
        upstream calls (Sobol vs Morris). Reserved for future branching.
    cases_list : list[int]
        List of case sizes (e.g. [100, 500, 1000]) whose result folders
        will be merged.
    read_dir : str | Path
        Base directory containing `sim_results_<cases>_cases` folders.
    write_dir : str | Path
        Target directory where `run_avg_<cases>_cases` folders with
        averaged KPIs will be created.

    Returns
    -------
    None
        All merged KPI tables are saved as parquet files in `write_dir`.
    """

    # ---------------- helpers ---------------- #

    def _detect_ext(base: Path) -> str:
        """Detect file extension ('parquet' or 'csv') in a run_* folder."""
        if list(base.glob("process_chunk_*.parquet")):
            return "parquet"
        if list(base.glob("process_chunk_*.csv")):
            return "csv"
        # Default/fallback
        return "parquet"

    def _sorted_chunk_paths(base: Path, kind: str, ext: str) -> list[Path]:
        """Return chunk file paths for a kind, sorted by chunk index."""
        paths = list(base.glob(f"{kind}_chunk_*.{ext}"))

        def idx(p: Path) -> int:
            m = re.search(r"_chunk_(\d{5})\.", p.name)
            return int(m.group(1)) if m else 10**9

        return sorted(paths, key=idx)

    def _read_one(path: Path, ext: str) -> pd.DataFrame:
        if ext == "parquet":
            return pd.read_parquet(path)
        return pd.read_csv(path)

    def _concat_kind(base: Path, kind: str, ext: str) -> pd.DataFrame:
        files = _sorted_chunk_paths(base, kind, ext)
        if not files:
            return pd.DataFrame()
        return pd.concat((_read_one(p, ext) for p in files), ignore_index=True)

    # ---------------- per-case loop ---------------- #

    for cases in cases_list:
        read_folder = read_dir / f"sim_results_{cases}_cases"
        if not read_folder.exists():
            print(f"⚠️ Missing case folder: {read_folder}\n")
            continue

        print(f"📂 Merging runs for {read_folder}\n")

        # run_avg folder handling
        write_folder = write_dir / f"run_avg_{cases}_cases"
        if write_folder.exists():
            shutil.rmtree(write_folder)
        write_folder.mkdir(parents=True, exist_ok=True)

        # find run_* read_folders
        run_dirs = sorted(
            [d for d in read_folder.iterdir() if d.is_dir() and re.match(r"run_\d+$", d.name)],
            key=lambda d: int(d.name.split("_")[1]),
        )
        if not run_dirs:
            print(f"⚠️ No run_* read_folders found in {read_folder}\n")
            continue

        all_proc, all_tasks, all_res, all_cases = [], [], [], []

        # -------- collect all runs -------- #
        for rd in run_dirs:
            run_idx = int(rd.name.split("_")[1])
            ext = _detect_ext(rd)

            proc = _concat_kind(rd, "process", ext)
            if not proc.empty:
                proc.insert(0, "run", run_idx)
                all_proc.append(proc)

            tsk = _concat_kind(rd, "tasks", ext)
            if not tsk.empty:
                tsk.insert(0, "run", run_idx)
                all_tasks.append(tsk)

            res = _concat_kind(rd, "resources", ext)
            if not res.empty:
                res.insert(0, "run", run_idx)
                all_res.append(res)

            cas = _concat_kind(rd, "cases", ext)
            if not cas.empty:
                cas.insert(0, "run", run_idx)
                all_cases.append(cas)

        if not any([all_proc, all_tasks, all_res, all_cases]):
            print(f"⚠️ No data found in runs for {read_folder}\n")
            continue

        process_df = pd.concat(all_proc, ignore_index=True) if all_proc else pd.DataFrame()
        tasks_df = pd.concat(all_tasks, ignore_index=True) if all_tasks else pd.DataFrame()
        resources_df = pd.concat(all_res, ignore_index=True) if all_res else pd.DataFrame()
        cases_df = pd.concat(all_cases, ignore_index=True) if all_cases else pd.DataFrame()

        # -------- averaging -------- #

        # df_cases: group by sample_id, case_id; average cycle_time_s
        if not cases_df.empty:
            if {"sample_id", "case_id", "cycle_time_s"} <= set(cases_df.columns):
                cases_kpis = (
                    cases_df
                    .groupby(["sample_id", "case_id"], as_index=False)["cycle_time_s"]
                    .mean()
                )
            else:
                print(f"⚠️ cases_df missing required columns in {read_folder}, skipping cases_kpis\n")
                cases_kpis = pd.DataFrame()
        else:
            cases_kpis = pd.DataFrame()

        # df_process: group by sample_id, metric; average min, max, avg, total, count
        if not process_df.empty:
            proc_cols = ["min", "max", "avg", "total", "count"]
            present_proc_cols = [c for c in proc_cols if c in process_df.columns]
            if {"sample_id", "metric"} <= set(process_df.columns) and present_proc_cols:
                process_kpis = (
                    process_df
                    .groupby(["sample_id", "metric"], as_index=False)[present_proc_cols]
                    .mean()
                )
            else:
                print(f"⚠️ process_df missing required columns in {read_folder}, skipping process_kpis\n")
                process_kpis = pd.DataFrame()
        else:
            process_kpis = pd.DataFrame()

        # df_resources: group by sample_id, resource_name; average tasks_allocated, available_time_s, utilization
        if not resources_df.empty:
            res_cols = ["tasks_allocated", "available_time_s", "utilization"]
            present_res_cols = [c for c in res_cols if c in resources_df.columns]
            if {"sample_id", "resource_name"} <= set(resources_df.columns) and present_res_cols:
                resources_kpis = (
                    resources_df
                    .groupby(["sample_id", "resource_name"], as_index=False)[present_res_cols]
                    .mean()
                )
            else:
                print(f"⚠️ resources_df missing required columns in {read_folder}, skipping resources_kpis\n")
                resources_kpis = pd.DataFrame()
        else:
            resources_kpis = pd.DataFrame()

        # df_tasks: group by sample_id + task_name; average KPIs
        if not tasks_df.empty:
            group_keys = ["sample_id", "task_name"]
            task_kpi_cols = [
                "count",
                "waiting_min", "waiting_max", "waiting_avg", "waiting_total",
                "processing_min", "processing_max", "processing_avg", "processing_total",
                "cycle_min", "cycle_max", "cycle_avg", "cycle_total",
                "idle_min", "idle_max", "idle_avg", "idle_total",
                "idle_cycle_min", "idle_cycle_max", "idle_cycle_avg", "idle_cycle_total",
                "idle_proc_min", "idle_proc_max", "idle_proc_avg", "idle_proc_total",
            ]

            # keep only columns actually present
            present_cols = [c for c in task_kpi_cols if c in tasks_df.columns]

            if all(k in tasks_df.columns for k in group_keys) and present_cols:
                tasks_kpis = (
                    tasks_df
                    .groupby(group_keys, as_index=False)[present_cols]
                    .mean()
                )
            else:
                print(f"⚠️ tasks_df missing required columns in {read_folder}, skipping tasks_kpis\n")
                tasks_kpis = pd.DataFrame()
        else:
            tasks_kpis = pd.DataFrame()


        # -------- write outputs to run_avg as parquet -------- #

        if not cases_kpis.empty:
            cases_kpis.to_parquet(write_folder / f"cases_kpis_{cases}_cases.parquet", index=False)
        if not process_kpis.empty:
            process_kpis.to_parquet(write_folder / f"process_kpis_{cases}_cases.parquet", index=False)
        if not resources_kpis.empty:
            resources_kpis.to_parquet(write_folder / f"resources_kpis_{cases}_cases.parquet", index=False)
        if not tasks_kpis.empty:
            tasks_kpis.to_parquet(write_folder / f"tasks_kpis_{cases}_cases.parquet", index=False)

        print(
            f"✅ Averaged KPIs for {cases} cases → {write_folder} "
            f"(cases:{len(cases_kpis)}, process:{len(process_kpis)}, "
            f"resources:{len(resources_kpis)}, tasks:{len(tasks_kpis)})\n"
        )
