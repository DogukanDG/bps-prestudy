from scipy.stats import uniform, lognorm, gamma, norm, expon
from typing import Tuple, Optional, Sequence
import pandas as pd
import numpy as np
import math


def convert_tasks_resources(
    samples: np.ndarray,
    merged_parameters: pd.DataFrame,
    tasks_resources: pd.DataFrame,
) -> pd.DataFrame:
    """
    Convert task–resource parameters from uniform samples into numeric
    values using their configured probability distributions, and return
    them in a long-format DataFrame.

    This wrapper:
      1. Uses `uniforms_to_distributions_task_resources` to inverse-transform
         the uniform samples of parameters with type 'task_resource'.
      2. Uses `build_tasks_resources_converted_df` to construct a long-format
         table with one row per (sample, parameter).

    Parameters
    ----------
    samples : np.ndarray
        Global Sobol/Morris sample matrix of shape (n_samples, n_parameters).
    merged_parameters : pd.DataFrame
        Full parameter table with at least ['name', 'type'], used to select
        columns corresponding to 'task_resource' parameters.
    tasks_resources : pd.DataFrame
        Task–resource definition table with columns including
        ['name', 'distribution_name', 'parameter_1', ..., 'parameter_4'],
        which specify the distribution family and its parameters.

    Returns
    -------
    pd.DataFrame
        Long-format DataFrame with columns ['name', 'sample', 'value'],
        where 'value' contains the transformed task–resource parameter
        values for each sample.
    """
    
    # ---- Step 1: inverse-transform uniforms via the configured distributions
    tr_samples_inverse, tr_names = uniforms_to_distributions_task_resources(
        samples=samples,
        merged_parameters=merged_parameters,
        tasks_resources=tasks_resources,
    )

    # ---- Step 2: build long-format DataFrame for downstream analysis/export
    tasks_resources_converted = build_tasks_resources_converted_df(
        tasks_resources=tasks_resources,
        tr_samples_inverse=tr_samples_inverse,
        tr_names=tr_names
    )

    return tasks_resources_converted


