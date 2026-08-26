from flask import Flask, request, jsonify
from datetime import datetime
from flask_cors import CORS
from pathlib import Path
import pandas as pd
import traceback
import json
import sys
import os
import re

from src.simulation_pipeline.upload_file_handler import extract_uploaded_files, cleanup_files
from src.simulation_pipeline.run_simulation_pipeline import run_simulation_pipeline
from src.sensitivity_analysis.run_sensitivity_analysis import run_sensitivity_analysis
from src.simod.run_simod import run_simod


app = Flask(__name__)
CORS(app)


def to_bool(v):
    """Coerce incoming form values ('true'/'false'/bool/None) to bool."""
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def get_flag(form, key, default=True):
    """Read a boolean analysis flag, defaulting to True if not present."""
    return to_bool(form.get(key, default))


def get_int_param(form, key, *, default=None, min_value=None, allow_empty=False):
    """
    Read an integer parameter from form data.

    - If not present and default is not None -> returns default.
    - If allow_empty and value is empty -> returns None.
    - Otherwise parses as int and optionally enforces min_value.
    """
    raw = form.get(key, None)

    if raw is None or raw == "":
        if allow_empty:
            return None
        if default is not None:
            return default
        raise ValueError(f"Missing required integer parameter: {key}")

    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be an integer.")

    if min_value is not None and value < min_value:
        raise ValueError(f"{key} must be >= {min_value}.")

    return value


