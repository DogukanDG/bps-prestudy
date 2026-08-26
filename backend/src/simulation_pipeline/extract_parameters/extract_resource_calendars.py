from .extract_arrival_calendar import _peel_categories_24, build_neighbor_map
from typing import Dict, Any, List, Set
import pandas as pd


DAY_ORDER = ["MONDAY", "TUESDAY", "WEDNESDAY",
             "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


def extract_resource_calendars(
    data: Dict[str, Any],
    arrival_calendar: pd.DataFrame,
    base_calendar: pd.DataFrame,
    seed: int | None = None,
) -> pd.DataFrame | None:
    """
    Extract resource calendar assignments from the config and expand them to slots.

    This function:
      1. Scans `data["resource_profiles"]` to build a table that maps each
         resource_id to the name of its assigned calendar.
      2. Uses `data["resource_calendars"]` and `base_calendar` to translate each
         calendar's time_periods into concrete slot IDs.
      3. Adds a 'slots' column (list of slot_ids) and a 'working_hours' column
         (number of slots).
      4. Calls `compute_peel_order_resource_calendars` to compute a peel_order
         per resource, using the arrival calendar as reference.

    Expected structure
    ------------------
    data["resource_profiles"] = [
        {
            "resource_list": [
                {"id": str, "calendar": str},
                ...
            ],
            ...
        },
        ...
    ]

    data["resource_calendars"] = [
        {
            "name": str,
            "time_periods": [
                {
                    "from": "MONDAY",
                    "to": "MONDAY",
                    "beginTime": "09:00:00",
                    "endTime": "17:00:00",
                },
                ...
            ]
        },
        ...
    ]

    Parameters
    ----------
    data : Dict[str, Any]
        Parsed JSON-like configuration containing 'resource_profiles'
        and 'resource_calendars'.
    arrival_calendar : pd.DataFrame
        Arrival calendar table with at least ['slots', 'peel_order'], used
        as reference when computing resource peel orders.
    base_calendar : pd.DataFrame
        Base calendar with columns ['slot_id', 'day', 'beginTime', 'endTime'].
    seed : int or None, optional
        Optional random seed passed through to the peeling logic for
        deterministic ordering.

    Returns
    -------
    pd.DataFrame or None
        Resource calendar table with columns:
        ['name', 'vis_name', 'type', 'resource_id', 'assigned_calendar',
         'slots', 'working_hours', 'peel_order'].
        If no resources are defined, an empty DataFrame is returned.
    """

    rows: List[Dict[str, Any]] = []
    resource_counter = 1

    # --- Build base table of resources -> assigned calendar name ---
    for profile in data.get("resource_profiles", []):
        for resource in profile.get("resource_list", []):
            rows.append({
                "name": f"Resource Calendar {resource_counter}",  # Resource Calendar 1, 2, ...
                "vis_name": f"Resource Calendar {resource.get('id')}",
                "type": "resource_calendar",
                "resource_id": resource.get("id"),
                "assigned_calendar": resource.get("calendar"),
            })
            resource_counter += 1

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    # --- Build calendar_name -> slot_ids map from base_calendar ---
    calendar_slots_map: Dict[str, List[int]] = {}

    for cal in data.get("resource_calendars", []):
        slots: List[int] = []

        for p in cal.get("time_periods", []):
            frm = p["from"]
            to = p.get("to", frm)

            i0, i1 = DAY_ORDER.index(frm), DAY_ORDER.index(to)
            days = DAY_ORDER[i0:i1 + 1] if i0 <= i1 else DAY_ORDER[i0:] + DAY_ORDER[:i1 + 1]

            for d in days:
                mask = (
                    (base_calendar["day"].eq(d)) &
                    (base_calendar["beginTime"].ge(p["beginTime"])) &
                    (base_calendar["endTime"].le(p["endTime"]))
                )
                slots.extend(base_calendar.loc[mask, "slot_id"].tolist())

        calendar_slots_map[cal["name"]] = sorted(set(slots))

    # Map slot lists + compute working hours
    df["slots"] = df["assigned_calendar"].map(
        lambda n: calendar_slots_map.get(n, [])
    )
    df["working_hours"] = df["slots"].str.len()

    df = compute_peel_order_resource_calendars(
            resource_calendars=df,
            arrival_calendar=arrival_calendar,
            base_calendar=base_calendar,
            seed=seed,
        )

    return df


def _concat_groups(groups: List[List[int]]) -> List[int]:
    """
    Flatten a list of lists of integers into a single list.

    Example
    -------
    [[1, 2], [3], []] -> [1, 2, 3]

    Parameters
    ----------
    groups : List[List[int]]
        Nested list where each inner list contains node or slot IDs.

    Returns
    -------
    List[int]
        Single list containing all elements from the inner lists in order.
    """
    out: List[int] = []
    for g in groups:
        out.extend(g)
    return out


def make_resource_peel_order(
    slots_row: List[int],
    arrival_slots: Set[int],
    arrival_peel_order: List[int],
    arrival_peel_set: Set[int],
    nbr_map: Dict[int, Set[int]],
    seed: int | None = None,
) -> List[int]:
    """
    Build the final peel order for a single resource calendar.

    The algorithm:
      1. Splits the resource's slot set into:
         - non-overlapping with arrival slots,
         - overlapping with arrival slots.
      2. Applies a full 24-step peeling (Z1–C12) to the non-overlapping slots
         using `_peel_categories_24`.
      3. For overlapping slots, follows the arrival calendar's `arrival_peel_order`
         to preserve their relative order.
      4. Appends any overlapping nodes that never appeared in the arrival peel
         order (e.g. 2-core nodes).
      5. Concatenates the two parts:
         non-overlap peel order → overlap order.

    Parameters
    ----------
    slots_row : List[int]
        Slot IDs assigned to this resource calendar.
    arrival_slots : Set[int]
        All slot IDs present in the arrival calendar.
    arrival_peel_order : List[int]
        Flattened peel order of the arrival calendar (Z1, C1, ..., Z12, C12).
    arrival_peel_set : Set[int]
        Set version of `arrival_peel_order` for fast membership checks.
    nbr_map : Dict[int, Set[int]]
        Neighbor map slot_id -> neighboring slot_ids.
    seed : int or None, optional
        Optional random seed used for the local peeling of non-overlapping slots.

    Returns
    -------
    List[int]
        Final peel order for the resource's slots, with non-overlapping slots
        peeled first and overlapping slots ordered consistently with the arrival
        calendar.
    """

    # Convert input safely
    s = set(int(x) for x in (slots_row or []))

    # ------------------------------------------------
    # 1) SPLIT SLOTS INTO: non-overlap, overlap
    # ------------------------------------------------
    non_overlap = sorted(s - arrival_slots)
    overlap = s & arrival_slots

    # ------------------------------------------------
    # 2) NON-OVERLAP: apply full 24-step peeling
    # ------------------------------------------------
    cats_non = _peel_categories_24(non_overlap, nbr_map, seed=seed)
    non_overlap_order = _concat_groups(cats_non)

    # ------------------------------------------------
    # 3) OVERLAP: follow arrival's peel order
    # ------------------------------------------------
    overlap_order = [x for x in arrival_peel_order if x in overlap]

    # Add any overlapping nodes that were never peeled in arrival calendar
    # (typically 2-core nodes)
    missing_overlap = [x for x in overlap if x not in arrival_peel_set]
    if missing_overlap:
        overlap_order.extend(sorted(missing_overlap))

    # ------------------------------------------------
    # 4) STITCH FINAL ORDER
    # ------------------------------------------------
    return non_overlap_order + overlap_order


def compute_peel_order_resource_calendars(
    resource_calendars: pd.DataFrame,
    arrival_calendar: pd.DataFrame,
    base_calendar: pd.DataFrame,
    seed: int | None = None,
) -> pd.DataFrame:
    """
    Compute peel orders for all resource calendars, aligned with the arrival calendar.

    For each resource calendar row, this function:
      - uses the global neighbor map from `base_calendar`,
      - reads its 'slots',
      - merges two components:
          * a 24-step peeling of slots not overlapping with the arrival calendar,
          * the arrival calendar's peel order for overlapping slots (with any
            missing overlap nodes appended),
      - stores the resulting sequence in a new 'peel_order' column.

    Parameters
    ----------
    resource_calendars : pd.DataFrame
        Table describing resource calendars; must contain a 'slots' column
        where each entry is a list of slot_ids.
    arrival_calendar : pd.DataFrame
        Arrival calendar with at least one row and columns:
        ['slots', 'peel_order']; only the first row is used as reference.
    base_calendar : pd.DataFrame
        Base calendar with 'slot_id' and 'neighboring_slots' columns, used
        to build the neighbor map for peeling.
    seed : int or None, optional
        Optional seed used when computing the non-overlapping peeling part.

    Returns
    -------
    pd.DataFrame
        Copy of `resource_calendars` with an added 'peel_order' column
        containing the final peel order (list[int]) for each resource.
    """

    # --- Build neighbor map ---
    nbr_map = build_neighbor_map(base_calendar)

    # --- Extract arrival calendar information ---
    arrival_slots = set(int(x) for x in arrival_calendar.iloc[0]["slots"])
    arrival_peel_order = list(arrival_calendar.iloc[0]["peel_order"])
    arrival_peel_set = set(arrival_peel_order)

    # --- Copy to avoid mutating user dataframe ---
    df = resource_calendars.copy()

    # --- Compute peel order row-by-row ---
    df["peel_order"] = df["slots"].apply(
        lambda sl: make_resource_peel_order(
            slots_row=sl,
            arrival_slots=arrival_slots,
            arrival_peel_order=arrival_peel_order,
            arrival_peel_set=arrival_peel_set,
            nbr_map=nbr_map,
            seed=seed,
        )
    )

    return df
