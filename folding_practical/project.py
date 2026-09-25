"""Group assignment and export logic for denaturation series."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Iterable, Mapping, MutableMapping, Optional, Union

import numpy as np
import pandas as pd

from .wells import expand_well_spec, normalize_well

EXPORT_COLUMNS = [
    "GuHCl concentration (M)",
    "raw fluorescence values",
    "normalized fluorescence values",
]

SPECTRUM_WAVELENGTH_COLUMN = "wavelength (nm)"
REPEAT_POLICIES = {"reject", "select", "mean"}
_REPEAT_POLICY_ALIASES = {
    "reject": "reject",
    "select": "select",
    "select_read": "select",
    "read": "select",
    "mean": "mean",
    "replicate_mean": "mean",
    "declared_replicate_mean": "mean",
}


def _canonical_repeat_policy(value: object) -> str:
    key = re.sub(r"[^a-z]+", "_", str(value).strip().lower()).strip("_")
    try:
        return _REPEAT_POLICY_ALIASES[key]
    except KeyError as exc:
        raise ValueError("repeat_policy must be 'reject', 'select', or 'mean'") from exc


@dataclass
class GroupAssignment:
    name: str
    plate_id: str
    wells: list[str]
    concentrations: list[float]
    measurement: str
    wavelength_nm: Optional[float] = None
    repeat_policy: str = "reject"
    acquisition_id: Optional[str] = None

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
        self.repeat_policy = _canonical_repeat_policy(self.repeat_policy)
        if self.acquisition_id is not None:
            self.acquisition_id = str(self.acquisition_id).strip() or None
        if self.repeat_policy == "select" and self.acquisition_id is None:
            raise ValueError("repeat_policy='select' requires an acquisition_id")
        if self.repeat_policy != "select" and self.acquisition_id is not None:
            raise ValueError("acquisition_id is only valid with repeat_policy='select'")


_GROUP_MAP_ALIASES = {
    "group": {"group", "groupname", "practicalgroup", "practicalgroupname"},
    "plate": {"plate", "plateid", "platenumber", "platefile"},
    "wells": {"wells", "wellrange", "wellranges", "wellspec", "wellspecification"},
    "measurement": {"measurement", "signal", "readout"},
    "wavelength": {"wavelength", "wavelengthnm", "emissionwavelength", "emissionwavelengthnm"},
    "repeat_policy": {"repeatpolicy", "repeat", "replicatepolicy", "replicates"},
    "acquisition_id": {"acquisition", "acquisitionid", "read", "readid"},
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
        # Spectrum plates repeat each source filename for every well and
        # wavelength. Resolve each distinct filename once per plate lookup.
        for source in sources.astype(str).unique():
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
    explicit = requested.strip()
    if explicit:
        matches = [value for value in available if value.lower() == explicit.lower()]
        if len(matches) == 1:
            return matches[0]
        choices = ", ".join(available) or "none"
        raise ValueError(
            f"Requested measurement {explicit!r} is unavailable on plate {plate_id!r}. "
            f"Available measurements: {choices}"
        )

    fallback = default.strip()
    if fallback:
        matches = [value for value in available if value.lower() == fallback.lower()]
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
    explicit = requested.strip()
    if "wavelength_nm" not in data.columns:
        if explicit:
            raise ValueError(
                f"Requested wavelength {explicit!r} nm is unavailable because the input "
                "has no wavelength-resolved measurements"
            )
        return None
    subset = data.loc[
        (data["plate_id"].astype(str) == plate_id)
        & (data["measurement"].astype(str) == measurement),
        ["wavelength_nm", "value"],
    ].copy()
    subset["wavelength_nm"] = pd.to_numeric(subset["wavelength_nm"], errors="coerce")
    subset = subset.loc[subset["wavelength_nm"].notna()]
    available = sorted(subset["wavelength_nm"].unique().tolist())
    if not available:
        if explicit:
            raise ValueError(
                f"Requested wavelength {explicit!r} nm is unavailable for measurement "
                f"{measurement!r} on plate {plate_id!r}"
            )
        return None

    if explicit:
        try:
            candidate = float(explicit)
        except ValueError as exc:
            raise ValueError(f"Requested wavelength {explicit!r} is not numeric") from exc
        matches = [value for value in available if np.isclose(value, candidate)]
        if matches:
            return float(matches[0])
        choices = ", ".join(f"{value:g}" for value in available)
        raise ValueError(
            f"Requested wavelength {candidate:g} nm is unavailable for measurement "
            f"{measurement!r} on plate {plate_id!r}. Available wavelengths: {choices} nm"
        )

    candidates: list[float] = []
    if default is not None:
        candidates.append(float(default))
    candidates.append(508.0)

    for candidate in candidates:
        matches = [value for value in available if np.isclose(value, candidate)]
        if matches:
            return float(matches[0])
    if len(available) == 1:
        return float(available[0])

    # Neither the requested wavelength nor the usual GFP emission peak is in
    # this scan, so read the curve at the brightest wavelength on the plate.
    # For an emission scan that is the emission maximum, and the choice is
    # shown in the group table so it can be checked.
    means = subset.groupby("wavelength_nm")["value"].mean()
    return float(means.idxmax())


def _parse_concentrations(value: str, default: list[float]) -> list[float]:
    if not value.strip():
        return [float(item) for item in default]
    tokens = [token for token in re.split(r"[,;|\s]+", value.strip()) if token]
    return [float(token) for token in tokens]


def inspect_group_map(path: Union[str, Path]) -> dict[str, object]:
    """Summarise a group map without needing plate data to be loaded.

    Returns the number of groups, the well count of each group, whether every
    group has the same number of wells, and whether concentrations are still
    missing. The GUI uses this to ask for a concentration list up front, and
    the batch exporter uses it to pick a sensible default.
    """
    table = pd.read_csv(path, dtype=str, keep_default_na=False)
    if table.empty:
        raise ValueError("The group map is empty")

    columns = list(table.columns)
    wells_column = _find_group_map_column(columns, "wells")
    if wells_column is None:
        raise ValueError("The group map is missing the well ranges column")
    group_column = _find_group_map_column(columns, "group")
    concentration_column = _find_group_map_column(columns, "concentrations")

    counts: list[int] = []
    names: list[str] = []
    for row_number, (_, row) in enumerate(table.iterrows(), start=2):
        try:
            counts.append(len(expand_well_spec(str(row[wells_column]))))
        except Exception as exc:
            raise ValueError(f"Group map row {row_number}: {exc}") from exc
        names.append(str(row[group_column]).strip() if group_column is not None else "")

    missing_concentrations = concentration_column is None or any(
        not str(value).strip() for value in table[concentration_column]
    )
    unique_counts = sorted(set(counts))
    return {
        "group_count": len(counts),
        "names": names,
        "counts": counts,
        "same_count": len(unique_counts) == 1,
        "well_count": unique_counts[0] if len(unique_counts) == 1 else None,
        "missing_concentrations": missing_concentrations,
    }


def load_group_map_assignments(
    data: pd.DataFrame,
    path: Union[str, Path],
    *,
    default_concentrations: list[float],
    default_measurement: str = "",
    default_wavelength_nm: Optional[float] = None,
    existing_assignments: Optional[Mapping[str, GroupAssignment]] = None,
    failures: Optional[MutableMapping[str, str]] = None,
) -> dict[str, GroupAssignment]:
    """Load practical groups from a simple CSV map.

    Pass a ``failures`` mapping to keep going when a row cannot be read — the
    reason is recorded against the group name and the remaining rows still
    load. Without it the first bad row raises, which is what a single manual
    import wants but not what a cohort-wide batch wants.
    """
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
    repeat_policy_column = _find_group_map_column(columns, "repeat_policy")
    acquisition_column = _find_group_map_column(columns, "acquisition_id")
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
        name = ""
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
                repeat_policy=(
                    str(row[repeat_policy_column]).strip()
                    if repeat_policy_column is not None and str(row[repeat_policy_column]).strip()
                    else "reject"
                ),
                acquisition_id=(
                    str(row[acquisition_column]).strip()
                    if acquisition_column is not None and str(row[acquisition_column]).strip()
                    else None
                ),
            )
            build_group_dataframe(data, assignment)
            for well in assignment.wells:
                previous = occupied.get((plate_id, well))
                if previous is not None:
                    raise ValueError(f"Well {well} is already assigned to {previous!r}")
            for well in assignment.wells:
                occupied[(plate_id, well)] = name
            assignments[name] = assignment
        except Exception as exc:
            if failures is None:
                raise ValueError(f"Group map row {row_number}: {exc}") from exc
            failures[name or f"row {row_number}"] = str(exc)

    return assignments


def normalize_fluorescence(values: Union[np.ndarray, pd.Series]) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    result = np.full(array.shape, np.nan, dtype=float)
    finite = np.isfinite(array)
    if not finite.any():
        return result
    minimum = float(np.min(array[finite]))
    maximum = float(np.max(array[finite]))
    span = maximum - minimum
    if not np.isfinite(span) or span <= 0:
        return result
    result[finite] = (array[finite] - minimum) / span
    return result


def _summarize_repeats(
    subset: pd.DataFrame,
    *,
    keys: list[str],
    repeat_policy: str,
    acquisition_id: Optional[str],
    context: str,
) -> pd.DataFrame:
    """Apply one explicit repeat policy and return value/n/std summaries."""
    policy = _canonical_repeat_policy(repeat_policy)

    working = subset.copy()
    working["value"] = pd.to_numeric(working["value"], errors="coerce")
    if "acquisition_id" not in working.columns:
        working["acquisition_id"] = "unspecified"
        has_declared_acquisitions = False
    else:
        working["acquisition_id"] = working["acquisition_id"].fillna("unspecified").astype(str)
        has_declared_acquisitions = True

    if has_declared_acquisitions:
        within_acquisition = working.groupby(
            keys + ["acquisition_id"], sort=False, dropna=False
        ).size()
        duplicated_within = within_acquisition[within_acquisition > 1]
        if not duplicated_within.empty:
            raise ValueError(
                f"{context} contains duplicate observations inside one acquisition; "
                "fix the import rather than averaging them as independent replicates"
            )

    if policy == "select":
        if not has_declared_acquisitions:
            raise ValueError(f"{context} cannot select a read because acquisition_id is unavailable")
        requested = str(acquisition_id or "").strip()
        available = list(dict.fromkeys(working["acquisition_id"].astype(str)))
        if requested not in available:
            choices = ", ".join(available) or "none"
            raise ValueError(
                f"Requested acquisition {requested!r} is unavailable for {context}. "
                f"Available acquisitions: {choices}"
            )
        working = working.loc[working["acquisition_id"] == requested].copy()

    counts = working.groupby(keys, sort=False, dropna=False).size()
    repeated = counts[counts > 1]
    if policy in {"reject", "select"} and not repeated.empty:
        examples = ", ".join(
            "/".join(str(part) for part in (key if isinstance(key, tuple) else (key,)))
            for key in repeated.index[:5]
        )
        if policy == "reject":
            acquisitions = ", ".join(dict.fromkeys(working["acquisition_id"].astype(str)))
            raise ValueError(
                f"{context} has repeated observations ({examples}); choose repeat_policy='select' "
                f"with an acquisition_id or repeat_policy='mean'. Acquisitions: {acquisitions}"
            )
        raise ValueError(f"Acquisition {acquisition_id!r} contains duplicate observations for {examples}")

    if policy == "mean":
        summary = (
            working.groupby(keys, sort=False, dropna=False)["value"]
            .agg(value="mean", n="count", std="std")
            .reset_index()
        )
        acquisition_lists = (
            working.groupby(keys, sort=False, dropna=False)["acquisition_id"]
            .agg(lambda values: ",".join(dict.fromkeys(values.astype(str))))
            .reset_index(name="acquisition_ids")
        )
        return summary.merge(acquisition_lists, on=keys, how="left", validate="one_to_one")

    working["n"] = working["value"].notna().astype(int)
    working["std"] = np.nan
    working["acquisition_ids"] = working["acquisition_id"]
    return working[keys + ["value", "n", "std", "acquisition_ids"]].reset_index(drop=True)


def summarize_group_observations(data: pd.DataFrame, assignment: GroupAssignment) -> pd.DataFrame:
    """Return one ordered value per group well plus explicit repeat n/std.

    Missing instrument observations remain present with ``value=NaN`` and
    ``n=0``. A well absent from the acquisition entirely is still an error.
    """
    required = {"plate_id", "well", "measurement", "value"}
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"Input data is missing columns: {', '.join(sorted(missing))}")

    plate_mask = data["plate_id"].astype(str) == str(assignment.plate_id)
    available_measurements = list(dict.fromkeys(data.loc[plate_mask, "measurement"].astype(str)))
    if assignment.measurement not in available_measurements:
        choices = ", ".join(available_measurements) or "none"
        raise ValueError(
            f"Requested measurement {assignment.measurement!r} is unavailable on plate "
            f"{assignment.plate_id!r}. Available measurements: {choices}"
        )

    mask = (
        plate_mask
        & (data["measurement"].astype(str) == str(assignment.measurement))
        & (data["well"].isin(assignment.wells))
    )
    if "wavelength_nm" in data.columns:
        wavelengths = pd.to_numeric(data["wavelength_nm"], errors="coerce")
        available_wavelengths = sorted(wavelengths.loc[mask].dropna().unique().tolist())
        if assignment.wavelength_nm is None:
            if len(available_wavelengths) > 1:
                raise ValueError(
                    f"Group {assignment.name!r} uses a wavelength-resolved signal; "
                    "select one emission wavelength before assigning the group"
                )
            if len(available_wavelengths) == 1:
                mask &= np.isclose(wavelengths, available_wavelengths[0], equal_nan=False)
        else:
            matches = [
                value for value in available_wavelengths if np.isclose(value, assignment.wavelength_nm)
            ]
            if not matches:
                choices = ", ".join(f"{value:g}" for value in available_wavelengths) or "none"
                raise ValueError(
                    f"Requested wavelength {assignment.wavelength_nm:g} nm is unavailable for "
                    f"measurement {assignment.measurement!r}. Available wavelengths: {choices}"
                )
            mask &= np.isclose(wavelengths, matches[0], equal_nan=False)
    elif assignment.wavelength_nm is not None:
        raise ValueError("Input data does not contain wavelength-resolved measurements")

    columns = ["well", "value"] + (["acquisition_id"] if "acquisition_id" in data.columns else [])
    subset = data.loc[mask, columns].copy()
    if subset.empty:
        raise ValueError(f"No measurements found for group {assignment.name!r}")
    present_wells = set(subset["well"].astype(str))
    missing_wells = [well for well in assignment.wells if well not in present_wells]
    if missing_wells:
        raise ValueError(f"Group {assignment.name!r} has no observation for wells: {', '.join(missing_wells)}")

    summary = _summarize_repeats(
        subset,
        keys=["well"],
        repeat_policy=assignment.repeat_policy,
        acquisition_id=assignment.acquisition_id,
        context=f"group {assignment.name!r}",
    )
    summarized_wells = set(summary["well"].astype(str))
    missing_after_policy = [well for well in assignment.wells if well not in summarized_wells]
    if missing_after_policy:
        detail = (
            f" in acquisition {assignment.acquisition_id!r}"
            if assignment.repeat_policy == "select"
            else " after applying the repeat policy"
        )
        raise ValueError(
            f"Group {assignment.name!r} has no observation for wells "
            f"{', '.join(missing_after_policy)}{detail}"
        )
    order = {well: index for index, well in enumerate(assignment.wells)}
    summary["_well_order"] = summary["well"].map(order)
    return summary.sort_values("_well_order").drop(columns="_well_order").reset_index(drop=True)


def build_group_dataframe(data: pd.DataFrame, assignment: GroupAssignment) -> pd.DataFrame:
    """Build the exact three-column export requested for one practical group."""
    summary = summarize_group_observations(data, assignment)
    raw = summary["value"].to_numpy(dtype=float)
    output = pd.DataFrame(
        {
            EXPORT_COLUMNS[0]: assignment.concentrations,
            EXPORT_COLUMNS[1]: raw,
            EXPORT_COLUMNS[2]: normalize_fluorescence(raw),
        }
    )
    output.attrs["repeat_policy"] = assignment.repeat_policy
    output.attrs["repeat_statistics"] = summary[
        ["well", "n", "std", "acquisition_ids"]
    ].copy()
    return output


def summarize_spectrum_observations(
    data: pd.DataFrame,
    *,
    plate_id: str,
    measurement: str,
    wells: list[str],
    repeat_policy: str = "reject",
    acquisition_id: Optional[str] = None,
) -> pd.DataFrame:
    """Return acquisition-aware wavelength values with repeat n/std."""
    policy = _canonical_repeat_policy(repeat_policy)
    if policy == "select" and not str(acquisition_id or "").strip():
        raise ValueError("repeat_policy='select' requires an acquisition_id")
    required = {"plate_id", "well", "measurement", "wavelength_nm", "value"}
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"Input data is missing columns: {', '.join(sorted(missing))}")

    canonical_wells = [normalize_well(well) for well in wells]
    if not canonical_wells:
        raise ValueError("Select at least one well")

    plate_mask = data["plate_id"].astype(str) == str(plate_id)
    available_measurements = list(dict.fromkeys(data.loc[plate_mask, "measurement"].astype(str)))
    if measurement not in available_measurements:
        choices = ", ".join(available_measurements) or "none"
        raise ValueError(
            f"Requested measurement {measurement!r} is unavailable on plate {plate_id!r}. "
            f"Available measurements: {choices}"
        )

    wavelengths = pd.to_numeric(data["wavelength_nm"], errors="coerce")
    columns = ["well", "wavelength_nm", "value"] + (
        ["acquisition_id"] if "acquisition_id" in data.columns else []
    )
    subset = data.loc[
        plate_mask
        & (data["measurement"].astype(str) == str(measurement))
        & (data["well"].isin(canonical_wells))
        & wavelengths.notna(),
        columns,
    ].copy()
    if subset.empty:
        raise ValueError("No wavelength-resolved data found for the selected wells")

    subset["wavelength_nm"] = pd.to_numeric(subset["wavelength_nm"], errors="raise")
    missing_wells = [well for well in canonical_wells if well not in set(subset["well"])]
    if missing_wells:
        raise ValueError(f"No spectrum found for wells: {', '.join(missing_wells)}")

    summary = _summarize_repeats(
        subset,
        keys=["well", "wavelength_nm"],
        repeat_policy=policy,
        acquisition_id=acquisition_id,
        context=f"spectrum {measurement!r} on plate {plate_id!r}",
    )
    summarized_wells = set(summary["well"].astype(str))
    missing_after_policy = [well for well in canonical_wells if well not in summarized_wells]
    if missing_after_policy:
        detail = f" in acquisition {acquisition_id!r}" if policy == "select" else ""
        raise ValueError(f"No spectrum found for wells: {', '.join(missing_after_policy)}{detail}")
    order = {well: index for index, well in enumerate(canonical_wells)}
    summary["_well_order"] = summary["well"].map(order)
    return summary.sort_values(["_well_order", "wavelength_nm"]).drop(
        columns="_well_order"
    ).reset_index(drop=True)


def build_spectrum_dataframe(
    data: pd.DataFrame,
    *,
    plate_id: str,
    measurement: str,
    wells: list[str],
    repeat_policy: str = "reject",
    acquisition_id: Optional[str] = None,
) -> pd.DataFrame:
    """Return wavelength-resolved fluorescence for selected wells.

    Repeated reads must be rejected, selected by acquisition ID, or explicitly
    declared replicates and averaged. Peak normalization is performed per well.
    """
    policy = _canonical_repeat_policy(repeat_policy)
    subset = summarize_spectrum_observations(
        data,
        plate_id=plate_id,
        measurement=measurement,
        wells=wells,
        repeat_policy=policy,
        acquisition_id=acquisition_id,
    )

    def peak_normalize(series: pd.Series) -> pd.Series:
        array = series.to_numpy(dtype=float)
        finite = np.isfinite(array)
        if not finite.any() or np.unique(array[finite]).size <= 1:
            return pd.Series(np.nan, index=series.index, dtype=float)
        maximum = float(np.max(np.abs(array[finite])))
        if not np.isfinite(maximum) or maximum == 0:
            return pd.Series(np.nan, index=series.index, dtype=float)
        return series.astype(float) / maximum

    subset["peak-normalized fluorescence values"] = subset.groupby("well")["value"].transform(peak_normalize)
    subset = subset.rename(columns={"value": "raw fluorescence values"})
    output = subset.reset_index(drop=True)
    output.attrs["repeat_policy"] = policy
    output.attrs["repeat_statistics"] = output[
        ["well", "wavelength_nm", "n", "std", "acquisition_ids"]
    ].copy()
    return output


def concentration_labels(concentrations: Iterable[float]) -> list[str]:
    """Turn a concentration list into CSV column headers such as ``0M``, ``0.4M``.

    Repeated concentrations (replicates within one group) are numbered so the
    header row stays unique and pandas does not silently merge the columns.
    """
    labels: list[str] = []
    seen: dict[str, int] = {}
    for value in concentrations:
        base = f"{float(value):g}M"
        seen[base] = seen.get(base, 0) + 1
        labels.append(base if seen[base] == 1 else f"{base} ({seen[base]})")
    return labels


def build_group_spectrum_matrix(data: pd.DataFrame, assignment: GroupAssignment) -> pd.DataFrame:
    """Return one wavelength-by-concentration table for a practical group.

    Rows are emission wavelengths in ascending order. Columns are the group's
    conditions in assignment order, labelled by GuHCl concentration, so the
    export reads ``wavelength (nm), 0M, 0.4M, 0.8M, ...``.
    """
    try:
        subset = summarize_spectrum_observations(
            data,
            plate_id=assignment.plate_id,
            measurement=assignment.measurement,
            wells=assignment.wells,
            repeat_policy=assignment.repeat_policy,
            acquisition_id=assignment.acquisition_id,
        )
    except ValueError as exc:
        if "No wavelength-resolved data" in str(exc):
            raise ValueError(
                f"Group {assignment.name!r} has no wavelength-resolved data. "
                f"Signal {assignment.measurement!r} on plate {assignment.plate_id!r} "
                "is a single readout, not an emission scan"
            ) from exc
        raise

    wide = subset.pivot(index="wavelength_nm", columns="well", values="value").sort_index()
    output = pd.DataFrame({SPECTRUM_WAVELENGTH_COLUMN: wide.index.to_numpy(dtype=float)})
    for label, well in zip(concentration_labels(assignment.concentrations), assignment.wells):
        output[label] = wide[well].to_numpy(dtype=float)
    output.attrs["repeat_policy"] = assignment.repeat_policy
    output.attrs["repeat_statistics"] = subset[
        ["well", "wavelength_nm", "n", "std", "acquisition_ids"]
    ].copy()
    return output


def export_group_spectrum_csv(
    data: pd.DataFrame,
    assignment: GroupAssignment,
    output_directory: Union[str, Path],
    *,
    suffix: str = "_spectra",
    collision_policy: str = "suffix",
) -> Path:
    """Write ``<group><suffix>.csv`` holding the wavelength-by-concentration table."""
    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    return _write_group_export(
        build_group_spectrum_matrix(data, assignment),
        output_dir,
        assignment.name,
        suffix=suffix,
        collision_policy=collision_policy,
    )


def safe_filename(group_name: str) -> str:
    safe = "".join(character if character.isalnum() or character in "-_" else "_" for character in group_name)
    safe = "_".join(part for part in safe.split("_") if part)
    base = safe or "group"
    if base != group_name:
        # Sanitisation is lossy (for example, both ``Group/A`` and ``Group:A``
        # become ``Group_A``).  Keep the readable stem but add a stable identity
        # so curve and spectrum exports cannot be paired with a different group.
        identity = hashlib.sha256(group_name.casefold().encode("utf-8")).hexdigest()[:8]
        return f"{base}_{identity}"
    return base


def _group_export_path(
    output_directory: Path,
    group_name: str,
    *,
    suffix: str,
    collision_policy: str,
) -> Path:
    """Reserve a case-insensitive-safe filename without overwriting a file."""
    if collision_policy not in {"suffix", "error"}:
        raise ValueError("collision_policy must be 'suffix' or 'error'")
    if any(character in suffix for character in ("/", "\\")):
        raise ValueError("Export suffix cannot contain a path separator")

    base = safe_filename(group_name)
    existing = [path for path in output_directory.iterdir() if path.is_file()]
    existing_names = {path.name.casefold(): path.name for path in existing}
    claimed_stems: list[str] = []
    for path in existing:
        if path.suffix.casefold() != ".csv":
            continue
        stem = path.stem
        if stem.casefold().endswith("_spectra"):
            stem = stem[: -len("_spectra")]
        if suffix and stem.casefold().endswith(suffix.casefold()):
            stem = stem[: -len(suffix)]
        claimed_stems.append(stem)

    number = 1
    while True:
        group_stem = base if number == 1 else f"{base}_{number}"
        filename = f"{group_stem}{suffix}.csv"
        existing_name = existing_names.get(filename.casefold())
        casefold_claims = [stem for stem in claimed_stems if stem.casefold() == group_stem.casefold()]
        exact_claim = group_stem in casefold_claims

        if existing_name is not None:
            if collision_policy == "error":
                raise FileExistsError(
                    f"Refusing to overwrite existing export {existing_name!r} in {output_directory}"
                )
            number += 1
            continue
        if casefold_claims and not exact_claim:
            if collision_policy == "error":
                raise FileExistsError(
                    f"Group name {group_name!r} collides case-insensitively with "
                    f"existing export stem {casefold_claims[0]!r}"
                )
            number += 1
            continue
        return output_directory / filename


def _write_group_export(
    frame: pd.DataFrame,
    output_directory: Path,
    group_name: str,
    *,
    suffix: str,
    collision_policy: str,
) -> Path:
    """Claim an export path exclusively, retrying suffix allocation after a race."""
    while True:
        output_path = _group_export_path(
            output_directory,
            group_name,
            suffix=suffix,
            collision_policy=collision_policy,
        )
        try:
            handle = output_path.open("x", encoding="utf-8", newline="")
        except FileExistsError as exc:
            if collision_policy == "error":
                raise FileExistsError(
                    f"Refusing to overwrite existing export {output_path.name!r} "
                    f"in {output_directory}"
                ) from exc
            continue
        try:
            with handle:
                frame.to_csv(handle, index=False)
        except BaseException:
            output_path.unlink(missing_ok=True)
            raise
        return output_path


def export_group_csv(
    data: pd.DataFrame,
    assignment: GroupAssignment,
    output_directory: Union[str, Path],
    *,
    collision_policy: str = "suffix",
) -> Path:
    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    return _write_group_export(
        build_group_dataframe(data, assignment),
        output_dir,
        assignment.name,
        suffix="",
        collision_policy=collision_policy,
    )
