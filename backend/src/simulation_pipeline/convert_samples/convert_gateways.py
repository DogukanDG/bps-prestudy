from typing import Tuple, List, Sequence
from collections import defaultdict
from scipy.stats import gamma
import pandas as pd
import numpy as np


def convert_gateways(
    samples: np.ndarray,
    merged_parameters: pd.DataFrame,
    gateways: pd.DataFrame,
) -> pd.DataFrame:
    """
    Convert uniform gateway samples into branch probabilities in long format.

    This wrapper performs the full transformation pipeline for gateway-related
    parameters:
      1. Use `uniforms_to_dirichlet_gamma_gateways` to map uniform samples for
         gateway parameters to Gamma-distributed values.
      2. Normalize the Gamma values within each gateway so that the outgoing
         branches of every gateway sum to 1 via `normalize_within_gateways`.
      3. Build a long-format DataFrame with one row per (sample, gateway
         branch) using `build_gateways_converted_df`.

    Parameters
    ----------
    samples : np.ndarray
        Global sample matrix of shape (n_samples, n_parameters), containing
        uniform samples for all parameters.
    merged_parameters : pd.DataFrame
        Global parameter table with at least ['name', 'type'], used to select
        the columns corresponding to gateway parameters.
    gateways : pd.DataFrame
        Gateway configuration table with at least ['name', 'value', 'gateway_id'],
        where 'value' provides base probabilities and 'gateway_id' groups branches
        belonging to the same gateway.

    Returns
    -------
    pd.DataFrame
        Long-format DataFrame with columns ['name', 'sample', 'probability'],
        where 'probability' gives the normalized branch probability for each
        gateway parameter and sample.
    """
    
    # ---- Step 1: Inverse-CDF Gamma transform -----------------------
    gw_samples_inverse, gw_names = uniforms_to_dirichlet_gamma_gateways(
        samples=samples,
        merged_parameters=merged_parameters,
        gateways=gateways,
    )

    # ---- Step 2: Normalize Gamma values so that each branch will sum up to 1.
    gw_probabilities = normalize_within_gateways(
        gw_samples_inverse=gw_samples_inverse,
        gw_names=gw_names,
        gateways=gateways,
    )

    # ---- Step 3: build long-format DataFrame for downstream analysis/export.
    gateways_converted = build_gateways_converted_df(
        gateways=gateways,
        gw_probabilities=gw_probabilities
    )

    return gateways_converted


def uniforms_to_dirichlet_gamma_gateways(
    samples: np.ndarray,
    merged_parameters: pd.DataFrame,
    gateways: pd.DataFrame,
    *,
    c: float = 5.0,
    eps_alpha: float = 1e-12,
    validate: bool = True,
) -> Tuple[np.ndarray, List[str]]:
    """
    Map uniform gateway samples to Gamma-distributed values using base probabilities.

    This function:
      - selects gateway-related columns from the global sample matrix based on
        `merged_parameters['type'] == "gateway"`,
      - optionally clips uniform values into (0, 1) for numerical safety,
      - aligns each gateway parameter with its base probability from
        `gateways['value']`,
      - computes Gamma shape parameters `alpha = max(c * base_prob, eps_alpha)`,
      - applies the Gamma inverse CDF (ppf) with scale=1 to obtain
        Gamma-distributed samples.

    Parameters
    ----------
    samples : np.ndarray
        Full SALib-style sample matrix (n_samples × n_parameters) containing
        uniform samples.
    merged_parameters : pd.DataFrame
        Parameter table containing at least ['name', 'type']; used to identify
        which columns correspond to gateway parameters.
    gateways : pd.DataFrame
        Gateway parameter table with at least:
          - 'name'  : str, gateway parameter name,
          - 'value' : float, base probability for that branch.
    c : float, optional
        Concentration multiplier for computing the Gamma shape parameter
        alpha = c * base_prob (default 20.0).
    eps_alpha : float, optional
        Minimum allowed shape parameter, used as a lower bound for alpha
        (default 1e-12).
    validate : bool, optional
        If True, clips uniform samples into (0, 1) when any values lie
        outside that open interval.

    Returns
    -------
    np.ndarray
        Gamma-transformed samples of shape (n_samples, n_gateway_parameters),
        where each column corresponds to a gateway branch.
    List[str]
        List of gateway parameter names in the same order as the columns
        of the returned Gamma sample array.

    Raises
    ------
    KeyError
        If any gateway name in `merged_parameters` is missing a base
        probability entry in `gateways`.
    """

    # -------------------------------------------
    # 1. Select gateway parameters
    # -------------------------------------------
    is_gw = (merged_parameters["type"].astype(str) == "gateway").to_numpy()
    gw_col_idx = np.flatnonzero(is_gw)

    gw_names = merged_parameters.loc[gw_col_idx, "name"].astype(str).tolist()

    # Extract only the gateway columns
    gw_samples = samples[:, gw_col_idx]

    # -------------------------------------------
    # 2. Validate U in (0,1)
    # -------------------------------------------
    if validate and (np.any(gw_samples <= 0.0) or np.any(gw_samples >= 1.0)):
        eps = np.finfo(float).eps
        gw_samples = np.clip(gw_samples, eps, 1.0 - eps)

    # -------------------------------------------
    # 3. Align base probabilities
    # -------------------------------------------
    base_series = gateways.set_index("name")["value"].astype(float)
    base_aligned = base_series.reindex(gw_names)

    # Check for missing base prob entries
    if base_aligned.isna().any():
        missing = base_aligned[base_aligned.isna()].index.tolist()
        raise KeyError(f"Missing base values for gateway names: {missing}")

    base_probs = base_aligned.to_numpy(float)

    # -------------------------------------------
    # 4. Compute alpha (shape parameter)
    # -------------------------------------------
    alphas = np.maximum(base_probs * c, eps_alpha)

    # -------------------------------------------
    # 5. Gamma inverse CDF for each uniform sample
    # -------------------------------------------
    gw_samples_inverse = gamma.ppf(gw_samples, a=alphas, scale=1.0)

    # Replace inf/NaN with small positive value
    gw_samples_inverse = np.where(np.isfinite(gw_samples_inverse), gw_samples_inverse, eps_alpha)

    return gw_samples_inverse, gw_names


