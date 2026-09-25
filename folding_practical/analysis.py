"""Pure request/response analysis service used by the browser application.

The public entry point is :func:`dispatch`.  It accepts ordinary Python
mappings and returns only JSON-compatible values.  File uploads are supplied
as text; this module never evaluates input or deserializes Python objects.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import numpy as np

from ._version import __version__ as SOFTWARE_VERSION
from .models import (
    FitResult,
    choose_best_fit,
    fit_four_parameter_logistic,
    fit_two_state_denaturation,
)
from .plate_io import load_plate_text
from .wells import normalize_well

PROJECT_SCHEMA_VERSION = "1.0"
PLATE_SCHEMA_VERSION = "plate-1.0"
PRACTICAL_SCHEMA_VERSION = "practical-1.0"
MAX_TEXT_BYTES = 2_000_000
MAX_OBSERVATIONS = 5_000
MAX_PLATE_ROWS = 100_000

_CONCENTRATION_HEADERS = {
    "concentration",
    "concentrationm",
    "denaturantconcentration",
    "denaturantconcentrationm",
    "guhcl",
    "guhclconcentration",
    "guhclconcentrationm",
}
_RAW_SIGNAL_HEADERS = {
    "fluorescence",
    "rawfluorescence",
    "rawfluorescencevalue",
    "rawfluorescencevalues",
    "rawsignal",
    "signal",
}
_NORMALIZED_HEADERS = {
    "normalizedfluorescence",
    "normalizedfluorescencevalue",
    "normalizedfluorescencevalues",
    "normalizedsignal",
}


class ContractError(ValueError):
    """A safe, user-facing service error."""

    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.code = code
        self.details = details


def _json_safe(value: Any) -> Any:
    """Recursively convert scientific values to strict JSON values."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, np.ndarray):
        return [_json_safe(item) for item in value.tolist()]
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe_name(value: Any, default: str) -> str:
    text = str(value or "").replace("\\", "/").split("/")[-1].strip()
    return text or default


def _download_name(value: Any, default: str) -> str:
    stem = Path(_safe_name(value, default)).stem
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    return safe or Path(default).stem


def _require_text(request: Mapping[str, Any], field: str = "text") -> str:
    text = request.get(field)
    if not isinstance(text, str):
        raise ContractError("invalid_request", f"{field!r} must be a string")
    size = len(text.encode("utf-8"))
    if size > MAX_TEXT_BYTES:
        raise ContractError(
            "upload_too_large",
            f"Text uploads are limited to {MAX_TEXT_BYTES} UTF-8 bytes",
            {"bytes": size, "limit": MAX_TEXT_BYTES},
        )
    return text


def _strict_json_loads(text: str) -> Any:
    def reject_constant(token: str) -> None:
        raise ValueError(f"Non-finite JSON number {token!r} is not allowed")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key {key!r}")
            result[key] = value
        return result

    return json.loads(
        text,
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicate_keys,
    )


def _finite_or_none(value: Any, field: str) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError("invalid_project", f"{field} must be numeric or null") from exc
    if not math.isfinite(number):
        return None
    return number


def _positive_or_none(value: Any, field: str) -> Optional[float]:
    number = _finite_or_none(value, field)
    if number is not None and number <= 0:
        raise ContractError("invalid_project", f"{field} must be positive")
    return number


def _source(
    name: str,
    text: str,
    kind: str,
    source_bytes_sha256: Any = None,
    source_encoding: Any = None,
) -> dict[str, Any]:
    source: dict[str, Any] = {
        "name": _safe_name(name, "upload.csv"),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "kind": kind,
    }
    if source_bytes_sha256 not in (None, ""):
        digest = str(source_bytes_sha256).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ContractError(
                "invalid_request",
                "source_bytes_sha256 must be a SHA-256 hex digest",
            )
        source["bytes_sha256"] = digest
    if source_encoding not in (None, ""):
        encoding = str(source_encoding).strip()
        if not encoding or len(encoding) > 80 or any(ord(char) < 32 for char in encoding):
            raise ContractError("invalid_request", "source_encoding is invalid")
        source["encoding"] = encoding
    return source


def _normalise_repeat_policy(value: Any) -> dict[str, Any]:
    if value is None:
        raw: Mapping[str, Any] = {}
    elif isinstance(value, str):
        raw = {"mode": value}
    elif isinstance(value, Mapping):
        raw = value
    else:
        raise ContractError("invalid_repeat_policy", "repeat_policy must be a string or object")

    mode = str(raw.get("mode", "reject")).strip().lower()
    if mode == "first":
        raise ContractError(
            "invalid_repeat_policy",
            "repeat_policy 'first' is unsafe; select an acquisition explicitly",
        )
    if mode not in {"reject", "select", "mean", "all"}:
        raise ContractError(
            "invalid_repeat_policy",
            "repeat_policy mode must be reject, select, mean, or all",
        )
    selected = raw.get("selected_acquisition_id", raw.get("acquisition_id"))
    selected_text = str(selected).strip() if selected is not None else ""
    technical = raw.get("technical_replicates", False) is True
    label_value = raw.get("label")
    label = str(label_value).strip() if label_value is not None else ""
    if mode == "select" and not selected_text:
        raise ContractError(
            "invalid_repeat_policy",
            "repeat_policy 'select' requires selected_acquisition_id",
        )
    if mode in {"mean", "all"} and (not technical or not label):
        raise ContractError(
            "invalid_repeat_policy",
            f"repeat_policy {mode!r} requires technical_replicates=true and a non-empty label",
        )
    return {
        "mode": mode,
        "selected_acquisition_id": selected_text if mode == "select" else None,
        "technical_replicates": technical if mode in {"mean", "all"} else False,
        "label": (label or None) if mode in {"mean", "all"} else None,
    }


