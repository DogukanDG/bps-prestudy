from SALib.analyze import sobol as sobol_analyze
import numpy as np
import pandas as pd


def sobol_analysis(
    stat_type: str,
    df_output_metric: pd.DataFrame,
    problem: dict,
    calc_second_order: bool,
    seed: int,
    cases_list: list[int],
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """
    Run (grouped) Sobol sensitivity analysis for a given KPI statistic
    across multiple case sizes.

    Parameters
    ----------
    stat_type : str
        Name of the statistic column in `df_output_metric`,
        e.g. "avg", "min", "max", or "total".
    df_output_metric : pd.DataFrame
        DataFrame containing the KPI values per sample and case size.
        Must include at least:
            ["num_cases", "sample_id", stat_type].
    problem : dict
        SALib Sobol problem definition. Must define either:
            - "num_vars" (int), or
            - "names" (list of parameter names),
        and may optionally include "groups" for grouped Sobol analysis.
    calc_second_order : bool
        If True, compute second-order indices S2 and S2_conf in addition
        to first-order (S1) and total-order (ST) indices.
    seed : int
        Random seed for the Sobol analysis (used internally by SALib).
    cases_list : list[int]
        List of num_cases values (e.g. [50, 100, 200]) for which the
        analysis is repeated. For each value, Y is taken from the rows
        with that num_cases.

    Returns
    -------
    sobol_results_first_and_total_order : pd.DataFrame
        First- and total-order Sobol indices aggregated for all cases.
        Columns:
            ["group", "cases", "S1", "S1_conf", "ST", "ST_conf"],
        sorted by ["cases", "ST"] (descending ST within each cases).
    sobol_results_second_order : pd.DataFrame or None
        If `calc_second_order` is True and the analysis succeeds:
            Columns:
                ["group_i", "group_j", "S2", "S2_conf", "cases"],
            with upper-triangular S2 pairs for each cases value.
        If `calc_second_order` is False, returns None.
    """

    if stat_type not in df_output_metric.columns:
        raise ValueError(
            f"stat_type='{stat_type}' is not a column in df_output_metric "
            f"(available: {list(df_output_metric.columns)})"
        )

    if "num_cases" not in df_output_metric.columns or "sample_id" not in df_output_metric.columns:
        raise ValueError("df_output_metric must contain 'num_cases' and 'sample_id' columns")

    # Determine dimensionality & group names from problem
    if not isinstance(problem, dict):
        raise TypeError("problem must be a dict compatible with SALib's Sobol problem definition")

    # num_vars: number of variables
    if "num_vars" in problem:
        num_vars = int(problem["num_vars"])
    elif "names" in problem:
        num_vars = len(problem["names"])
    else:
        raise ValueError("problem must contain 'num_vars' or 'names' to determine dimensionality")

    # Group handling
    if "groups" in problem and problem["groups"] is not None:
        groups = list(problem["groups"])
        # preserve first-appearance order for group names
        seen = {}
        group_names = [seen.setdefault(g, g) for g in groups if g not in seen]
        group_names_len = len(group_names)
    else:
        group_names = [f"x{i}" for i in range(num_vars)]
        group_names_len = num_vars

    group_rows: list[dict] = []
    group_rows_second_order: list[dict] = []

    # Loop over each num_cases
    for cases in cases_list:
        # Filter df_output_metric by num_cases
        df_cases = df_output_metric[df_output_metric["num_cases"] == cases].copy()

        if df_cases.empty:
            # Nothing for this case value → skip
            continue

        # Build Y using the chosen stat_type, sorted by sample_id
        Y = (
            df_cases.sort_values("sample_id")[stat_type]
            .astype(float)
            .to_numpy()
        )

        # Run Sobol analysis
        try:
            sobol_analysis_result = sobol_analyze.analyze(
                problem=problem,
                Y=Y,
                calc_second_order=calc_second_order,
                print_to_console=False,
                seed=seed,
            )
        except Exception:
            # If Sobol fails for this num_cases, store NaNs for this cases entry
            for g_name in group_names:
                group_rows.append(
                    {
                        "group": g_name,
                        "cases": cases,
                        "S1": np.nan,
                        "S1_conf": np.nan,
                        "ST": np.nan,
                        "ST_conf": np.nan,
                    }
                )
            if calc_second_order:
                group_rows_second_order.append(
                    {
                        "group_i": np.nan,
                        "group_j": np.nan,
                        "S2": np.nan,
                        "S2_conf": np.nan,
                        "cases": cases,
                    }
                )
            continue

        # ---- First & total order ----
        S1 = np.asarray(sobol_analysis_result.get("S1", np.full(group_names_len, np.nan)))
        S1_conf = np.asarray(sobol_analysis_result.get("S1_conf", np.full(group_names_len, np.nan)))
        ST = np.asarray(sobol_analysis_result.get("ST", np.full(group_names_len, np.nan)))
        ST_conf = np.asarray(sobol_analysis_result.get("ST_conf", np.full(group_names_len, np.nan)))

        for g_name, s1, s1c, st, stc in zip(group_names, S1, S1_conf, ST, ST_conf):
            group_rows.append(
                {
                    "cases": cases,
                    "group": g_name,
                    "S1": float(s1),
                    "S1_conf": float(s1c),
                    "ST": float(st),
                    "ST_conf": float(stc),
                }
            )

        # ---- Second order (S2) ----
        if calc_second_order:
            S2 = np.asarray(sobol_analysis_result.get("S2", np.full((group_names_len, group_names_len), np.nan)), dtype=float)
            S2_conf = np.asarray(sobol_analysis_result.get("S2_conf", np.full((group_names_len, group_names_len), np.nan)), dtype=float)

            if S2.ndim != 2 or S2.shape[0] != S2.shape[1] or S2.shape[0] != len(group_names):
                raise ValueError(
                    f"S2 must be a square matrix with size == len(group_names); "
                    f"got S2.shape={S2.shape}, len(group_names)={len(group_names)}"
                )

            iu = np.triu_indices_from(S2, k=1)

            for i, j in zip(*iu):
                group_rows_second_order.append(
                    {
                        "cases": cases,
                        "group_i": group_names[i],
                        "group_j": group_names[j],
                        "S2": float(S2[i, j]),
                        "S2_conf": float(S2_conf[i, j]),
                    }
                )

    # Build DataFrames from collected rows
    sobol_results_first_and_total_order = (
        pd.DataFrame(group_rows)
        .sort_values(["cases", "ST"], ascending=[True, False])
        .reset_index(drop=True)
    )

    if calc_second_order:
        sobol_results_second_order = (
            pd.DataFrame(group_rows_second_order)
            .sort_values(["cases", "S2"], ascending=[True, False])
            .reset_index(drop=True)
        )
    else:
        sobol_results_second_order = None

    return sobol_results_first_and_total_order, sobol_results_second_order
