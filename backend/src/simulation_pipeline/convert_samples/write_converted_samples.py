from typing import List, Dict, Any, Optional, Tuple
from contextlib import contextmanager
from joblib import Parallel, delayed
from functools import partial
from tqdm import tqdm
import pandas as pd
import numpy as np
import joblib
import copy
import json
import math
import os
import gc


def write_all_samples_to_json_files(
    data: dict,
    samples: np.ndarray,
    *,
    is_sobol: bool,
    is_gateway: bool = True,
    is_tasks_resources: bool = True,
    is_arrival_calendar: bool = True,
    is_resource_calendars: bool = True,
    is_arrival_distribution: bool = True,
    is_resource_numbers: bool = True,
    gateways: Optional[pd.DataFrame],
    gateways_converted: Optional[pd.DataFrame],
    tasks_resources: Optional[pd.DataFrame],
    tasks_resources_converted: Optional[pd.DataFrame],
    base_calendar: Optional[pd.DataFrame],
    arrival_calendar_converted: Optional[pd.DataFrame],
    resource_calendars_converted: Optional[pd.DataFrame],
    resource_calendars: Optional[pd.DataFrame],
    arrival_distribution_converted: Optional[pd.DataFrame],
    resource_numbers: Optional[pd.DataFrame],
    resource_numbers_converted: Optional[pd.DataFrame],
    sobol_chunk_size: int,
    morris_chunk_size: int,
    simulation_results_folder: str,
) -> None:
    """
    Chunk, apply, and write sampled configurations as JSON files to disk.

    For a given global sample matrix, this function:
      - splits the samples into chunks (Sobol or Morris sized),
      - filters all converted parameter tables to the current chunk,
      - applies each sample in the chunk to the base JSON configuration
        using `apply_one_core`,
      - writes one JSON file per chunk under
        `<simulation_results_folder>/samples/`,
      - displays progress bars for chunk and per-sample processing.

    Parameters
    ----------
    data : dict
        Base JSON-like configuration (parsed from the original simulation JSON)
        that will be updated with sampled parameter values.
    samples : np.ndarray
        Global sample matrix of shape (n_samples, n_parameters) produced by
        a sampling method such as Sobol or Morris.
    is_sobol : bool
        If True, uses `sobol_chunk_size` and names files
        `samples_sobol_<lo>_<hi>.json`; otherwise uses `morris_chunk_size`
        and `samples_morris_<lo>_<hi>.json`.
    is_gateway : bool, optional
        If True, applies sampled gateway branching probabilities using
        `gateways` and `gateways_converted`.
    is_tasks_resources : bool, optional
        If True, applies sampled task–resource distributions using
        `tasks_resources` and `tasks_resources_converted`.
    is_arrival_calendar : bool, optional
        If True, rebuilds `arrival_time_calendar` from
        `arrival_calendar_converted` and `base_calendar`.
    is_resource_calendars : bool, optional
        If True, rebuilds resource calendars per resource from
        `resource_calendars_converted`, `resource_calendars`, and `base_calendar`.
    is_arrival_distribution : bool, optional
        If True, overwrites `arrival_time_distribution` with a fixed value
        from `arrival_distribution_converted`.
    is_resource_numbers : bool, optional
        If True, updates resource amounts in `resource_profiles` using
        `resource_numbers` and `resource_numbers_converted`.
    gateways : Optional[pd.DataFrame]
        Original gateway parameter table; required if `is_gateway` is True.
    gateways_converted : Optional[pd.DataFrame]
        Long-format gateway probabilities including a 'sample' column.
    tasks_resources : Optional[pd.DataFrame]
        Original task–resource parameter table; required if
        `is_tasks_resources` is True.
    tasks_resources_converted : Optional[pd.DataFrame]
        Long-format task–resource values including a 'sample' column.
    base_calendar : Optional[pd.DataFrame]
        Base calendar definition with slot metadata; required for calendar
        operations (arrival and resource calendars).
    arrival_calendar_converted : Optional[pd.DataFrame]
        Long-format arrival calendar table with columns including
        ['sample', 'slots'].
    resource_calendars_converted : Optional[pd.DataFrame]
        Long-format resource calendar table with columns including
        ['sample', 'slots'].
    resource_calendars : Optional[pd.DataFrame]
        Original resource calendar mapping including 'resource_id' and 'name'.
    arrival_distribution_converted : Optional[pd.DataFrame]
        Long-format arrival-distribution values with 'sample' and 'value'.
    resource_numbers : Optional[pd.DataFrame]
        Original resource-number parameter table with at least ['name', 'resource_id'].
    resource_numbers_converted : Optional[pd.DataFrame]
        Long-format resource-number values with 'sample' and 'value'.
    sobol_chunk_size : int
        Number of samples per chunk when `is_sobol` is True.
    morris_chunk_size : int
        Number of samples per chunk when `is_sobol` is False.
    simulation_results_folder : str
        Root directory into which the `samples` subfolder and JSON files
        will be written.

    Returns
    -------
    None
        The function writes JSON files to disk and prints a summary;
        the internally collected list of file paths is not returned.
    """
    # Create SA-specific output folder inside results folder
    folder = os.path.join(simulation_results_folder, "samples")
    os.makedirs(folder, exist_ok=True)

    n_samples = samples.shape[0]
    CHUNK_SIZE = sobol_chunk_size if is_sobol else morris_chunk_size
    total_chunks = math.ceil(n_samples / CHUNK_SIZE)

    files_written: List[str] = []

    # Global progress bar over chunks
    global_pbar = tqdm(
        total=total_chunks,
        desc="Chunks",
        position=0,
        leave=True,
        dynamic_ncols=True,
    )

    for chunk_idx in range(total_chunks):
        lo = chunk_idx * CHUNK_SIZE
        hi = min(n_samples, (chunk_idx + 1) * CHUNK_SIZE) - 1
        if lo > hi:
            break

        samples_this_chunk = range(lo, hi + 1)

        # Filter only the *_final dataframes for this chunk
        gateways_chunk = filter_df_to_chunks(gateways_converted, lo, hi)
        tasks_resources_chunk = filter_df_to_chunks(tasks_resources_converted, lo, hi)
        arrival_calendar_chunk = filter_df_to_chunks(arrival_calendar_converted, lo, hi)
        resource_calendars_chunk = filter_df_to_chunks(resource_calendars_converted, lo, hi)
        arrival_distribution_chunk = filter_df_to_chunks(arrival_distribution_converted, lo, hi)
        resource_numbers_chunk = filter_df_to_chunks(resource_numbers_converted, lo, hi)

        # Pre-bind constants for this chunk only
        apply_bound_chunk = partial(
            apply_one_core,
            data=data,
            is_gateway=is_gateway,
            is_tasks_resources=is_tasks_resources,
            is_arrival_calendar=is_arrival_calendar,
            is_resource_calendars=is_resource_calendars,
            is_arrival_distribution=is_arrival_distribution,
            is_resource_numbers=is_resource_numbers,
            gateways=gateways,
            gateways_converted=gateways_chunk,
            tasks_resources=tasks_resources,
            tasks_resources_converted=tasks_resources_chunk,
            base_calendar=base_calendar,
            arrival_calendar_converted=arrival_calendar_chunk,
            resource_calendars=resource_calendars,
            resource_calendars_converted=resource_calendars_chunk,
            arrival_distribution_converted=arrival_distribution_chunk,
            resource_numbers=resource_numbers,
            resource_numbers_converted=resource_numbers_chunk,
        )

        # Inner per-chunk bar
        desc = f"Chunk {chunk_idx + 1}/{total_chunks} [{lo}-{hi}]"
        with joblib_tqdm(total=len(samples_this_chunk), desc=desc, position=1, leave=False):
            pairs = Parallel(
                n_jobs=-5,
                backend="loky",
                prefer="processes",
                batch_size=CHUNK_SIZE,
                verbose=0,
            )(delayed(apply_bound_chunk)(s) for s in samples_this_chunk)

        # Write just this chunk to disk
        chunk_dict = {k: v for k, v in pairs}
        safe_chunk = {k: make_json_safe(v) for k, v in chunk_dict.items()}

        # Choose folder and filename based on method
        if is_sobol:
            out_name = f"samples_sobol_{lo:05d}_{hi:05d}.json"
        else:
            out_name = f"samples_morris_{lo:05d}_{hi:05d}.json"

        out_path = os.path.join(folder, out_name)

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(safe_chunk, f, ensure_ascii=False, indent=2)

        files_written.append(out_path)

        # Free memory before next chunk
        del pairs, chunk_dict, safe_chunk
        gc.collect()

        # Advance global progress
        global_pbar.update(1)

    global_pbar.close()
    print(f"✅ Wrote {len(files_written)} JSON files.")
    if files_written:
        print("First/last files:", files_written[0], "…", files_written[-1])