def _normalise_blank_correction(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    enabled = raw.get("enabled", False) is True
    method = str(raw.get("method") or ("mean" if enabled else "")).strip().lower()
    if enabled and method != "mean":
        raise ContractError("invalid_blank_correction", "Only explicit mean blank correction is supported")
    wells: list[str] = []
    for item in raw.get("blank_wells", []):
        try:
            wells.append(normalize_well(str(item)))
        except ValueError as exc:
            raise ContractError("invalid_blank_correction", str(exc)) from exc
    blank_value = _finite_or_none(raw.get("blank_value"), "blank_correction.blank_value")
    count_raw = raw.get("blank_observation_count", 0)
    try:
        count = max(0, int(count_raw))
    except (TypeError, ValueError) as exc:
        raise ContractError("invalid_blank_correction", "blank_observation_count must be an integer") from exc
    return {
        "enabled": enabled,
        "method": method or None,
        "blank_wells": wells,
        "blank_value": blank_value,
        "blank_observation_count": count,
    }


def _normalise_settings(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    temperature = _finite_or_none(raw.get("temperature_k", 298.15), "settings.temperature_k")
    if temperature is None or not 260.0 <= temperature <= 330.0:
        raise ContractError(
            "invalid_project",
            "settings.temperature_k must be in kelvin between 260 and 330",
        )
    fit_signal = str(raw.get("fit_signal", "raw_signal")).strip()
    if fit_signal not in {"raw_signal", "blank_corrected_signal"}:
        raise ContractError(
            "invalid_project",
            "settings.fit_signal must be raw_signal or blank_corrected_signal",
        )
    requested_raw = raw.get("requested_selection")
    if requested_raw is None:
        requested: Mapping[str, Any] = {}
    elif isinstance(requested_raw, Mapping):
        requested = requested_raw
    else:
        raise ContractError(
            "invalid_project",
            "settings.requested_selection must be an object",
        )
    requested_measurement = requested.get("measurement")
    requested_plate_id = requested.get("plate_id")
    requested_acquisition = requested.get("acquisition_id")
    repeat_policy = _normalise_repeat_policy(raw.get("repeat_policy"))
    return {
        "temperature_k": temperature,
        "group_name": str(raw.get("group_name", "")).strip(),
        "measurement": str(raw.get("measurement", "raw fluorescence values")).strip(),
        "wavelength_nm": _positive_or_none(raw.get("wavelength_nm"), "settings.wavelength_nm"),
        "excitation_nm": _positive_or_none(raw.get("excitation_nm"), "settings.excitation_nm"),
        "requested_selection": {
            "plate_id": (
                str(requested_plate_id).strip()
                if requested_plate_id not in (None, "")
                else None
            ),
            "measurement": (
                str(requested_measurement).strip()
                if requested_measurement not in (None, "")
                else None
            ),
            "wavelength_nm": _positive_or_none(
                requested.get("wavelength_nm"),
                "settings.requested_selection.wavelength_nm",
            ),
            "excitation_nm": _positive_or_none(
                requested.get("excitation_nm"),
                "settings.requested_selection.excitation_nm",
            ),
            "acquisition_id": (
                str(requested_acquisition).strip()
                if repeat_policy["mode"] == "select"
                and requested_acquisition not in (None, "")
                else None
            ),
        },
        "repeat_policy": repeat_policy,
        "blank_correction": _normalise_blank_correction(raw.get("blank_correction")),
        "fit_signal": fit_signal,
    }


def _normalise_source(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError("invalid_project", "project.source must be an object")
    digest = str(value.get("sha256", "")).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ContractError("invalid_project", "project.source.sha256 must be a SHA-256 hex digest")
    source: dict[str, Any] = {
        "name": _safe_name(value.get("name"), "source.csv"),
        "sha256": digest,
        "kind": str(value.get("kind", "unknown")).strip() or "unknown",
    }
    bytes_digest_value = value.get("bytes_sha256", value.get("source_bytes_sha256"))
    if bytes_digest_value not in (None, ""):
        bytes_digest = str(bytes_digest_value).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", bytes_digest):
            raise ContractError(
                "invalid_project",
                "project.source.bytes_sha256 must be a SHA-256 hex digest",
            )
        source["bytes_sha256"] = bytes_digest
    encoding_value = value.get("encoding", value.get("source_encoding"))
    if encoding_value not in (None, ""):
        encoding = str(encoding_value).strip()
        if not encoding or len(encoding) > 80 or any(ord(char) < 32 for char in encoding):
            raise ContractError("invalid_project", "project.source.encoding is invalid")
        source["encoding"] = encoding
    return source


def _normalise_acquisition(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    acquisition_id = raw.get("acquisition_id")
    read = raw.get("read")
    acquisition_ids = [str(item) for item in raw.get("acquisition_ids", []) if item is not None]
    source_rows: list[int] = []
    for item in raw.get("source_rows", []):
        try:
            source_rows.append(int(item))
        except (TypeError, ValueError):
            continue
    count = raw.get("replicate_count")
    try:
        replicate_count = int(count) if count is not None else None
    except (TypeError, ValueError):
        replicate_count = None
    return {
        "acquisition_id": str(acquisition_id) if acquisition_id is not None else None,
        "read": _json_safe(read),
        "acquisition_ids": acquisition_ids,
        "source_rows": source_rows,
        "replicate_count": replicate_count,
        "replicate_std": _finite_or_none(raw.get("replicate_std"), "acquisition.replicate_std"),
        "technical_replicate_label": (
            str(raw.get("technical_replicate_label"))
            if raw.get("technical_replicate_label") is not None
            else None
        ),
    }


def _normalise_observation(value: Any, index: int, fit_signal: str) -> tuple[dict[str, Any], Optional[str]]:
    if not isinstance(value, Mapping):
        raise ContractError("invalid_project", f"observations[{index}] must be an object")
    row_id = str(value.get("row_id", f"row-{index + 1:04d}")).strip()
    if not row_id:
        raise ContractError("invalid_project", f"observations[{index}].row_id cannot be empty")
    concentration = _finite_or_none(value.get("concentration_m"), f"observations[{index}].concentration_m")
    raw_signal = _finite_or_none(value.get("raw_signal"), f"observations[{index}].raw_signal")
    corrected = _finite_or_none(
        value.get("blank_corrected_signal"),
        f"observations[{index}].blank_corrected_signal",
    )
    source_row_value = value.get("source_row")
    try:
        source_row = int(source_row_value) if source_row_value is not None else None
    except (TypeError, ValueError) as exc:
        raise ContractError("invalid_project", f"observations[{index}].source_row must be an integer") from exc
    well_value = value.get("well")
    try:
        well = normalize_well(str(well_value)) if well_value not in (None, "") else None
    except ValueError as exc:
        raise ContractError("invalid_project", str(exc)) from exc

    excluded = value.get("excluded", False) is True
    reason_value = value.get("exclusion_reason")
    reason = str(reason_value).strip() if reason_value is not None else ""
    missing: list[str] = []
    if concentration is None:
        missing.append("missing concentration")
    if raw_signal is None:
        missing.append("missing raw signal")
    if fit_signal == "blank_corrected_signal" and corrected is None:
        missing.append("missing blank-corrected signal")
    warning = None
    if missing and not excluded:
        excluded = True
        reason = "; ".join(missing)
        warning = f"{row_id} was excluded automatically: {reason}"
    if excluded and not reason:
        reason = "excluded without a supplied rationale"
        warning = f"{row_id} has no specific exclusion rationale"

    observation = {
        "row_id": row_id,
        "concentration_m": concentration,
        "raw_signal": raw_signal,
        "blank_corrected_signal": corrected,
        "source_row": source_row,
        "well": well,
        "acquisition": _normalise_acquisition(value.get("acquisition")),
        "excluded": excluded,
        "exclusion_reason": reason or None,
    }
    return observation, warning


def _project_content(project: Mapping[str, Any]) -> dict[str, Any]:
    source = project["source"]
    return {
        "source": {
            "sha256": source["sha256"],
            "bytes_sha256": source.get("bytes_sha256"),
            "encoding": source.get("encoding"),
            "kind": source["kind"],
        },
        "settings": project["settings"],
        "observations": project["observations"],
    }


def _normalise_project(
    value: Any,
    *,
    drop_result: bool,
    preserve_matching_result: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(value, Mapping):
        raise ContractError("invalid_project", "project must be an object")
    if str(value.get("schema_version", "")) != PROJECT_SCHEMA_VERSION:
        raise ContractError(
            "unsupported_schema",
            f"Expected project schema {PROJECT_SCHEMA_VERSION!r}",
        )
    settings = _normalise_settings(value.get("settings"))
    raw_observations = value.get("observations")
    if not isinstance(raw_observations, list) or not raw_observations:
        raise ContractError("invalid_project", "project.observations must be a non-empty list")
    if len(raw_observations) > MAX_OBSERVATIONS:
        raise ContractError(
            "too_many_observations",
            f"Projects are limited to {MAX_OBSERVATIONS} observations",
        )

    observations: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_observations):
        observation, warning = _normalise_observation(raw, index, settings["fit_signal"])
        if observation["row_id"] in seen_ids:
            raise ContractError("invalid_project", f"Duplicate row_id {observation['row_id']!r}")
        seen_ids.add(observation["row_id"])
        observations.append(observation)
        if warning:
            warnings.append(warning)

    visual_midpoint = _finite_or_none(value.get("visual_midpoint_m"), "visual_midpoint_m")
    project: dict[str, Any] = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "software_version": str(value.get("software_version") or SOFTWARE_VERSION),
        "source": _normalise_source(value.get("source")),
        "settings": settings,
        "observations": observations,
        "exclusions": [
            {"row_id": row["row_id"], "rationale": row["exclusion_reason"]}
            for row in observations
            if row["excluded"]
        ],
        "notes": str(value.get("notes", "")),
        "visual_midpoint_m": visual_midpoint,
        "content_fingerprint": "",
        "result": None,
    }
    computed = _fingerprint(_project_content(project))
    claimed = str(value.get("content_fingerprint", ""))
    project["content_fingerprint"] = computed
    if claimed and claimed != computed:
        warnings.append("The stored content fingerprint did not match and was recomputed.")

    supplied_result = value.get("result")
    if supplied_result is not None:
        supplied_safe = _json_safe(supplied_result)
        result_fingerprint = (
            str(supplied_safe.get("project_fingerprint", ""))
            if isinstance(supplied_safe, Mapping)
            else ""
        )
        if drop_result:
            if result_fingerprint and result_fingerprint != computed:
                warnings.append("A stale stored fit result was discarded.")
            else:
                warnings.append("A stored fit result was discarded; re-fit after loading to validate it.")
        elif preserve_matching_result and result_fingerprint == computed:
            project["result"] = supplied_safe
        else:
            warnings.append("A stale or unverifiable fit result was discarded.")
    return project, warnings


def _new_project(
    *,
    source: Mapping[str, Any],
    settings: Mapping[str, Any],
    observations: list[Mapping[str, Any]],
    notes: str = "",
    visual_midpoint_m: Any = None,
) -> tuple[dict[str, Any], list[str]]:
    draft = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "software_version": SOFTWARE_VERSION,
        "source": dict(source),
        "settings": dict(settings),
        "observations": observations,
        "exclusions": [],
        "notes": notes,
        "visual_midpoint_m": visual_midpoint_m,
        "content_fingerprint": "",
        "result": None,
    }
    return _normalise_project(draft, drop_result=True)


def _header_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _legacy_number(value: str) -> tuple[Optional[float], Optional[str]]:
    text = value.strip()
    if not text or text.lower() in {"na", "n/a", "nan", "null", "none", "-"}:
        return None, "missing"
    try:
        number = float(text)
    except ValueError:
        return None, "invalid"
    if not math.isfinite(number):
        return None, "non-finite"
    return number, None


def _parse_legacy_series(text: str, filename: str, request: Mapping[str, Any]) -> dict[str, Any]:
    sample = text[:8192]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = ","
    numbered_rows = list(enumerate(csv.reader(io.StringIO(text), delimiter=delimiter), start=1))
    header_position = next(
        (position for position, (_, row) in enumerate(numbered_rows) if any(cell.strip() for cell in row)),
        None,
    )
    if header_position is None:
        raise ContractError("invalid_series", "The uploaded series CSV is empty")
    header_line, headers = numbered_rows[header_position]
    keys = [_header_key(item) for item in headers]
    if len(set(keys)) != len(keys):
        raise ContractError("invalid_series", "The CSV has duplicate column headings")

    def find_index(aliases: set[str], label: str) -> int:
        matches = [index for index, key in enumerate(keys) if key in aliases]
        if len(matches) != 1:
            raise ContractError("invalid_series", f"Could not identify one {label} column")
        return matches[0]

    concentration_index = find_index(_CONCENTRATION_HEADERS, "concentration")
    raw_index = find_index(_RAW_SIGNAL_HEADERS, "raw fluorescence")
    normalized_present = any(key in _NORMALIZED_HEADERS for key in keys)

    observations: list[dict[str, Any]] = []
    for line_number, row in numbered_rows[header_position + 1 :]:
        if not any(cell.strip() for cell in row):
            continue
        if len(observations) >= MAX_OBSERVATIONS:
            raise ContractError(
                "too_many_observations",
                f"Projects are limited to {MAX_OBSERVATIONS} observations",
            )
        cells = row + [""] * (len(headers) - len(row))
        concentration, concentration_problem = _legacy_number(cells[concentration_index])
        signal, signal_problem = _legacy_number(cells[raw_index])
        problems: list[str] = []
        if concentration_problem:
            problems.append(f"{concentration_problem} concentration")
        if signal_problem:
            problems.append(f"{signal_problem} raw signal")
        observations.append(
            {
                "row_id": f"row-{line_number:04d}",
                "concentration_m": concentration,
                "raw_signal": signal,
                "blank_corrected_signal": None,
                "source_row": line_number,
                "well": None,
                "acquisition": {"acquisition_id": None, "read": None},
                "excluded": bool(problems),
                "exclusion_reason": "; ".join(problems) if problems else None,
            }
        )
    if not observations:
        raise ContractError("invalid_series", "The uploaded series has no data rows")

    supplied_settings = dict(request.get("settings", {})) if isinstance(request.get("settings"), Mapping) else {}
    supplied_settings.setdefault("measurement", headers[raw_index].strip() or "raw fluorescence values")
    supplied_settings.setdefault("fit_signal", "raw_signal")
    supplied_settings.setdefault("repeat_policy", {"mode": "reject"})
    project, warnings = _new_project(
        source=_source(
            filename,
            text,
            "legacy_series",
            request.get("source_bytes_sha256"),
            request.get("source_encoding"),
        ),
        settings=supplied_settings,
        observations=observations,
        notes=str(request.get("notes", "")),
        visual_midpoint_m=request.get("visual_midpoint_m"),
    )
    if normalized_present:
        warnings.append(
            "The supplied normalized column was not used; fitting defaults to the raw fluorescence column."
        )
    return {"project": project, "warnings": warnings, "header_row": header_line}


def _plate_row(record: Mapping[str, Any], source_row: int, source_hash: str) -> dict[str, Any]:
    value = _finite_or_none(record.get("value"), "plate value")
    wavelength = _finite_or_none(record.get("wavelength_nm"), "plate wavelength_nm")
    excitation = _finite_or_none(record.get("excitation_nm"), "plate excitation_nm")
    column_value = record.get("column")
    try:
        column = int(column_value) if column_value is not None and not _is_missing(column_value) else None
    except (TypeError, ValueError):
        column = None
    well_value = record.get("well")
    well = normalize_well(str(well_value)) if not _is_missing(well_value) else None
    acquisition = record.get("acquisition_id")
    read = record.get("read")
    return {
        "plate_id": str(record.get("plate_id", "")),
        "source_file": _safe_name(record.get("source_file"), "plate.csv"),
        "source_sha256": str(record.get("source_sha256") or source_hash),
        "source_row": int(record.get("source_row", source_row)),
        "well": well,
        "row": str(record.get("row") or (well[0] if well else "")) or None,
        "column": column,
        "measurement": str(record.get("measurement", "")),
        "wavelength_nm": wavelength,
        "excitation_nm": excitation,
        "value": value,
        "acquisition_id": None if _is_missing(acquisition) else str(acquisition),
        "read": None if _is_missing(read) else _json_safe(read),
    }


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (float, np.floating)):
        return not math.isfinite(float(value))
    return False


def _summarise_measurements(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["plate_id"]), str(row["measurement"]))].append(row)
    output: list[dict[str, Any]] = []
    for (plate_id, measurement), selected in sorted(grouped.items()):
        output.append(
            {
                "plate_id": plate_id,
                "measurement": measurement,
                "wavelengths_nm": sorted(
                    {float(row["wavelength_nm"]) for row in selected if row["wavelength_nm"] is not None}
                ),
                "excitations_nm": sorted(
                    {float(row["excitation_nm"]) for row in selected if row["excitation_nm"] is not None}
                ),
                "acquisition_ids": sorted(
                    {str(row["acquisition_id"]) for row in selected if row["acquisition_id"] is not None}
                ),
                "row_count": len(selected),
            }
        )
    return output


