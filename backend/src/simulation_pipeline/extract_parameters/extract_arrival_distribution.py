from typing import Dict, Any, Tuple, Optional
import pandas as pd


def extract_arrival_distribution(
    data: Dict[str, Any]
) -> Tuple[pd.DataFrame, Optional[dict]]:
    """
    Extract the arrival-time distribution from the config and prepare it for SA.

    The function reads `data["arrival_time_distribution"]`, parses its
    `distribution_name` and up to four parameters, and returns a one-row
    DataFrame for supported distributions or an empty DataFrame plus a
    warning dict otherwise.

    Behaviour
    ---------
    - Allowed distributions: {"gamma", "norm", "expon", "lognorm", "uniform"}
        → kept as a single-row DataFrame, warning is None.

    - 'fix' distribution:
        → excluded from sensitivity analysis (cannot be perturbed),
          returns an empty DataFrame and a warning dict, and prints a message.

    - Any other distribution name:
        → treated as unsupported, returns an empty DataFrame and a warning
          dict, and prints a message.

    Parameters
    ----------
    data : Dict[str, Any]
        JSON-like configuration containing an 'arrival_time_distribution'
        entry with fields:
          - 'distribution_name'
          - 'distribution_params' : list of {"value": ...} dicts.

    Returns
    -------
    Tuple[pd.DataFrame, Optional[dict]]
        df :
            - For allowed distributions: one-row DataFrame with columns
              ['name', 'vis_name', 'type', 'distribution_name',
               'parameter_1', 'parameter_2', 'parameter_3', 'parameter_4'].
            - For 'fix', unsupported, or missing distributions: empty DataFrame
              with the same columns.
        extract_arrival_distribution_warning :
            - None if the distribution is allowed and used for SA.
            - A dict describing why the distribution was discarded otherwise
              (e.g. fixed_value or unsupported), including a human-readable
              'message' field.
    """

    allowed = {"gamma", "norm", "expon", "lognorm", "uniform"}

    ad = data.get("arrival_time_distribution", {}) or {}

    dist_name = ad.get("distribution_name")
    params_raw = ad.get("distribution_params", []) or []

    # Extract up to 4 parameter values
    params = [p.get("value") for p in params_raw if isinstance(p, dict)]
    p1 = params[0] if len(params) > 0 else None
    p2 = params[1] if len(params) > 1 else None
    p3 = params[2] if len(params) > 2 else None
    p4 = params[3] if len(params) > 3 else None

    cols = [
        "name", "vis_name", "type", "distribution_name",
        "parameter_1", "parameter_2", "parameter_3", "parameter_4",
    ]

    # Full-row DataFrame (we may or may not keep it)
    df = pd.DataFrame(
        [{
            "name": "Arrival Distribution",
            "vis_name": "Arrival Distribution",
            "type": "arrival_dist",
            "distribution_name": dist_name,
            "parameter_1": p1,
            "parameter_2": p2,
            "parameter_3": p3,
            "parameter_4": p4,
        }],
        columns=cols,
    )

    # If no distribution defined at all -> no warning, just empty df
    if dist_name is None:
        empty_df = pd.DataFrame(columns=cols)
        return empty_df, None

    # --- Case 1: 'fix' distribution (constant -> cannot be perturbed) ---
    if dist_name == "fix":
        fixed_value = p1  # usually the first/only value

        print(
            f"[arrival_distribution] Found 'fix' arrival distribution with "
            f"value={fixed_value}. It will be excluded from sensitivity "
            f"analysis because fixed values cannot be perturbed."
        )

        extract_arrival_distribution_warning = {
            "distribution_name": dist_name,
            "status": "fixed_value",
            "action": "discarded",
            "fixed_value": fixed_value,
            "message": (
                f"Arrival distribution is 'fix' with value {fixed_value} and "
                f"was excluded because fixed values cannot be perturbed."
            ),
        }

        empty_df = pd.DataFrame(columns=cols)
        return empty_df, extract_arrival_distribution_warning

    # --- Case 2: Unsupported distribution (not in allowed) ---
    if dist_name not in allowed:
        print(
            f"[arrival_distribution] WARNING: Arrival distribution '{dist_name}' "
            f"is not supported for sensitivity analysis and will be ignored."
        )

        extract_arrival_distribution_warning = {
            "distribution_name": dist_name,
            "status": "unsupported",
            "action": "discarded",
            "message": (
                f"Arrival distribution '{dist_name}' is not supported for "
                f"sensitivity analysis and was ignored."
            ),
        }

        empty_df = pd.DataFrame(columns=cols)
        return empty_df, extract_arrival_distribution_warning

    # --- Case 3: Allowed distribution -> keep, no warning ---
    # Convert parameters to numeric for safety
    for col in ["parameter_1", "parameter_2", "parameter_3", "parameter_4"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df, None
