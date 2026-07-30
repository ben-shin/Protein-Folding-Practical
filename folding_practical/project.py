"""Group assignment and export logic for denaturation series."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd

from .wells import normalize_well

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