def _import_plate(request: Mapping[str, Any]) -> dict[str, Any]:
    text = _require_text(request)
    filename = _safe_name(request.get("filename"), "plate.csv")
    plate_id_value = request.get("plate_id")
    plate_id = str(plate_id_value).strip() if plate_id_value is not None else None
    try:
        frame = load_plate_text(text, source_file=filename, plate_id=plate_id or None)
    except Exception as exc:
        raise ContractError("invalid_plate", str(exc)) from exc
    if len(frame) > MAX_PLATE_ROWS:
        raise ContractError(
            "too_many_plate_rows",
            f"Parsed plate data are limited to {MAX_PLATE_ROWS} tidy rows",
        )
    source = _source(
        filename,
        text,
        "plate_csv",
        request.get("source_bytes_sha256"),
        request.get("source_encoding"),
    )
    rows = [
        _plate_row(record, index + 1, source["sha256"])
        for index, record in enumerate(frame.to_dict(orient="records"))
    ]
    plate: dict[str, Any] = {
        "schema_version": PLATE_SCHEMA_VERSION,
        "source": source,
        "plate_ids": list(dict.fromkeys(str(row["plate_id"]) for row in rows)),
        "rows": rows,
        "measurements": _summarise_measurements(rows),
        "content_fingerprint": "",
    }
    plate["content_fingerprint"] = _fingerprint(
        {"source": source, "plate_ids": plate["plate_ids"], "rows": rows}
    )
    return {"plate": plate}


