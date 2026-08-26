from typing import Dict, Any
from pathlib import Path
import numpy as np
import json
import os

from prosimos.simulation_engine import run_simulation as _run_simulation
from src.simulation_pipeline.extract_parameters.extract_parameters import extract_parameters
from src.simulation_pipeline.extract_parameters.extract_tasks_resources import extract_tasks_resources
from src.simulation_pipeline.extract_parameters.extract_arrival_distribution import extract_arrival_distribution
from src.simulation_pipeline.extract_parameters.write_distributions_warning_files import write_tasks_resources_warning_file
from src.simulation_pipeline.extract_parameters.write_distributions_warning_files import write_arrival_distribution_warning_file
from src.simulation_pipeline.sampling.run_sampling import run_sampling
from src.simulation_pipeline.convert_samples.convert_samples import convert_samples
from src.simulation_pipeline.convert_samples.write_converted_samples import write_all_samples_to_json_files
from src.simulation_pipeline.convert_samples.fix_tasks_resources import fix_tasks_resources
from src.simulation_pipeline.convert_samples.write_fix_tasks_resources import write_fix_tasks_resources
from src.simulation_pipeline.convert_samples.fix_arrival_distribution import fix_arrival_distribution
from src.simulation_pipeline.convert_samples.write_fix_arrival_distribution import write_fix_arrival_distribution
from src.simulation_pipeline.simulation.simulate_samples import simulate_samples