@app.route("/simulate", methods=["POST"])
def simulate():
    """
    Handle process simulation + sensitivity-analysis setup.

    This endpoint:
      1. Receives a BPMN file and a JSON configuration file (multipart/form-data).
      2. Reads analysis options from form fields (Sobol vs. Morris, flags for
         parameter dimensions, sampling settings, replication runs, case counts).
      3. Builds a unique simulation results folder and passes all arguments to
         `run_simulation_pipeline`, which performs sampling, simulation, and
         writes all outputs to disk.
      4. Returns a JSON response with a summary of the simulation run.

    Form fields
    -----------
    bpmn : file
        Uploaded BPMN model.
    json : file
        Uploaded Prosimos JSON configuration.
    is_sobol : bool-like, optional
        "true"/"false" (default: true). If false, Morris is used.
    is_groups : bool-like, optional
        "true"/"false" (default: true). Controls grouped vs within-dimension SA.
    is_gateway, is_arrival_distribution, is_arrival_calendar,
    is_tasks_resources, is_resource_calendars, is_resource_numbers : bool-like
        Flags indicating which parameter dimensions are included in the SA scope.
    n_samples : int, optional
        Sobol base sample size (if is_sobol=True).
    calc_second_order : bool-like, optional
        Whether to compute second-order Sobol indices.
    n_trajectories : int, optional
        Number of Morris trajectories (if is_sobol=False).
    num_levels : int, optional
        Number of levels in Morris design (if is_sobol=False).
    seed : int, optional
        Random seed for sampling.
    cases_list : JSON str
        JSON-encoded list of case volumes, e.g. "[100, 500, 3000]".
    replication_runs : int, optional
        Number of Replication runs per case volume (default: 1).
    simulation_results_folder : str, optional
        Custom folder name under
        "output/simulation_and_sensitivity_analysis_outputs/".

    Returns
    -------
    flask.Response
        JSON with either:
          - success summary returned by `run_simulation_pipeline`, HTTP 200
          - {"error": "..."} and HTTP 400/500 on failure.
    """
    bpmn_path = json_path = None
    try:
        # 1) Extract uploaded files (BPMN + JSON)
        bpmn_path, json_path = extract_uploaded_files(request)

        # 2) Read analysis method + flags (sent by the frontend as booleans)
        # Frontend sends: is_sobol (true/false). If false -> Morris.
        is_sobol = to_bool(request.form.get("is_sobol", True))
        # Frontend sends: is_groups (true/false). If false -> within groups.
        is_groups = to_bool(request.form.get("is_groups", True))

        # 3) Dimension flags
        flags = {
            "is_gateway":              get_flag(request.form, "is_gateway", True),
            "is_arrival_distribution": get_flag(request.form, "is_arrival_distribution", True),
            "is_arrival_calendar":     get_flag(request.form, "is_arrival_calendar", True),
            "is_tasks_resources":      get_flag(request.form, "is_tasks_resources", True),
            "is_resource_calendars":   get_flag(request.form, "is_resource_calendars", True),
            "is_resource_numbers":     get_flag(request.form, "is_resource_numbers", True),
        }

        # 4) Sensitivity-analysis-specific parameters
        # ---- Sobol ----
        n_samples = None
        calc_second_order = False

        # ---- Morris ----
        n_trajectories = None
        num_levels = None

        # Seed (optional)
        seed = None

        if is_sobol:
            # If not present, default to 256; must be integer > 1
            n_samples = get_int_param(
                request.form,
                "n_samples",
                default=256,
                min_value=2,
                allow_empty=False,
            )
            calc_second_order = to_bool(
                request.form.get("calc_second_order", False)
            )
        else:
            # Morris: number of trajectories (default 30, >1)
            n_trajectories = get_int_param(
                request.form,
                "n_trajectories",
                default=30,
                min_value=2,
                allow_empty=False,
            )
            # Morris: number of levels (default 6, >=4)
            num_levels = get_int_param(
                request.form,
                "num_levels",
                default=6,
                min_value=4,
                allow_empty=False,
            )

        # Seed (optional, integer > 1 if provided)
        raw_seed = request.form.get("seed", "").strip()
        if raw_seed != "":
            seed = get_int_param(
                request.form,
                "seed",
                default=None,
                min_value=2,
                allow_empty=True,
            )

        # 5) Common sensitivity parameters: cases_list and replication_runs
        # Frontend sends case_counts as JSON string, e.g. "[100, 500, 3000]"
        raw_cases = request.form.get("cases_list", "").strip()
        if not raw_cases:
            # If frontend somehow sends nothing, fall back to [100]
            cases_list = [100]
        else:
            try:
                tmp = json.loads(raw_cases)
            except json.JSONDecodeError:
                raise ValueError(
                    "case_counts must be a JSON-encoded list of positive integers."
                )

            if not isinstance(tmp, list) or len(tmp) == 0:
                raise ValueError(
                    "case_counts must be a non-empty JSON list of positive integers."
                )

            cases_list = []
            for v in tmp:
                # Accept int or numeric string
                if isinstance(v, str):
                    try:
                        v_int = int(v)
                    except ValueError:
                        raise ValueError(
                            "case_counts must contain only positive integers."
                        )
                elif isinstance(v, (int, float)):
                    v_int = int(v)
                    if v_int != v:
                        raise ValueError(
                            "case_counts values must be integers (no decimals)."
                        )
                else:
                    raise ValueError(
                        "case_counts must contain only integers or numeric strings."
                    )

                if v_int <= 0:
                    raise ValueError(
                        "case_counts must contain only positive integers."
                    )
                cases_list.append(v_int)

        # Number of Replication Runs (n_replication_runs / replication_runs)
        replication_runs = get_int_param(
            request.form,
            "replication_runs",
            default=1,
            min_value=1,
            allow_empty=False,
        )

        simulation_results_folder = request.form.get(
            "simulation_results_folder", ""
        ).strip()

        # Base directory for simulation + sensitivity analysis
        base_output = os.path.join("output", "simulation_and_sensitivity_analysis_outputs")

        # If user gives a name, prepend the base path
        if simulation_results_folder:
            simulation_results_folder = os.path.join(base_output, simulation_results_folder)
        else:
            # Auto-generate if user didn't provide a name
            simulation_results_folder = os.path.join(
                base_output,
                f"Simulation and Sensitivity Analysis Results {datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )

        # Abort if such folder already exists
        if os.path.exists(simulation_results_folder):
            return jsonify({
                "error": (
                    f'Simulation and sensitivity analysis results folder "{simulation_results_folder}" already exists. '
                    f'Please choose another name.'
                )
            }), 400

        # 6) Run simulation (runner extended to accept replication_runs and cases_list)
        result = run_simulation_pipeline(
            bpmn_path=bpmn_path,
            json_path=json_path,
            is_sobol=is_sobol,
            is_groups=is_groups,
            n_samples=n_samples,
            calc_second_order=calc_second_order,
            n_trajectories=n_trajectories,
            num_levels=num_levels,
            seed=seed,
            replication_runs=replication_runs,
            cases_list=cases_list,
            simulation_results_folder=simulation_results_folder,
            **flags,
        )

        return result, 200

    except Exception as e:
        return jsonify({
            "error": str(e),
            "error_type": type(e).__name__,
            "trace": traceback.format_exc(),
        }), 500

    finally:
        cleanup_files(bpmn_path, json_path)


def get_latest_simulation_folder(base_dir="output/simulation_and_sensitivity_analysis_outputs"):
    """
    Find the most recently modified simulation results folder.

    Parameters
    ----------
    base_dir : str, optional
        Root directory containing simulation result subfolders.
        Defaults to "output/simulation_and_sensitivity_analysis_outputs".

    Returns
    -------
    str
        Name of the latest (most recently modified) subfolder inside `base_dir`.

    Raises
    ------
    ValueError
        If `base_dir` does not exist or contains no subdirectories.
    """
    base = Path(base_dir)
    if not base.exists():
        raise ValueError("Output directory does not exist.")

    candidates = [
        p for p in base.iterdir()
        if p.is_dir()
    ]

    if not candidates:
        raise ValueError("No simulation results folder found inside output/")

    # Sort by modification time descending
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0].name


