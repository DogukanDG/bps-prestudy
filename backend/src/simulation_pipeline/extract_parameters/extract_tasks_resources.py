from typing import Any, Dict, List
import pandas as pd
import xml.etree.ElementTree as ET


def extract_tasks_resources(data: Dict[str, Any], bpmn_path: str) -> tuple[pd.DataFrame, dict]:
    """
    Extract task–resource distribution parameters from the configuration.

    This function reads `data["task_resource_distribution"]`, flattens all
    task–resource entries into a tabular format, and collects their
    distribution names and up to four numeric parameters. It then filters
    out distributions that are not supported for sensitivity analysis
    (i.e. anything other than gamma, norm, lognorm, expon, uniform) and
    returns both the filtered DataFrame and a summary of discarded types.

    Expected structure
    ------------------
    data["task_resource_distribution"] = [
        {
            "task_id": str,
            "resources": [
                {
                    "resource_id": str,
                    "distribution_name": str,
                    "distribution_params": [
                        {"value": float}, ...
                    ]
                },
                ...
            ]
        },
        ...
    ]

    Parameters
    ----------
    data : Dict[str, Any]
        Parsed JSON-like configuration containing a
        "task_resource_distribution" list.

    Returns
    -------
    pd.DataFrame
        A table with one row per (task_id, resource_id) distribution and
        columns:
            ["name", "vis_name", "type", "task_id", "resource_id",
             "distribution_name", "parameter_1", "parameter_2",
             "parameter_3", "parameter_4"].
        Only rows whose `distribution_name` is in
        {"gamma", "norm", "expon", "lognorm", "uniform"} are retained
        after filtering.
    dict
        Warning summary describing all distributions that were present but
        not used for sensitivity analysis, with keys:
            - "total": total number of rows before filtering (int)
            - "distributions": list of dicts with
                {
                    "name": <str>,   # distribution_name
                    "count": <int>,  # how many times it occurred
                    "pct": <float>,  # percentage of total
                }
        If no rows are found at all, returns:
            {"total": 0, "distributions": []}.
    """
    columns = [
        "name", "vis_name", "type", "task_id", "resource_id",
        "distribution_name", "parameter_1", "parameter_2",
        "parameter_3", "parameter_4"
    ]

    # Parse BPMN file to build task_id -> task_name mapping
    task_id_to_name = {}
    try:
        tree = ET.parse(bpmn_path)
        root = tree.getroot()
        # BPMN uses default namespace (xmlns without prefix), so we use full qualified name
        # This is an XML namespace identifier, not a website URL
        # Find all task elements using full namespace URI
        for task in root.findall('.//{http://www.omg.org/spec/BPMN/20100524/MODEL}task'):
            task_id = task.get('id')
            task_name = task.get('name', task_id)  # Use task_id as fallback if no name
            if task_id:
                task_id_to_name[task_id] = task_name
    except Exception as e:
        print(f"Warning: Could not parse BPMN file at {bpmn_path}: {e}")
        print("Will use task IDs instead of task names.")

    raw_list = data.get("task_resource_distribution", [])
    rows: List[Dict[str, Any]] = []
    tr_counter = 1

    for tr in raw_list:
        task_id = tr.get("task_id")
        task_name = task_id_to_name.get(task_id, task_id)  # Use task_id as fallback
        resources = tr.get("resources", [])

        for res in resources:
            params_raw = res.get("distribution_params", []) or []
            params = [p.get("value") for p in params_raw if isinstance(p, dict)]

            # Extract first four parameters
            p1 = params[0] if len(params) > 0 else None
            p2 = params[1] if len(params) > 1 else None
            p3 = params[2] if len(params) > 2 else None
            p4 = params[3] if len(params) > 3 else None

            rows.append({
                "name": f"Task Resource {tr_counter}",
                "vis_name": f"Task {task_name} Resource {res.get('resource_id')}",
                "type": "task_resource",
                "task_id": task_id,
                "resource_id": res.get("resource_id"),
                "distribution_name": res.get("distribution_name"),
                "parameter_1": p1,
                "parameter_2": p2,
                "parameter_3": p3,
                "parameter_4": p4,
            })
            tr_counter += 1

    df = pd.DataFrame(rows, columns=columns)

    if df.empty:
        return df, {"total": 0, "distributions": []}

    # --- Inform about not allowed distributions before dropping them ---
    total = len(df)

    # Allowed SA distributions
    allowed = {"gamma", "norm", "expon", "lognorm", "uniform"}

    # Count only distributions *not* in allowed like fix or triang
    unsupported_dist_counts = df.loc[
        ~df["distribution_name"].isin(allowed),
        "distribution_name"
    ].value_counts()

    unsupported_dist_list = []

    for dist_name, count in unsupported_dist_counts.items():
        pct = (count / total * 100.0) if total > 0 else 0.0
        unsupported_dist_list.append({
            "name": str(dist_name),
            "count": int(count),
            "pct": pct,
        })

    # --- Backend warnings ---
    print("\n[tasks_resources] Fixed/Unsupported distributions found BEFORE filtering:")
    for entry in unsupported_dist_list:
        print(f"  - {entry['name']}: {entry['count']}/{total} ({entry['pct']:.1f}%)\n")

    # Unified warning structure
    extract_tasks_resources_warning = {
        "total": total,
        "distributions": unsupported_dist_list,
    }

    # remove fixed distributions — constant => not part of SA
    # remove unsupported distributions such as triang. 
    df = df[df["distribution_name"].isin(allowed)].reset_index(drop=True)

    # convert parameters to numbers
    for col in ["parameter_1", "parameter_2", "parameter_3", "parameter_4"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df, extract_tasks_resources_warning
