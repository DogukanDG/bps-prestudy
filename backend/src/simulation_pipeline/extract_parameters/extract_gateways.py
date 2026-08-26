from typing import Any, Dict, List
import pandas as pd


def extract_gateways(data: Dict[str, Any]) -> pd.DataFrame:
    """
    Extract gateway branching probabilities from the parsed config dict.

    Parameters
    ----------
    data : dict
        Parsed JSON configuration. Expected structure:
        data["gateway_branching_probabilities"] = [
            {
                "gateway_id": str,
                "probabilities": [
                    {"path_id": str, "value": float},
                    ...
                ]
            },
            ...
        ]

    Returns
    -------
    pd.DataFrame
        Columns: ["name", "vis_name", "type", "gateway_id", "path_id", "value"]
        One row per gateway path probability. May return empty DataFrame.
    """
    columns = ["name", "vis_name", "type", "gateway_id", "path_id", "value"]

    raw_list = data.get("gateway_branching_probabilities", [])
    rows: List[Dict[str, Any]] = []
    gw_counter = 1

    for gw in raw_list:
        gateway_id = gw.get("gateway_id")
        probabilities = gw.get("probabilities", [])

        for prob in probabilities:
            rows.append({
                "name": f"Gateway_{gw_counter}",
                "vis_name": f"Gateway {gateway_id}",
                "type": "gateway",
                "gateway_id": gateway_id,
                "path_id": prob.get("path_id"),
                "value": prob.get("value"),
            })
            gw_counter += 1

    df = pd.DataFrame(rows, columns=columns)

    if not df.empty:
        df["value"] = pd.to_numeric(df["value"], errors="coerce")

    return df
