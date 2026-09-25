"""Local browser plate/group import and student CSV archive exports."""

from __future__ import annotations

import base64
import csv
import io
import math
import zipfile
from collections import defaultdict
from typing import Any, Mapping

import pandas as pd

from . import analysis as core
from .project import _find_group_map_column, load_group_map_assignments

MAX_PLATE_FILES = 32
MAX_TOTAL_UPLOAD_BYTES = 20_000_000
MAX_GROUPS = 500
DEFAULT_CONCENTRATIONS = [round(index * 0.4, 1) for index in range(16)]


def import_plates(request: Mapping[str, Any]) -> dict[str, Any]:
    files = request.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_PLATE_FILES:
        raise core.ContractError("invalid_plate", f"Select 1–{MAX_PLATE_FILES} plate CSV files.")
    if not all(isinstance(item, Mapping) for item in files):
        raise core.ContractError("invalid_plate", "Each plate upload must contain a filename and text.")
    if sum(len(core._require_text(item).encode("utf-8")) for item in files) > MAX_TOTAL_UPLOAD_BYTES:
        raise core.ContractError("upload_too_large", "The combined plate upload exceeds 20 MB.")
    sources, rows, ids = [], [], set()
    for item in files:
        imported = core._import_plate(item)["plate"]
        overlap = ids.intersection(imported["plate_ids"])
        if overlap:
            raise core.ContractError(
                "duplicate_plate",
                f"Plate IDs occur in more than one file: {', '.join(sorted(overlap))}. "
                "Use distinct plate filenames or IDs.",
            )
        ids.update(imported["plate_ids"])
        rows.extend(imported["rows"])
        sources.append(imported["source"])
        if len(rows) > core.MAX_PLATE_ROWS:
            raise core.ContractError("too_many_plate_rows", f"Combined plates exceed {core.MAX_PLATE_ROWS} records.")
    if len(files) == 1:
        return {"plate": imported}
    source = core._source(f"{len(files)} plate files", core._canonical_json(sources), "plate_csv")
    plate = {
        "schema_version": core.PLATE_SCHEMA_VERSION,
        "source": source,
        "plate_ids": list(dict.fromkeys(row["plate_id"] for row in rows)),
        "rows": rows,
        "measurements": core._summarise_measurements(rows),
    }
    plate["content_fingerprint"] = core._fingerprint(
        {key: plate[key] for key in ("source", "plate_ids", "rows")}
    )
    return {"plate": plate}


