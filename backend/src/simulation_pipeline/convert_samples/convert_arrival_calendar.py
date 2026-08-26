from decimal import Decimal, ROUND_HALF_UP
from typing import Tuple, Sequence
import pandas as pd
import numpy as np


def convert_arrival_calendar(
    samples: np.ndarray,
    merged_parameters: pd.DataFrame,
    arrival_calendar: pd.DataFrame,
) -> pd.DataFrame:
    """
    Convert uniform arrival-calendar samples into discrete working-hour
    values and corresponding time-slot allocations, and return them in
    a long-format table.

    Parameters
    ----------
    samples : np.ndarray
        Global sample matrix of shape (n_samples, n_parameters) containing
        the uniform draws for all parameters (including arrival-calendar
        parameters).
    merged_parameters : pd.DataFrame
        Full parameter definition table with at least ['name', 'type'],
        used to identify which columns in `samples` correspond to
        arrival-calendar parameters.
    arrival_calendar : pd.DataFrame
        Arrival calendar configuration with at least
        ['name', 'peel_order', 'working_hours'], used to map working
        hours to specific time slots.

    Returns
    -------
    pd.DataFrame
        Long-format DataFrame with columns
        ['name', 'sample', 'value', 'slots'], where:
        - 'name' is the arrival-calendar parameter name,
        - 'sample' is the sample index,
        - 'value' is the chosen working-hour value (integer),
        - 'slots' is the list of allocated time-slot IDs derived from
          'peel_order' and 'value'.
    """

    # ---- Step 1: map uniform samples to number of hours
    ac_samples_inverse, ac_names = uniforms_to_hours_arrival_calendar(
        samples=samples,
        merged_parameters=merged_parameters,
        arrival_calendar=arrival_calendar,
    )

    # ---- Step 2: build long-format DataFrame for downstream analysis/export.
    arrival_calendar_converted = build_arrival_calendar_converted_df(
        ac_samples_inverse=ac_samples_inverse,
        ac_names=ac_names,
        arrival_calendar=arrival_calendar,
    )

    return arrival_calendar_converted


