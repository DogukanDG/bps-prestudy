from typing import Dict, Any
import pandas as pd
import numpy as np


def merge_parameter_tables(
    parameter_tables: Dict[str, Any],
    *,
    is_groups:  bool,
    is_gateway: bool,
    is_arrival_distribution: bool,
    is_arrival_calendar: bool,
    is_tasks_resources: bool,
    is_resource_calendars: bool,
    is_resource_numbers: bool,
) -> pd.DataFrame:
    """
    Merge all enabled parameter tables into a single parameter index table.

    For each active dimension (gateways, task–resources, calendars, etc.),
    this function:
      - selects the ["name", "type", ...] columns from the corresponding
        parameter table in `parameter_tables`,
      - assigns a "groups" label depending on `is_groups`,
      - concatenates everything into one DataFrame `merged_parameters`.

    Grouping behaviour
    -------------------
    If `is_groups` is True:
        - All rows from the same dimension receive a shared group label:
          "gateways", "tasks_resources", "arrival_calendar",
          "resource_calendars", "arrival_distribution", "resource_numbers".

    If `is_groups` is False:
        - Gateway parameters are grouped by their `gateway_id`
          (each gateway becomes its own group).
        - All other dimensions receive NaN as "groups" (no grouping).

    Parameters
    ----------
    parameter_tables : dict
        Dictionary of extracted parameter tables. Depending on which
        flags are enabled, may contain:
          - "gateways"            : gateway parameter table
          - "tasks_resources"     : task–resource parameter table
          - "arrival_calendar"    : arrival calendar table
          - "resource_calendars"  : resource calendar table
          - "arrival_distribution": arrival distribution table
          - "resource_numbers"    : resource-number table
    is_groups : bool
        Selects between "between-groups" (True) and "within-group" (False)
        grouping logic, as described above.
    is_gateway : bool
        If True, include gateway parameters from parameter_tables["gateways"].
    is_arrival_distribution : bool
        If True, include parameters from parameter_tables["arrival_distribution"].
    is_arrival_calendar : bool
        If True, include parameters from parameter_tables["arrival_calendar"].
    is_tasks_resources : bool
        If True, include parameters from parameter_tables["tasks_resources"].
    is_resource_calendars : bool
        If True, include parameters from parameter_tables["resource_calendars"].
    is_resource_numbers : bool
        If True, include parameters from parameter_tables["resource_numbers"].

    Returns
    -------
    pd.DataFrame
        Combined parameter table with a fixed row order:
        gateways → tasks_resources → arrival_calendar →
        resource_calendars → arrival_distribution → resource_numbers.

        Columns:
          - "name"   : parameter identifier (e.g. gw_1, tr_3, ac_1, ...)
          - "type"   : parameter type (e.g. "gateway", "task_resource", ...)
          - "groups" : grouping label used by the sensitivity analysis.
    """

    # ---- Gateways ----
    if is_gateway and "gateways" in parameter_tables:
        df_g = parameter_tables["gateways"].loc[:, ["name", "vis_name", "type"]].copy()
        if is_groups:
            df_g["groups"] = "Gateways"
        else:
            df_g["groups"] = df_g["vis_name"]
        df_g = df_g.loc[:, ["name", "type", "groups"]]
    else:
        df_g = pd.DataFrame(columns=["name", "type", "groups"])

    # ---- Task resources ----
    if is_tasks_resources and "tasks_resources" in parameter_tables:
        df_tr = parameter_tables["tasks_resources"].loc[:, ["name", "vis_name", "type"]].copy()
        if is_groups:
            df_tr["groups"] = "Tasks and Resources Distributions"
        else: 
            df_tr["groups"] = df_tr["vis_name"]
        df_tr = df_tr.loc[:, ["name", "type", "groups"]]
    else:
        df_tr = pd.DataFrame(columns=["name", "type", "groups"])

    # ---- Arrival calendar ----
    if is_arrival_calendar and "arrival_calendar" in parameter_tables:
        df_ac = parameter_tables["arrival_calendar"].loc[:, ["name", "vis_name", "type"]].copy()
        if is_groups:
            df_ac["groups"] = "Arrival Calendar"
        else: 
            df_ac["groups"] = df_ac["vis_name"]
        df_ac = df_ac.loc[:, ["name", "type", "groups"]]
    else:
        df_ac = pd.DataFrame(columns=["name", "type", "groups"])

    # ---- Resource calendars ----
    if is_resource_calendars and "resource_calendars" in parameter_tables:
        df_rc = parameter_tables["resource_calendars"].loc[:, ["name", "vis_name", "type"]].copy()
        if is_groups:
            df_rc["groups"] = "Resource Calendars"
        else: 
            df_rc["groups"] = df_rc["vis_name"]
        df_rc = df_rc.loc[:, ["name", "type", "groups"]]
    else:
        df_rc = pd.DataFrame(columns=["name", "type", "groups"])

    # ---- Arrival distribution ----
    if is_arrival_distribution and "arrival_distribution" in parameter_tables:
        df_ad = parameter_tables["arrival_distribution"].loc[:, ["name", "vis_name", "type"]].copy()
        if is_groups:
            df_ad["groups"] = "Arrival Distribution"
        else: 
            df_ad["groups"] = df_ad["vis_name"]
        df_ad = df_ad.loc[:, ["name", "type", "groups"]]
    else:
        df_ad = pd.DataFrame(columns=["name", "type", "groups"])

    # ---- Resource numbers ----
    if is_resource_numbers and "resource_numbers" in parameter_tables:
        df_rn = parameter_tables["resource_numbers"].loc[:, ["name", "vis_name", "type"]].copy()
        if is_groups:
            df_rn["groups"] = "Resource Numbers"
        else: 
            df_rn["groups"] = df_rn["vis_name"]
        df_rn = df_rn.loc[:, ["name", "type", "groups"]]
    else:
        df_rn = pd.DataFrame(columns=["name", "type", "groups"])

    # ---- Combine in a fixed order ----
    merged_parameters = pd.concat(
        [df_g, df_tr, df_ac, df_rc, df_ad, df_rn],
        ignore_index=True,
    )

    return merged_parameters