def run_simulation_pipeline(
    *,
    bpmn_path: str,
    json_path: str,
    is_sobol: bool,
    is_groups: bool,
    is_gateway: bool,
    is_arrival_distribution: bool,
    is_arrival_calendar: bool,
    is_tasks_resources: bool,
    is_resource_calendars: bool,
    is_resource_numbers: bool,
    n_samples: int | None = None,
    calc_second_order: bool = False,
    n_trajectories: int | None = None,
    num_levels: int | None = None,
    replication_runs: int,
    cases_list: list[int],
    simulation_results_folder: str,
    seed: int | None = None,
) -> Dict[str, Any]:
    """
    Orchestrate sampling, parameter conversion, JSON generation, and process simulation.

    This high-level driver function performs the full pipeline:
      1. Validates the sensitivity-analysis configuration (within / between groups).
      2. Loads the base JSON configuration.
      3. Extracts parameter tables from the JSON according to selected dimensions
         (gateways, arrival distribution, arrival calendar, task–resources,
          resource calendars, resource numbers).
      4. Writes any extraction warnings (task–resources, arrival distribution)
         to dedicated text files in the results folder.
      5. Runs the sampling procedure (Sobol or Morris) and builds the SALib
         `problem` definition and sample matrix.
      6. Converts sampled uniforms into domain-specific values (probabilities,
         distributions, calendars, counts).
      7. Writes all sampled configurations to chunked JSON files for Prosimos.
      8. Runs the replication process simulation and the base Prosimos
         simulation on the original BPMN/JSON.
      9. Returns a small summary dict containing the maximum cycle time.

    Parameters
    ----------
    bpmn_path : str
        Path to the BPMN model file used by Prosimos.
    json_path : str
        Path to the base JSON configuration file for the process.
    is_sobol : bool
        If True, use Sobol (global) sensitivity analysis; if False, use
        Morris (local) sensitivity analysis.
    is_groups : bool
        If True, run a between-groups design (at least two dimensions
        must be active). If False, run a within-group design (exactly
        one dimension must be active).
    is_gateway : bool
        Include gateway branching probabilities in the sensitivity analysis.
    is_arrival_distribution : bool
        Include arrival time distribution parameters in the sensitivity analysis.
    is_arrival_calendar : bool
        Include arrival-time calendar parameters (global arrival slots) in SA.
    is_tasks_resources : bool
        Include task–resource distribution parameters in the analysis.
    is_resource_calendars : bool
        Include resource calendar parameters (per-resource schedules).
    is_resource_numbers : bool
        Include resource-number parameters (resource amounts).
    n_samples : int or None, optional
        Number of Sobol samples (for global SA). Ignored for Morris.
    calc_second_order : bool, optional
        If True, request second-order Sobol indices during sampling/analysis.
    n_trajectories : int or None, optional
        Number of trajectories for Morris (local) SA. Ignored for Sobol.
    num_levels : int or None, optional
        Number of levels for Morris sampling (e.g. 4, 6, 8). May also be
        passed on to downstream SA routines.
    replication_runs : int
        Number of replication runs per configuration in the Prosimos
        simulation.
    cases_list : list[int]
        List of case counts to simulate (e.g. [100, 500, 1000]) for the
        SA simulation part.
    simulation_results_folder : str
        Target directory where all outputs (user_config, warnings, sampled
        JSON files, simulation outputs) will be written. Must not exist
        beforehand.
    seed : int or None, optional
        Random seed used throughout sampling and peeling logic to make
        results reproducible.

    Returns
    -------
    Dict[str, Any]
        A summary dictionary with at least:
        {
            "status": "success",
            "max_cycle_time": float  # maximum cycle time from the base run
        }
        If the results folder already exists or the SA configuration is
        invalid, a ValueError is raised instead of returning.
    """

    # --- Ensure results folder is unique and create it ---
    if os.path.exists(simulation_results_folder):
        # This will be caught in Flask and returned as {"error": "..."}
        raise ValueError(
            f"Results folder '{simulation_results_folder}' already exists. "
            "Please choose a different name."
        )
    os.makedirs(simulation_results_folder, exist_ok=False)

    def _safe(x):
        """Make values JSON serializable."""
        if isinstance(x, Path):
            return str(x)
        if isinstance(x, (np.integer, int)):
            return int(x)
        if isinstance(x, (np.floating, float)):
            return float(x)
        if isinstance(x, (list, tuple)):
            return [_safe(v) for v in x]
        if isinstance(x, dict):
            return {k: _safe(v) for k, v in x.items()}
        return x

    user_config = {
        "is_sobol": is_sobol,
        "is_groups": is_groups,
        "is_gateway": is_gateway,
        "is_arrival_distribution": is_arrival_distribution,
        "is_arrival_calendar": is_arrival_calendar,
        "is_tasks_resources": is_tasks_resources,
        "is_resource_calendars": is_resource_calendars,
        "is_resource_numbers": is_resource_numbers,
        "n_samples": n_samples,
        "calc_second_order": calc_second_order,
        "n_trajectories": n_trajectories,
        "num_levels": num_levels,
        "replication_runs": replication_runs,
        "cases_list": cases_list,
        "simulation_results_folder": simulation_results_folder,
        "seed": seed,
    }

    with open(os.path.join(simulation_results_folder, "user_config.json"), "w") as f:
        json.dump(user_config, f, indent=2)
    print(f"Saved all configuration input to {simulation_results_folder}\\user_config.json")

    # --- Build SA context from flags
    dims_map = {
        "is_gateway": is_gateway,
        "is_arrival_distribution": is_arrival_distribution,
        "is_arrival_calendar": is_arrival_calendar,
        "is_tasks_resources": is_tasks_resources,
        "is_resource_calendars": is_resource_calendars,
        "is_resource_numbers": is_resource_numbers,
    }
    selected_dimensions = [k for k, v in dims_map.items() if v]

    # --- Backend-side validation (mirrors the frontend rules)
    if is_groups:
        if len(selected_dimensions) < 2:
            raise ValueError(
                "For 'Between groups' (is_groups=True), select at least two dimensions."
            )
    else:
        if len(selected_dimensions) != 1:
            raise ValueError(
                "For 'Within group' (is_groups=False), select exactly one dimension."
            )
        
    # -------------------------------------------------------------------------
    # 1) LOAD JSON CONFIG
    # -------------------------------------------------------------------------
    with open(json_path, "r") as f:
        config = json.load(f)

    # -------------------------------------------------------------------------
    # 2) EXTRACT PARAMETER TABLES
    # -------------------------------------------------------------------------
    parameter_tables, extract_tasks_resources_warning, extract_arrival_distribution_warning = extract_parameters(
        config,
        bpmn_path=bpmn_path,
        is_gateway=is_gateway,
        is_arrival_distribution=is_arrival_distribution,
        is_arrival_calendar=is_arrival_calendar,
        is_tasks_resources=is_tasks_resources,
        is_resource_calendars=is_resource_calendars,
        is_resource_numbers=is_resource_numbers,
        seed=seed
    )

    # Write tasks-resources and arrival-distribution warnings to files (if any)
    # -------------------------------------------------------------------------
    if extract_tasks_resources_warning:
        warning_path_tr = Path(simulation_results_folder) / "extract_tasks_resources_warning.txt"
        write_tasks_resources_warning_file(extract_tasks_resources_warning, warning_path_tr)
    if extract_arrival_distribution_warning:
        warning_path_ad = Path(simulation_results_folder) / "extract_arrival_distribution_warning.txt"
        write_arrival_distribution_warning_file(extract_arrival_distribution_warning, warning_path_ad)

    # -------------------------------------------------------------------------
    # Fix tasks_resources and arrival_distribution if not selected for SA
    # -------------------------------------------------------------------------
    if not is_tasks_resources:
        tasks_resources_df, _ = extract_tasks_resources(config, bpmn_path)
        fix_tasks_resources_df = fix_tasks_resources(tasks_resources_df)
        write_fix_tasks_resources(fix_tasks_resources_df, config)

    if not is_arrival_distribution:
        arrival_distribution_df, _ = extract_arrival_distribution(config)
        fix_arrival_distribution_df = fix_arrival_distribution(arrival_distribution_df)
        write_fix_arrival_distribution(config, fix_arrival_distribution_df)

    merged_parameters, problem, sensitivity_analysis_samples, simulation_samples = run_sampling(
        parameter_tables,
        is_sobol=is_sobol,
        is_groups=is_groups,
        n_samples=n_samples,
        calc_second_order=calc_second_order,
        n_trajectories=n_trajectories,
        num_levels=num_levels,
        seed=seed,
        is_gateway=is_gateway,
        is_arrival_distribution=is_arrival_distribution,
        is_arrival_calendar=is_arrival_calendar,
        is_tasks_resources=is_tasks_resources,
        is_resource_calendars=is_resource_calendars,
        is_resource_numbers=is_resource_numbers,
    )

    converted_samples = convert_samples(
        samples=simulation_samples,
        merged_parameters=merged_parameters,
        parameter_tables=parameter_tables,
        is_gateway=is_gateway,
        is_arrival_distribution=is_arrival_distribution,
        is_arrival_calendar=is_arrival_calendar,
        is_tasks_resources=is_tasks_resources,
        is_resource_calendars=is_resource_calendars,
        is_resource_numbers=is_resource_numbers
    )

    write_all_samples_to_json_files(
        data=config,
        samples=simulation_samples,
        is_sobol=is_sobol,
        is_gateway=is_gateway,
        is_arrival_distribution=is_arrival_distribution,
        is_arrival_calendar=is_arrival_calendar,
        is_tasks_resources=is_tasks_resources,
        is_resource_calendars=is_resource_calendars,
        is_resource_numbers=is_resource_numbers,
        gateways=parameter_tables.get("gateways"),
        gateways_converted=converted_samples.get("gateways"),
        tasks_resources=parameter_tables.get("tasks_resources"),
        tasks_resources_converted=converted_samples.get("tasks_resources"),
        base_calendar=parameter_tables.get("base_calendar"),
        arrival_calendar_converted=converted_samples.get("arrival_calendar"),
        resource_calendars=parameter_tables.get("resource_calendars"),
        resource_calendars_converted=converted_samples.get("resource_calendars"),
        arrival_distribution_converted=converted_samples.get("arrival_distribution"),
        resource_numbers=parameter_tables.get("resource_numbers"),
        resource_numbers_converted=converted_samples.get("resource_numbers"),
        sobol_chunk_size=n_samples,
        morris_chunk_size=n_trajectories,
        simulation_results_folder=simulation_results_folder,
    )
    
    simulate_samples(
        is_sobol=is_sobol,
        is_groups=is_groups,
        problem=problem,
        bpmn_path=bpmn_path,
        replication_runs=replication_runs,
        cases_list=cases_list,
        simulation_results_folder=simulation_results_folder,
        samples=sensitivity_analysis_samples,
        num_levels=num_levels,
        calc_second_order=calc_second_order,
        seed=seed,
    )

    return (
        "The sampling and simulation pipeline has completed successfully. "
        f"All results have been written to the directory "
        f"'{simulation_results_folder}'."
    )