def _validate_plate(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != PLATE_SCHEMA_VERSION:
        raise ContractError("invalid_plate", f"Expected {PLATE_SCHEMA_VERSION!r} plate data")
    source = _normalise_source(value.get("source"))
    raw_rows = value.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ContractError("invalid_plate", "plate.rows must be a non-empty list")
    if len(raw_rows) > MAX_PLATE_ROWS:
        raise ContractError("too_many_plate_rows", f"plate.rows exceeds {MAX_PLATE_ROWS}")
    rows = [_plate_row(row, index + 1, source["sha256"]) for index, row in enumerate(raw_rows)]
    plate_ids = list(dict.fromkeys(str(row["plate_id"]) for row in rows))
    computed = _fingerprint({"source": source, "plate_ids": plate_ids, "rows": rows})
    if str(value.get("content_fingerprint", "")) != computed:
        raise ContractError("plate_fingerprint_mismatch", "The tidy plate content has changed since import")
    return {
        "schema_version": PLATE_SCHEMA_VERSION,
        "source": source,
        "plate_ids": plate_ids,
        "rows": rows,
        "measurements": _summarise_measurements(rows),
        "content_fingerprint": computed,
    }


def _well_assignments(request: Mapping[str, Any]) -> list[tuple[str, Optional[float]]]:
    raw_wells = request.get("wells")
    if not isinstance(raw_wells, list) or not raw_wells:
        raise ContractError("invalid_group", "wells must be a non-empty list")
    concentrations = request.get("concentrations")
    output: list[tuple[str, Optional[float]]] = []
    if all(isinstance(item, Mapping) for item in raw_wells):
        for index, item in enumerate(raw_wells):
            try:
                well = normalize_well(str(item.get("well", "")))
            except ValueError as exc:
                raise ContractError("invalid_group", str(exc)) from exc
            concentration = _finite_or_none(item.get("concentration_m"), f"wells[{index}].concentration_m")
            output.append((well, concentration))
    elif all(isinstance(item, str) for item in raw_wells):
        if not isinstance(concentrations, list) or len(concentrations) != len(raw_wells):
            raise ContractError("invalid_group", "String wells require a matching concentrations list")
        for index, (well_value, concentration_value) in enumerate(zip(raw_wells, concentrations)):
            try:
                well = normalize_well(well_value)
            except ValueError as exc:
                raise ContractError("invalid_group", str(exc)) from exc
            output.append(
                (well, _finite_or_none(concentration_value, f"concentrations[{index}]"))
            )
    else:
        raise ContractError("invalid_group", "wells must contain either strings or mapping objects")
    names = [well for well, _ in output]
    if len(set(names)) != len(names):
        raise ContractError("invalid_group", "Each sample well may be assigned only once")
    return output


def _match_optional_number(actual: Any, selected: Optional[float]) -> bool:
    if selected is None:
        return actual is None
    return actual is not None and bool(np.isclose(float(actual), selected))


def _choose_numeric_selection(
    rows: Sequence[Mapping[str, Any]],
    field: str,
    supplied: Any,
) -> Optional[float]:
    available = sorted({float(row[field]) for row in rows if row.get(field) is not None})
    if supplied is not None and supplied != "":
        selected = _positive_or_none(supplied, field)
        if not available or not any(np.isclose(selected, item) for item in available):
            raise ContractError(
                "invalid_group",
                f"Selected {field} {selected:g} is unavailable",
                {"available": available},
            )
        return selected
    if len(available) > 1:
        raise ContractError(
            "selection_required",
            f"Select one {field}",
            {"available": available},
        )
    return available[0] if available else None


def _acquisition_id(row: Mapping[str, Any]) -> str:
    value = row.get("acquisition_id")
    return str(value) if value is not None else f"source-row-{row.get('source_row')}"


def _reject_duplicate_rows_within_acquisition(
    rows: Sequence[Mapping[str, Any]],
) -> None:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("well") is None:
            continue
        key = (str(row["well"]), _acquisition_id(row))
        grouped[key].append(row)
    for (well, acquisition_id), duplicates in grouped.items():
        if len(duplicates) <= 1:
            continue
        raise ContractError(
            "repeated_measurement",
            f"Well {well} has {len(duplicates)} rows within acquisition "
            f"{acquisition_id!r}; one acquisition may contain at most one "
            "observation per well for the selected signal",
            {
                "well": well,
                "acquisition_id": acquisition_id,
                "row_count": len(duplicates),
                "source_rows": [row.get("source_row") for row in duplicates],
            },
        )


def _apply_repeat_policy(
    rows: list[Mapping[str, Any]],
    policy: Mapping[str, Any],
    *,
    well: str,
) -> list[Mapping[str, Any]]:
    mode = policy["mode"]
    if mode == "select":
        selected = str(policy["selected_acquisition_id"])
        return [row for row in rows if _acquisition_id(row) == selected]
    if mode == "reject" and len(rows) > 1:
        raise ContractError(
            "repeated_measurement",
            f"Well {well} has {len(rows)} matching measurements; choose select, mean, or all explicitly",
            {"acquisition_ids": [_acquisition_id(row) for row in rows]},
        )
    return rows


def _prepare_group(request: Mapping[str, Any]) -> dict[str, Any]:
    plate = _validate_plate(request.get("plate"))
    selection_raw = request.get("selection")
    selection = selection_raw if isinstance(selection_raw, Mapping) else {}
    assignments = _well_assignments(request)

    plate_id = str(selection.get("plate_id", "")).strip()
    if not plate_id:
        if len(plate["plate_ids"]) != 1:
            raise ContractError("selection_required", "Select one plate_id", {"available": plate["plate_ids"]})
        plate_id = plate["plate_ids"][0]
    if plate_id not in plate["plate_ids"]:
        raise ContractError("invalid_group", f"Plate {plate_id!r} is unavailable")
    plate_rows = [row for row in plate["rows"] if row["plate_id"] == plate_id]

    available_measurements = list(dict.fromkeys(str(row["measurement"]) for row in plate_rows))
    measurement = str(selection.get("measurement", "")).strip()
    if not measurement:
        if len(available_measurements) != 1:
            raise ContractError(
                "selection_required",
                "Select one measurement",
                {"available": available_measurements},
            )
        measurement = available_measurements[0]
    if measurement not in available_measurements:
        raise ContractError(
            "invalid_group",
            f"Measurement {measurement!r} is unavailable",
            {"available": available_measurements},
        )
    measurement_rows = [row for row in plate_rows if row["measurement"] == measurement]
    wavelength = _choose_numeric_selection(measurement_rows, "wavelength_nm", selection.get("wavelength_nm"))
    excitation = _choose_numeric_selection(measurement_rows, "excitation_nm", selection.get("excitation_nm"))
    selected_rows = [
        row
        for row in measurement_rows
        if _match_optional_number(row["wavelength_nm"], wavelength)
        and _match_optional_number(row["excitation_nm"], excitation)
    ]
    _reject_duplicate_rows_within_acquisition(selected_rows)

    policy_input: Any = selection.get("repeat_policy", request.get("repeat_policy"))
    if isinstance(policy_input, Mapping):
        policy_input = dict(policy_input)
        if "selected_acquisition_id" not in policy_input and selection.get("acquisition_id") is not None:
            policy_input["selected_acquisition_id"] = selection.get("acquisition_id")
    policy = _normalise_repeat_policy(policy_input)
    blank_input = request.get("blank_correction")
    if isinstance(blank_input, bool):
        blank_input = {"enabled": blank_input, "blank_wells": request.get("blank_wells", [])}
    elif isinstance(blank_input, Mapping):
        blank_input = dict(blank_input)
        blank_input.setdefault("blank_wells", request.get("blank_wells", []))
    else:
        blank_input = {"enabled": False, "blank_wells": request.get("blank_wells", [])}
    blank = _normalise_blank_correction(blank_input)
    sample_wells = {well for well, _ in assignments}
    overlap = sample_wells.intersection(blank["blank_wells"])
    if overlap:
        raise ContractError("invalid_blank_correction", f"Blank and sample wells overlap: {', '.join(sorted(overlap))}")

    rows_by_well: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in selected_rows:
        if row["well"] is not None:
            rows_by_well[str(row["well"])].append(row)

    blank_values: list[float] = []
    if blank["enabled"]:
        if not blank["blank_wells"]:
            raise ContractError("invalid_blank_correction", "Blank correction requires blank_wells")
        for well in blank["blank_wells"]:
            retained = _apply_repeat_policy(rows_by_well.get(well, []), policy, well=well)
            blank_values.extend(float(row["value"]) for row in retained if row["value"] is not None)
        if not blank_values:
            raise ContractError("invalid_blank_correction", "No finite blank measurements matched the selection")
        blank["blank_value"] = float(np.mean(blank_values))
        blank["blank_observation_count"] = len(blank_values)

    observations: list[dict[str, Any]] = []
    warnings: list[str] = []
    for well, concentration in assignments:
        matched = _apply_repeat_policy(rows_by_well.get(well, []), policy, well=well)
        mode = policy["mode"]
        if mode == "mean" and matched:
            finite_rows = [row for row in matched if row["value"] is not None]
            raw_value = float(np.mean([row["value"] for row in finite_rows])) if finite_rows else None
            std = (
                float(np.std([row["value"] for row in finite_rows], ddof=1))
                if len(finite_rows) > 1
                else 0.0 if finite_rows else None
            )
            source_rows = [int(row["source_row"]) for row in matched]
            observation_rows: list[tuple[Optional[Mapping[str, Any]], Optional[float], dict[str, Any]]] = [
                (
                    finite_rows[0] if finite_rows else matched[0],
                    raw_value,
                    {
                        "acquisition_id": None,
                        "read": None,
                        "acquisition_ids": [_acquisition_id(row) for row in matched],
                        "source_rows": source_rows,
                        "replicate_count": len(finite_rows),
                        "replicate_std": std,
                        "technical_replicate_label": policy["label"],
                    },
                )
            ]
        elif mode == "all" and matched:
            observation_rows = [
                (
                    row,
                    row["value"],
                    {
                        "acquisition_id": _acquisition_id(row),
                        "read": row.get("read"),
                        "acquisition_ids": [_acquisition_id(row)],
                        "source_rows": [int(row["source_row"])],
                        "replicate_count": 1 if row["value"] is not None else 0,
                        "replicate_std": None,
                        "technical_replicate_label": policy["label"],
                    },
                )
                for row in matched
            ]
        elif matched:
            row = matched[0]
            observation_rows = [
                (
                    row,
                    row["value"],
                    {
                        "acquisition_id": _acquisition_id(row),
                        "read": row.get("read"),
                        "acquisition_ids": [_acquisition_id(row)],
                        "source_rows": [int(row["source_row"])],
                        "replicate_count": 1 if row["value"] is not None else 0,
                        "replicate_std": None,
                        "technical_replicate_label": None,
                    },
                )
            ]
        else:
            observation_rows = [(None, None, {"acquisition_id": None, "read": None})]

        for repeat_index, (source_row, raw_value, acquisition) in enumerate(observation_rows, start=1):
            suffix = f"-{repeat_index}" if len(observation_rows) > 1 else ""
            problems: list[str] = []
            if concentration is None:
                problems.append("missing concentration")
            if raw_value is None:
                problems.append("no matching finite plate measurement")
            corrected = (
                float(raw_value) - float(blank["blank_value"])
                if raw_value is not None and blank["enabled"]
                else None
            )
            observations.append(
                {
                    "row_id": f"{well}{suffix}",
                    "concentration_m": concentration,
                    "raw_signal": raw_value,
                    "blank_corrected_signal": corrected,
                    "source_row": int(source_row["source_row"]) if source_row is not None else None,
                    "well": well,
                    "acquisition": acquisition,
                    "excluded": bool(problems),
                    "exclusion_reason": "; ".join(problems) if problems else None,
                }
            )
            if problems:
                warnings.append(f"{well}{suffix} was excluded: {'; '.join(problems)}")
    if len(observations) > MAX_OBSERVATIONS:
        raise ContractError("too_many_observations", f"Prepared groups are limited to {MAX_OBSERVATIONS} observations")

    settings = {
        "temperature_k": selection.get("temperature_k", request.get("temperature_k", 298.15)),
        "group_name": selection.get("group_name", request.get("group_name", "")),
        "measurement": measurement,
        "wavelength_nm": wavelength,
        "excitation_nm": excitation,
        "requested_selection": {
            "plate_id": selection.get("plate_id"),
            "measurement": selection.get("measurement"),
            "wavelength_nm": selection.get("wavelength_nm"),
            "excitation_nm": selection.get("excitation_nm"),
            "acquisition_id": (
                policy.get("selected_acquisition_id")
                if policy["mode"] == "select"
                else None
            ),
        },
        "repeat_policy": policy,
        "blank_correction": blank,
        "fit_signal": "blank_corrected_signal" if blank["enabled"] else "raw_signal",
    }
    project, validation_warnings = _new_project(
        source=plate["source"],
        settings=settings,
        observations=observations,
        notes=str(request.get("notes", "")),
        visual_midpoint_m=request.get("visual_midpoint_m"),
    )
    return {"project": project, "warnings": warnings + validation_warnings}


def _fit_result_payload(
    result: FitResult,
    x: np.ndarray,
    y: np.ndarray,
    row_ids: list[str],
) -> dict[str, Any]:
    predicted: list[Any] = []
    residuals: list[Any] = []
    curve_x: list[Any] = []
    curve_y: list[Any] = []
    if result.success and result.prediction_function is not None:
        try:
            predicted_array = result.predict(x)
            predicted = _json_safe(predicted_array)
            residuals = _json_safe(y - predicted_array)
            if x.size:
                grid = np.linspace(float(np.min(x)), float(np.max(x)), 200)
                curve_x = _json_safe(grid)
                curve_y = _json_safe(result.predict(grid))
        except Exception as exc:
            predicted = []
            residuals = []
            curve_x = []
            curve_y = []
            extra_warning = f"Prediction serialization failed: {exc}"
            result_warnings = list(getattr(result, "warnings", [])) + [extra_warning]
        else:
            result_warnings = list(getattr(result, "warnings", []))
    else:
        result_warnings = list(getattr(result, "warnings", []))
    retained_indices = getattr(result, "retained_indices", np.arange(len(x), dtype=int))
    excluded_indices = getattr(result, "excluded_indices", np.array([], dtype=int))
    return {
        "model_name": result.model_name,
        "success": bool(result.success),
        "message": result.message,
        "interpretation_status": getattr(result, "interpretation_status", "fit_failed"),
        "parameters": result.parameters,
        "standard_errors": result.standard_errors,
        "metrics": result.metrics,
        "diagnostics": getattr(result, "diagnostics", {}),
        "warnings": result_warnings,
        "data_fingerprint": getattr(result, "data_fingerprint", ""),
        "parameter_order": list(result.parameter_order),
        "covariance": result.covariance,
        "curve": {"x": curve_x, "y": curve_y},
        "observed": {
            "row_ids": row_ids,
            "x": x,
            "y": y,
            "predicted": predicted,
            "residuals": residuals,
            "retained_indices": retained_indices,
            "excluded_indices": excluded_indices,
        },
    }


def _fit(request: Mapping[str, Any]) -> dict[str, Any]:
    project, warnings = _normalise_project(
        request.get("project"),
        drop_result=False,
        preserve_matching_result=False,
    )
    if request.get("temperature_k") is not None:
        project["settings"]["temperature_k"] = _finite_or_none(
            request.get("temperature_k"), "temperature_k"
        )
        project, changed_warnings = _normalise_project(
            project,
            drop_result=False,
            preserve_matching_result=False,
        )
        warnings.extend(changed_warnings)
    model = str(request.get("model", "compare")).strip().lower()
    if model not in {"two_state", "logistic", "compare"}:
        raise ContractError("invalid_model", "model must be two_state, logistic, or compare")
    signal_field = project["settings"]["fit_signal"]
    active = [row for row in project["observations"] if not row["excluded"]]
    x = np.asarray([row["concentration_m"] for row in active], dtype=float)
    y = np.asarray([row[signal_field] for row in active], dtype=float)
    row_ids = [str(row["row_id"]) for row in active]
    results: list[FitResult] = []
    if model in {"two_state", "compare"}:
        results.append(
            fit_two_state_denaturation(
                x,
                y,
                temperature_k=float(project["settings"]["temperature_k"]),
            )
        )
    if model in {"logistic", "compare"}:
        results.append(fit_four_parameter_logistic(x, y))
    fit_payloads = [_fit_result_payload(item, x, y, row_ids) for item in results]
    try:
        preferred = choose_best_fit(results)
        preferred_name = preferred.model_name if preferred is not None else None
        preferred_note = (
            "Preferred only by information criterion among successful fits on identical data; "
            "this is descriptive and does not establish mechanistic validity."
        )
    except ValueError as exc:
        preferred_name = None
        preferred_note = str(exc)
        warnings.append(str(exc))
    result_payload = {
        "project_fingerprint": project["content_fingerprint"],
        "signal_field": signal_field,
        "fit_request": {
            "model": model,
            "temperature_k": project["settings"]["temperature_k"],
        },
        "fits": fit_payloads,
        "preferred_model": preferred_name,
        "preferred_model_note": preferred_note,
    }
    project["software_version"] = SOFTWARE_VERSION
    project["result"] = _json_safe(result_payload)
    return {"project": project, "result": result_payload, "warnings": warnings}


def _validate_project_action(request: Mapping[str, Any]) -> dict[str, Any]:
    project, warnings = _normalise_project(request.get("project"), drop_result=True)
    return {"project": project, "warnings": warnings}


def _load_project(request: Mapping[str, Any]) -> dict[str, Any]:
    text = _require_text(request)
    try:
        value = _strict_json_loads(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ContractError("invalid_json", str(exc)) from exc
    project, warnings = _normalise_project(value, drop_result=True)
    return {"project": project, "warnings": warnings}


def _export_project(request: Mapping[str, Any]) -> dict[str, Any]:
    project, warnings = _normalise_project(
        request.get("project"),
        drop_result=False,
        preserve_matching_result=True,
    )
    text = json.dumps(_json_safe(project), allow_nan=False, ensure_ascii=False, indent=2) + "\n"
    stem = _download_name(project["settings"]["group_name"] or project["source"]["name"], "folding_project")
    return {
        "project": project,
        "warnings": warnings,
        "filename": f"{stem}.folding.json",
        "mime": "application/json",
        "text": text,
    }


def _export_legacy_csv(request: Mapping[str, Any]) -> dict[str, Any]:
    project, warnings = _normalise_project(
        request.get("project"),
        drop_result=False,
        preserve_matching_result=True,
    )
    normalization = str(request.get("normalization", "none")).strip().lower()
    if normalization not in {"none", "minmax"}:
        raise ContractError("invalid_export", "normalization must be none or minmax")
    include_excluded = request.get("include_excluded", False) is True
    rows = project["observations"] if include_excluded else [
        row for row in project["observations"] if not row["excluded"]
    ]
    active_raw = np.asarray(
        [row["raw_signal"] for row in rows if row["raw_signal"] is not None],
        dtype=float,
    )
    minimum = float(np.min(active_raw)) if active_raw.size else None
    span = float(np.ptp(active_raw)) if active_raw.size else None
    if normalization == "minmax" and (span is None or span <= 0):
        reason = "no_dynamic_range" if active_raw.size else "no_finite_raw_signal"
        warnings.append(
            "Min-max normalization was left blank: "
            f"{reason}"
            + (
                " (all finite raw values are equal)."
                if reason == "no_dynamic_range"
                else "."
            )
        )
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "GuHCl concentration (M)",
            "raw fluorescence values",
            "normalized fluorescence values",
        ]
    )
    for row in rows:
        raw = row["raw_signal"]
        if (
            normalization == "minmax"
            and raw is not None
            and minimum is not None
            and span is not None
            and span > 0
        ):
            normalized: Any = (float(raw) - minimum) / span
        else:
            normalized = ""
        writer.writerow(
            [
                "" if row["concentration_m"] is None else row["concentration_m"],
                "" if raw is None else raw,
                normalized,
            ]
        )
    stem = _download_name(project["settings"]["group_name"] or project["source"]["name"], "series")
    return {
        "project": project,
        "warnings": warnings,
        "filename": f"{stem}.csv",
        "mime": "text/csv",
        "text": output.getvalue(),
        "normalization": normalization,
        "omitted_exclusions": 0 if include_excluded else len(project["exclusions"]),
    }


