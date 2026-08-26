from scipy.stats import uniform, lognorm, gamma, norm, expon
from typing import Tuple, Optional, Sequence
import pandas as pd
import numpy as np
import math


def convert_arrival_distribution(
    samples: np.ndarray,
    merged_parameters: pd.DataFrame,
    arrival_distribution: pd.DataFrame,
) -> pd.DataFrame:
    """
    Convert arrival-distribution parameters from uniform samples into
    numeric values using their configured probability distributions, and
    return a long-format DataFrame with one row per (sample, parameter).

    Parameters
    ----------
    samples : np.ndarray
        Global Sobol/Morris sample matrix of shape (n_samples, n_parameters).
    merged_parameters : pd.DataFrame
        Full parameter table with at least the columns ['name', 'type'],
        used to identify which columns correspond to arrival distributions.
    arrival_distribution : pd.DataFrame
        Arrival-distribution parameter table containing columns such as
        ['name', 'distribution_name', 'parameter_1', ..., 'parameter_4'],
        defining the distribution type and its parameters.

    Returns
    -------
    pd.DataFrame
        Long-format DataFrame with columns ['name', 'sample', 'value'],
        where 'value' contains the transformed arrival-distribution parameter
        values (e.g. inter-arrival times) for each sample and parameter.
    """
    
    # ---- Step 1: inverse-transform uniforms via the configured distributions
    ad_samples_inverse, ad_names = uniforms_to_distributions_arrival_distribution(
        samples=samples,
        merged_parameters=merged_parameters,
        arrival_distribution=arrival_distribution,
    )

    # ---- Step 2: build long-format DataFrame for downstream analysis/export
    arrival_distribution_converted = build_arrival_distribution_converted_df(
        arrival_distribution=arrival_distribution,
        ad_samples_inverse=ad_samples_inverse,
        ad_names=ad_names
    )

    return arrival_distribution_converted


