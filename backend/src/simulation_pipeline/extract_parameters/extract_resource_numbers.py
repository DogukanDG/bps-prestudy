from typing import Dict, Any
import pandas as pd


def extract_resource_numbers(
    data: Dict[str, Any]
) -> pd.DataFrame | None:
    """
    Extract resource-number parameters from the configuration.

    This function scans all entries under `data["resource_profiles"]`
    and collects each resource's ID into a compact parameter table.
    Each resource becomes a row with a generated name ("rn_1", "rn_2", ...),
    a fixed type "resource_number", and its corresponding `resource_id`.

    Expected structure
    ------------------
    data["resource_profiles"] = [
        {
            "resource_list": [
                {"id": str, ...},
                ...
            ]
        },
        ...
    ]

    Parameters
    ----------
    data : Dict[str, Any]
        Parsed JSON-like configuration containing a "resource_profiles" list.

    Returns
    -------
    pd.DataFrame or None
        DataFrame with columns ["name", "vis_name", "type", "resource_id"], one row per
        resource found. Returns an empty DataFrame if no resources exist.
    """

    rows = []
    rn_counter = 1

    for profile in data.get("resource_profiles", []) or []:
        for resource in profile.get("resource_list", []) or []:
            rows.append({
                "name": f"Resource Number {rn_counter}",
                "vis_name": f"Resource Numbers {resource.get('id')}",
                "type": "resource_number",
                "resource_id": resource.get("id"),
            })
            rn_counter += 1

    df = pd.DataFrame(
        rows,
        columns=["name", "vis_name", "type", "resource_id"]
    ).reset_index(drop=True)

    return df
