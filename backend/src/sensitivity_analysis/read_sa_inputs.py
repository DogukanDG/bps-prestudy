import pandas as pd
import numpy as np


def read_sa_inputs(kpi, stat_type, process_kpis, sa_config):
    """
    Filter the process_kpis DataFrame for the selected KPI/stat_type,
    and read sensitivity-analysis configuration stored in sa_config.json.
    
    Parameters
    ----------
    kpi : str
        KPI / metric name (e.g., "cycle_time").
    stat_type : str
        One of: {"min", "max", "avg", "total"}.
    process_kpis : pd.DataFrame
        DataFrame containing KPI statistics per sample/case.
        Must contain at least the columns:
            ["num_cases", "sample_id", "metric", "min", "max", "avg", "total"].
    sa_config : dict
        Sensitivity analysis configuration dictionary loaded from JSON.
        Must always contain:
            - "problem"   : SALib problem dict,
            - "is_sobol"  : bool,
            - "is_groups" : bool,
            - "cases_list": list[int]
        and then:
            If is_sobol is True:
                - "calc_second_order": bool
                - "seed"             : int
            If is_sobol is False (e.g. Morris):
                - "samples"   : 2D array-like,
                - "conf_level": float,
                - "num_levels": int,
                - "seed"      : int

    Returns
    -------
    df_output_metric : pd.DataFrame
        Filtered KPI statistic with columns:
            ["num_cases", "sample_id", stat_type]

    config_values : dict
        If is_sobol is True:
            {
                "problem": dict,
                "is_sobol": True,
                "is_groups": bool,
                "cases_list": list[int],
                "calc_second_order": bool,
                "seed": int,
            }

        If is_sobol is False:
            {
                "problem": dict,
                "is_sobol": False,
                "is_groups": bool,
                "cases_list": list[int],
                "samples": np.ndarray,
                "conf_level": float,
                "num_levels": int,
                "seed": int,
            }
    """

    # ---------------------------
    # 1) Filter process_kpis
    # ---------------------------
    if not isinstance(process_kpis, pd.DataFrame):
        raise TypeError("process_kpis must be a pandas DataFrame")

    required_cols = {"num_cases", "sample_id", "metric", "min", "max", "avg", "total"}
    missing = required_cols - set(process_kpis.columns)
    if missing:
        raise ValueError(f"process_kpis DataFrame missing columns: {missing}")

    if stat_type not in {"min", "max", "avg", "total"}:
        raise ValueError("stat_type must be one of: 'min', 'max', 'avg', 'total'")

    df_metric = process_kpis[process_kpis["metric"] == kpi].copy()
    if df_metric.empty:
        raise ValueError(f"No rows found for metric '{kpi}' in process_kpis DataFrame")

    # Exactly three columns in the end
    df_output_metric = df_metric[["num_cases", "sample_id", stat_type]].copy()
    df_output_metric = df_output_metric.reset_index(drop=True)

    # ---------------------------
    # 2) Parse sa_config
    # ---------------------------
    if not isinstance(sa_config, dict):
        raise TypeError("sa_config must be a dict loaded from JSON.")

    # We always need at least these
    base_required = ["problem", "is_sobol", "is_groups", "cases_list"]
    for key in base_required:
        if key not in sa_config:
            raise ValueError(f"sa_config missing required key: '{key}'")

    problem = sa_config["problem"]
    is_sobol = bool(sa_config["is_sobol"])
    is_groups = bool(sa_config["is_groups"])
    cases_list = sa_config["cases_list"]
    seed = sa_config["seed"]

    # Branch on is_sobol and build DIFFERENT config dicts
    if is_sobol:
        # ----- Sobol: only these keys are required and returned -----
        required_config = [
            "problem",
            "is_sobol",
            "is_groups",
            "cases_list",
            "calc_second_order",
            "seed",
        ]
        for key in required_config:
            if key not in sa_config:
                raise ValueError(f"sa_config missing required key for Sobol: '{key}'")

        calc_second_order = bool(sa_config["calc_second_order"])

        config_values = {
            "problem": problem,
            "is_sobol": True,
            "is_groups" : is_groups,
            "cases_list": cases_list,
            "calc_second_order": calc_second_order,
            "seed": seed,
        }

    else:
        # ----- Non-Sobol (e.g. Morris): these keys are required and returned -----
        required_config = [
            "problem",
            "is_sobol",
            "is_groups",
            "cases_list",
            "samples",
            "conf_level",
            "num_levels",
            "seed",
        ]
        for key in required_config:
            if key not in sa_config:
                raise ValueError(f"sa_config missing required key for non-Sobol: '{key}'")

        samples_raw = sa_config["samples"]
        samples = np.array(samples_raw, dtype=float)

        conf_level = sa_config["conf_level"]
        num_levels = sa_config["num_levels"]

        config_values = {
            "problem": problem,
            "is_sobol": False,
            "is_groups": is_groups,
            "cases_list": cases_list,
            "samples": samples,
            "conf_level": conf_level,
            "num_levels": num_levels,
            "seed": seed,
        }

    return df_output_metric, config_values