@contextmanager
def joblib_tqdm(total: int, desc: str, position: int = 1, leave: bool = False):
    """
    Context manager wiring a tqdm progress bar into joblib's batch completion.

    Within the context, joblib's `BatchCompletionCallBack` is temporarily
    patched so that each completed batch updates the given progress bar.
    When the context exits, the original callback is restored and the bar
    is closed.

    Parameters
    ----------
    total : int
        Total number of iterations (e.g. samples) to report to tqdm.
    desc : str
        Description text shown next to the progress bar.
    position : int, optional
        Line position for the bar (useful when displaying multiple bars).
    leave : bool, optional
        If True, leaves the progress bar on screen after completion.

    Yields
    ------
    tqdm.tqdm
        The created tqdm progress bar instance for use inside the context.
    """

    pbar = tqdm(total=total, desc=desc, position=position, leave=leave, dynamic_ncols=True)
    old_callback = joblib.parallel.BatchCompletionCallBack

    class TqdmBatchCompletionCallback(old_callback):
        def __call__(self, *args, **kwargs):
            try:
                # One callback per finished batch; advance by batch_size
                pbar.update(self.batch_size)
            finally:
                return super().__call__(*args, **kwargs)

    joblib.parallel.BatchCompletionCallBack = TqdmBatchCompletionCallback
    try:
        yield pbar
    finally:
        joblib.parallel.BatchCompletionCallBack = old_callback
        pbar.close()


