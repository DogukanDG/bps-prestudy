from typing import Tuple, Sequence
import numpy as np
import pandas as pd


def convert_resource_numbers(
    samples: np.ndarray,
    merged_parameters: pd.DataFrame,
) -> pd.DataFrame:
    """
    Convert resource-number parameters from uniform samples into integer
    resource counts and return them in a long-format table.

    This wrapper:
      1. Uses `uniforms_to_resource_numbers` to map uniform samples for
         parameters of type 'resource_number' to integer counts.
      2. Uses `build_resource_numbers_converted_df` to construct a
         long-format DataFrame with one row per (sample, parameter).

    Parameters
    ----------
    samples : np.ndarray
        Global uniform sample matrix of shape (n_samples, n_parameters),
        e.g. from Sobol/Morris sampling.
    merged_parameters : pd.DataFrame
        Full parameter table containing at least ['name', 'type'], used
        to locate which columns correspond to 'resource_number'.

    Returns
    -------
    pd.DataFrame
        Long-format table with columns ['name', 'sample', 'value'],
        where 'value' contains the final integer resource counts per
        sample and parameter.
    """
    
    # ---- Step 1: convert uniform samples to integer resource counts
    rn_samples_inverse, rn_names = uniforms_to_resource_numbers(
        samples=samples,
        merged_parameters=merged_parameters,
    )

    # ---- Step 2: build long-format DataFrame for downstream analysis/export
    resource_numbers_converted = build_resource_numbers_converted_df(
        rn_samples_inverse=rn_samples_inverse,
        rn_names=rn_names
    )

    return resource_numbers_converted


def uniforms_to_resource_numbers(
    samples: np.ndarray,
    merged_parameters: pd.DataFrame,
    *,
    validate: bool = True,
    min_val: int = 1,
    max_val: int = 20,
) -> Tuple[np.ndarray, Sequence[str]]:
    """
    Convert uniform samples for resource-number parameters into integer counts.

    Steps:
      1. Extract columns whose type == 'resource_number'.
      2. Optionally clip uniforms into (0, 1) for numerical safety.
      3. Normalize each column to [0, 1] using its min/max.
      4. Linearly map normalized values to [min_val, max_val].
      5. Ceil-round to whole units and clip to [min_val, max_val].

    Parameters
    ----------
    samples : np.ndarray
        Global uniform sample matrix of shape (n_samples, n_parameters).
    merged_parameters : pd.DataFrame
        Parameter table with at least ['name', 'type'], used to locate
        the resource_number columns in `samples`.
    validate : bool, optional
        If True, clamp uniform values into the open interval (0, 1)
        before normalization.
    min_val : int, optional
        Minimum allowed resource count (default 1).
    max_val : int, optional
        Maximum allowed resource count (default 20).

    Variables
    ---------
    rn_samples : np.ndarray
        (n_samples × n_rn) raw sliced uniforms for resource numbers.
    rn_pct : np.ndarray
        (n_samples × n_rn) normalized fractions in [0, 1].

    Returns
    -------
    np.ndarray
        (n_samples × n_rn) integer resource counts after mapping and
        ceil-rounding, clipped to [min_val, max_val].
    Sequence[str]
        Names of the resource-number parameters, aligned with the
        columns of the returned array.
    """

    # --- 1) find resource_number columns ---
    is_rn = (merged_parameters["type"].astype(str) == "resource_number").to_numpy()
    rn_col_idx = np.flatnonzero(is_rn)
    rn_names = merged_parameters.loc[rn_col_idx, "name"].astype(str).tolist()

    # --- 2) slice sample matrix ---
    rn_samples = samples[:, rn_col_idx]

    # --- 3) validate / clip to (0,1) ---
    if validate and not (np.all(rn_samples > 0.0) and np.all(rn_samples < 1.0)):
        eps = np.finfo(float).eps
        rn_samples = np.clip(rn_samples, eps, 1.0 - eps)

    # --- 4) normalize per column to [0,1] ---
    umin = rn_samples.min(axis=0, keepdims=True)
    umax = rn_samples.max(axis=0, keepdims=True)
    denom = np.where(umax > umin, umax - umin, 1.0)

    rn_pct = (rn_samples - umin) / denom
    rn_pct = np.where((umax > umin), rn_pct, 1.0)  # flat-column fallback

    # --- 5) map to resource counts & ceil round ---
    span = float(max_val - min_val)
    mapped = min_val + span * rn_pct        # float, in [min_val, max_val]
    rn_samples_inverse = np.ceil(mapped).astype(int)
    rn_samples_inverse = np.clip(rn_samples_inverse, min_val, max_val)

    return rn_samples_inverse, rn_names


def build_resource_numbers_converted_df(
    rn_samples_inverse: np.ndarray,
    rn_names: Sequence[str],
) -> pd.DataFrame:
    """
    Build a long-format DataFrame for resource-number parameters.

    Parameters
    ----------
    rn_samples_inverse : np.ndarray
        Integer resource counts of shape (n_samples, n_resource_numbers),
        as produced by `uniforms_to_resource_numbers`.
    rn_names : Sequence[str]
        Parameter names aligned with the columns of `rn_samples_inverse`.

    Returns
    -------
    pd.DataFrame
        Long-form table with columns ['name', 'sample', 'value'], where
        each row corresponds to a single (sample, parameter) resource
        count.
    """

    n_samples, n_rn = rn_samples_inverse.shape
    rn_names = np.asarray(rn_names)

    if rn_names.size != n_rn:
        raise ValueError("rn_samples_inverse columns do not match rn_names length.")

    resource_numbers_converted = pd.DataFrame(
        {
            "name":   np.tile(rn_names, n_samples),
            "sample": np.repeat(np.arange(n_samples), n_rn),
            "value":  rn_samples_inverse.ravel().astype(int),
        }
    )

    return resource_numbers_converted