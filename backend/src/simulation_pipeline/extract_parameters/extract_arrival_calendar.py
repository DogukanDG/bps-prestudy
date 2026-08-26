from typing import Dict, Any, List, Set
import pandas as pd
import numpy as np


def extract_arrival_calendar(
    data: Dict[str, Any],
    base_calendar: pd.DataFrame,
    seed: int | None = None,
) -> pd.DataFrame:
    """
    Extract a single arrival-time calendar definition and compute its peel order.

    This function:
      1. Reads `data["arrival_time_calendar"]`, which is expected to be a list
         of period dictionaries with 'from', 'to' (optional), 'beginTime',
         and 'endTime'.
      2. Maps these periods onto slot IDs from `base_calendar`.
      3. Builds a one-row DataFrame describing this arrival calendar with
         columns:
           - 'name'          : fixed identifier "ac_1",
           - 'vis_name'      : naming for visualizations
           - 'type'          : "arrival_calendar",
           - 'slots'         : sorted, unique list of slot_ids,
           - 'working_hours' : number of slots.
      4. Calls `compute_peel_order_arrival_calendar` to add a 'peel_order'
         column using the 24-phase peeling logic.

    Parameters
    ----------
    data : Dict[str, Any]
        JSON-like configuration containing an 'arrival_time_calendar' entry.
    base_calendar : pd.DataFrame
        Base calendar with columns ['slot_id', 'day', 'beginTime', 'endTime'].
    seed : int | None, optional
        Optional random seed passed through to the peeling logic for
        reproducible peel orders.

    Returns
    -------
    pd.DataFrame
        Arrival calendar table with columns:
        ['name', 'vis_name', 'type', 'slots', 'working_hours', 'peel_order'].
        If no slots are matched, 'slots' will be empty and
        'working_hours' will be 0.
    """

    DAY_ORDER = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY",
                 "FRIDAY", "SATURDAY", "SUNDAY"]

    def _days_between(frm: str, to: str) -> List[str]:
        """Return all days from frm → to inclusive."""
        i0, i1 = DAY_ORDER.index(frm), DAY_ORDER.index(to)
        return DAY_ORDER[i0:i1 + 1] if i0 <= i1 else DAY_ORDER[i0:] + DAY_ORDER[:i1 + 1]

    slots: List[int] = []

    for p in data.get("arrival_time_calendar", []):
        day_from = p["from"]
        day_to = p.get("to", day_from)

        for d in _days_between(day_from, day_to):
            mask = (
                (base_calendar["day"] == d) &
                (base_calendar["beginTime"] >= p["beginTime"]) &
                (base_calendar["endTime"] <= p["endTime"])
            )
            slots.extend(base_calendar.loc[mask, "slot_id"].tolist())

    # Unique + sorted slots
    slots = sorted(set(slots))

    df = pd.DataFrame([{
        "name": "Arrival Calendar",
        "vis_name": "Arrival Calendar",
        "type": "arrival_calendar",
        "slots": slots,
        "working_hours": len(slots),
    }])

    df = compute_peel_order_arrival_calendar(
            base_calendar=base_calendar,
            arrival_calendar=df,
            seed=seed,
        )

    return df


# --------------------------------
# Neighbor map: base_calendar -> {slot_id: set(neighbor_slots)}
# --------------------------------
def build_neighbor_map(base_calendar: pd.DataFrame) -> Dict[int, Set[int]]:
    """
    Build a slot-to-neighbors map from a base calendar with neighboring slots.

    Parameters
    ----------
    base_calendar : pd.DataFrame
        Calendar DataFrame that must contain:
          - 'slot_id'           : unique slot identifier (int),
          - 'neighboring_slots' : list-like of neighboring slot_ids.

    Returns
    -------
    Dict[int, Set[int]]
        Dictionary mapping each slot_id to a set of neighboring slot_ids.
        Slots with no neighbors will map to an empty set.
    """
    nbr_map: Dict[int, Set[int]] = {}
    for r in base_calendar.itertuples(index=False):
        sid = int(r.slot_id)
        nbrs = set(int(x) for x in getattr(r, "neighboring_slots", []) or [])
        nbr_map[sid] = nbrs
    return nbr_map


