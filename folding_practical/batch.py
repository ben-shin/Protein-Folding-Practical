"""One-shot batch export: plate CSVs plus a group map in, per-group CSVs out.

This is the path that does not need the plate map at all. Point it at every
plate reading, point it at the group map, choose a folder, and every practical
group gets its files written in one pass.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, Union

import numpy as np
import pandas as pd

from .plate_io import load_plate_csvs
from .project import (
    GroupAssignment,
    export_group_csv,
    export_group_spectrum_csv,
    inspect_group_map,
    load_group_map_assignments,
)

DEFAULT_CONCENTRATION_START = 0.0
DEFAULT_CONCENTRATION_STOP = 6.0

PathLike = Union[str, Path]
ProgressCallback = Callable[[int, int, str], None]


@dataclass
class BatchExportResult:
    """What a batch run produced, including the groups it could not write."""

    output_directory: Path
    plate_ids: list[str] = field(default_factory=list)
    concentrations: list[float] = field(default_factory=list)
    spectrum_files: dict[str, Path] = field(default_factory=dict)
    curve_files: dict[str, Path] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    data: pd.DataFrame = field(default_factory=pd.DataFrame)
    assignments: dict[str, GroupAssignment] = field(default_factory=dict)

    @property
    def written_count(self) -> int:
        return len(self.spectrum_files) + len(self.curve_files)

    @property
    def group_count(self) -> int:
        return len(set(self.spectrum_files) | set(self.curve_files) | set(self.failures))

    def summary(self) -> str:
        parts = [
            f"{self.group_count} group(s) from {len(self.plate_ids)} plate(s)",
            f"{self.written_count} file(s) written to {self.output_directory}",
        ]
        if self.failures:
            parts.append(f"{len(self.failures)} group(s) failed")
        return " — ".join(parts)


def default_concentrations_for(
    count: int,
    start: float = DEFAULT_CONCENTRATION_START,
    stop: float = DEFAULT_CONCENTRATION_STOP,
) -> list[float]:
    """Evenly spaced concentrations for a group of ``count`` conditions."""
    if count < 1:
        raise ValueError("A group needs at least one condition")
    return [float(value) for value in np.linspace(float(start), float(stop), count)]


def resolve_batch_concentrations(
    group_map_path: PathLike,
    concentrations: Optional[Sequence[float]] = None,
    *,
    start: float = DEFAULT_CONCENTRATION_START,
    stop: float = DEFAULT_CONCENTRATION_STOP,
) -> list[float]:
    """Decide which concentrations the batch should fall back to.

    An explicit list always wins. Otherwise a concentrations column in the map
    supplies them per group, and failing that the range is spread evenly over
    however many wells each group has.
    """
    if concentrations:
        return [float(value) for value in concentrations]

    info = inspect_group_map(group_map_path)
    if not info["missing_concentrations"]:
        return []
    if not info["same_count"]:
        counts = sorted(set(info["counts"]))  # type: ignore[arg-type]
        raise ValueError(
            "The groups do not all have the same number of wells "
            f"({', '.join(str(count) for count in counts)}). Add a concentrations "
            "column to the group map, or pass an explicit concentration list"
        )
    return default_concentrations_for(int(info["well_count"]), start, stop)


def _without_group_map(plate_paths: Iterable[PathLike], group_map_path: PathLike) -> list[Path]:
    """Drop the group map if a wildcard swept it into the plate selection."""
    group_map = Path(group_map_path).resolve()
    kept: list[Path] = []
    for path in plate_paths:
        candidate = Path(path)
        try:
            if candidate.resolve() == group_map:
                continue
        except OSError:
            pass
        kept.append(candidate)
    return kept


def run_batch_export(
    plate_paths: Iterable[PathLike],
    group_map_path: PathLike,
    output_directory: PathLike,
    *,
    concentrations: Optional[Sequence[float]] = None,
    start: float = DEFAULT_CONCENTRATION_START,
    stop: float = DEFAULT_CONCENTRATION_STOP,
    include_spectra: bool = True,
    include_curves: bool = True,
    measurement: str = "",
    wavelength_nm: Optional[float] = None,
    progress: Optional[ProgressCallback] = None,
) -> BatchExportResult:
    """Import every plate, apply the group map, and write one CSV set per group.

    A group that cannot be written (missing wells, wrong plate, a signal with no
    emission scan) is recorded in ``failures`` instead of aborting the run, so
    one bad row in the map never costs the whole cohort its exports.
    """
    paths = _without_group_map(plate_paths, group_map_path)
    if not paths:
        raise ValueError("Select at least one plate CSV file")

    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)

    data = load_plate_csvs(paths)
    resolved = resolve_batch_concentrations(group_map_path, concentrations, start=start, stop=stop)
    row_failures: dict[str, str] = {}
    assignments: dict[str, GroupAssignment] = load_group_map_assignments(
        data,
        group_map_path,
        default_concentrations=resolved,
        default_measurement=measurement,
        default_wavelength_nm=wavelength_nm,
        failures=row_failures,
    )

    result = BatchExportResult(
        output_directory=output_dir,
        plate_ids=list(dict.fromkeys(data["plate_id"].astype(str))),
        concentrations=list(resolved),
        data=data,
        assignments=assignments,
        failures=row_failures,
    )

    total = len(assignments)
    for index, (name, assignment) in enumerate(assignments.items(), start=1):
        if progress is not None:
            progress(index, total, name)
        try:
            if include_spectra:
                result.spectrum_files[name] = export_group_spectrum_csv(data, assignment, output_dir)
            if include_curves:
                result.curve_files[name] = export_group_csv(data, assignment, output_dir)
        except Exception as exc:
            result.failures[name] = str(exc)
    return result


def _parse_concentration_list(text: str) -> list[float]:
    tokens = [token for token in text.replace(";", ",").replace(" ", ",").split(",") if token]
    return [float(token) for token in tokens]


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="protein-folding-batch",
        description=(
            "Export one CSV per practical group from a set of plate reader files "
            "and a group map, without opening the desktop app."
        ),
    )
    parser.add_argument("--plates", nargs="+", required=True, metavar="CSV", help="Plate reader CSV exports")
    parser.add_argument("--groups", required=True, metavar="CSV", help="Group map CSV")
    parser.add_argument("--out", required=True, metavar="DIR", help="Output directory")
    parser.add_argument(
        "--concentrations",
        default="",
        metavar="LIST",
        help="Explicit GuHCl concentrations, for example \"0,0.4,0.8\"",
    )
    parser.add_argument(
        "--range",
        nargs=2,
        type=float,
        default=(DEFAULT_CONCENTRATION_START, DEFAULT_CONCENTRATION_STOP),
        metavar=("START", "STOP"),
        help="Concentration range to spread evenly when no list is given (default: 0 6)",
    )
    parser.add_argument("--measurement", default="", help="Signal name, when a plate holds more than one")
    parser.add_argument("--wavelength", type=float, default=None, help="Emission wavelength for the denaturation curve")
    parser.add_argument("--no-spectra", action="store_true", help="Skip the wavelength-by-concentration tables")
    parser.add_argument("--no-curves", action="store_true", help="Skip the three-column denaturation tables")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_argument_parser()
    arguments = parser.parse_args(argv)
    try:
        result = run_batch_export(
            arguments.plates,
            arguments.groups,
            arguments.out,
            concentrations=_parse_concentration_list(arguments.concentrations),
            start=arguments.range[0],
            stop=arguments.range[1],
            include_spectra=not arguments.no_spectra,
            include_curves=not arguments.no_curves,
            measurement=arguments.measurement,
            wavelength_nm=arguments.wavelength,
            progress=lambda index, total, name: print(f"[{index}/{total}] {name}"),
        )
    except Exception as exc:
        parser.exit(status=2, message=f"{parser.prog}: {exc}\n")

    print(result.summary())
    for name, message in result.failures.items():
        print(f"  failed: {name}: {message}")
    return 1 if result.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