def apply_one_core(
    sample: int,
    *,
    data: dict,
    is_gateway: bool = True,
    is_tasks_resources: bool = True,
    is_arrival_calendar: bool = True,
    is_resource_calendars: bool = True,
    is_arrival_distribution: bool = True,
    is_resource_numbers: bool = True,
    gateways: Optional[pd.DataFrame],
    gateways_converted: Optional[pd.DataFrame],
    tasks_resources: Optional[pd.DataFrame],
    tasks_resources_converted: Optional[pd.DataFrame],
    base_calendar: Optional[pd.DataFrame],
    arrival_calendar_converted: Optional[pd.DataFrame],
    resource_calendars: Optional[pd.DataFrame],
    resource_calendars_converted: Optional[pd.DataFrame],
    arrival_distribution_converted: Optional[pd.DataFrame],
    resource_numbers: Optional[pd.DataFrame],
    resource_numbers_converted: Optional[pd.DataFrame],
) -> Tuple[int, Dict[str, Any]]:
    """
    Apply one sample index to the base configuration and return the result.

    This helper is intended for parallel execution. For a given sample index:
      - it calls `apply_sampled_values` with all feature toggles and
        converted DataFrames,
      - it returns a `(sample_index, updated_config_dict)` pair that can be
        collected and written to disk by the caller.

    Parameters
    ----------
    sample : int
        Sample index (row index in the global sample matrix) to apply.
    data : dict
        Base JSON-like configuration to update (will be deep-copied inside
        `apply_sampled_values`).
    is_gateway : bool, optional
        If True, applies sampled gateway probabilities.
    is_tasks_resources : bool, optional
        If True, applies sampled task–resource values.
    is_arrival_calendar : bool, optional
        If True, updates the arrival time calendar from sampled slots.
    is_resource_calendars : bool, optional
        If True, rebuilds resource calendars from sampled slots.
    is_arrival_distribution : bool, optional
        If True, replaces the arrival-time distribution with a fixed value.
    is_resource_numbers : bool, optional
        If True, updates resource amounts in resource profiles.
    gateways : Optional[pd.DataFrame]
        Original gateway parameter table.
    gateways_converted : Optional[pd.DataFrame]
        Long-format gateway probabilities including 'sample'.
    tasks_resources : Optional[pd.DataFrame]
        Original task–resource parameter table.
    tasks_resources_converted : Optional[pd.DataFrame]
        Long-format task–resource values including 'sample'.
    base_calendar : Optional[pd.DataFrame]
        Base calendar used to translate slot IDs into time periods.
    arrival_calendar_converted : Optional[pd.DataFrame]
        Long-format arrival calendar table including 'sample' and 'slots'.
    resource_calendars : Optional[pd.DataFrame]
        Original resource calendar parameter table.
    resource_calendars_converted : Optional[pd.DataFrame]
        Long-format resource calendar table including 'sample' and 'slots'.
    arrival_distribution_converted : Optional[pd.DataFrame]
        Long-format arrival-distribution values with 'sample' and 'value'.
    resource_numbers : Optional[pd.DataFrame]
        Original resource-number parameter table.
    resource_numbers_converted : Optional[pd.DataFrame]
        Long-format resource-number values with 'sample' and 'value'.

    Returns
    -------
    Tuple[int, Dict[str, Any]]
        A tuple `(sample, updated_config)` where `updated_config` is the
        JSON-like configuration with all enabled sampled values applied.
    """
    return sample, apply_sampled_values(
        data=data,
        is_gateway=is_gateway,
        is_tasks_resources=is_tasks_resources,
        is_arrival_calendar=is_arrival_calendar,
        is_resource_calendars=is_resource_calendars,
        is_arrival_distribution=is_arrival_distribution,
        is_resource_numbers=is_resource_numbers,
        gateways=gateways,
        gateways_converted=gateways_converted,
        tasks_resources=tasks_resources,
        tasks_resources_converted=tasks_resources_converted,
        base_calendar=base_calendar,
        arrival_calendar_converted=arrival_calendar_converted,
        resource_calendars=resource_calendars,
        resource_calendars_converted=resource_calendars_converted,
        arrival_distribution_converted=arrival_distribution_converted,
        resource_numbers=resource_numbers,
        resource_numbers_converted=resource_numbers_converted,
        sample=sample,
    )


