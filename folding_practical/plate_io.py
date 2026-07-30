"""Import CLARIOstar and generic 96-well CSV exports into a tidy table."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterable, Optional, Union

import numpy as np
import pandas as pd

from .wells import PLATE_ROWS, normalize_well, well_sort_key

_WELL_HEADER_ALIASES = {
    "well",
    "well id",
    "wellid",
    "well position",
    "position",
}
_METADATA_COLUMN_HINTS = {
    "row",
    "column",
    "col",
    "content",
    "sample",
    "sample id",
    "sample name",
    "name",
    "group",
}

_SPECTRUM_HEADER_RE = re.compile(
    r"Raw\s+Data\s*\(Em\s+Spectrum\)\s*"
    r"(?P<excitation>\d+(?:\.\d+)?)\s*-\s*(?P<excitation_bandwidth>\d+(?:\.\d+)?)\s*/\s*"
    r"(?P<emission>\d+(?:\.\d+)?)\s*-\s*(?P<emission_bandwidth>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

_TIDY_COLUMNS = [
    "plate_id",
    "source_file",
    "well",
    "row",
    "column",
    "measurement",
    "wavelength_nm",
    "excitation_nm",
    "value",
]


def _read_rectangular_csv(path: Path) -> pd.DataFrame:
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            errors.append(str(exc))
            continue

        sample = text[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ","

        rows = list(csv.reader(text.splitlines(), delimiter=delimiter))
        if not rows:
            raise ValueError(f"{path.name} is empty")
        width = max(len(row) for row in rows)
        padded = [row + [""] * (width - len(row)) for row in rows]
        return pd.DataFrame(padded)

    raise ValueError(f"Could not decode {path.name}: {'; '.join(errors)}")


def _clean_header(value: object, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(value).strip())
    return text or fallback


def _numeric_ratio(series: pd.Series) -> float:
    cleaned = series.astype(str).str.strip().replace("", np.nan)
    nonempty = cleaned.notna().sum()
    if nonempty == 0:
        return 0.0
    converted = pd.to_numeric(cleaned.str.replace(",", ".", regex=False), errors="coerce")
    return float(converted.notna().sum() / nonempty)


def _canonical_long_table(
    *,
    source_file: str,
    plate_id: str,
    wells: pd.Series,
    measurement_frame: pd.DataFrame,
) -> pd.DataFrame:
    records: list[pd.DataFrame] = []
    canonical_wells: list[Optional[str]] = []
    for value in wells:
        try:
            canonical_wells.append(normalize_well(str(value)))
        except ValueError:
            canonical_wells.append(None)

    well_series = pd.Series(canonical_wells, index=wells.index, dtype="object")
    for measurement in measurement_frame.columns:
        values = pd.to_numeric(
            measurement_frame[measurement].astype(str).str.strip().str.replace(",", ".", regex=False),
            errors="coerce",
        )
        valid = well_series.notna() & values.notna()
        if not valid.any():
            continue
        part = pd.DataFrame(
            {
                "plate_id": plate_id,
                "source_file": source_file,
                "well": well_series.loc[valid].astype(str),
                "measurement": str(measurement),
                "value": values.loc[valid].astype(float),
            }
        )
        records.append(part)

    if not records:
        return pd.DataFrame(columns=_TIDY_COLUMNS)

    output = pd.concat(records, ignore_index=True)
    output["row"] = output["well"].str[0]
    output["column"] = output["well"].str[1:].astype(int)
    output["wavelength_nm"] = np.nan
    output["excitation_nm"] = np.nan
    output = output[_TIDY_COLUMNS]
    output = output.sort_values(
        ["measurement", "well"],
        key=lambda column: column.map(well_sort_key) if column.name == "well" else column,
    ).reset_index(drop=True)
    return output


def _parse_clariostar_emission_spectrum(
    raw: pd.DataFrame,
    source_file: str,
    plate_id: str,
) -> Optional[pd.DataFrame]:
    """Parse CLARIOstar repeated 8x12 emission-spectrum blocks.

    A CLARIOstar spectrum export contains one complete plate grid per emission
    wavelength, for example ``Raw Data (Em Spectrum) 472-16 / 500-10``.
    The first number is the excitation wavelength and the second is the
    emission wavelength. Keeping wavelength as its own numeric column avoids
    silently collapsing the file to the final grid.
    """

    records: list[dict[str, object]] = []
    for header_index in range(len(raw)):
        header_text = " ".join(str(value).strip() for value in raw.iloc[header_index] if str(value).strip())
        match = _SPECTRUM_HEADER_RE.search(header_text)
        if not match:
            continue

        excitation = float(match.group("excitation"))
        emission = float(match.group("emission"))
        measurement = f"Emission spectrum (Ex {excitation:g} nm)"

        # the column-number row is usually right after the heading.
        # search a few rows forward to allow small header differences.
        column_header_index: Optional[int] = None
        column_numbers: list[int] = []
        for candidate_index in range(header_index + 1, min(header_index + 5, len(raw))):
            candidate = [str(value).strip() for value in raw.iloc[candidate_index].tolist()]
            parsed_columns: list[int] = []
            for value in candidate:
                try:
                    number = int(float(value))
                except (TypeError, ValueError):
                    continue
                if 1 <= number <= 12:
                    parsed_columns.append(number)
            if len(parsed_columns) >= 3:
                column_header_index = candidate_index
                column_numbers = parsed_columns[:12]
                break
        if column_header_index is None:
            continue

        found_rows = 0
        for row_index in range(column_header_index + 1, min(column_header_index + 12, len(raw))):
            values = [str(value).strip() for value in raw.iloc[row_index].tolist()]
            row_label_position = next(
                (
                    index
                    for index, value in enumerate(values)
                    if len(value) == 1 and value.upper() in PLATE_ROWS
                ),
                None,
            )
            if row_label_position is None:
                if found_rows:
                    break
                continue

            row_letter = values[row_label_position].upper()
            numeric_values = pd.to_numeric(
                pd.Series(values[row_label_position + 1 : row_label_position + 1 + len(column_numbers)])
                .str.replace(",", ".", regex=False),
                errors="coerce",
            )
            if int(numeric_values.notna().sum()) < 3:
                continue

            found_rows += 1
            for column_number, value in zip(column_numbers, numeric_values):
                if pd.isna(value):
                    continue
                records.append(
                    {
                        "plate_id": plate_id,
                        "source_file": source_file,
                        "well": f"{row_letter}{column_number}",
                        "row": row_letter,
                        "column": int(column_number),
                        "measurement": measurement,
                        "wavelength_nm": emission,
                        "excitation_nm": excitation,
                        "value": float(value),
                    }
                )

    if not records:
        return None

    output = pd.DataFrame.from_records(records, columns=_TIDY_COLUMNS)
    output = output.drop_duplicates(
        subset=["plate_id", "measurement", "wavelength_nm", "well"],
        keep="last",
    )
    output["_well_order"] = output["well"].map(well_sort_key)
    output = output.sort_values(["measurement", "wavelength_nm", "_well_order"]).drop(columns="_well_order")
    return output.reset_index(drop=True)


def _parse_long_format(raw: pd.DataFrame, source_file: str, plate_id: str) -> Optional[pd.DataFrame]:
    for header_index in range(min(len(raw), 60)):
        header_values = [str(value).strip() for value in raw.iloc[header_index].tolist()]
        normalized = [re.sub(r"\s+", " ", value.lower()) for value in header_values]
        well_positions = [index for index, value in enumerate(normalized) if value in _WELL_HEADER_ALIASES]
        if not well_positions:
            continue

        well_column = well_positions[0]
        body_end = len(raw)
        for later_index in range(header_index + 1, len(raw)):
            later_normalized = [re.sub(r"\s+", " ", str(value).strip().lower()) for value in raw.iloc[later_index]]
            if any(value in _WELL_HEADER_ALIASES for value in later_normalized):
                body_end = later_index
                break

        body = raw.iloc[header_index + 1 : body_end].copy()
        headers: list[str] = []
        seen: dict[str, int] = {}
        for index, value in enumerate(header_values):
            base = _clean_header(value, f"Column {index + 1}")
            seen[base] = seen.get(base, 0) + 1
            headers.append(base if seen[base] == 1 else f"{base} ({seen[base]})")
        body.columns = headers

        wells = body.iloc[:, well_column]
        measurement_columns: list[str] = []
        for column_index, column_name in enumerate(body.columns):
            if column_index == well_column:
                continue
            lower_name = re.sub(r"\s+", " ", column_name.lower())
            if lower_name in _METADATA_COLUMN_HINTS:
                continue
            if _numeric_ratio(body[column_name]) >= 0.55:
                measurement_columns.append(column_name)

        if measurement_columns:
            parsed = _canonical_long_table(
                source_file=source_file,
                plate_id=plate_id,
                wells=wells,
                measurement_frame=body[measurement_columns],
            )
            if not parsed.empty:
                return parsed
    return None


def _parse_well_value_rows(raw: pd.DataFrame, source_file: str, plate_id: str) -> Optional[pd.DataFrame]:
    best_records: list[tuple[str, float]] = []
    for well_column in range(raw.shape[1]):
        records: list[tuple[str, float]] = []
        for _, row in raw.iterrows():
            try:
                well = normalize_well(str(row.iloc[well_column]))
            except ValueError:
                continue
            numeric_candidates = pd.to_numeric(
                row.iloc[well_column + 1 :].astype(str).str.replace(",", ".", regex=False),
                errors="coerce",
            ).dropna()
            if numeric_candidates.empty:
                continue
            records.append((well, float(numeric_candidates.iloc[-1])))
        if len(records) > len(best_records):
            best_records = records

    if len(best_records) < 3:
        return None
    frame = pd.DataFrame(best_records, columns=["well", "Signal"])
    return _canonical_long_table(
        source_file=source_file,
        plate_id=plate_id,
        wells=frame["well"],
        measurement_frame=frame[["Signal"]],
    )


def _parse_plate_grid(raw: pd.DataFrame, source_file: str, plate_id: str) -> Optional[pd.DataFrame]:
    records: list[tuple[str, float]] = []
    for _, row in raw.iterrows():
        row_values = [str(value).strip() for value in row.tolist()]
        row_label_index = next(
            (
                i
                for i, value in enumerate(row_values)
                if len(value) == 1 and value.upper() in PLATE_ROWS
            ),
            None,
        )
        if row_label_index is None:
            continue

        numeric = pd.to_numeric(
            pd.Series(row_values[row_label_index + 1 : 13 + row_label_index]).str.replace(",", ".", regex=False),
            errors="coerce",
        )
        valid_count = int(numeric.notna().sum())
        if valid_count < 3:
            continue
        row_letter = row_values[row_label_index].upper()
        for index, value in enumerate(numeric, start=1):
            if pd.notna(value) and index <= 12:
                records.append((f"{row_letter}{index}", float(value)))

    if len(records) < 3:
        return None
    frame = pd.DataFrame(records, columns=["well", "Signal"])
    frame = frame.drop_duplicates(subset="well", keep="last")
    return _canonical_long_table(
        source_file=source_file,
        plate_id=plate_id,
        wells=frame["well"],
        measurement_frame=frame[["Signal"]],
    )


def load_plate_csv(path: Union[str, Path], plate_id: Optional[str] = None) -> pd.DataFrame:
    """Load one CSV and return tidy measurement data.

    The returned table has one row per well and measurement, with columns
    ``plate_id``, ``source_file``, ``well``, ``row``, ``column``,
    ``measurement``, and ``value``.
    """
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)
    plate_name = plate_id or csv_path.stem
    raw = _read_rectangular_csv(csv_path)

    for parser in (
        _parse_clariostar_emission_spectrum,
        _parse_long_format,
        _parse_well_value_rows,
        _parse_plate_grid,
    ):
        parsed = parser(raw, csv_path.name, plate_name)
        if parsed is not None and not parsed.empty:
            return parsed

    raise ValueError(
        f"Could not identify well-level numeric data in {csv_path.name}. "
        "Use a CLARIOstar long export with a Well column, a plate-grid export, "
        "or a two-column Well/Value file."
    )


def load_plate_csvs(paths: Iterable[Union[str, Path]]) -> pd.DataFrame:
    """Load multiple CSVs, assigning unique plate IDs derived from file names."""
    frames: list[pd.DataFrame] = []
    used_ids: dict[str, int] = {}
    for path in paths:
        csv_path = Path(path)
        base = csv_path.stem
        used_ids[base] = used_ids.get(base, 0) + 1
        plate_id = base if used_ids[base] == 1 else f"{base}_{used_ids[base]}"
        frames.append(load_plate_csv(csv_path, plate_id=plate_id))
    if not frames:
        return pd.DataFrame(
            columns=_TIDY_COLUMNS
        )
    return pd.concat(frames, ignore_index=True)
