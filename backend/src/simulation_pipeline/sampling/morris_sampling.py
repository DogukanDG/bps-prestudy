from typing import Tuple, Dict, Any
from SALib.sample import morris
import pandas as pd
import numpy as np


def morris_sampling(
    merged_parameters: pd.DataFrame,
    *,
    n_trajectories: int = 20,
    num_levels: int = 6,
    optimal_trajectories: int | None = None,
    local_optimization: bool = False,
    seed: int | None = None,
    is_groups: bool,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Build a SALib Morris problem definition (with groups) and draw a
    Morris sampling matrix for all active parameters.

    Behaviour
    ---------
    - Uses `merged_parameters["name"]` as parameter names.
    - Uses `merged_parameters["groups"]` as Morris groups:
    - All parameters share the same bounds [0.05, 0.95].
    - Calls `SALib.sample.morris.sample(...)` with the given options.

    Parameters
    ----------
    merged_parameters : pd.DataFrame
        Parameter index table with at least the columns:
          - "name"   : parameter identifiers (str)
          - "groups" : group labels or singletongs for ungrouped parameters.
    n_trajectories : int, default 20
        Base number of Morris trajectories N to generate.
    num_levels : int, default 6
        Number of grid levels for each parameter (must be even, e.g. 4, 6, 8).
    optimal_trajectories : int or None, default None
        If not None, SALib selects this many trajectories from the generated
        set using its "optimal trajectories" heuristic.
    local_optimization : bool, default False
        If True, enables SALib's local optimization for the trajectory
        selection step.
    seed : int or None, default None
        Random seed passed to SALib for reproducibility.
    is_groups: bool
        If True, use group-based SA (group labels in the merged parameters).
        If False, treat parameters individually (except for gateway grouping).

    Returns
    -------
    sensitivity_analysis_samples : np.ndarray
        Morris sample matrix of shape:
          ((G or D) + 1) * T  by  D
        where:
          - D is the total number of parameters,
          - G is the number of distinct groups (if groups are used),
          - T is the effective number of trajectories
            (N or `optimal_trajectories` if provided).
    problem : dict
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
    if num_levels % 2 != 0:
        raise ValueError("num_levels must be even (e.g., 4, 6, 8).")

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


    problem: Dict[str, Any] = {
        "num_vars": D,
        "names": names,
        "bounds": [[0.05, 0.95]] * D,
        "groups": groups,
    }

    sensitivity_analysis_samples = morris.sample(
        problem,
        N=n_trajectories,
        num_levels=num_levels,
        optimal_trajectories=optimal_trajectories,
        local_optimization=local_optimization,
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