def normalize_within_gateways(
    gw_samples_inverse: np.ndarray,
    gw_names: Sequence[str],
    gateways: pd.DataFrame,
    eps_sum: float = 1e-12
) -> np.ndarray:
    """
    Normalize Gamma-sampled gateway branch values to per-gateway probabilities.

    Columns belonging to the same `gateway_id` (from `gateways`) are grouped
    and normalized so that, for each sample, the outgoing branches of that
    gateway sum to 1. If the sum of a gateway's branch weights is extremely
    small, the function falls back to a uniform distribution over its branches.

    Parameters
    ----------
    gw_samples_inverse : np.ndarray
        Gamma-sampled values of shape (n_samples, n_gateway_parameters),
        as produced by `uniforms_to_dirichlet_gamma_gateways`.
    gw_names : Sequence[str]
        Names of gateway parameters, aligned with the columns of
        `gw_samples_inverse`.
    gateways : pd.DataFrame
        Gateway configuration with at least ['name', 'gateway_id'], where
        'gateway_id' groups branches belonging to the same gateway.
    eps_sum : float, optional
        Threshold used to detect near-zero sums when normalizing. If a sum is
        below this threshold, a uniform distribution is assigned instead
        (default 1e-12).

    Returns
    -------
    np.ndarray
        Array of normalized probabilities with the same shape as
        `gw_samples_inverse`, where each row contains valid probability
        vectors for all gateway branches.
    """

    # Map names -> gateway_id aligned to gw_names (not gateways order)
    name_to_gid = gateways.set_index("name")["gateway_id"]
    gids_aligned = name_to_gid.reindex(gw_names)

    if gids_aligned.isna().any():
        missing = gids_aligned[gids_aligned.isna()].index.tolist()
        raise KeyError(f"Missing gateway_id for names: {missing}")

    gids = gids_aligned.to_numpy(dtype=object)

    # Build column groups by gateway_id (preserve encounter order)
    groups = defaultdict(list)
    for j, gid in enumerate(gids):
        groups[gid].append(j)

    # Normalize per group, per sample
    gw_probabilities = np.empty_like(gw_samples_inverse, dtype=float)
    for gid, cols in groups.items():
        block = gw_samples_inverse[:, cols]              # (M, K_gw)
        sums = block.sum(axis=1, keepdims=True)          # (M, 1)
        safe = np.where(sums > eps_sum, sums, 1.0)
        normed = block / safe
        # Fallback to uniform if a row sum is ~0
        need_uniform = (sums <= eps_sum).ravel()
        if np.any(need_uniform):
            K = len(cols)
            normed[need_uniform, :] = 1.0 / K
        gw_probabilities[:, cols] = normed

    return gw_probabilities


def build_gateways_converted_df(
    gateways: pd.DataFrame,
    gw_probabilities: np.ndarray
) -> pd.DataFrame:
    """
    Construct a long-format DataFrame of gateway branch probabilities.

    Produces one row per (sample, gateway branch) containing the branch
    probability, suitable for export or downstream analysis.

    Parameters
    ----------
    gateways : pd.DataFrame
        Gateway configuration table with at least a 'name' column. The order
        of `gateways['name']` must be aligned with the columns of
        `gw_probabilities`.
    gw_probabilities : np.ndarray
        Array of shape (n_samples, n_gateway_parameters) containing normalized
        probabilities for each gateway branch and sample.

    Returns
    -------
    pd.DataFrame
        Long-format table with columns:
          - 'name'       : gateway branch name,
          - 'sample'     : sample index (0-based),
          - 'probability': normalized probability for that branch.
    """
    n_samples, n_params = gw_probabilities.shape

    gateways_converted = pd.DataFrame({
        "name": np.tile(gateways["name"].to_numpy(), n_samples),
        "sample": np.repeat(np.arange(n_samples), n_params),
        "probability": gw_probabilities.ravel(),
    })

    return gateways_converted