def make_json_safe(obj: Any) -> Any:
    """
    Recursively convert NumPy scalars/arrays into plain Python types so
    that `json.dump` can serialize the object without errors.

    The conversion rules applied are:
      - dict             → same structure, values processed recursively,
      - list/tuple       → list with each element processed recursively,
      - np.integer       → int,
      - np.floating      → float,
      - np.bool_         → bool,
      - anything else    → returned unchanged.

    Parameters
    ----------
    obj : Any
        Arbitrary Python object potentially containing NumPy types.

    Returns
    -------
    Any
        A JSON-serializable object structurally equivalent to `obj`, but
        with NumPy scalars/arrays replaced by plain Python types.
    """
    import numpy as np

    if isinstance(obj, dict):
        return {k: make_json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [make_json_safe(v) for v in obj]
    elif isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        return float(obj)
    elif isinstance(obj, (np.bool_,)):
        return bool(obj)
    else:
        return obj


def filter_df_to_chunks(df: Optional[pd.DataFrame], lo: int, hi: int) -> Optional[pd.DataFrame]:
    """
    Filter a long-format DataFrame to a contiguous window of sample indices.

    The function expects a 'sample' column and returns only those rows
    whose sample index lies in the inclusive range [lo, hi]. If the input
    DataFrame is None, empty, or lacks a 'sample' column, it is returned
    unchanged.

    Parameters
    ----------
    df : Optional[pd.DataFrame]
        Long-format DataFrame with a 'sample' column, or None.
    lo : int
        Lower bound (inclusive) of the sample index range.
    hi : int
        Upper bound (inclusive) of the sample index range.

    Returns
    -------
    Optional[pd.DataFrame]
        A copy of the filtered DataFrame restricted to [lo, hi], or the
        original/None if no filtering is applicable.
    """
    if df is None:
        return None
    if getattr(df, "empty", True):
        return df
    if "sample" not in df.columns:
        return df
    return df.loc[(df["sample"] >= lo) & (df["sample"] <= hi)].copy()


def slots_to_periods(
    slots: List[int],
    base_calendar: pd.DataFrame,
) -> List[Dict[str, str]]:
    """
    Convert a list of slot IDs into merged, continuous daily time periods.

    The function:
      - looks up each slot's day, beginTime, and endTime in `base_calendar`,
      - groups slots by day (preserving original day order),
      - sorts slots within each day by beginTime,
      - merges consecutive slots whenever the previous slot's endTime
        equals the next slot's beginTime.

    Parameters
    ----------
    slots : List[int]
        List of slot IDs indicating active time slots (may contain duplicates).
    base_calendar : pd.DataFrame
        Base calendar table with columns:
            ['slot_id', 'day', 'beginTime', 'endTime'].

    Returns
    -------
    List[Dict[str, str]]
        One dictionary per merged continuous block of the form:
        {
            "from": <day>,
            "to": <day>,
            "beginTime": "HH:MM:SS",
            "endTime":   "HH:MM:SS"
        }.
        An empty list is returned if `slots` is empty.
    """

    if not slots:
        return []

    # Extract relevant info in slot_id order
    base = base_calendar.set_index("slot_id")[["day", "beginTime", "endTime"]]
    rows = base.loc[sorted(set(slots))].reset_index()

    merged_periods = []

    # Group rows per day (do not sort days alphabetically)
    for day, g in rows.groupby("day", sort=False):
        g = g.sort_values("beginTime", kind="mergesort")

        begins = g["beginTime"].to_numpy()
        ends   = g["endTime"].to_numpy()

        # Determine where new segments begin
        prev_end = np.roll(ends, 1)
        starts_new = np.ones(len(g), dtype=bool)
        starts_new[1:] = prev_end[1:] != begins[1:]

        start_idx = np.flatnonzero(starts_new)
        end_idx   = np.r_[start_idx[1:] - 1, len(g) - 1]

        # Build merged blocks
        for s, e in zip(start_idx, end_idx):
            merged_periods.append(
                {
                    "from": day,
                    "to": day,
                    "beginTime": begins[s],
                    "endTime": ends[e],
                }
            )

    return merged_periods


def apply_sampled_values(
    data: dict,
    *,
    # Feature toggles (default: enabled)
    is_gateway: bool = True,
    is_tasks_resources: bool = True,
    is_arrival_calendar: bool = True,
    is_resource_calendars: bool = True,
    is_arrival_distribution: bool = True,
    is_resource_numbers: bool = True,

    # Input converted DataFrames (optional)
    gateways: Optional[pd.DataFrame],
    gateways_converted: Optional[pd.DataFrame],
    tasks_resources: Optional[pd.DataFrame],
    tasks_resources_converted: Optional[pd.DataFrame],
    base_calendar: Optional[pd.DataFrame],
    arrival_calendar_converted: Optional[pd.DataFrame],
    resource_calendars: Optional[pd.DataFrame],
    resource_calendars_converted: Optional[pd.DataFrame],
    arrival_distribution_converted: Optional[pd.DataFrame],
    resource_numbers: Optional[pd.DataFrame],
    resource_numbers_converted: Optional[pd.DataFrame],

    # Which sample index to apply
    sample: int = 0,
) -> Dict[str, Any]:
    """
    Apply all enabled sampled parameter values to a base simulation config.

    For the given `sample` index, this function:
      - updates gateway branching probabilities in
        `gateway_branching_probabilities`,
      - converts task–resource distributions to fixed values in
        `task_resource_distribution`,
      - rebuilds `arrival_time_calendar` from sampled arrival slots,
      - builds per-resource calendars from sampled resource slots and
        writes them to `resource_calendars`,
      - updates `resource_profiles` to point to the new calendars,
      - overwrites `arrival_time_distribution` with a fixed value,
      - updates resource amounts in `resource_profiles` based on sampled
        resource numbers.

    The original `data` dict is not mutated; a deep copy is updated and
    returned.

    Parameters
    ----------
    data : dict
        Base simulation configuration (JSON-like dict) to which sampled
        values will be applied.
    is_gateway : bool, optional
        If True, applies gateway probabilities using `gateways` and
        `gateways_converted`.
    is_tasks_resources : bool, optional
        If True, applies task–resource values using `tasks_resources`
        and `tasks_resources_converted`.
    is_arrival_calendar : bool, optional
        If True, rebuilds `arrival_time_calendar` from
        `arrival_calendar_converted` and `base_calendar`.
    is_resource_calendars : bool, optional
        If True, rebuilds per-resource calendars from
        `resource_calendars_converted`, `resource_calendars`, and
        `base_calendar`, and wires them into `resource_profiles`.
    is_arrival_distribution : bool, optional
        If True, replaces `arrival_time_distribution` with a fixed
        distribution based on `arrival_distribution_converted`.
    is_resource_numbers : bool, optional
        If True, updates resource amounts in `resource_profiles` based
        on `resource_numbers` and `resource_numbers_converted`.
    gateways : Optional[pd.DataFrame]
        Original gateway parameter table with mapping to gateway and path IDs.
    gateways_converted : Optional[pd.DataFrame]
        Long-format gateway probabilities with columns including
        ['name', 'sample', 'probability'].
    tasks_resources : Optional[pd.DataFrame]
        Original task–resource parameter table with
        ['name', 'task_id', 'resource_id'].
    tasks_resources_converted : Optional[pd.DataFrame]
        Long-format task–resource values with columns including
        ['name', 'sample', 'value'].
    base_calendar : Optional[pd.DataFrame]
        Base calendar with slot definitions, required for slot-to-period
        conversions.
    arrival_calendar_converted : Optional[pd.DataFrame]
        Long-format arrival calendar table with ['sample', 'slots'].
    resource_calendars : Optional[pd.DataFrame]
        Original resource calendar mapping with ['name', 'resource_id'].
    resource_calendars_converted : Optional[pd.DataFrame]
        Long-format resource calendar table with ['name', 'sample', 'slots'].
    arrival_distribution_converted : Optional[pd.DataFrame]
        Long-format arrival-distribution values with ['sample', 'value'].
    resource_numbers : Optional[pd.DataFrame]
        Original resource-number parameter table with ['name', 'resource_id'].
    resource_numbers_converted : Optional[pd.DataFrame]
        Long-format resource-number values with ['name', 'sample', 'value'].
    sample : int, optional
        Sample index to apply (row index in the global sample matrix).

    Returns
    -------
    Dict[str, Any]
        A deep-copied configuration dict with all enabled sampled values
        integrated according to the provided flags and DataFrames.
    """

    # deepcopy is necessary to avoid mutating the original structure
    new_data = copy.deepcopy(data)

    # ---------- 1) Gateways ----------
    if is_gateway and gateways_converted is not None and not gateways_converted.empty:
        samp_gw = gateways_converted.loc[
            gateways_converted["sample"] == sample, ["name", "probability"]
        ]
        if not samp_gw.empty and gateways is not None and not gateways.empty:
            # Map 'name' -> probability, then align on gateways rows
            prob_map = samp_gw.set_index("name")["probability"]
            # Filter gateways to only names present in this sample for less work
            gpart = gateways.loc[
                gateways["name"].isin(prob_map.index),
                ["name", "gateway_id", "path_id"],
            ].copy()
            if not gpart.empty:
                gpart["probability"] = gpart["name"].map(prob_map).astype(float)
                # Build lookup (tuple->float) from arrays (faster than iterrows/itertuples)
                gids = gpart["gateway_id"].to_numpy()
                pids = gpart["path_id"].to_numpy()
                vals = gpart["probability"].to_numpy()
                gw_lookup = {(gids[i], pids[i]): vals[i] for i in range(len(gpart))}

                # Update JSON
                for gw in new_data.get("gateway_branching_probabilities", []):
                    gid = gw.get("gateway_id")
                    probs = gw.get("probabilities", ())
                    for p in probs:
                        k = (gid, p.get("path_id"))
                        v = gw_lookup.get(k)
                        if v is not None:
                            p["value"] = v


    # ---------- 2) Task–resources ----------
    if (
        is_tasks_resources
        and tasks_resources_converted is not None
        and not tasks_resources_converted.empty
    ):
        samp_tr = tasks_resources_converted.loc[
            tasks_resources_converted["sample"] == sample, ["name", "value"]
        ]
        if (
            not samp_tr.empty
            and tasks_resources is not None
            and not tasks_resources.empty
        ):
            val_map = samp_tr.set_index("name")["value"]
            # Filter only names we have for this sample
            tpart = tasks_resources.loc[
                tasks_resources["name"].isin(val_map.index),
                ["name", "task_id", "resource_id"],
            ].copy()
            if not tpart.empty:
                tpart["task_id"] = tpart["task_id"].astype(str)
                tpart["resource_id"] = tpart["resource_id"].astype(str)
                tpart["value"] = tpart["name"].map(val_map).astype(float)

                tids = tpart["task_id"].to_numpy()
                rids = tpart["resource_id"].to_numpy()
                vals = tpart["value"].to_numpy()
                tr_lookup = {(tids[i], rids[i]): vals[i] for i in range(len(tpart))}

                for tr in new_data.get("task_resource_distribution", []):
                    tid = str(tr.get("task_id"))
                    for res in tr.get("resources", ()):
                        rid = str(res.get("resource_id"))
                        v = tr_lookup.get((tid, rid))
                        if v is not None:
                            res["distribution_name"] = "fix"
                            res["distribution_params"] = [{"value": float(v)}]

    # ---------- 3) Arrival calendar (slots -> merged periods) ----------
    if (
        is_arrival_calendar
        and base_calendar is not None
        and not base_calendar.empty
        and arrival_calendar_converted is not None
        and not arrival_calendar_converted.empty
    ):
        samp_arr = arrival_calendar_converted.loc[
            arrival_calendar_converted["sample"] == sample
        ]
        if not samp_arr.empty and "slots" in samp_arr.columns:
            slots_series = samp_arr["slots"]
            slots_union = set()
            for s in slots_series:
                if isinstance(s, (list, tuple, set, np.ndarray)):
                    slots_union.update(map(int, s))
            periods = slots_to_periods(sorted(slots_union), base_calendar)
            new_data["arrival_time_calendar"] = periods

    # ---------- 4) Resource calendars (per resource_id, slots -> merged periods) ----------
    if (
        is_resource_calendars
        and resource_calendars is not None
        and not resource_calendars.empty
        and base_calendar is not None
        and not base_calendar.empty
        and resource_calendars_converted is not None
        and not resource_calendars_converted.empty
    ):
        samp_rc = resource_calendars_converted.loc[
            resource_calendars_converted["sample"] == sample, ["name", "slots"]
        ]
        if not samp_rc.empty:
            # Map name -> resource_id (faster than merge)
            name_to_res = resource_calendars.set_index("name")["resource_id"]
            rc = samp_rc.copy()
            rc["resource_id"] = rc["name"].map(name_to_res)
            rc = rc.dropna(subset=["resource_id"])

            # Union slots per resource_id
            slot_sets: Dict[str, set[int]] = {}
            for rid, s in zip(
                rc["resource_id"].astype(str).to_numpy(), rc["slots"].to_numpy()
            ):
                if isinstance(s, (list, tuple, set, np.ndarray)):
                    dset = slot_sets.get(rid)
                    if dset is None:
                        dset = set()
                        slot_sets[rid] = dset
                    dset.update(map(int, s))

            # Memoize identical slot-tuples to avoid recomputing periods
            periods_cache: Dict[tuple[int, ...], list[dict]] = {}
            resource_cals = []
            for rid, sset in slot_sets.items():
                slots_sorted = tuple(sorted(sset))
                periods = periods_cache.get(slots_sorted)
                if periods is None:
                    periods = slots_to_periods(list(slots_sorted), base_calendar)
                    periods_cache[slots_sorted] = periods
                cal_name = f"{rid}_profile_calendar"
                resource_cals.append(
                    {"id": cal_name, "name": cal_name, "time_periods": periods}
                )

            new_data["resource_calendars"] = resource_cals

    # ---------- 5) Resource profiles: set calendar = "<id>_profile_calendar" ----------
    if is_resource_calendars or is_resource_numbers:
        rprofiles = new_data.get("resource_profiles")
        if is_resource_calendars and isinstance(rprofiles, list):
            for prof in rprofiles:
                rlist = prof.get("resource_list", [])
                for res in rlist:
                    rid = str(res.get("id", "")).strip()
                    if rid:
                        res["calendar"] = f"{rid}_profile_calendar"

    # ---------- 6) Arrival distribution: set to FIX from sampled value ----------
    if (
        is_arrival_distribution
        and arrival_distribution_converted is not None
        and not arrival_distribution_converted.empty
    ):
        samp_ad_vals = arrival_distribution_converted.loc[
            arrival_distribution_converted["sample"] == sample, "value"
        ]
        if not samp_ad_vals.empty:
            v = float(samp_ad_vals.iloc[0])
            new_data["arrival_time_distribution"] = {
                "distribution_name": "fix",
                "distribution_params": [{"value": v}],
            }

    # ---------- 7) Resource numbers: update amounts in resource_profiles ----------
    if (
        is_resource_numbers
        and resource_numbers_converted is not None
        and not resource_numbers_converted.empty
        and resource_numbers is not None
        and not resource_numbers.empty
    ):
        rprofiles = new_data.get("resource_profiles")
        if isinstance(rprofiles, list):
            samp_rn = resource_numbers_converted.loc[
                resource_numbers_converted["sample"] == sample, ["name", "value"]
            ]
            if not samp_rn.empty:
                name_to_value = samp_rn.set_index("name")["value"]
                name_to_resid = resource_numbers.set_index("name")["resource_id"]

                # Build resource_id -> amount lookup
                common_names = name_to_value.index.intersection(name_to_resid.index)
                if len(common_names) > 0:
                    resid = name_to_resid.loc[common_names].astype(str).to_numpy()
                    vals = name_to_value.loc[common_names].to_numpy(dtype=float)
                    rn_lookup = {
                        resid[i]: int(round(vals[i])) for i in range(len(resid))
                    }

                    for prof in rprofiles:
                        rlist = prof.get("resource_list", [])
                        for res in rlist:
                            rid = str(res.get("id", "")).strip()
                            if rid in rn_lookup:
                                res["amount"] = rn_lookup[rid]
                        # Enforce id/name = "{resource_id}_profile" if exactly one resource present
                        if len(rlist) == 1:
                            rid_single = str(rlist[0].get("id", "")).strip()
                            if rid_single:
                                prof_id = f"{rid_single}_profile"
                                prof["id"] = prof_id
                                prof["name"] = prof_id

    return new_data
