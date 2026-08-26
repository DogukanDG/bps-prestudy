from typing import Dict, Any
import pandas as pd

from src.simulation_pipeline.extract_parameters.extract_gateways import extract_gateways
from src.simulation_pipeline.extract_parameters.extract_tasks_resources import extract_tasks_resources
from src.simulation_pipeline.extract_parameters.build_base_calendar import build_calendar_with_neighbors
from src.simulation_pipeline.extract_parameters.extract_arrival_calendar import extract_arrival_calendar
from src.simulation_pipeline.extract_parameters.extract_resource_calendars import extract_resource_calendars
from src.simulation_pipeline.extract_parameters.extract_arrival_distribution import extract_arrival_distribution
from src.simulation_pipeline.extract_parameters.extract_resource_numbers import extract_resource_numbers


def extract_parameters(
    config: Dict[str, Any],
    *,
    bpmn_path: str,
    is_gateway: bool,
    is_arrival_distribution: bool,
    is_arrival_calendar: bool,
    is_tasks_resources: bool,
    is_resource_calendars: bool,
    is_resource_numbers: bool,
    seed: int | None = None,
) -> Dict[str, pd.DataFrame]:
    """
    Extract parameter tables from the simulation configuration based on the
    active sensitivity-analysis flags.

    This function selectively runs individual extract_* functions depending on
    which dimensions are enabled (e.g., gateways, task–resource mappings,
    arrival distribution, arrival calendar, resource calendars, resource numbers).

    Parameters
    ----------
    config : dict
        Parsed configuration JSON/dict containing the original process model.
    is_gateway : bool
        Whether to extract gateway branching probabilities.
    is_arrival_distribution : bool
        Whether to extract the arrival-time distribution.
    is_arrival_calendar : bool
        Whether to extract and construct the arrival calendar.
    is_tasks_resources : bool
        Whether to extract task–resource distribution parameters.
    is_resource_calendars : bool
        Whether to extract resource calendars.
    is_resource_numbers : bool
        Whether to extract resource-number parameters.
    seed : int or None
        Optional random seed for reproducible peel-order generation.

    Returns
    -------
    tuple
        (
            parameter_tables : dict[str, pd.DataFrame]
                A dictionary containing only the extracted parameter tables
                relevant to the enabled flags. Possible keys include:
                "gateways", "tasks_resources", "base_calendar",
                "arrival_calendar", "resource_calendars",
                "arrival_distribution", "resource_numbers".
            extract_tasks_resources_warning : dict or None
                Warning information from extract_tasks_resources() if applicable.
            extract_arrival_distribution_warning : dict or None
                Warning information from extract_arrival_distribution() if applicable.
        )
    """
    parameter_tables = {}
    extract_tasks_resources_warning = None
    extract_arrival_distribution_warning = None

    if is_gateway:
        parameter_tables["gateways"] = extract_gateways(config)

    
    if is_tasks_resources:
        parameter_tables["tasks_resources"], extract_tasks_resources_warning = extract_tasks_resources(config, bpmn_path)

    if is_arrival_calendar or is_resource_calendars:
        parameter_tables["base_calendar"] = build_calendar_with_neighbors()
        parameter_tables["arrival_calendar"] = extract_arrival_calendar(
            config,
            base_calendar=parameter_tables["base_calendar"],
            seed=seed,
        )

    if is_resource_calendars:
        parameter_tables["resource_calendars"] = extract_resource_calendars(
            config,
            arrival_calendar=parameter_tables["arrival_calendar"],
            base_calendar=parameter_tables["base_calendar"],
            seed=seed,
        )

    if is_arrival_distribution:
        parameter_tables["arrival_distribution"], extract_arrival_distribution_warning = extract_arrival_distribution(config)

    if is_resource_numbers:
        parameter_tables["resource_numbers"] = extract_resource_numbers(config)

    return parameter_tables, extract_tasks_resources_warning, extract_arrival_distribution_warning