@app.route("/sensitivity-analysis", methods=["POST"])
def sensitivity_analysis():
    """
    Run Sobol or Morris sensitivity analysis on existing simulation results.

    This endpoint:
      1. Selects a simulation run folder (explicitly or via latest-folder lookup).
      2. Loads the sensitivity-analysis configuration from `sa_config.json`.
      3. Loads and concatenates `process_kpis_*.parquet` files for all case sizes.
      4. Calls `run_sensitivity_analysis` with the requested KPI and statistic.
      5. Writes the SA results to a new folder under
         `<run_folder>/sensitivity_analysis_outputs/`.

    JSON body
    ---------
    run_folder : str, optional
        Name of the simulation folder under
        "output/simulation_and_sensitivity_analysis_outputs".
        If empty, the latest folder is auto-selected.
    kpi : str
        KPI/metric to analyze (e.g. "cycle_time").
    stat_type : str
        Statistic to analyze: one of {"min", "max", "avg", "total"}.
    output_folder : str, optional
        Custom name for the sensitivity-analysis output folder.

    Returns
    -------
    flask.Response
        JSON summary from `run_sensitivity_analysis` on success (HTTP 200),
        or {"error": "..."} with HTTP 400/500 on failure.
    """
    try:
        data = request.get_json(force=True)

        run_folder = data.get("run_folder", "").strip()
        kpi = data.get("kpi", "").strip()
        stat_type = data.get("stat_type", "").strip()
        output_folder = data.get("output_folder", "").strip()

        if not kpi:
            return jsonify({"error": "Missing parameter: kpi"}), 400
        if not stat_type:
            return jsonify({"error": "Missing parameter: stat_type"}), 400

        # If no folder given → choose latest simulation results automatically
        if not run_folder:
            run_folder = get_latest_simulation_folder()
            print("Auto-selected latest simulation folder:", run_folder, "\n")

        # Build the input folder path
        base_output = Path("output/simulation_and_sensitivity_analysis_outputs")
        sa_dir = base_output / run_folder / "sensitivity_analysis_inputs"

        if not sa_dir.exists():
            return jsonify({"error": f"sensitivity_analysis_inputs not found at {sa_dir}"}), 400

        # Gather sa_config.json file
        config_files = list(sa_dir.rglob("sa_config.json"))

        # Load sa_config.json
        with open(config_files[0], "r") as f:
            sa_config = json.load(f)

        # Build output path for sensitivity analysis outputs
        output_dir = base_output / run_folder / "sensitivity_analysis_outputs"

        if output_folder:
            output_folder = os.path.join(output_dir, output_folder)
        else:
            output_folder = os.path.join(
                output_dir,
                f"Sensitivity Analysis Results {datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )

        # Abort if such folder already exists
        if os.path.exists(output_folder):
            return jsonify({
                "error": (
                    f'Sensitivity analysis results folder "{output_folder}" already exists. '
                    f'Please choose another name.'
                )
            }), 400
        
        # Recursively gather all parquet files
        parquet_files = list(sa_dir.rglob("process_kpis_*.parquet"))

        if not parquet_files:
            return jsonify({"error": "No process_kpis_*.parquet found inside sensitivity_analysis_inputs"}), 400
        if not config_files:
            return jsonify({"error": "No sa_config.json found inside sensitivity_analysis_inputs"}), 400

        parquet_files_processed = []

        for pfile in parquet_files:
            # Read parquet
            df = pd.read_parquet(pfile)

            # Drop 'count' if present
            if "count" in df.columns:
                df = df.drop(columns=["count"])

            # Extract case number from filename
            # Example filenames:
            #   process_kpis_100_cases.parquet
            #   process_kpis_3000_cases.parquet
            # Regex: find a group of digits between underscores
            match = re.search(r"process_kpis_(\d+)_cases", pfile.stem)
            if not match:
                raise ValueError(f"Could not extract num_cases from filename: {pfile.name}")

            num_cases = int(match.group(1))

            # Add the num_cases column
            df["num_cases"] = num_cases

            # Accumulate
            parquet_files_processed.append(df)

        # Concat all process_kpis
        process_kpis = pd.concat(parquet_files_processed, ignore_index=True)

        # Call your analysis runner
        result = run_sensitivity_analysis(
            kpi=kpi,
            stat_type=stat_type,
            process_kpis=process_kpis,
            sa_config=sa_config,
            output_folder=output_folder,
        )

        return jsonify(result), 200

    except Exception as e:
        return jsonify({
            "error": str(e),
            "error_type": type(e).__name__,
            "trace": traceback.format_exc(),
        }), 500
    

@app.route("/simod", methods=["POST"])
def simod():
    """
    Handle SIMOD model discovery requests.

    This endpoint expects three uploaded files (multipart/form-data),
    saves them to a timestamped folder, and runs SIMOD:

      - train_csv  (CSV): training event log

    Files are stored under:
      output/simod_outputs/SIMOD YYYYMMDD_HHMMSS/inputs/
    and SIMOD outputs are written to:
      output/simod_outputs/SIMOD YYYYMMDD_HHMMSS/outputs/

    Form fields
    -----------
    train_csv : file
        Training event log in CSV format.

    results_folder_name : str, optional
        Optional folder name under output/simod_outputs/.
        If omitted or empty, a timestamped name is used.

    Returns
    -------
    flask.Response
        JSON summary returned by `run_simod` (including input/output paths)
        on success (HTTP 200), or {"error": "..."} with HTTP 400/500 on
        missing files or runtime errors.
    """
    try:
        train_file = request.files.get("train_csv")

        if not train_file or not train_file.filename:
            return jsonify({"error": "Missing train_csv file."}), 400

        # Optional user-provided folder name
        results_folder_name = request.form.get("results_folder_name", "")
        results_folder_name = (results_folder_name or "").strip()

        # Build output folder name, but don't create it here
        base_output = Path("output") / "simod_outputs"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # If user did not provide a name, fall back to timestamped naming
        folder_name = results_folder_name if results_folder_name else f"SIMOD {timestamp}"

        # Basic sanitization to prevent path traversal and illegal names
        # (keeps it predictable across OSes)
        folder_name = folder_name.replace("\\", "_").replace("/", "_").replace("..", "_").strip()
        if not folder_name:
            folder_name = f"SIMOD {timestamp}"

        output_folder = base_output / folder_name

        output_folder = Path(output_folder)
        if output_folder.exists():
            raise ValueError(f'Output folder "{output_folder}" already exists.')

        # Pass raw files + folder target into run_simod
        result = run_simod(
            train_file=train_file,
            output_folder=output_folder,
        )

        return result, 200

    except Exception as e:
        return jsonify({
            "error": str(e),
            "error_type": type(e).__name__,
            "trace": traceback.format_exc(),
        }), 500


@app.route("/health")
def health():
    return jsonify({"status": "healthy", "python": sys.version})


if __name__ == "__main__":
    # app.run(host="0.0.0.0", port=5000, debug=False)
    # debug=True for dev purposes
    app.run(host="0.0.0.0", port=5000, debug=True)