def uniforms_to_distributions_task_resources(
    samples: np.ndarray,
    merged_parameters: pd.DataFrame,
    tasks_resources: pd.DataFrame,
    validate: bool = True,
) -> Tuple[np.ndarray, Sequence[str]]:
    """
    Inverse-transform uniform samples for task–resource parameters into
    numeric values using their configured probability distributions.

    For each parameter of type 'task_resource', this function:
      - selects the corresponding column from the global uniform sample matrix,
      - optionally clips the uniforms into (0, 1) for numerical safety,
      - applies the appropriate inverse CDF (uniform, lognorm, gamma, norm,
        expon) with optional truncation based on the parameters in
        `tasks_resources`.

    Distribution parameter mapping (per row in tasks_resources):
      - uniform:  parameter_1 = minimum,      parameter_2 = maximum
      - lognorm:  parameter_1 = mean,         parameter_2 = variance,
                   parameter_3 = minimum,     parameter_4 = maximum
      - gamma:    parameter_1 = mean,         parameter_2 = variance,
                   parameter_3 = minimum,     parameter_4 = maximum
      - norm:     parameter_1 = mean,         parameter_2 = std,
                   parameter_3 = minimum,     parameter_4 = maximum
      - expon:    parameter_1 = mean,         parameter_2 = minimum,
                   parameter_3 = maximum

    Parameters
    ----------
    samples : np.ndarray
        Global uniform sample matrix of shape (n_samples, n_parameters).
    merged_parameters : pd.DataFrame
        Parameter table with at least ['name', 'type'], used to locate
        'task_resource' columns in `samples`.
    tasks_resources : pd.DataFrame
        Task–resource configuration table with columns
        ['name', 'distribution_name', 'parameter_1', ..., 'parameter_4'],
        defining the distribution and its parameters for each entry.
    validate : bool, optional
        If True, clips the selected uniform samples into (0, 1) where needed.

    Returns
    -------
    np.ndarray
        Array of shape (n_samples, n_tr) containing the inverse-transformed
        numeric task–resource values.
    Sequence[str]
        Sequence of task–resource parameter names, aligned with the columns
        of the returned numeric array.
    """

    # 1) pick task_resource columns (by type) and names
    is_tr = (merged_parameters["type"].astype(str) == "task_resource").to_numpy()
    tr_col_idx = np.flatnonzero(is_tr)
    tr_names = merged_parameters.loc[tr_col_idx, "name"].astype(str).tolist()

    # 2) slice uniforms for task_resources
    tr_samples = samples[:, tr_col_idx]

    # 3) optional safety: clip only if anything falls outside (0,1)
    if validate and not (np.all(tr_samples > 0.0) and np.all(tr_samples < 1.0)):
        eps = np.finfo(float).eps
        tr_samples = np.clip(tr_samples, eps, 1.0 - eps)

    # 4) lookup rows and parameters
    lut = tasks_resources.set_index("name")
    dist_series = lut["distribution_name"].reindex(tr_names)
    if dist_series.isna().any():
        missing = dist_series[dist_series.isna()].index.tolist()
        raise KeyError(f"tasks_resources missing rows for names: {missing}")

    # helper: truncated ppf
    def trunc_ppf(u, dist, low, high):
        if low is None and high is None:
            return dist.ppf(u)
        a = dist.cdf(low) if low is not None else 0.0
        b = dist.cdf(high) if high is not None else 1.0
        # guard
        eps = np.finfo(float).eps
        a = min(max(a, 0.0), 1.0 - eps)
        b = min(max(b, a + eps), 1.0)
        return dist.ppf(a + (b - a) * u)

    # 5) inverse transform per column
    M, D_tr = tr_samples.shape
    tr_samples_inverse = np.empty_like(tr_samples, dtype=float)

    for j, name in enumerate(tr_names):
        row = lut.loc[name]
        d = str(row["distribution_name"]).lower()

        def f(x):
            # None for missing/NaN
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        p1 = f(row.get("parameter_1"))
        p2 = f(row.get("parameter_2"))
        p3 = f(row.get("parameter_3"))
        p4 = f(row.get("parameter_4"))

        ucol = tr_samples[:, j]

        if d == "uniform":
            # [min, max]
            minimum, maximum = float(p1), float(p2)
            tr_samples_inverse[:, j] = uniform.ppf(ucol, loc=minimum, scale=(maximum - minimum))

        elif d == "lognorm":
            # [mean, var, minimum, maximum]
            mean, var = float(p1), float(p2)
            pow_mean = pow(mean, 2)
            phi = math.sqrt(var + pow_mean)
            mu = math.log(pow_mean / phi)
            sigma = math.sqrt(math.log(phi ** 2 / pow_mean))
            base = lognorm(s=sigma, scale=math.exp(mu))
            tr_samples_inverse[:, j] = trunc_ppf(ucol, base, p3, p4)

        elif d == "gamma":
            # [mean, var, minimum, maximum]
            mean, var = float(p1), float(p2)
            a = pow(mean, 2) / var
            scale = var / mean
            base = gamma(a=a, scale=scale)
            tr_samples_inverse[:, j] = trunc_ppf(ucol, base, p3, p4)

        elif d == "norm":
            # [mean, std, minimum, maximum]
            mean, std = float(p1), float(p2)
            base = norm(loc=mean, scale=std)
            tr_samples_inverse[:, j] = trunc_ppf(ucol, base, p3, p4)

        elif d == "expon":
            # [mean, minimum, maximum]  (loc=0)
            mean, minimum = float(p1), float(p2)
            scale = mean - minimum
            if scale < 0.0:
                scale = mean
            base = expon(loc=minimum, scale=scale)
            tr_samples_inverse[:, j] = trunc_ppf(ucol, base, p2, p3)

    return tr_samples_inverse, tr_names


def build_tasks_resources_converted_df(
    tasks_resources: pd.DataFrame,
    tr_samples_inverse: np.ndarray,
    tr_names: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """
    Build a long-format DataFrame of task–resource values per sample.

    Parameters
    ----------
    tasks_resources : pd.DataFrame
        Task–resource table; used as a fallback source of names if
        `tr_names` is None.
    tr_samples_inverse : np.ndarray
        Numeric task–resource values of shape (n_samples, n_params),
        obtained from `uniforms_to_distributions_task_resources`.
    tr_names : Optional[Sequence[str]], optional
        Explicit parameter names aligned with the columns of
        `tr_samples_inverse`. If None, `tasks_resources['name']`
        is used instead.

    Returns
    -------
    pd.DataFrame
        Long-format table with columns ['name', 'sample', 'value'],
        where each row corresponds to a single (sample, parameter)
        task–resource value.
    """

    n_samples, n_params = tr_samples_inverse.shape

    # If explicit tr_names provided → use them
    if tr_names is not None:
        col_names = np.asarray(tr_names)
        if len(col_names) != n_params:
            raise ValueError(
                "Number of columns in tr_samples_inverse does not match provided tr_names length."
            )

    else:
        # Use tasks_resources['name']
        col_names = tasks_resources["name"].to_numpy()
        if len(col_names) != n_params:
            raise ValueError(
                "Number of columns in tr_samples_inverse does not match tasks_resources['name'] length."
            )

    tasks_resources_converted = pd.DataFrame({
        "name": np.tile(col_names, n_samples),
        "sample": np.repeat(np.arange(n_samples), n_params),
        "value": tr_samples_inverse.ravel(),
    })

    return tasks_resources_converted
