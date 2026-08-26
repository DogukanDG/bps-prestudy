from src.simulation_pipeline.convert_samples.convert_gateways import convert_gateways
from src.simulation_pipeline.convert_samples.convert_tasks_resources import convert_tasks_resources
from src.simulation_pipeline.convert_samples.convert_arrival_calendar import convert_arrival_calendar
from src.simulation_pipeline.convert_samples.convert_resource_calendars import convert_resource_calendars
from src.simulation_pipeline.convert_samples.convert_arrival_distribution import convert_arrival_distribution
from src.simulation_pipeline.convert_samples.convert_resource_numbers import convert_resource_numbers
from typing import Dict
import pandas as pd
import numpy as np


def convert_samples(
    *,
    samples: np.ndarray,
    merged_parameters: pd.DataFrame,
    parameter_tables: dict,
    is_gateway: bool,
    is_arrival_distribution: bool,
    is_arrival_calendar: bool,
    is_tasks_resources: bool,
    is_resource_calendars: bool,
    is_resource_numbers: bool,
) -> Dict[str, pd.DataFrame]:
    """
    Apply all enabled parameter-specific transformations to a uniform
    sample matrix and return the converted results as a dictionary.

    Depending on the boolean flags, this function dispatches to the
    corresponding converters:
      - `convert_gateways`           (if is_gateway is True)
      - `convert_tasks_resources`    (if is_tasks_resources is True)
      - `convert_arrival_calendar`   (if is_arrival_calendar is True)
      - `convert_resource_calendars` (if is_resource_calendars is True)
      - `convert_arrival_distribution` (if is_arrival_distribution is True)
      - `convert_resource_numbers`   (if is_resource_numbers is True)

    Each converter uses `samples`, `merged_parameters`, and the relevant
    sub-table from `parameter_tables` (where required) to transform
    uniform samples into meaningful numeric quantities (e.g. probabilities,
    working hours, counts), typically returned as long-format DataFrames.

    Parameters
    ----------
    samples : np.ndarray
        Uniform sampling matrix (e.g. Sobol/Morris) of shape
        (n_samples, n_parameters).
    merged_parameters : pd.DataFrame
        Final merged parameter definition table (df_parameters) containing
        at least ['name', 'type'], used to locate parameter-specific columns
        in `samples`.
    parameter_tables : dict
        Dictionary of parameter-specific tables, e.g.
        {
            "gateways": df_gateways,
            "arrival_distribution": df_arrival_dist,
            "arrival_calendar": df_arrival_cal,
            "resource_calendars": df_resource_cal,
            "tasks_resources": df_tasks_resources,
            ...
        }
        Only the tables required by the enabled flags need to be present.
    is_gateway : bool
        If True, convert gateway parameters via `convert_gateways` and
        store the result under the key "gateways".
    is_arrival_distribution : bool
        If True, convert arrival-distribution parameters via
        `convert_arrival_distribution` and store under "arrival_distribution".
    is_arrival_calendar : bool
        If True, convert arrival-calendar parameters via
        `convert_arrival_calendar` and store under "arrival_calendar".
    is_tasks_resources : bool
        If True, convert task-resource distribution parameters via
        `convert_tasks_resources` and store under "tasks_resources".
    is_resource_calendars : bool
        If True, convert resource-calendar parameters via
        `convert_resource_calendars` and store under "resource_calendars".
    is_resource_numbers : bool
        If True, convert resource-number parameters via
        `convert_resource_numbers` and store under "resource_numbers".

    Returns
    -------
    Dict[str, pd.DataFrame]
        Dictionary mapping component names to their converted DataFrames,
        e.g.
        {
            "gateways": df_gateways_converted,
            "tasks_resources": df_tasks_resources_converted,
            "arrival_calendar": df_arrival_calendar_converted,
            "resource_calendars": df_resource_calendars_converted,
            "arrival_distribution": df_arrival_dist_converted,
            "resource_numbers": df_resource_numbers_converted,
        }
        Only entries corresponding to flags set to True are included.
    """

    converted_samples = {}

    # ------------------------------------------------------------
    # 1) Gateways
    # ------------------------------------------------------------
    if is_gateway:
        converted_samples["gateways"] = convert_gateways(
            samples=samples,
            merged_parameters=merged_parameters,
            gateways=parameter_tables["gateways"],
        )

    # ------------------------------------------------------------
    # 2) Task-resource distributions
    # ------------------------------------------------------------
    if is_tasks_resources:
        converted_samples["tasks_resources"] = convert_tasks_resources(
            samples=samples,
            merged_parameters=merged_parameters,
            tasks_resources=parameter_tables["tasks_resources"],
        )

    # ------------------------------------------------------------
    # 3) Arrival calendar
    # ------------------------------------------------------------
    if is_arrival_calendar:
        converted_samples["arrival_calendar"] = convert_arrival_calendar(
            samples=samples,
            merged_parameters=merged_parameters,
            arrival_calendar=parameter_tables["arrival_calendar"],
        )

    # ------------------------------------------------------------
    # 4) Resource calendars
    # ------------------------------------------------------------
    if is_resource_calendars:
        converted_samples["resource_calendars"] = convert_resource_calendars(
            samples=samples,
            merged_parameters=merged_parameters,
            resource_calendars=parameter_tables["resource_calendars"],
        )

    # ------------------------------------------------------------
    # 5) Arrival distribution
    # ------------------------------------------------------------
    if is_arrival_distribution:
        converted_samples["arrival_distribution"] = convert_arrival_distribution(
            samples=samples,
            merged_parameters=merged_parameters,
            arrival_distribution=parameter_tables["arrival_distribution"],
        )

    # ------------------------------------------------------------
    # 6) Resource numbers
    # ------------------------------------------------------------
    if is_resource_numbers:
        converted_samples["resource_numbers"] = convert_resource_numbers(
            samples=samples,
            merged_parameters=merged_parameters,
        )

    return converted_samples
