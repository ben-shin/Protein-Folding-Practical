"""Group assignment and export logic for denaturation series."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping, Optional, Union

import numpy as np
import pandas as pd

from .wells import expand_well_spec, normalize_well

EXPORT_COLUMNS = [
    "GuHCl concentration (M)",
    "raw fluorescence values",
    "normalized fluorescence values",
]


@dataclass
class GroupAssignment:
    name: str
    plate_id: str
    wells: list[str]
    concentrations: list[float]
    measurement: str
    wavelength_nm: Optional[float] = None

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Group name cannot be empty")
        self.wells = [normalize_well(well) for well in self.wells]
        self.concentrations = [float(value) for value in self.concentrations]
        if len(self.wells) != len(self.concentrations):
            raise ValueError("The number of wells must match the number of GuHCl concentrations")
        if len(self.wells) < 3:
            raise ValueError("A group needs at least three conditions")
        if len(set(self.wells)) != len(self.wells):
            raise ValueError("A group cannot contain duplicate wells")
        if not np.all(np.isfinite(self.concentrations)):
            raise ValueError("Concentrations must all be finite numbers")
        if self.wavelength_nm is not None:
            self.wavelength_nm = float(self.wavelength_nm)
            if not np.isfinite(self.wavelength_nm):
                raise ValueError("Wavelength must be a finite number")


_GROUP_MAP_ALIASES = {
    "group": {"group", "groupname", "practicalgroup", "practicalgroupname"},
    "plate": {"plate", "plateid", "platenumber", "platefile"},
    "wells": {"wells", "wellrange", "wellranges", "wellspec", "wellspecification"},
    "measurement": {"measurement", "signal", "readout"},
    "wavelength": {"wavelength", "wavelengthnm", "emissionwavelength", "emissionwavelengthnm"},
    "concentrations": {
        "concentrations",
        "guhcl",
        "guhclconcentrations",
        "guhclconcentrationsm",
    },
}


def _normalize_label(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _find_group_map_column(columns: list[object], field: str) -> Optional[object]:
    aliases = _GROUP_MAP_ALIASES[field]
    for column in columns:
        if _normalize_label(column) in aliases:
            return column
    return None


def _plate_aliases(data: pd.DataFrame, plate_id: str) -> set[str]:
    names = {str(plate_id)}
    if "source_file" in data.columns:
        sources = data.loc[data["plate_id"].astype(str) == str(plate_id), "source_file"].dropna()
        for source in sources.astype(str):
            names.add(source)
            names.add(Path(source).stem)
    aliases: set[str] = set()
    for name in names:
        aliases.add(_normalize_label(name))
        leading = re.match(r"^[A-Za-z]+\d+", name.strip())
        if leading:
            aliases.add(_normalize_label(leading.group(0)))
    return {alias for alias in aliases if alias}


def _resolve_plate_id(data: pd.DataFrame, requested: str) -> str:
    requested_key = _normalize_label(requested)
    if not requested_key:
        raise ValueError("Plate number cannot be empty")
    matches = [
        str(plate_id)
        for plate_id in dict.fromkeys(data["plate_id"].astype(str))
        if requested_key in _plate_aliases(data, str(plate_id))
    ]
    if not matches:
        available = ", ".join(dict.fromkeys(data["plate_id"].astype(str)))
        raise ValueError(f"Plate {requested!r} was not loaded. Available plates: {available}")
    if len(matches) > 1:
        raise ValueError(f"Plate {requested!r} matches more than one loaded plate: {', '.join(matches)}")
    return matches[0]


def _resolve_measurement(data: pd.DataFrame, plate_id: str, requested: str, default: str) -> str:
    available = list(
        dict.fromkeys(data.loc[data["plate_id"].astype(str) == plate_id, "measurement"].astype(str))
    )
    choice = requested.strip() or default.strip()
    if choice:
        matches = [value for value in available if value.lower() == choice.lower()]
        if len(matches) == 1:
            return matches[0]
    if len(available) == 1:
        return available[0]
    raise ValueError(
        f"Plate {plate_id!r} has several signals. Add a measurement column or select the signal first"
    )


def _resolve_wavelength(
    data: pd.DataFrame,
    plate_id: str,
    measurement: str,
    requested: str,
    default: Optional[float],
) -> Optional[float]:
    if "wavelength_nm" not in data.columns:
        return None
    subset = data.loc[
        (data["plate_id"].astype(str) == plate_id)
        & (data["measurement"].astype(str) == measurement),
        "wavelength_nm",
    ]
    available = sorted(pd.to_numeric(subset, errors="coerce").dropna().unique().tolist())
    if not available:
        return None

    candidates: list[float] = []
    if requested.strip():
        candidates.append(float(requested))
    if default is not None:
        candidates.append(float(default))
    candidates.append(508.0)

    for candidate in candidates:
        matches = [value for value in available if np.isclose(value, candidate)]
        if matches:
            return float(matches[0])
    if len(available) == 1:
        return float(available[0])
    raise ValueError(
        f"Plate {plate_id!r} has several wavelengths. Add a wavelength column or select one first"
    )


def _parse_concentrations(value: str, default: list[float]) -> list[float]:
    if not value.strip():
        return [float(item) for item in default]
    tokens = [token for token in re.split(r"[,;|\s]+", value.strip()) if token]
    return [float(token) for token in tokens]


def load_group_map_assignments(
    data: pd.DataFrame,
    path: Union[str, Path],
    *,
    default_concentrations: list[float],
    default_measurement: str = "",
    default_wavelength_nm: Optional[float] = None,
    existing_assignments: Optional[Mapping[str, GroupAssignment]] = None,
) -> dict[str, GroupAssignment]:
    """Load practical groups from a simple CSV map."""
    if data.empty:
        raise ValueError("Load plate data first")

    table = pd.read_csv(path, dtype=str, keep_default_na=False)
    if table.empty:
        raise ValueError("The group map is empty")

    columns = list(table.columns)
    group_column = _find_group_map_column(columns, "group")
    plate_column = _find_group_map_column(columns, "plate")
    wells_column = _find_group_map_column(columns, "wells")
    missing = [
        label
        for label, column in (
            ("group name", group_column),
            ("plate number", plate_column),
            ("well ranges", wells_column),
        )
        if column is None
    ]
    if missing:
        raise ValueError(f"The group map is missing columns: {', '.join(missing)}")

    measurement_column = _find_group_map_column(columns, "measurement")
    wavelength_column = _find_group_map_column(columns, "wavelength")
    concentration_column = _find_group_map_column(columns, "concentrations")

    imported_names = [str(value).strip() for value in table[group_column]]
    if any(not name for name in imported_names):
        raise ValueError("Every row needs a group name")
    duplicates = sorted({name for name in imported_names if imported_names.count(name) > 1})
    if duplicates:
        raise ValueError(f"The group map repeats group names: {', '.join(duplicates)}")

    occupied: dict[tuple[str, str], str] = {}
    for name, assignment in (existing_assignments or {}).items():
        if name in imported_names:
            continue
        for well in assignment.wells:
            occupied[(assignment.plate_id, well)] = name

    assignments: dict[str, GroupAssignment] = {}
    for row_number, (_, row) in enumerate(table.iterrows(), start=2):
        try:
            name = str(row[group_column]).strip()
            plate_id = _resolve_plate_id(data, str(row[plate_column]))
            wells = expand_well_spec(str(row[wells_column]))
            concentrations = _parse_concentrations(
                str(row[concentration_column]) if concentration_column is not None else "",
                default_concentrations,
            )
            measurement = _resolve_measurement(
                data,
                plate_id,
                str(row[measurement_column]) if measurement_column is not None else "",
                default_measurement,
            )
            wavelength = _resolve_wavelength(
                data,
                plate_id,
                measurement,
                str(row[wavelength_column]) if wavelength_column is not None else "",
                default_wavelength_nm,
            )
            assignment = GroupAssignment(
                name=name,
                plate_id=plate_id,
                wells=wells,
                concentrations=concentrations,
                measurement=measurement,
                wavelength_nm=wavelength,
            )
            build_group_dataframe(data, assignment)
            for well in assignment.wells:
                previous = occupied.get((plate_id, well))
                if previous is not None:
                    raise ValueError(f"Well {well} is already assigned to {previous!r}")
                occupied[(plate_id, well)] = name
            assignments[name] = assignment
        except Exception as exc:
            raise ValueError(f"Group map row {row_number}: {exc}") from exc

    return assignments


def normalize_fluorescence(values: Union[np.ndarray, pd.Series]) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    minimum = np.nanmin(array)
    maximum = np.nanmax(array)
    span = maximum - minimum
    if not np.isfinite(span) or span == 0:
        return np.zeros_like(array, dtype=float)
    return (array - minimum) / span


def build_group_dataframe(data: pd.DataFrame, assignment: GroupAssignment) -> pd.DataFrame:
    """Build the exact three-column export requested for one practical group."""
    required = {"plate_id", "well", "measurement", "value"}
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"Input data is missing columns: {', '.join(sorted(missing))}")

    mask = (
        (data["plate_id"] == assignment.plate_id)
        & (data["measurement"] == assignment.measurement)
        & (data["well"].isin(assignment.wells))
    )
    if assignment.wavelength_nm is None and "wavelength_nm" in data.columns:
        candidate_wavelengths = pd.to_numeric(data.loc[mask, "wavelength_nm"], errors="coerce").dropna().unique()
        if len(candidate_wavelengths) > 1:
            raise ValueError(
                f"Group {assignment.name!r} uses a wavelength-resolved signal; "
                "select one emission wavelength before assigning the group"
            )
    if assignment.wavelength_nm is not None:
        if "wavelength_nm" not in data.columns:
            raise ValueError("Input data does not contain wavelength-resolved measurements")
        wavelengths = pd.to_numeric(data["wavelength_nm"], errors="coerce")
        mask &= np.isclose(wavelengths, assignment.wavelength_nm, equal_nan=False)

    subset = data.loc[mask, ["well", "value"]].copy()
    if subset.empty:
        raise ValueError(f"No measurements found for group {assignment.name!r}")

    duplicated = subset["well"].duplicated(keep=False)
    if duplicated.any():
        subset = subset.groupby("well", as_index=False)["value"].mean()

    value_by_well = subset.set_index("well")["value"]
    missing_wells = [well for well in assignment.wells if well not in value_by_well.index]
    if missing_wells:
        raise ValueError(
            f"Group {assignment.name!r} has no value for wells: {', '.join(missing_wells)}"
        )

    raw = np.array([float(value_by_well.loc[well]) for well in assignment.wells], dtype=float)
    output = pd.DataFrame(
        {
            EXPORT_COLUMNS[0]: assignment.concentrations,
            EXPORT_COLUMNS[1]: raw,
            EXPORT_COLUMNS[2]: normalize_fluorescence(raw),
        }
    )
    return output


def build_spectrum_dataframe(
    data: pd.DataFrame,
    *,
    plate_id: str,
    measurement: str,
    wells: list[str],
) -> pd.DataFrame:
    """Return wavelength-resolved fluorescence for selected wells.

    Replicate values at the same well/wavelength are averaged. Peak
    normalization is performed independently for each well, preserving the
    spectral shape while allowing spectra with different absolute intensity to
    be compared on one graph.
    """

    required = {"plate_id", "well", "measurement", "wavelength_nm", "value"}
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"Input data is missing columns: {', '.join(sorted(missing))}")

    canonical_wells = [normalize_well(well) for well in wells]
    if not canonical_wells:
        raise ValueError("Select at least one well")

    wavelengths = pd.to_numeric(data["wavelength_nm"], errors="coerce")
    subset = data.loc[
        (data["plate_id"] == plate_id)
        & (data["measurement"] == measurement)
        & (data["well"].isin(canonical_wells))
        & wavelengths.notna(),
        ["well", "wavelength_nm", "value"],
    ].copy()
    if subset.empty:
        raise ValueError("No wavelength-resolved data found for the selected wells")

    subset["wavelength_nm"] = pd.to_numeric(subset["wavelength_nm"], errors="raise")
    subset["value"] = pd.to_numeric(subset["value"], errors="raise")
    subset = subset.groupby(["well", "wavelength_nm"], as_index=False)["value"].mean()

    missing_wells = [well for well in canonical_wells if well not in set(subset["well"])]
    if missing_wells:
        raise ValueError(f"No spectrum found for wells: {', '.join(missing_wells)}")

    def peak_normalize(series: pd.Series) -> pd.Series:
        maximum = float(np.nanmax(np.abs(series.to_numpy(dtype=float))))
        if not np.isfinite(maximum) or maximum == 0:
            return pd.Series(np.zeros(len(series)), index=series.index, dtype=float)
        return series.astype(float) / maximum

    subset["peak-normalized fluorescence values"] = subset.groupby("well")["value"].transform(peak_normalize)
    subset = subset.rename(columns={"value": "raw fluorescence values"})
    order = {well: index for index, well in enumerate(canonical_wells)}
    subset["_well_order"] = subset["well"].map(order)
    return (
        subset.sort_values(["_well_order", "wavelength_nm"])
        .drop(columns="_well_order")
        .reset_index(drop=True)
    )


def safe_filename(group_name: str) -> str:
    safe = "".join(character if character.isalnum() or character in "-_" else "_" for character in group_name)
    safe = "_".join(part for part in safe.split("_") if part)
    return safe or "group"


def export_group_csv(data: pd.DataFrame, assignment: GroupAssignment, output_directory: Union[str, Path]) -> Path:
    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{safe_filename(assignment.name)}.csv"
    build_group_dataframe(data, assignment).to_csv(output_path, index=False)
    return output_path
