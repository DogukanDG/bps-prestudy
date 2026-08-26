from typing import Tuple, Dict, Any
from SALib.sample import sobol
import pandas as pd
import numpy as np


def sobol_sampling(
    merged_parameters: pd.DataFrame,
    *,
    n_samples: int = 128,
    calc_second_order: bool = False,
    seed: int | None = None,
    is_groups: bool,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Build a SALib Sobol problem (with groups) and generate a Sobol
    sampling matrix for all active parameters.

    Behaviour
    ---------
    - Uses `merged_parameters["name"]` as parameter names.
    - Uses `merged_parameters["groups"]` as group labels:
    - All parameters share the same bounds [0.05, 0.95].
    - Calls `SALib.sample.sobol.sample(...)` with the given options.

    Parameters
    ----------
    merged_parameters : pd.DataFrame
        Parameter index table with at least the columns:
          - "name"   : parameter identifiers (str)
          - "groups" : group labels or singletongs for ungrouped parameters.
    n_samples : int, default 128
        Base Sobol sample size N. The actual number of generated rows
        follows SALib's Sobol design (e.g. (2 + calc_second_order) * N * D).
    calc_second_order : bool, default False
        If True, Sobol sampling includes the extra blocks needed to
        compute second-order Sobol indices.
    seed : int or None, default None
        Random seed passed to SALib for reproducible sampling.
    is_groups: bool
        If True, use group-based SA (group labels in the merged parameters).
        If False, treat parameters individually (except for gateway grouping).

    Returns
    -------
    sensitivity_analysis_samples : np.ndarray
        Sobol sample matrix in [0, 1], shape (M, D), where D is the number
        of parameters and M is determined by SALib from N, D and
        `calc_second_order`.
    problem : dict[str, Any]
        SALib problem dictionary with keys:
          - "num_vars" : int, number of parameters D
          - "names"    : list[str], parameter names
          - "bounds"   : list[list[float]], [0.05, 0.95] for each parameter
          - "groups"   : list[str], group name for each parameter
    simulation_samples : np.ndarray
        Expanded sample matrix of shape (n_rows, M) where M is the number
        of rows in merged_parameters. Maps SALib samples back to all
        individual parameters by broadcasting grouped columns.
    """
    
    # Extract parameter names and build SALib groups
    # Initialize
    names: list[str] = []
    groups: list[str] = []
    types: list[str] = []
    D = 0

    def distinct_preserve_order(seq: list[str]) -> list[str]:
        return list(dict.fromkeys(seq))

    # 1. Distinct types in first-appearance order
    types_in_order = distinct_preserve_order(
        merged_parameters["type"].astype(str).tolist()
    )

    # 2. Loop over types and append inline
    for t in types_in_order:
        subset = merged_parameters.loc[
            merged_parameters["type"].astype(str).eq(t),
            ["name", "groups"],
        ]

        if subset.empty:
            continue

        # ----- Gateway logic -----
        if t == "gateway":
            gw_names = subset["name"].astype(str).tolist()
            names.extend(gw_names)
            types.extend([t] * len(gw_names))
            D += len(gw_names)

            gw_groups = subset["groups"].astype(str).tolist()

            if not is_groups:
                groups.extend(gw_groups)
            else:
                groups.extend(["Gateways"] * len(gw_names))

        # ----- Non-gateway logic -----
        else:
            block_groups = subset["groups"].astype(str).tolist()
            block_groups_distinct = distinct_preserve_order(block_groups)

            names.extend(block_groups_distinct)
            groups.extend(block_groups_distinct)
            types.extend([t] * len(block_groups_distinct))
            D += len(block_groups_distinct)


    # SALib problem definition
    problem = {
        "num_vars": D,
        "names": names,
        "bounds": [[0.05, 0.95]] * D,    # uniform bounds
        "groups": groups,
    }

    # Generate Sobol samples
    sensitivity_analysis_samples = sobol.sample(
        problem,
        N=n_samples,
        calc_second_order=calc_second_order,
        seed=seed,
    )

    # Map SALib samples (D columns) back to merged_parameters (M rows).
    # - Gateways: one-to-one mapping (each gateway gets its own column).
    # - Other types: broadcast the same column to all parameters of that type.

    merged_types = merged_parameters["type"].astype(str).to_numpy()
    M = merged_types.shape[0]

    col_indices = np.empty(M, dtype=np.int64)

    sample_col = 0  # pointer into columns of sensitivity_analysis_samples
    current_non_gateway_type = None
    current_non_gateway_col = None

    for i, t_row in enumerate(merged_types):
        if t_row == "gateway":
            # one-to-one: each gateway row consumes one column
            col_indices[i] = sample_col
            sample_col += 1

            # reset non-gateway tracker (safe)
            current_non_gateway_type = None
            current_non_gateway_col = None

        else:
            if is_groups:
                # grouped: broadcast per (contiguous) type block
                if t_row != current_non_gateway_type:
                    current_non_gateway_type = t_row
                    current_non_gateway_col = sample_col
                    sample_col += 1
                col_indices[i] = current_non_gateway_col
            else:
                # ungrouped: one-to-one (same as gateway logic)
                col_indices[i] = sample_col
                sample_col += 1

    # Expand columns: shape becomes (n_rows, M)
    simulation_samples = sensitivity_analysis_samples[:, col_indices]

    # Optional sanity checks (recommended while developing)
    # assert simulation_samples.shape[1] == len(merged_parameters)
    # assert sample_col == sensitivity_analysis_samples.shape[1], (sample_col, sensitivity_analysis_samples.shape[1])

    return sensitivity_analysis_samples, problem, simulation_samples
