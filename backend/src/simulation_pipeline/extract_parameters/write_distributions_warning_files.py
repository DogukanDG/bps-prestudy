from typing import Optional, Dict, Any
from pathlib import Path


def write_tasks_resources_warning_file(
    warning_dict: dict,
    warning_path: Path
) -> None:
    """
    Write a human-readable warning report for task–resource distributions.

    The function takes the summary dictionary returned by
    `extract_tasks_resources()` and writes a text file describing:

      - The total number of task–resource distributions encountered.
      - How many of them used a fixed ('fix') distribution, and what
        percentage they represent.
      - How many used unsupported distribution types (e.g. 'triang'),
        including their counts and percentages.
      - Explanatory notes on why these distributions were ignored in the
        sensitivity analysis.

    Parameters
    ----------
    warning_dict : dict
        Output of `extract_tasks_resources()`, expected format:
        {
            "total": int,
            "distributions": [
                {"name": str, "count": int, "pct": float},
                ...
            ]
        }
        The "distributions" list may include an entry for "fix" and
        entries for other unsupported distribution names.
    warning_path : Path
        Full path to the warning file to write, e.g.
        `simulation_results_folder / "warnings_tasks_resources.txt"`.

    Returns
    -------
    None
        The function writes the file to disk and prints the path; it does
        not return a value.
    """

    total = warning_dict.get("total", 0)
    dist_list = warning_dict.get("distributions", []) or []

    fixed_entry = next((d for d in dist_list if d["name"] == "fix"), None)
    other_unsupported = [d for d in dist_list if d["name"] != "fix"]

    warning_lines = [
        "TASK–RESOURCE PARAMETER EXTRACTION WARNING",
        "-----------------------------------------",
        f"Total task–resource distributions: {total}",
        "",
    ]

    # -------------------------
    # 1. FIX distributions info
    # -------------------------
    if fixed_entry:
        n_fixed = fixed_entry["count"]
        pct_fixed = fixed_entry["pct"]

        warning_lines.extend([
            f"Fixed ('fix') distributions ignored: {n_fixed}",
            f"Percentage fixed:                   {pct_fixed:.2f}%",
            "",
            "Reason:",
            "  Fixed ('fix') task–resource distributions contain no stochastic",
            "  variability and are therefore left as-is and excluded from sensitivity analysis.",
            "",
        ])

    # --------------------------------------------
    # 2. Unsupported distributions (not in allowed)
    # --------------------------------------------
    if other_unsupported:
        warning_lines.append(
            "Unsupported distributions detected and left as-is (excluded from SA):"
        )
        for d in other_unsupported:
            warning_lines.append(
                f"  - {d['name']}: {d['count']}/{total} ({d['pct']:.2f}%)"
            )

        warning_lines.extend([
            "",
            "Reason:",
            "  These distribution types were not present in the example datasets",
            "  used to develop the SA pipeline, and they could not be tested.",
            "  Therefore they cannot be safely sampled or perturbed.",
            "  Please change the code to perturb them in the sensitivity analysis.",
            "",
        ])

    # ----------------------
    # 3. No warnings at all
    # ----------------------
    if not fixed_entry and not other_unsupported:
        warning_lines.append(
            "No unsupported or fixed distributions found. All distributions valid."
        )

    # Write file
    warning_path = Path(warning_path)
    with open(warning_path, "w") as f:
        f.write("\n".join(warning_lines) + "\n")

    print(f"[extract_tasks_resources] Warning written to {warning_path}\n")


def write_arrival_distribution_warning_file(
    warning: Optional[Dict[str, Any]],
    warning_path: Path
) -> None:
    """
    Write a human-readable warning report for the arrival-time distribution.

    This function expects the warning dictionary returned by
    `extract_arrival_distribution()` when the arrival distribution is either
    fixed or unsupported for sensitivity analysis. It then writes a small
    text report summarizing the distribution name, status, and message, and
    optionally the fixed value.

    Note
    ----
    Although the type is Optional, this function assumes `warning` is a dict
    with at least the keys "distribution_name", "status", and "message".
    Passing `None` will raise an error because `warning.get(...)` is called
    unconditionally.

    Parameters
    ----------
    warning : dict or None
        Warning dictionary from `extract_arrival_distribution()`, e.g.:
        {
            "distribution_name": "fix",
            "status": "fixed_value",
            "action": "discarded",
            "fixed_value": 0.0,
            "message": "Arrival distribution is 'fix' with value 0.0 and was excluded..."
        }
        or, for unsupported types:
        {
            "distribution_name": "triang",
            "status": "unsupported",
            "action": "discarded",
            "message": "Arrival distribution 'triang' is not supported..."
        }
        Must not be None for this function to work correctly.
    warning_path : Path
        File path where the warning text file will be written.

    Returns
    -------
    None
        The function writes the file to disk and prints the path; it does
        not return a value.
    """

    dist_name = warning.get("distribution_name")
    status = warning.get("status")
    message = warning.get("message", "")

    # Build text
    header = (
        "ARRIVAL DISTRIBUTION WARNING\n"
        "----------------------------\n"
    )

    body_lines = [
        f"Distribution name: {dist_name}",
        f"Status:            {status}",
        "",
        f"{message}",
        "",
    ]

    # If fixed distribution → include fixed value
    if status == "fixed_value" and "fixed_value" in warning:
        body_lines.append(
            f"Fixed value:       {warning['fixed_value']}"
        )

    # Final text
    text = header + "\n".join(body_lines) + "\n"

    # Write to file
    with open(warning_path, "w") as f:
        f.write(text)

    print(f"[arrival_distribution] Warning written to {warning_path}")
