from typing import Dict, Any
import pandas as pd


def write_fix_tasks_resources(
    fix_tasks_resources_df: pd.DataFrame,
    config: Dict[str, Any],
) -> None:
    """
    Apply fixed (median) task-resource values to a simulation configuration.

    This function updates the `task_resource_distribution` section of the
    config in place by replacing each matching (task_id, resource_id) entry
    with a fixed distribution using the provided median value.

    Parameters
    ----------
    fix_tasks_resources_df : pd.DataFrame
        DataFrame with columns ['task_id', 'resource_id', 'median_value'],
        typically the output of `fix_tasks_resources`.
    config : Dict[str, Any]
        Simulation configuration (JSON-like dict) to update in place.

    Returns
    -------
    None
        The config is modified in place.
    """

    if fix_tasks_resources_df is None or fix_tasks_resources_df.empty:
        return

    # Build lookup: (task_id, resource_id) -> median_value
    df = fix_tasks_resources_df.copy()
    df["task_id"] = df["task_id"].astype(str)
    df["resource_id"] = df["resource_id"].astype(str)

    tids = df["task_id"].to_numpy()
    rids = df["resource_id"].to_numpy()
    vals = df["median_value"].to_numpy()
    tr_lookup = {(tids[i], rids[i]): vals[i] for i in range(len(df))}

    # Update task_resource_distribution in config
    for tr in config.get("task_resource_distribution", []):
        tid = str(tr.get("task_id"))
        for res in tr.get("resources", ()):
            rid = str(res.get("resource_id"))
            v = tr_lookup.get((tid, rid))
            if v is not None:
                res["distribution_name"] = "fix"
                res["distribution_params"] = [{"value": float(v)}]
