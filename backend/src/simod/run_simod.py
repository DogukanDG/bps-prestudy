from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename
from typing import Dict, Any
from ruamel.yaml import YAML
from pathlib import Path
import shutil

from simod.settings.simod_settings import SimodSettings
from simod.event_log.event_log import EventLog
from simod.simod import Simod


def run_simod(
    train_file: FileStorage,
    output_folder: Path,
) -> Dict[str, Any]:
    """
    Run a complete SIMOD discovery pipeline for uploaded event logs.

    The function:
      1. Creates an `output_folder` with `inputs/` and `outputs/` subfolders.
      2. Saves the uploaded training log into `output_folder/inputs/`.
      3. Creates a configuration file in `output_folder/inputs/.
      4. Builds and preprocesses the event log.
      5. Executes SIMOD, writing all results into `output_folder/outputs/`.

    Parameters
    ----------
    train_file : FileStorage
        Uploaded training event log file (typically a CSV) from the request.
    output_folder : Path
        Target directory where inputs and outputs will be created.

    Returns
    -------
    dict
        Summary of the SIMOD run, including:
        - "message" : str
            Status message about the completed discovery.
        - "output_folder" : str
            Absolute or relative path to the main output folder.
        - "inputs" : dict
            Paths to the stored training log, test log, and config file.
        - "simod_output_dir" : str
            Path to the directory containing SIMOD-generated outputs.
    """

    # --- 1) Create output_folder ---
    output_folder.mkdir(parents=True, exist_ok=False)

    # --- 2) Create inputs and outputs directories ---
    inputs_dir = output_folder / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=False)

    simod_output_dir = output_folder / "outputs"
    simod_output_dir.mkdir(parents=True, exist_ok=False)

    # --- 3) Save uploaded training log ---
    train_path = inputs_dir / secure_filename(train_file.filename or "train.csv")
    train_file.save(str(train_path))

    # --- 4) Copy existing config.yml into inputs/ ---
    base_dir = Path(__file__).parent
    source_config_path = base_dir / "config.yml"

    if not source_config_path.exists():
        raise FileNotFoundError(f"config.yml not found at {source_config_path}")

    config_path = inputs_dir / "config.yml"
    shutil.copy(source_config_path, config_path)

    # --- 5) Update train_log_path inside the copied config.yml ---
    yaml_ruamel = YAML()
    yaml_ruamel.preserve_quotes = True

    with open(config_path, "r", encoding="utf-8") as f:
        config_data = yaml_ruamel.load(f)

    config_data["common"]["train_log_path"] = secure_filename(train_file.filename or "train.csv")

    with open(config_path, "w", encoding="utf-8") as f:
        yaml_ruamel.dump(config_data, f)

    # --- 6) Load SIMOD settings from updated config ---
    settings = SimodSettings.from_path(config_path)

    # --- 7) Build and preprocess event log ---
    event_log = EventLog.from_path(
        log_ids=settings.common.log_ids,
        train_log_path=settings.common.train_log_path,
        preprocessing_settings=settings.preprocessing,
    )

    # --- 8) Run SIMOD ---
    simod = Simod(
        settings=settings,
        event_log=event_log,
        output_dir=simod_output_dir,
    )
    simod.run()

    # --- 9) Return summary ---
    return (
        "SIMOD model discovery successfully completed. "
        "The discovered BPMN model and the corresponding parameters JSON file "
        f"have been saved to {simod_output_dir}."
    )
