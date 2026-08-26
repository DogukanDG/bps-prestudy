from SALib.analyze import morris as morris_analyze
import numpy as np
import pandas as pd


def morris_analysis(
    stat_type: str,
    df_output_metric: pd.DataFrame,
    problem: dict,
    samples: np.ndarray,
    conf_level: float,
    num_levels: int,
    seed: int,
    cases_list: list[int],
) -> pd.DataFrame:
    """
    Run a Morris sensitivity analysis for a chosen KPI across multiple
    case sizes and collect the results in a single table.

    Behaviour
    ---------
    For each value in `cases_list`, the function:
      - filters `df_output_metric` to rows with that `num_cases`,
      - sorts by `sample_id` and extracts the column given by `stat_type`
        as the response vector `Y`,
      - calls `SALib.analyze.morris.analyze(...)` with the provided
        `problem`, `samples` and analysis options,
      - extracts Morris indices (mu, mu_star, mu_star_conf, sigma),
      - appends one row per parameter/group to the results list.

    Any case for which the data do not align with the design matrix
    (e.g. missing rows, wrong length of Y) is skipped with a warning.

    Parameters
    ----------
    stat_type : str
        Name of the KPI/statistic column in `df_output_metric`
        (e.g. "avg", "min", "max", "total"). Must exist as a column.
    df_output_metric : pd.DataFrame
        Simulation output table with at least:
          - "num_cases" : int, scenario / case size identifier,
          - "sample_id" : int, index matching the design matrix rows,
          - `stat_type` : float, KPI value for each (num_cases, sample_id).
    problem : dict
        SALib Morris problem definition; must contain either
        "num_vars" or "names" to determine dimensionality.
    samples : np.ndarray
        Morris design matrix X (U_var), shape (n_rows, n_vars), used
        to generate the simulations that produced `df_output_metric`.
    conf_level : float
        Confidence level passed to `morris_analyze.analyze` for
        estimating `mu_star_conf` (e.g. 0.95).
    num_levels : int
        Number of levels in the Morris design (must match the design
        used to create `samples`).
    seed : int
        Random seed for the Morris analysis (used internally by SALib).
    cases_list : list[int]
        List of `num_cases` values for which the Morris analysis will
        be computed and reported.

    Returns
    -------
    pd.DataFrame
        If at least one case is successfully analyzed, returns a DataFrame
        with columns:
          - "name"        : parameter or group name from the analysis,
          - "cases"       : num_cases value for this analysis run,
          - "mu"          : mean elementary effect (signed),
          - "mu_star"     : mean absolute elementary effect,
          - "mu_star_conf": confidence interval half-width for mu_star,
          - "sigma"       : standard deviation of elementary effects.
        Rows are sorted by ["cases", "mu_star"] (mu_star descending).

        If no results can be computed for any case (e.g. all skipped),
        returns an empty DataFrame with the same columns.
    """

    # Basic checks
    if stat_type not in df_output_metric.columns:
        raise ValueError(
            f"stat_type='{stat_type}' is not a column in df_output_metric "
            f"(available: {list(df_output_metric.columns)})"
        )

    if "num_cases" not in df_output_metric.columns or "sample_id" not in df_output_metric.columns:
        raise ValueError("df_output_metric must contain 'num_cases' and 'sample_id' columns")

    if not isinstance(problem, dict):
        raise TypeError("problem must be a dict compatible with SALib's Morris problem definition")

    if not isinstance(samples, np.ndarray):
        raise TypeError("samples must be a numpy.ndarray")

    # Determine dimensionality num_vars
    if "num_vars" in problem:
        num_vars = int(problem["num_vars"])
    elif "names" in problem:
        num_vars = len(problem["names"])
    else:
        raise ValueError("problem must contain 'num_vars' or 'names' to determine dimensionality")

    n_rows, n_cols = samples.shape
    if n_cols != num_vars:
        raise ValueError(
            f"Design matrix samples has {n_cols} columns but problem expects {num_vars} variables."
        )

    group_rows: list[dict] = []

    for cases in cases_list:
        # --- Filter df_output_metric for this num_cases ---
        df_cases = df_output_metric[df_output_metric["num_cases"] == cases].copy()

        if df_cases.empty:
            print(f"⚠️ No rows in df_output_metric for num_cases={cases}; skipping")
            continue

        # Prepare Y aligned to the Morris design rows
        Y = (
            df_cases.sort_values("sample_id")[stat_type]
            .astype(float)
            .to_numpy()
        )

        if len(Y) != n_rows:
            print(
                f"⚠️ len(Y)={len(Y)} != rows in samples ({n_rows}) for cases={cases}; skipping"
            )
            continue

        try:
            morris_analysis_result = morris_analyze.analyze(
                problem=problem,
                X=samples,
                Y=Y,
                conf_level=conf_level,
                num_levels=num_levels,
                print_to_console=False,
                seed=seed,
            )
        except Exception as e:
            print(f"⚠️ Morris analyze failed for cases={cases}: {e}")
            continue

        group_names = morris_analysis_result.get("names", None)

        if group_names is None:
            # Fallback: use generic names if missing
            group_names = [f"x{i}" for i in range(num_vars)]

        mu =          np.asarray(morris_analysis_result.get("mu",             np.full(num_vars, np.nan)))
        mu_star =     np.asarray(morris_analysis_result.get("mu_star",        np.full(num_vars, np.nan)))
        mu_star_conf = np.asarray(morris_analysis_result.get("mu_star_conf",  np.full(num_vars, np.nan)))
        sigma =       np.asarray(morris_analysis_result.get("sigma",          np.full(num_vars, np.nan)))

        for group_name, a, b, c, s in zip(group_names, mu, mu_star, mu_star_conf, sigma):
            group_rows.append(
                {
                    "name": group_name,
                    "cases": cases,
                    "mu": float(a),
                    "mu_star": float(b),
                    "mu_star_conf": float(c),
                    "sigma": float(s),
                }
            )

    if not group_rows:
        return pd.DataFrame(
            columns=["name", "cases", "mu", "mu_star", "mu_star_conf", "sigma"]
        )

    return (
        pd.DataFrame(group_rows)
        .sort_values(["cases", "mu_star"], ascending=[True, False])
        .reset_index(drop=True)
    )
