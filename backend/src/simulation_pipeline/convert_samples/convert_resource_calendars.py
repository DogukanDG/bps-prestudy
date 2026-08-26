from decimal import Decimal, ROUND_HALF_UP
from typing import Tuple, Sequence
import pandas as pd
import numpy as np


def convert_resource_calendars(
    samples: np.ndarray,
    merged_parameters: pd.DataFrame,
    resource_calendars: pd.DataFrame,
) -> pd.DataFrame:
    """
    Convert resource calendar parameters from uniform samples into
    integer working-hour values and corresponding active time slots.

    This wrapper performs:
      1. Inverse transform: convert uniform sample columns associated with
         resource calendars into working hours using
         `uniforms_to_hours_resource_calendars`.
      2. Expansion: build a long-format DataFrame assigning time slots
         according to each calendar's `peel_order` via
         `build_resource_calendars_converted_df`.

    Parameters
    ----------
    samples : np.ndarray
        Global sample matrix of shape (n_samples, n_parameters), e.g. from
        Sobol/Morris sampling.
    merged_parameters : pd.DataFrame
        Full parameter table containing at least ['name', 'type'], used to
        identify which columns correspond to resource calendars.
    resource_calendars : pd.DataFrame
        Resource calendar table containing at least:
        ['name', 'working_hours', 'peel_order'], where 'working_hours' is the
        base number of hours and 'peel_order' encodes the slot ordering.

    Returns
    -------
    pd.DataFrame
        Long-format DataFrame with columns:
        ['name', 'sample', 'value', 'slots'],
        where 'value' is the integer working-hour allocation and 'slots' is
        the list of active time-slot IDs selected according to the peel order.
    """

    # ---- Step 1: map uniform samples to number of hours
    rc_samples_inverse, rc_names = uniforms_to_hours_resource_calendars(
        samples=samples,
        merged_parameters=merged_parameters,
        resource_calendars=resource_calendars,
    )

    # ---- Step 2: build long-format DataFrame for downstream analysis/export.
    resource_calendars_converted = build_resource_calendars_converted_df(
        rc_samples_inverse=rc_samples_inverse,
        rc_names=rc_names,
        resource_calendars=resource_calendars,
    )

    return resource_calendars_converted


