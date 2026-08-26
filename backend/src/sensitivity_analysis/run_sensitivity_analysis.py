from pathlib import Path
import pandas as pd
import numpy as np
import json

from src.sensitivity_analysis.read_sa_inputs import read_sa_inputs
from src.sensitivity_analysis.sobol_analysis import sobol_analysis
from src.sensitivity_analysis.morris_analysis import morris_analysis


def run_sensitivity_analysis(
    kpi: str,
    stat_type: str,
    process_kpis: pd.DataFrame,
    sa_config: dict,
    output_folder: Path,
) -> dict:
    """
    Run a Sobol or Morris sensitivity analysis for a selected KPI/statistic
    and save the results as JSON files in the given output folder.

    Parameters
    ----------
    kpi : str
        Name of the KPI / metric to analyse
        (e.g. "cycle_time", "processing_time").
    stat_type : str
        Statistic to use for the analysis. Must be one of:
        {"min", "max", "avg", "total"} and correspond to a column
        in `process_kpis`.
    process_kpis : pd.DataFrame
        DataFrame with KPI statistics per (sample_id, num_cases).
        Must contain at least the columns:
        ["num_cases", "sample_id", "metric", "min", "max", "avg", "total"].
    sa_config : dict
        Sensitivity-analysis configuration loaded from JSON.
        For Sobol, it must include:
            {"problem", "is_sobol", "is_groups", "cases_list", "calc_second_order"}.
        For Morris, it must include:
            {
                "problem", "is_sobol", "is_groups", "cases_list",
                "samples", "conf_level", "num_levels", "seed"
            }.
    output_folder : Path or str
        Directory where the sensitivity-analysis outputs will be written.
        The function creates the folder if it does not exist and writes:
            - user_config.json
            - sobol_first_and_total_order.json / sobol_second_order.json
              (for Sobol), or
            - morris_first_order.json (for Morris).

    Returns
    -------
    dict
        Summary dictionary with JSON-serializable fields:

        Always:
        {
            "kpi": str,
            "stat_type": str,
            "is_sobol": bool,
            "is_groups": bool,
            "output_folder": str,
            ...
        }

        If is_sobol is True (Sobol analysis):
            - always includes:
                "calc_second_order": bool,
                "sobol_results_first_and_total_order": ...
            - and if calc_second_order is True:
                "sobol_results_second_order": ...

        If is_sobol is False (Morris analysis):
            - includes:
                "morris_results_first_order": ...
    """

    # Helper: recursively convert numpy types to JSON-serializable objects
    def _to_serializable(obj):
        if isinstance(obj, pd.DataFrame):
            return obj.to_dict(orient="records")
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.floating, np.integer)):
            return obj.item()
        if isinstance(obj, dict):
            return {k: _to_serializable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_to_serializable(v) for v in obj]
        return obj

    # Ensure output folder exists
    output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)

    # Dump minimal configuration into user_config.json
    user_config = {
        "kpi": kpi,
        "stat_type": stat_type,
    }

    config_path = output_folder / "user_config.json"
    with open(config_path, "w") as f:
        json.dump(user_config, f, indent=2)
    print(f"Sensitivity-analysis config saved to {config_path}\n")

    # Step 1: filter KPI data and parse SA configuration
    df_output_metric, config_values = read_sa_inputs(
        kpi=kpi,
        stat_type=stat_type,
        process_kpis=process_kpis,
        sa_config=sa_config,
    )

    is_sobol = config_values["is_sobol"]
    is_groups = config_values["is_groups"]
    summary = {
        "kpi": kpi,
        "stat_type": stat_type,
        "is_sobol": bool(is_sobol),
        "is_groups": bool(is_groups),
        "output_folder": str(output_folder),
    }

    if is_sobol:
        calc_second_order = bool(config_values["calc_second_order"])

        sobol_results_first_and_total_order, sobol_results_second_order = sobol_analysis(
            stat_type=stat_type,
            df_output_metric=df_output_metric,
            problem=config_values["problem"],
            calc_second_order=calc_second_order,
            seed=config_values["seed"],
            cases_list=config_values["cases_list"],
        )

        sobol_results_first_and_total_order = (
            sobol_results_first_and_total_order
            .replace({np.nan: None})
            .replace({float("inf"): None, float("-inf"): None})
        )

        # Save Sobol results to JSON
        sobol_first_total_path = output_folder / "sobol_first_and_total_order.json"
        with open(sobol_first_total_path, "w") as f:
            json.dump(_to_serializable(sobol_results_first_and_total_order), f, indent=2)

        if calc_second_order:
            sobol_results_second_order = (
                sobol_results_second_order
                .replace({np.nan: None})
                .replace({float("inf"): None, float("-inf"): None})
            )
            sobol_second_path = output_folder / "sobol_second_order.json"
            with open(sobol_second_path, "w") as f:
                json.dump(_to_serializable(sobol_results_second_order), f, indent=2)

        # Populate return payload for Sobol
        summary["calc_second_order"] = calc_second_order
        summary["sobol_results_first_and_total_order"] = _to_serializable(
            sobol_results_first_and_total_order
        )
        if calc_second_order:
            summary["sobol_results_second_order"] = _to_serializable(
                sobol_results_second_order
            )

    else:
        morris_results_first_order = morris_analysis(
            stat_type=stat_type,
            df_output_metric=df_output_metric,
            problem=config_values["problem"],
            samples=config_values["samples"],
            conf_level=config_values["conf_level"],
            num_levels=config_values["num_levels"],
            seed=config_values["seed"],
            cases_list=config_values["cases_list"],
        )

        morris_results_first_order = (
            morris_results_first_order
            .replace({np.nan: None})
            .replace({float("inf"): None, float("-inf"): None})
        )

        # Save Morris results to JSON
        morris_path = output_folder / "morris_first_order.json"
        with open(morris_path, "w") as f:
            json.dump(_to_serializable(morris_results_first_order), f, indent=2)

        # Populate return payload for Morris
        summary["morris_results_first_order"] = _to_serializable(
            morris_results_first_order
        )

    return summary