def prepare_groups(request: Mapping[str, Any]) -> dict[str, Any]:
    """Prepare valid map rows together, retaining actionable failures per group."""
    text = core._require_text(request).lstrip("\ufeff")
    # Validate the CSV shape before pandas can silently reinterpret an extra
    # comma as a row index, or rename duplicate column headers.
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise core.ContractError("invalid_group_map", "The group list is empty.") from exc
    if len({core._header_key(value) for value in header}) != len(header):
        raise core.ContractError("invalid_group_map", "Group list column names must be unique.")
    records = [row for row in reader if any(value.strip() for value in row)]
    if not records or len(records) > MAX_GROUPS:
        raise core.ContractError("invalid_group_map", f"Provide 1–{MAX_GROUPS} groups in the group list.")
    for index, record in enumerate(records, 2):
        if len(record) != len(header):
            raise core.ContractError(
                "invalid_group_map",
                f"Group list row {index} has {len(record)} fields; expected {len(header)}. "
                "Quote comma-separated wells and concentrations.",
            )
    group_column = _find_group_map_column(header, "group")
    if group_column is not None:
        names = [row[header.index(group_column)].strip() for row in records]
        keys = [name.casefold() for name in names]
        if len(set(keys)) != len(keys):
            raise core.ContractError("invalid_group_map", "Group names must be unique, ignoring capitalization.")

    plate = core._validate_plate(request.get("plate"))
    defaults = request.get("concentrations", DEFAULT_CONCENTRATIONS)
    if not isinstance(defaults, list):
        raise core.ContractError("invalid_group_map", "Default concentrations must be a list.")
    try:
        defaults = [float(value) for value in defaults]
    except (ValueError, TypeError) as exc:
        raise core.ContractError("invalid_group_map", "Concentrations must be numeric.") from exc
    if any(not math.isfinite(value) or value < 0 for value in defaults):
        raise core.ContractError("invalid_group_map", "Concentrations must be finite, nonnegative numbers.")
    policy = core._normalise_repeat_policy(request.get("repeat_policy"))
    repeat_column = _find_group_map_column(header, "repeat_policy")
    acquisition_column = _find_group_map_column(header, "acquisition_id")
    if repeat_column is None:
        repeat_column = "repeat_policy"
        header.append(repeat_column)
        for row in records:
            row.append("")
    if acquisition_column is None:
        acquisition_column = "acquisition_id"
        header.append(acquisition_column)
        for row in records:
            row.append("")
    repeat_index, acquisition_index = header.index(repeat_column), header.index(acquisition_column)
    labels = {}
    for row in records:
        if not row[repeat_index].strip():
            row[repeat_index] = policy["mode"]
            if group_column is not None and policy["mode"] == "mean":
                labels[row[header.index(group_column)].strip()] = policy["label"]
        if row[repeat_index].strip() == "select" and not row[acquisition_index].strip() and policy["mode"] == "select":
            row[acquisition_index] = policy["selected_acquisition_id"]
    normalized_csv = io.StringIO()
    writer = csv.writer(normalized_csv, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(records)
    failures: dict[str, str] = {}
    assignments = load_group_map_assignments(
        pd.DataFrame(plate["rows"]),
        io.StringIO(normalized_csv.getvalue()),
        default_concentrations=defaults,
        default_measurement=str(request.get("measurement") or ""),
        default_wavelength_nm=request.get("wavelength_nm"),
        failures=failures,
    )
    # Validate the full upload once; group preparation operates only on the
    # selected wells/wavelength, instead of rehashing every plate for each group.
    grouped = defaultdict(list)
    for row in plate["rows"]:
        grouped[(row["plate_id"], row["measurement"])].append(row)
    existing = {str(name).casefold() for name in request.get("existing_names", [])}
    projects, warnings = [], []
    observation_count = int(request.get("existing_observation_count", 0))
    for name, assignment in assignments.items():
        try:
            if name.casefold() in existing:
                raise ValueError("This group is already prepared. Use a unique name or reset before importing again.")
            if len(name) > 100:
                raise ValueError("Group names must be no more than 100 characters.")
            if any(value < 0 for value in assignment.concentrations):
                raise ValueError("Concentrations must be nonnegative.")
            wells = set(assignment.wells)
            rows = [
                row for row in grouped[(assignment.plate_id, assignment.measurement)]
                if row["well"] in wells
                and core._match_optional_number(row["wavelength_nm"], assignment.wavelength_nm)
            ]
            subset = {
                "schema_version": core.PLATE_SCHEMA_VERSION,
                "source": plate["source"],
                "plate_ids": [assignment.plate_id],
                "rows": rows,
            }
            subset["content_fingerprint"] = core._fingerprint(
                {key: subset[key] for key in ("source", "plate_ids", "rows")}
            )
            data = core._prepare_group({
                "plate": subset,
                "selection": {
                    "group_name": name,
                    "plate_id": assignment.plate_id,
                    "measurement": assignment.measurement,
                    "wavelength_nm": assignment.wavelength_nm,
                    "temperature_k": request.get("temperature_k", 298.15),
                    "repeat_policy": {
                        "mode": assignment.repeat_policy,
                        "selected_acquisition_id": assignment.acquisition_id,
                        "technical_replicates": assignment.repeat_policy == "mean",
                        "label": labels.get(name, "Technical replicates declared by group list") if assignment.repeat_policy == "mean" else None,
                    },
                },
                "wells": assignment.wells,
                "concentrations": assignment.concentrations,
            })
            project = data["project"]
            if observation_count + len(project["observations"]) > core.MAX_OBSERVATIONS:
                raise ValueError(f"Prepared groups exceed {core.MAX_OBSERVATIONS} observations.")
            projects.append(project)
            observation_count += len(project["observations"])
            warnings.extend(f"{name}: {warning}" for warning in data["warnings"])
        except (ValueError, core.ContractError) as exc:
            failures[name] = str(exc)
    return {"projects": projects, "failures": failures, "warnings": warnings, "group_count": len(records)}


def export_group_csvs(request: Mapping[str, Any]) -> dict[str, Any]:
    """One archive avoids browsers blocking successive automatic downloads."""
    practical, warnings = core._normalise_practical(
        {"schema_version": core.PRACTICAL_SCHEMA_VERSION, "projects": request.get("projects")},
        drop_results=True,
    )
    stream = io.BytesIO()
    filenames, used = [], set()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for project in practical["projects"]:
            data = core._export_legacy_csv({"project": project, "include_excluded": True, "normalization": "minmax"})
            stem = data["filename"][:-4]
            filename, suffix = data["filename"], 2
            while filename.casefold() in used:
                filename = f"{stem}_{suffix}.csv"
                suffix += 1
            used.add(filename.casefold())
            filenames.append(filename)
            archive.writestr(filename, data["text"])
            warnings.extend(data["warnings"])
    return {
        "filename": "group-csvs.zip",
        "mime": "application/zip",
        "base64": base64.b64encode(stream.getvalue()).decode("ascii"),
        "filenames": filenames,
        "warnings": warnings,
    }