def _round_half_up(x: np.ndarray) -> np.ndarray:
    """
    Vectorized 'round half up' to the nearest integer.

    Parameters
    ----------
    x : np.ndarray
        Array of floating-point values to be rounded.

    Returns
    -------
    np.ndarray
        Array of integers with the same shape as `x`, where .5 values are
        rounded away from zero (half up).
    """
    vfunc = np.vectorize(
        lambda v: int(Decimal(v).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    )
    return vfunc(x)


def uniforms_to_hours_resource_calendars(
    samples: np.ndarray,
    merged_parameters: pd.DataFrame,
    resource_calendars: pd.DataFrame,  # must contain ["name", "working_hours"]
    validate: bool = True,
) -> Tuple[np.ndarray, Sequence[str]]:
    """
    Convert uniform samples for resource calendars into integer working-hour values.

    For each parameter of type 'resource_calendar', this function:
      - extracts the corresponding uniform samples from the global sample matrix,
      - optionally clips them into the open interval (0, 1),
      - rescales each column to percentages in [5%, 100%],
      - multiplies by the base working hours from `resource_calendars`,
      - applies half-up rounding and enforces a minimum of 1 hour.

    Parameters
    ----------
    samples : np.ndarray
        Global sample matrix of shape (n_samples, n_parameters).
    merged_parameters : pd.DataFrame
        Parameter table with at least ['name', 'type'], used to locate the
        resource-calendar columns in `samples`.
    resource_calendars : pd.DataFrame
        Resource calendar definition table with at least
        ['name', 'working_hours'], providing base hours per calendar.
    validate : bool, optional
        If True, clips the selected uniform samples into (0, 1) where needed.

    Variables
    ---------
    rc_pct : np.ndarray
        Array of shape (n_samples, n_rc) with per-calendar percentages
        in [0.05, 1.0] after column-wise scaling.
    rc_samples : np.ndarray
        Array of shape (n_samples, n_rc) with raw uniform samples for
        resource calendars.

    Returns
    -------
    np.ndarray
        Array of shape (n_samples, n_rc) with integer working hours
        (>= 1) for each resource calendar.
    Sequence[str]
        Names of the resource calendar parameters, aligned with the
        columns of the returned array.
    """

    # pick resource_calendar columns
    is_rc = (merged_parameters["type"].astype(str) == "resource_calendar").to_numpy()
    rc_col_idx = np.flatnonzero(is_rc)
    rc_names = merged_parameters.loc[rc_col_idx, "name"].astype(str).tolist()

    # slice
    rc_samples = samples[:, rc_col_idx]

    # validate/clamp to (0,1)
    if validate and not (np.all(rc_samples > 0.0) and np.all(rc_samples < 1.0)):
        eps = np.finfo(float).eps
        rc_samples = np.clip(rc_samples, eps, 1.0 - eps)

    # base working rc_hours per resource calendar name
    lut = resource_calendars.set_index("name")["working_hours"]
    base_hours = np.array(
        [lut.loc[n] if n in lut.index else 0 for n in rc_names],
        dtype=float,
    )

    # column-wise min/max -> scale to [0.05, 1.0]
    umin = rc_samples.min(axis=0, keepdims=True)       # (1 x D_rc)
    umax = rc_samples.max(axis=0, keepdims=True)       # (1 x D_rc)
    denom = np.where(umax > umin, umax - umin, 1.0)
    rc_pct = 0.05 + 0.95 * (rc_samples - umin) / denom
    # flat column guard: if umax == umin, set to 1.0
    rc_pct = np.where((umax > umin), rc_pct, 1.0)

    # scale by base rc_hours and round; enforce minimum 1
    rc_hours = rc_pct * base_hours[np.newaxis, :]   # (M x D_rc)
    rc_samples_inverse = _round_half_up(rc_hours).astype(int)
    rc_samples_inverse = np.maximum(rc_samples_inverse, 1)

    return rc_samples_inverse, rc_names


def build_resource_calendars_converted_df(
    rc_samples_inverse: np.ndarray,
    rc_names: Sequence[str],
    resource_calendars: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build a long-format DataFrame for resource calendars, including slot allocation.

    Parameters
    ----------
    rc_samples_inverse : np.ndarray
        Array of shape (n_samples, n_calendars) with integer working hours
        per sample and resource calendar.
    rc_names : Sequence[str]
        Resource calendar names aligned with the columns of `rc_samples_inverse`.
    resource_calendars : pd.DataFrame
        Original resource calendar table with at least:
        ['name', 'peel_order', 'working_hours'], where 'peel_order' encodes
        the ordered list of slots for each calendar.

    Returns
    -------
    pd.DataFrame
        Long-form DataFrame with columns:
        ['name', 'sample', 'value', 'slots'], where:
          - 'name'   is the calendar name,
          - 'sample' is the sample index (0-based),
          - 'value'  is the integer working-hour allocation,
          - 'slots'  is the sorted list of slot IDs chosen as the last
                     `value` entries of the corresponding `peel_order`.
    """
    n_samples, n_cals = rc_samples_inverse.shape
    rc_names = np.asarray(rc_names)
    if rc_names.size != n_cals:
        raise ValueError("rc_samples_inverse columns do not match rc_names length.")

    # Base long-form table: one row per (sample, calendar)
    resource_calendars_converted = pd.DataFrame(
        {
            "name": np.tile(rc_names, n_samples),
            "sample": np.repeat(np.arange(n_samples), n_cals),
            "value": rc_samples_inverse.ravel().astype(int),
        }
    )[["name", "sample", "value"]]

    # Lookups from original resource_calendars
    rc_peel_lut = (
        resource_calendars
        .set_index("name")[["peel_order", "working_hours"]]
        .to_dict(orient="index")
    )

    # Prepare slots column
    resource_calendars_converted["slots"] = [[] for _ in range(len(resource_calendars_converted))]

    for i, row in resource_calendars_converted.iterrows():
        name = row["name"]
        target_hours = int(row["value"])

        info = rc_peel_lut.get(name, {"peel_order": [], "working_hours": 0})
        peel_order = list(map(int, info.get("peel_order", []) or []))

        if target_hours <= 0 or not peel_order:
            chosen = []
        else:
            # take the last `target_hours` items (clamped by length implicitly)
            chosen = peel_order[-target_hours:]

        # current implementation sorts the chosen slots
        resource_calendars_converted.at[i, "slots"] = sorted(chosen)

    return resource_calendars_converted
