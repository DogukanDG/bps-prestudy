from flask import Request
from typing import Tuple
import tempfile
import os


def extract_uploaded_files(request: Request) -> Tuple[str, str]:
    """
    Extract BPMN and JSON files from a Flask POST request and store
    them as temporary files on disk.

    The function expects two form-file fields:
      - "bpmn": BPMN process model (.bpmn)
      - "json": Prosimos configuration file (.json)

    Each file is saved using NamedTemporaryFile (delete=False), and the
    resulting file paths are returned so downstream functions can load them.

    Parameters
    ----------
    request : Request
        Flask request object containing uploaded files.

    Returns
    -------
    Tuple[str, str]
        (temp_bpmn_path, temp_json_path), both absolute file paths to the
        saved temporary files.

    Raises
    ------
    ValueError
        If either of the expected files ("bpmn", "json") is missing.
    """
    if 'bpmn' not in request.files or 'json' not in request.files:
        raise ValueError("Both BPMN and JSON files must be provided")

    bpmn_file = request.files['bpmn']
    json_file = request.files['json']

    bpmn_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".bpmn")
    json_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")

    bpmn_file.save(bpmn_temp.name)
    json_file.save(json_temp.name)

    return bpmn_temp.name, json_temp.name


def cleanup_files(*paths: str):
    """
    Delete temporary files that were created during processing.

    Parameters
    ----------
    *paths : str
        One or more filesystem paths to remove. Non-existent paths
        are ignored.

    Notes
    -----
    This is typically used to clean up temporary BPMN/JSON files
    created by `extract_uploaded_files`.
    """
    for p in paths:
        if p and os.path.exists(p):
            os.unlink(p)