def _markdown_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}" if math.isfinite(value) else ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _export_report(request: Mapping[str, Any]) -> dict[str, Any]:
    project, warnings = _normalise_project(
        request.get("project"),
        drop_result=False,
        preserve_matching_result=True,
    )
    settings = project["settings"]
    lines = [
        f"# Folding analysis: {settings['group_name'] or project['source']['name']}",
        "",
        f"- Source: `{project['source']['name']}`",
        f"- Decoded UTF-8 text SHA-256: `{project['source']['sha256']}`",
        *(
            [f"- Original file bytes SHA-256: `{project['source']['bytes_sha256']}`"]
            if project["source"].get("bytes_sha256")
            else []
        ),
        *(
            [f"- Source text decoding: `{project['source']['encoding']}`"]
            if project["source"].get("encoding")
            else []
        ),
        f"- Project fingerprint: `{project['content_fingerprint']}`",
        f"- Software/schema: {project['software_version']} / {project['schema_version']}",
        f"- Temperature: {settings['temperature_k']:.2f} K",
        f"- Measurement: {settings['measurement'] or 'unspecified'}",
        f"- Wavelength/excitation: {_markdown_cell(settings['wavelength_nm'])} / {_markdown_cell(settings['excitation_nm'])} nm",
        "- Requested plate selection: `"
        + _canonical_json(settings["requested_selection"])
        + "`",
        "- Resolved plate selection: `"
        + _canonical_json(
            {
                "measurement": settings["measurement"],
                "wavelength_nm": settings["wavelength_nm"],
                "excitation_nm": settings["excitation_nm"],
            }
        )
        + "`",
        "- Repeat policy: `" + _canonical_json(settings["repeat_policy"]) + "`",
        "- Blank correction: `" + _canonical_json(settings["blank_correction"]) + "`",
        f"- Fitted signal: `{settings['fit_signal']}`",
        "",
        "Numerical optimizer success is not equivalent to scientific interpretability; consult each fit's interpretation status and warnings.",
        "",
        "## Scientific assumptions and limits",
        "",
        "- These fluorescence fits do not by themselves establish equilibrium, reversibility, a two-state mechanism, or that the measured signal reports only folding.",
        "- The two-state LEM interpretation assumes reversible two-state equilibrium unfolding, linear denaturant dependence of free energy, linear folded and unfolded signal baselines, accurate denaturant concentration and temperature, and independent observations with an appropriate residual-error model.",
        "- The four-parameter logistic curve is descriptive. Its midpoint is not automatically a thermodynamic midpoint, and choosing a lower information criterion is not a mechanistic hypothesis test.",
        "- AIC, AICc, and BIC use an independent Gaussian residual likelihood with residual variance estimated as RSS/n. The reported information-criterion parameter count is the model parameter count plus one residual-variance parameter; AICc is undefined when n <= k + 1.",
        "- Information criteria are comparable only for fits to the same retained observations and response signal. Replicates are retained, selected, or averaged exactly as stated above; averaging changes the observational unit.",
        "- Standard errors are local asymptotic estimates from the optimizer covariance matrix. Bound hits, poor coverage, non-identifiability, correlated/heteroscedastic residuals, or ill-conditioned covariance can make them misleading.",
        "- Blank correction, where enabled, subtracts the stated arithmetic mean and does not propagate blank uncertainty into fitted standard errors.",
        "",
        "## Observations",
        "",
        "| row_id | concentration (M) | raw signal | blank-corrected signal | well | excluded | rationale |",
        "| --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for row in project["observations"]:
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(item)
                for item in (
                    row["row_id"],
                    row["concentration_m"],
                    row["raw_signal"],
                    row["blank_corrected_signal"],
                    row["well"],
                    row["excluded"],
                    row["exclusion_reason"],
                )
            )
            + " |"
        )
    lines.extend(["", "## Fit results", ""])
    result = project.get("result")
    if not isinstance(result, Mapping):
        lines.append("No validated fit is stored. Re-fit this project after loading.")
    else:
        for fit in result.get("fits", []):
            lines.extend(
                [
                    f"### {fit.get('model_name', 'Fit')}",
                    "",
                    f"- Optimizer success: {fit.get('success')}",
                    f"- Interpretation status: `{fit.get('interpretation_status')}`",
                    f"- Message: {fit.get('message') or 'none'}",
                    f"- Warnings: {'; '.join(str(item) for item in fit.get('warnings', [])) or 'none'}",
                    "",
                    "| parameter | estimate | standard error |",
                    "| --- | ---: | ---: |",
                ]
            )
            errors = fit.get("standard_errors", {})
            for name, estimate in fit.get("parameters", {}).items():
                lines.append(
                    f"| {_markdown_cell(name)} | {_markdown_cell(estimate)} | {_markdown_cell(errors.get(name))} |"
                )
            lines.extend(
                [
                    "",
                    "#### Fit metrics",
                    "",
                    "| metric | value |",
                    "| --- | ---: |",
                ]
            )
            for name, metric in fit.get("metrics", {}).items():
                lines.append(f"| {_markdown_cell(name)} | {_markdown_cell(metric)} |")
            lines.extend(
                [
                    "",
                    "#### Diagnostics",
                    "",
                    "```json",
                    json.dumps(
                        _json_safe(fit.get("diagnostics", {})),
                        allow_nan=False,
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "```",
                    "",
                    "#### Parameter covariance matrix",
                    "",
                    "```json",
                    json.dumps(
                        _json_safe(fit.get("covariance")),
                        allow_nan=False,
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "```",
                    "",
                ]
            )
    if warnings:
        lines.extend(["## Project validation warnings", ""])
        lines.extend(f"- {item}" for item in warnings)
        lines.append("")
    if project["notes"]:
        lines.extend(["## Notes", "", project["notes"], ""])
    stem = _download_name(settings["group_name"] or project["source"]["name"], "analysis")
    return {
        "project": project,
        "warnings": warnings,
        "filename": f"{stem}_report.md",
        "mime": "text/markdown",
        "text": "\n".join(lines).rstrip() + "\n",
    }


def _normalise_practical(
    value: Any,
    *,
    drop_results: bool,
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(value, Mapping) or value.get("schema_version") != PRACTICAL_SCHEMA_VERSION:
        raise ContractError("unsupported_schema", f"Expected practical schema {PRACTICAL_SCHEMA_VERSION!r}")
    raw_projects = value.get("projects")
    if not isinstance(raw_projects, list) or not raw_projects:
        raise ContractError("invalid_practical", "practical.projects must be a non-empty list")
    projects: list[dict[str, Any]] = []
    warnings: list[str] = []
    observation_count = 0
    group_name_indices: dict[str, int] = {}
    for index, raw in enumerate(raw_projects):
        project, project_warnings = _normalise_project(
            raw,
            drop_result=drop_results,
            preserve_matching_result=not drop_results,
        )
        group_name = project["settings"]["group_name"]
        group_key = group_name.casefold()
        if group_key in group_name_indices:
            first_index = group_name_indices[group_key]
            raise ContractError(
                "invalid_practical",
                f"Prepared group names must be unique; {group_name!r} is duplicated",
                {
                    "group_name": group_name,
                    "project_indices": [first_index, index],
                },
            )
        group_name_indices[group_key] = index
        observation_count += len(project["observations"])
        projects.append(project)
        warnings.extend(f"Project {index + 1}: {item}" for item in project_warnings)
    if observation_count > MAX_OBSERVATIONS:
        raise ContractError(
            "too_many_observations",
            f"A practical is limited to {MAX_OBSERVATIONS} observations across projects",
        )
    try:
        selected_index = int(value.get("selected_index", 0))
    except (TypeError, ValueError) as exc:
        raise ContractError("invalid_practical", "selected_index must be an integer") from exc
    if not 0 <= selected_index < len(projects):
        raise ContractError("invalid_practical", "selected_index is outside the projects list")
    locks_value = value.get("instructor_locks", {})
    if not isinstance(locks_value, Mapping):
        raise ContractError("invalid_practical", "instructor_locks must be an object")
    locks = _json_safe(dict(locks_value))
    if "model" in locks_value:
        model_value = locks_value["model"]
        if not isinstance(model_value, str):
            raise ContractError(
                "invalid_practical",
                "instructor_locks.model must be two_state, logistic, or compare",
            )
        model = model_value.strip().lower()
        if model not in {"two_state", "logistic", "compare"}:
            raise ContractError(
                "invalid_practical",
                "instructor_locks.model must be two_state, logistic, or compare",
            )
        locks["model"] = model
    if "temperature_k" in locks_value:
        temperature_value = locks_value["temperature_k"]
        if isinstance(temperature_value, (bool, np.bool_)):
            locks["temperature_k"] = bool(temperature_value)
        elif isinstance(temperature_value, (int, float, np.integer, np.floating)):
            temperature = float(temperature_value)
            if not math.isfinite(temperature) or not 260.0 <= temperature <= 330.0:
                raise ContractError(
                    "invalid_practical",
                    "instructor_locks.temperature_k must be a boolean or 260-330 K",
                )
            locks["temperature_k"] = temperature
        else:
            raise ContractError(
                "invalid_practical",
                "instructor_locks.temperature_k must be a boolean or 260-330 K",
            )
    practical: dict[str, Any] = {
        "schema_version": PRACTICAL_SCHEMA_VERSION,
        "software_version": str(value.get("software_version") or SOFTWARE_VERSION),
        "title": str(value.get("title", "Folding practical")),
        "projects": projects,
        "selected_index": selected_index,
        "instructor_locks": locks,
        "content_fingerprint": "",
    }
    practical["content_fingerprint"] = _fingerprint(
        {
            "title": practical["title"],
            "project_fingerprints": [item["content_fingerprint"] for item in projects],
            "selected_index": selected_index,
            "instructor_locks": locks,
        }
    )
    return practical, warnings


def _export_practical(request: Mapping[str, Any]) -> dict[str, Any]:
    supplied = request.get("practical")
    if supplied is None:
        supplied = {
            "schema_version": PRACTICAL_SCHEMA_VERSION,
            "software_version": SOFTWARE_VERSION,
            "title": request.get("title", "Folding practical"),
            "projects": request.get("projects"),
            "selected_index": request.get("selected_index", 0),
            "instructor_locks": request.get("instructor_locks", {}),
        }
    practical, warnings = _normalise_practical(supplied, drop_results=False)
    text = json.dumps(_json_safe(practical), allow_nan=False, ensure_ascii=False, indent=2) + "\n"
    return {
        "practical": practical,
        "warnings": warnings,
        "filename": f"{_download_name(practical['title'], 'folding_practical')}.practical.json",
        "mime": "application/json",
        "text": text,
    }


def _load_practical(request: Mapping[str, Any]) -> dict[str, Any]:
    text = _require_text(request)
    try:
        supplied = _strict_json_loads(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ContractError("invalid_json", str(exc)) from exc
    practical, warnings = _normalise_practical(supplied, drop_results=True)
    return {"practical": practical, "warnings": warnings}


def _synthetic_plate_text() -> str:
    # Match the practical: 16 conditions across A1-A12, then B1-B4.
    sample = [1008, 1006, 996, 980, 940, 875, 760, 610, 440, 295, 190, 135, 113, 103, 99, 98]
    blanks = [20, 21, 19, 20] + [""] * 8
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["Synthetic teaching example only"])
    writer.writerow(["", "Raw Data (Em Spectrum) 472-16 / 508-10"])
    writer.writerow([""] + list(range(1, 13)))
    writer.writerow(["A"] + sample[:12])
    writer.writerow(["B"] + sample[12:] + [""] * 8)
    writer.writerow(["H"] + blanks)
    return output.getvalue()


def _example_plate(_: Mapping[str, Any]) -> dict[str, Any]:
    text = _synthetic_plate_text()
    filename = "synthetic_teaching_plate.csv"
    plate = _import_plate({"text": text, "filename": filename})["plate"]
    concentrations = [index * 4 / 10 for index in range(16)]
    wells = [f"A{index}" for index in range(1, 13)] + [f"B{index}" for index in range(1, 5)]
    prepare_request = {
        "action": "prepare_group",
        "plate": plate,
        "selection": {
            "group_name": "Synthetic teaching example",
            "plate_id": plate["plate_ids"][0],
            "measurement": "Emission spectrum (Ex 472 nm)",
            "wavelength_nm": 508.0,
            "excitation_nm": 472.0,
            "repeat_policy": {"mode": "reject"},
            "temperature_k": 298.15,
        },
        "wells": [
            {"well": well, "concentration_m": value}
            for well, value in zip(wells, concentrations)
        ],
        "blank_correction": {
            "enabled": True,
            "method": "mean",
            "blank_wells": ["H1", "H2", "H3", "H4"],
        },
        "notes": "Synthetic teaching example; not experimental measurements.",
    }
    project = _prepare_group(prepare_request)["project"]
    return {
        "project": project,
        "plate": plate,
        "filename": filename,
        "text": text,
        "prepare_group_request": prepare_request,
    }


def _example(request: Mapping[str, Any]) -> dict[str, Any]:
    example = _example_plate(request)
    return {"project": example["project"]}


def _batch_action(action: str, request: Mapping[str, Any]) -> dict[str, Any]:
    from . import browser_batch
    return getattr(browser_batch, action)(request)


_ACTIONS: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "example": _example,
    "example_plate": _example_plate,
    "import_series": lambda request: _parse_legacy_series(
        _require_text(request),
        _safe_name(request.get("filename"), "series.csv"),
        request,
    ),
    "import_plate": _import_plate,
    "import_plates": lambda request: _batch_action("import_plates", request),
    "prepare_groups": lambda request: _batch_action("prepare_groups", request),
    "export_group_csvs": lambda request: _batch_action("export_group_csvs", request),
    "prepare_group": _prepare_group,
    "validate_project": _validate_project_action,
    "load_project": _load_project,
    "fit": _fit,
    "export_project": _export_project,
    "export_legacy_csv": _export_legacy_csv,
    "export_report": _export_report,
    "export_practical": _export_practical,
    "load_practical": _load_practical,
}


def dispatch(request: Mapping[str, Any]) -> dict[str, Any]:
    """Run one browser-service action and return a strict-JSON-friendly dict."""
    action = ""
    try:
        if not isinstance(request, Mapping):
            raise ContractError("invalid_request", "request must be an object")
        action = str(request.get("action", "")).strip()
        if not action:
            raise ContractError("invalid_request", "request.action is required")
        handler = _ACTIONS.get(action)
        if handler is None:
            raise ContractError(
                "unknown_action",
                f"Unknown action {action!r}",
                {"available": sorted(_ACTIONS)},
            )
        response = {"ok": True, "action": action, "data": handler(request)}
    except ContractError as exc:
        response = {
            "ok": False,
            "action": action,
            "error": {"code": exc.code, "message": str(exc), "details": exc.details},
        }
    except Exception as exc:  # keep worker messages safe and deterministic
        response = {
            "ok": False,
            "action": action,
            "error": {
                "code": "analysis_error",
                "message": str(exc) or exc.__class__.__name__,
                "details": {"type": exc.__class__.__name__},
            },
        }
    safe = _json_safe(response)
    # This assertion is deliberate: every action must survive the worker's
    # json.dumps(..., allow_nan=False) boundary.
    json.dumps(safe, allow_nan=False)
    return safe


__all__ = ["dispatch"]