def _round_half_up(x: np.ndarray) -> np.ndarray:
    """
    Vectorized 'round half up' to nearest integer.
    """
    vfunc = np.vectorize(
        lambda v: int(Decimal(v).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    )
    return vfunc(x)


def uniforms_to_hours_arrival_calendar(
    samples: np.ndarray,
    merged_parameters: pd.DataFrame,
    arrival_calendar: pd.DataFrame,
    validate: bool = True,
) -> Tuple[np.ndarray, Sequence[str]]:
    """
    Map uniform samples for arrival-calendar parameters to integer working hours.

    For each parameter of type "arrival_calendar", this function:
      - selects the corresponding columns from the global sample matrix,
      - optionally clips uniform samples into (0, 1) for numerical safety,
      - normalizes them column-wise into a percentage range [5%, 100%],
      - scales these percentages by the base working hours defined in
        `arrival_calendar['working_hours']`,
      - and applies half-up rounding to obtain integer working-hour values.

    Parameters
    ----------
    samples : np.ndarray
        Global sample matrix of shape (n_samples, n_parameters) containing
        uniform samples for all parameters.
    merged_parameters : pd.DataFrame
        Parameter definition table with at least ['name', 'type'], used to
        identify which columns in `samples` belong to the arrival calendar.
    arrival_calendar : pd.DataFrame
        Arrival calendar configuration with at least
        ['name', 'slots', 'working_hours'], providing the base working hours
        per arrival-calendar parameter.
    validate : bool, optional
        If True, clips the selected uniform samples into (0, 1) where needed.

    Variables
    --------
    ac_pct : np.ndarray
        Array of shape (n_samples, n_ac_params) with per-parameter
        percentages in [0.05, 1.0].
    ac_samples : np.ndarray
        Array of shape (n_samples, n_ac_params) with the raw selected
        arrival-calendar uniforms.

    Returns
    -------
    ac_samples_inverse : np.ndarray
        Integer working-hour values of shape (n_samples, n_ac_params) obtained
        after scaling by base hours and half-up rounding (minimum of 1 hour).
    ac_names : Sequence[str]
        Names of the arrival-calendar parameters, aligned with the columns
        of `ac_samples_inverse`.
    """

    # Pick arrival_calendar columns
    is_ac = (merged_parameters["type"].astype(str) == "arrival_calendar").to_numpy()
    ac_col_idx = np.flatnonzero(is_ac)
    ac_names = merged_parameters.loc[ac_col_idx, "name"].astype(str).tolist()

    # Slice uniforms
    ac_samples = samples[:, ac_col_idx]

    # Optional safety: clip into (0,1)
    if validate and not (np.all(ac_samples > 0.0) and np.all(ac_samples < 1.0)):
        eps = np.finfo(float).eps
        ac_samples = np.clip(ac_samples, eps, 1.0 - eps)

    # Lookup base working_hours per arrival calendar name
    lut = arrival_calendar.set_index("name")["working_hours"]
    base_hours = np.array(
        [lut.loc[n] if n in lut.index else 0 for n in ac_names],
        dtype=float,
    )

    # Column-wise min/max to map into [0.05, 1.0]
    umin = ac_samples.min(axis=0, keepdims=True)
    umax = ac_samples.max(axis=0, keepdims=True)
    denom = np.where(umax > umin, umax - umin, 1.0)          # avoid div0
    ac_pct = 0.05 + 0.95 * (ac_samples - umin) / denom             # (M x D_ac)
    ac_pct = np.where((umax > umin), ac_pct, 1.0)            # if flat column -> 100%

    # scale by base ac_hours and round; enforce minimum 1
    ac_hours = ac_pct * base_hours[np.newaxis, :]               # (M x D_ac)
    ac_samples_inverse = _round_half_up(ac_hours).astype(int)
    ac_samples_inverse = np.maximum(ac_samples_inverse, 1)

    return ac_samples_inverse, ac_names


def build_arrival_calendar_converted_df(
    ac_samples_inverse: np.ndarray,
    ac_names: Sequence[str],
    arrival_calendar: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build a long-format arrival-calendar table including allocated time slots.

    For each arrival-calendar parameter and each sample, this function creates
    one row containing the corresponding working-hour value and the list of
    allocated time slots. Time slots are selected according to the
    `peel_order` defined in the original `arrival_calendar` table.

    Parameters
    ----------
    ac_samples_inverse : np.ndarray
        Array of shape (n_samples, n_calendars) with integer working hours
        per sample and arrival calendar.
    ac_names : Sequence[str]
        Names of the arrival-calendar parameters, aligned with the columns
        of `ac_samples_inverse`.
    arrival_calendar : pd.DataFrame
        Original arrival calendar configuration with at least
        ['name', 'peel_order', 'working_hours'], used to map working hours
        to specific time-slot IDs.

    Returns
    -------
    pd.DataFrame
        Long-format DataFrame with columns:
        - 'name'   : arrival-calendar name,
        - 'sample' : sample index (0-based),
        - 'value'  : integer working hours for that sample and calendar,
        - 'slots'  : sorted list of selected time-slot IDs, derived from
                     the last `value` entries of the corresponding
                     `peel_order`.
    """
    n_samples, n_cals = ac_samples_inverse.shape
    ac_names = np.asarray(ac_names)
    if ac_names.size != n_cals:
        raise ValueError("ac_samples_inverse columns do not match ac_names length.")

    # Base long-form table: one row per (sample, calendar)
    arrival_calendar_converted = pd.DataFrame(
        {
            "name": np.tile(ac_names, n_samples),
            "sample": np.repeat(np.arange(n_samples), n_cals),
            "value": ac_samples_inverse.ravel().astype(int),
        }
    )[["name", "sample", "value"]]

    # Lookups from original arrival_calendar
    ac_peel_lut = (
        arrival_calendar
        .set_index("name")[["peel_order", "working_hours"]]
        .to_dict(orient="index")
    )

    # Prepare slots column
    arrival_calendar_converted["slots"] = [[] for _ in range(len(arrival_calendar_converted))]

    for i, row in arrival_calendar_converted.iterrows():
        name = row["name"]
        target_hours = int(row["value"])

        info = ac_peel_lut.get(name, {"peel_order": [], "working_hours": 0})
        peel_order = list(map(int, info.get("peel_order", []) or []))

        if target_hours <= 0 or not peel_order:
            chosen = []
        else:
            # take the last `target_hours` items
            chosen = peel_order[-target_hours:]

        # Note: current implementation sorts the chosen slots
        arrival_calendar_converted.at[i, "slots"] = sorted(chosen)

    return arrival_calendar_converted