def uniforms_to_distributions_arrival_distribution(
    samples: np.ndarray,
    merged_parameters: pd.DataFrame,
    arrival_distribution: pd.DataFrame,
    validate: bool = True,
) -> Tuple[np.ndarray, Sequence[str]]:
    """
    Extract arrival-distribution parameters from the global sample matrix and
    map their uniform samples to numeric values via configured distributions.

    For each parameter of type 'arrival_dist', this function:
      - selects the corresponding columns from the global sample matrix,
      - optionally clips the uniform samples into (0, 1) for numerical safety,
      - applies the appropriate inverse CDF (uniform, lognorm, gamma, norm,
        expon, or pass-through) with optional truncation based on the
        configured bounds.

    Distribution parameter mapping (per row in arrival_distribution):
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
        Full sample matrix of shape (n_samples, n_parameters), e.g. from
        Sobol or Morris sampling.
    merged_parameters : pd.DataFrame
        Global parameter table with at least ['name', 'type'], used to
        select columns of type 'arrival_dist'.
    arrival_distribution : pd.DataFrame
        Arrival-distribution table with columns
        ['name', 'distribution_name', 'parameter_1', ..., 'parameter_4'],
        defining the distribution family and its parameters for each name.
    validate : bool, optional
        If True, clips selected uniform samples into (0, 1) where needed.

    Returns
    -------
    np.ndarray
        Array of shape (n_samples, n_arrival_dist) containing the
        inverse-transformed numeric values (e.g. inter-arrival times)
        for each arrival-distribution parameter.
    Sequence[str]
        List of parameter names (ad_names) aligned with the columns of
        the returned numeric array.
    """

    # 1) select arrival_dist columns and ad_names in the global parameter table
    is_ad = (merged_parameters["type"].astype(str) == "arrival_dist").to_numpy()
    ad_col_idx = np.flatnonzero(is_ad)
    ad_names = merged_parameters.loc[ad_col_idx, "name"].astype(str).tolist()

    # 2) slice uniforms for arrival
    ad_samples = samples[:, ad_col_idx]

    # 3) optional safety: clip only if anything falls outside (0,1)
    if validate and not (np.all(ad_samples > 0.0) and np.all(ad_samples < 1.0)):
        eps = np.finfo(float).eps
        ad_samples = np.clip(ad_samples, eps, 1.0 - eps)

    # 4) lookup rows and parameters (by name) from arrival_distribution
    lut = arrival_distribution.set_index("name")
    dist_series = lut["distribution_name"].reindex(ad_names)
    if dist_series.isna().any():
        missing = dist_series[dist_series.isna()].index.tolist()
        raise KeyError(f"arrival_distribution missing rows for ad_names: {missing}")

    # helper: truncated ppf
    def trunc_ppf(u, dist, low, high):
        if low is None and high is None:
            return dist.ppf(u)
        a = dist.cdf(low) if low is not None else 0.0
        b = dist.cdf(high) if high is not None else 1.0
        # guard
        eps_local = np.finfo(float).eps
        a = min(max(a, 0.0), 1.0 - eps_local)
        b = min(max(b, a + eps_local), 1.0)
        return dist.ppf(a + (b - a) * u)

    # 5) inverse-transform per arrival column
    M, D_ad = ad_samples.shape
    ad_samples_inverse = np.empty_like(ad_samples, dtype=float)

    for j, name in enumerate(ad_names):
        row = lut.loc[name]
        d = str(row["distribution_name"]).lower()

        def f(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        p1 = f(row.get("parameter_1"))
        p2 = f(row.get("parameter_2"))
        p3 = f(row.get("parameter_3"))
        p4 = f(row.get("parameter_4"))

        ucol = ad_samples[:, j]

        if d == "uniform":
            # [min, max]
            minimum, maximum = float(p1), float(p2)
            ad_samples_inverse[:, j] = uniform.ppf(ucol, loc=minimum, scale=(maximum - minimum))

        elif d == "lognorm":
            # [mean, var, minimum, maximum]
            mean, var = float(p1), float(p2)
            pow_mean = mean ** 2
            phi = math.sqrt(var + pow_mean)
            mu = math.log(pow_mean / phi)
            sigma = math.sqrt(math.log(phi ** 2 / pow_mean))
            base = lognorm(s=sigma, scale=math.exp(mu))
            ad_samples_inverse[:, j] = trunc_ppf(ucol, base, p3, p4)

        elif d == "gamma":
            # [mean, var, minimum, maximum]
            mean, var = float(p1), float(p2)
            a = mean ** 2 / var
            scale = var / mean
            base = gamma(a=a, scale=scale)
            ad_samples_inverse[:, j] = trunc_ppf(ucol, base, p3, p4)

        elif d == "norm":
            # [mean, std, minimum, maximum]
            mean, std = float(p1), float(p2)
            base = norm(loc=mean, scale=std)
            ad_samples_inverse[:, j] = trunc_ppf(ucol, base, p3, p4)

        elif d == "expon":
            # [mean, minimum, maximum]
            mean, minimum = float(p1), float(p2)
            scale = mean - minimum
            if scale < 0.0:
                scale = mean
            base = expon(loc=minimum, scale=scale)
            ad_samples_inverse[:, j] = trunc_ppf(ucol, base, p2, p3)

        else:
            # unknown -> pass-through
            ad_samples_inverse[:, j] = ucol

    return ad_samples_inverse, ad_names


def build_arrival_distribution_converted_df(
    arrival_distribution: pd.DataFrame,
    ad_samples_inverse: np.ndarray,
    ad_names: Optional[Sequence[str]] = None,   # pass ad_names if aligned
) -> pd.DataFrame:
    """
    Build a long-format DataFrame of arrival-distribution values.

    Parameters
    ----------
    arrival_distribution : pd.DataFrame
        Arrival-distribution table with at least a 'name' column.
        Used as a fallback source for parameter names if `ad_names`
        is not explicitly provided.
    ad_samples_inverse : np.ndarray
        Array of shape (n_samples, n_params) containing numeric values
        obtained from inverse-transforming the uniform arrival samples.
    ad_names : Optional[Sequence[str]], optional
        Explicit parameter names aligned with the columns of
        `ad_samples_inverse`. If None, `arrival_distribution['name']`
        is used instead.

    Returns
    -------
    pd.DataFrame
        Long-form table with columns ['name', 'sample', 'value'], where
        each row corresponds to a single (sample, parameter) combination
        for the arrival-distribution parameters.
    """
    n_samples, n_params = ad_samples_inverse.shape

    if ad_names is None:
        col_names = arrival_distribution["name"].to_numpy()
        if len(col_names) != n_params:
            raise ValueError(
                "Number of columns in ad_samples_inverse does not match "
                "arrival_distribution['name'] length."
            )
    else:
        col_names = np.asarray(ad_names)
        if len(col_names) != n_params:
            raise ValueError(
                "Number of columns in ad_samples_inverse does not match provided ad_names length."
            )

    arrival_distribution_converted = pd.DataFrame(
        {
            "name": np.tile(col_names, n_samples),
            "sample": np.repeat(np.arange(n_samples), n_params),
            "value": ad_samples_inverse.ravel(),
        }
    )

    return arrival_distribution_converted

