from typing import Dict, Any
import pandas as pd


def write_fix_arrival_distribution(
    config: Dict[str, Any],
    fix_arrival_distribution_df: pd.DataFrame,
) -> None:
    """
    Apply fixed (median) arrival distribution value to a simulation configuration.

    This function updates the `arrival_time_distribution` section of the
    config in place by replacing it with a fixed distribution using the
    provided median value.

    Parameters
    ----------
    config : Dict[str, Any]
        Simulation configuration (JSON-like dict) to update in place.
    fix_arrival_distribution_df : pd.DataFrame
        DataFrame with columns ['name', 'median_value'] (single row),
        typically the output of `fix_arrival_distribution`.

    Returns
    -------
    None
        The config is modified in place.
    """

    if fix_arrival_distribution_df is None or fix_arrival_distribution_df.empty:
        return

    # Extract the median value from the single-row DataFrame
    v = float(fix_arrival_distribution_df["median_value"].iloc[0])

    config["arrival_time_distribution"] = {
        "distribution_name": "fix",
        "distribution_params": [{"value": v}],
    }