# --------------------------------
# 24-check peeling logic
# --------------------------------
def _peel_categories_24(
    slots: List[int],
    nbr_map: Dict[int, Set[int]],
    seed: int | None = None,
) -> List[List[int]]:
    """
    Run a 24-phase peeling (Z₁–Z₁₂, C₁–C₁₂) on the subgraph induced by `slots`.

    In each of 12 rounds r:
      - Z_r removes all current degree-0 nodes from the subgraph.
      - C_r removes all current degree-1 nodes and updates neighbor degrees.
    The function records which nodes are removed at each phase. Nodes that
    remain in the 2-core after the procedure are not included in the output.

    Parameters
    ----------
    slots : List[int]
        Slot IDs forming the node set of the induced subgraph.
    nbr_map : Dict[int, Set[int]]
        Global neighbor map slot_id -> neighboring slot_ids.
    seed : int | None, optional
        Random seed for shuffling node order within each category to make
        the peeling order reproducible.

    Returns
    -------
    List[List[int]]
        A list of length 24:
        [Z1, C1, Z2, C2, ..., Z12, C12],
        where each element is the list of node IDs peeled at that phase.
        If `slots` is empty, returns 24 empty lists.
    """
    rng = np.random.default_rng(seed)
    S = set(int(x) for x in (slots or []))
    if not S:
        return [[] for _ in range(2 * 12)]

    # Induced adjacency/degree over S
    adj = {v: (nbr_map.get(v, set()) & S) for v in S}
    deg = {v: len(adj[v]) for v in S}

    cats: List[List[int]] = [[] for _ in range(2 * 12)]

    for r in range(1, 12 + 1):
        z_idx = 2 * (r - 1)  # Z_r
        c_idx = z_idx + 1    # C_r

        # --- Z_r: sweep ALL current degree-0 nodes
        z_nodes = [v for v in S if deg[v] == 0]
        if z_nodes:
            rng.shuffle(z_nodes)
            cats[z_idx].extend(z_nodes)
            # removing deg-0 nodes does NOT change any neighbor's degree
            for v in z_nodes:
                S.discard(v)

        # --- C_r: sweep ALL current degree-1 nodes
        c_nodes = [v for v in S if deg[v] == 1]
        if c_nodes:
            rng.shuffle(c_nodes)
            cats[c_idx].extend(c_nodes)
            for v in c_nodes:
                S.discard(v)
                # unlink v from its neighbors still in S and decrement their degrees
                for u in list(adj[v]):
                    if u in S:
                        adj[u].discard(v)
                        deg[u] -= 1

        # Early stop if neither sweep removed anything
        if not z_nodes and not c_nodes:
            break

    return cats


# --------------------------------
# Compute peeling order for a given slot set
# --------------------------------
def compute_peel_order_for_slots(
    slots: List[int],
    nbr_map: Dict[int, Set[int]],
    seed: int | None = None,
) -> List[int]:
    """
    Compute a flattened 24-phase peeling order for a set of slots.

    This function runs `_peel_categories_24` on the induced subgraph and
    flattens the result into a single list in strict phase order:
    Z1, C1, Z2, C2, ..., Z12, C12. Nodes that stay in the 2-core are not
    part of the returned list.

    Parameters
    ----------
    slots : List[int]
        Slot IDs forming the node set of the induced subgraph.
    nbr_map : Dict[int, Set[int]]
        Neighbor map slot_id -> neighboring slot_ids.
    seed : int | None, optional
        Optional random seed for reproducible randomness inside the peeling.

    Returns
    -------
    List[int]
        Flattened peel order of all nodes removed during the 24 phases.
        May be empty if no nodes can be peeled.
    """
    cats = _peel_categories_24(slots, nbr_map, seed=seed)
    out: List[int] = []
    for group in cats:
        out.extend(group)
    return out


# --------------------------------
# Arrival calendar integration
# --------------------------------
def compute_peel_order_arrival_calendar(
    base_calendar: pd.DataFrame,
    arrival_calendar: pd.DataFrame,
    seed: int | None = None,
) -> pd.DataFrame:
    """
    Compute and attach peel orders for each arrival calendar in a table.

    For every row in `arrival_calendar`, this function:
      - reads the list of active 'slots',
      - builds a neighbor map from `base_calendar`,
      - runs the 24-phase peeling logic via `compute_peel_order_for_slots`,
      - stores the resulting flattened peel order in a new 'peel_order'
        column.

    Parameters
    ----------
    base_calendar : pd.DataFrame
        Base calendar with at least:
          - 'slot_id',
          - 'neighboring_slots' (list-like).
    arrival_calendar : pd.DataFrame
        Arrival calendar table with at least a 'slots' column containing
        lists of slot_ids for each calendar entry.
    seed : int | None, optional
        Optional random seed passed to the peeling logic for reproducible
        peel orders across runs.

    Returns
    -------
    pd.DataFrame
        Copy of `arrival_calendar` with an added 'peel_order' column
        containing a list[int] per row that encodes the peel order for
        the corresponding set of slots.
    """
    
    # Build neighbor map once
    nbr_map = build_neighbor_map(base_calendar)

    # Compute peel_order per arrival calendar row
    arrival_calendar = arrival_calendar.copy()
    arrival_calendar["peel_order"] = arrival_calendar["slots"].apply(
        lambda s: compute_peel_order_for_slots(s, nbr_map, seed=seed)
    )

    return arrival_calendar

