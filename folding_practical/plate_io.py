"""Import CLARIOstar and generic 96-well CSV exports into a tidy table."""

from __future__ import annotations

import csv
import hashlib
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
    "source_sha256",
    "source_row",
    "acquisition_id",
    "well",
    "row",
    "column",
    "measurement",
    "wavelength_nm",
    "excitation_nm",
    "value",
]


def _read_rectangular_text(text: str, source_file: str) -> pd.DataFrame:
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    rows = list(csv.reader(text.splitlines(), delimiter=delimiter))
    if not rows:
        raise ValueError(f"{source_file} is empty")
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    return pd.DataFrame(padded)


def _decode_csv_bytes(content: bytes, source_file: str) -> str:
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError as exc:
            errors.append(str(exc))
    raise ValueError(f"Could not decode {source_file}: {'; '.join(errors)}")


def _source_name(value: str) -> str:
    """Return a basename for either POSIX or browser-supplied Windows paths."""
    return str(value).replace("\\", "/").rsplit("/", 1)[-1] or "plate.csv"


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
    acquisition_id: str,
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
        # A missing instrument observation is still an observation.  Keep the
        # row with a NaN value so callers can distinguish a blank read from a
        # well that was never present in the acquisition.
        valid = well_series.notna()
        if not valid.any():
            continue
        part = pd.DataFrame(
            {
                "plate_id": plate_id,
                "source_file": source_file,
                "source_sha256": None,
                "source_row": well_series.loc[valid].index.to_series().astype(int) + 1,
                "acquisition_id": acquisition_id,
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
        ["acquisition_id", "measurement", "well"],
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
    acquisition_number = 1
    wavelengths_in_acquisition: set[float] = set()
    for header_index in range(len(raw)):
        header_text = " ".join(str(value).strip() for value in raw.iloc[header_index] if str(value).strip())
        match = _SPECTRUM_HEADER_RE.search(header_text)
        if not match:
            continue

        excitation = float(match.group("excitation"))
        emission = float(match.group("emission"))
        # A wavelength repeated later in the file starts a new scan.  Adjacent
        # wavelength blocks are one acquisition, rather than independent
        # replicates, so a complete spectrum keeps one acquisition ID.
        if emission in wavelengths_in_acquisition:
            acquisition_number += 1
            wavelengths_in_acquisition.clear()
        wavelengths_in_acquisition.add(emission)
        acquisition_id = f"spectrum_{acquisition_number}"
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
            found_rows += 1
            for column_number, value in zip(column_numbers, numeric_values):
                records.append(
                    {
                        "plate_id": plate_id,
                        "source_file": source_file,
                        "source_sha256": None,
                        "source_row": row_index + 1,
                        "acquisition_id": acquisition_id,
                        "well": f"{row_letter}{column_number}",
                        "row": row_letter,
                        "column": int(column_number),
                        "measurement": measurement,
                        "wavelength_nm": emission,
                        "excitation_nm": excitation,
                        "value": float(value) if pd.notna(value) else np.nan,
                    }
                )

    if not records:
        return None

    output = pd.DataFrame.from_records(records, columns=_TIDY_COLUMNS)
    output = output.drop_duplicates(
        subset=["plate_id", "acquisition_id", "measurement", "wavelength_nm", "well"],
        keep="last",
    )
    output["_well_order"] = output["well"].map(well_sort_key)
    output = output.sort_values(
        ["acquisition_id", "measurement", "wavelength_nm", "_well_order"]
    ).drop(columns="_well_order")
    return output.reset_index(drop=True)


def _parse_long_format(raw: pd.DataFrame, source_file: str, plate_id: str) -> Optional[pd.DataFrame]:
    sections: list[pd.DataFrame] = []
    acquisition_number = 0
    # Repeated 96-well reads can place later headers well beyond row 60.
    for header_index in range(len(raw)):
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
            # Some instruments repeat the well sequence under one header rather
            # than writing a fresh header for each read.  Split at the first
            # repeated canonical well so selecting an acquisition never silently
            # chooses one of two rows carrying the same acquisition ID.
            segment_starts = [0]
            seen_wells: set[str] = set()
            for position, value in enumerate(wells):
                try:
                    canonical_well = normalize_well(str(value))
                except ValueError:
                    continue
                if canonical_well in seen_wells:
                    segment_starts.append(position)
                    seen_wells.clear()
                seen_wells.add(canonical_well)
            segment_starts.append(len(body))

            for start, end in zip(segment_starts, segment_starts[1:]):
                segment = body.iloc[start:end]
                if segment.empty:
                    continue
                acquisition_number += 1
                parsed = _canonical_long_table(
                    source_file=source_file,
                    plate_id=plate_id,
                    acquisition_id=f"long_{acquisition_number}",
                    wells=segment.iloc[:, well_column],
                    measurement_frame=segment[measurement_columns],
                )
                if not parsed.empty:
                    sections.append(parsed)
    if not sections:
        return None
    return pd.concat(sections, ignore_index=True)[_TIDY_COLUMNS]


def _parse_well_value_rows(raw: pd.DataFrame, source_file: str, plate_id: str) -> Optional[pd.DataFrame]:
    best_records: list[tuple[str, float, int, str]] = []
    for well_column in range(raw.shape[1]):
        records: list[tuple[str, float, int, str]] = []
        acquisition_number = 1
        wells_in_acquisition: set[str] = set()
        for raw_index, row in raw.iterrows():
            try:
                well = normalize_well(str(row.iloc[well_column]))
            except ValueError:
                continue
            if well in wells_in_acquisition:
                acquisition_number += 1
                wells_in_acquisition.clear()
            wells_in_acquisition.add(well)
            numeric_candidates = pd.to_numeric(
                row.iloc[well_column + 1 :].astype(str).str.replace(",", ".", regex=False),
                errors="coerce",
            ).dropna()
            value = float(numeric_candidates.iloc[-1]) if not numeric_candidates.empty else np.nan
            records.append((well, value, int(raw_index) + 1, f"rows_{acquisition_number}"))
        if len(records) > len(best_records):
            best_records = records

    if len(best_records) < 3:
        return None
    frame = pd.DataFrame(
        best_records,
        columns=["well", "value", "source_row", "acquisition_id"],
    )
    frame["plate_id"] = plate_id
    frame["source_file"] = source_file
    frame["source_sha256"] = None
    frame["row"] = frame["well"].str[0]
    frame["column"] = frame["well"].str[1:].astype(int)
    frame["measurement"] = "Signal"
    frame["wavelength_nm"] = np.nan
    frame["excitation_nm"] = np.nan
    return frame[_TIDY_COLUMNS]


def _parse_plate_grid(raw: pd.DataFrame, source_file: str, plate_id: str) -> Optional[pd.DataFrame]:
    records: list[dict[str, object]] = []
    acquisition_number = 1
    rows_in_acquisition: set[str] = set()
    for raw_index, row in raw.iterrows():
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

        row_letter = row_values[row_label_index].upper()
        if row_letter in rows_in_acquisition:
            acquisition_number += 1
            rows_in_acquisition.clear()
        rows_in_acquisition.add(row_letter)

        column_positions: list[tuple[int, int]] = []
        # Prefer the explicit plate-column header immediately above the row;
        # this retains an empty interior cell without inventing trailing wells.
        for candidate_index in range(raw_index - 1, max(-1, raw_index - 5), -1):
            candidate = [str(value).strip() for value in raw.iloc[candidate_index].tolist()]
            if any(len(value) == 1 and value.upper() in PLATE_ROWS for value in candidate):
                continue
            parsed: list[tuple[int, int]] = []
            for position in range(row_label_index + 1, len(candidate)):
                try:
                    column_number = int(float(candidate[position]))
                except (TypeError, ValueError):
                    continue
                if 1 <= column_number <= 12:
                    parsed.append((position, column_number))
            if len(parsed) >= 3:
                column_positions = parsed[:12]
                break

        if not column_positions:
            nonempty_positions = [
                position
                for position in range(row_label_index + 1, len(row_values))
                if row_values[position]
            ]
            if len(nonempty_positions) < 3:
                continue
            last_position = min(max(nonempty_positions), row_label_index + 12)
            column_positions = [
                (position, position - row_label_index)
                for position in range(row_label_index + 1, last_position + 1)
            ]

        for position, column_number in column_positions:
            value = pd.to_numeric(
                pd.Series([row_values[position]]).str.replace(",", ".", regex=False),
                errors="coerce",
            ).iloc[0]
            records.append(
                {
                    "plate_id": plate_id,
                    "source_file": source_file,
                    "source_sha256": None,
                    "source_row": int(raw_index) + 1,
                    "acquisition_id": f"grid_{acquisition_number}",
                    "well": f"{row_letter}{column_number}",
                    "row": row_letter,
                    "column": column_number,
                    "measurement": "Signal",
                    "wavelength_nm": np.nan,
                    "excitation_nm": np.nan,
                    "value": float(value) if pd.notna(value) else np.nan,
                }
            )

    if len(records) < 3:
        return None
    return pd.DataFrame.from_records(records, columns=_TIDY_COLUMNS)


def _parse_plate_table(
    raw: pd.DataFrame,
    *,
    source_file: str,
    plate_id: str,
    source_sha256: str,
) -> pd.DataFrame:
    for parser in (
        _parse_clariostar_emission_spectrum,
        _parse_long_format,
        _parse_well_value_rows,
        _parse_plate_grid,
    ):
        parsed = parser(raw, source_file, plate_id)
        if parsed is not None and not parsed.empty:
            parsed = parsed.copy()
            parsed["source_sha256"] = source_sha256
            return parsed[_TIDY_COLUMNS]

    raise ValueError(
        f"Could not identify well-level numeric data in {source_file}. "
        "Use a CLARIOstar long export with a Well column, a plate-grid export, "
        "or a two-column Well/Value file."
    )


def load_plate_text(
    text: str,
    *,
    source_file: str,
    plate_id: Optional[str] = None,
) -> pd.DataFrame:
    """Load CSV text directly, preserving a content fingerprint for provenance."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    name = _source_name(source_file)
    content = text.encode("utf-8")
    raw = _read_rectangular_text(text, name)
    return _parse_plate_table(
        raw,
        source_file=name,
        plate_id=plate_id or Path(name).stem,
        source_sha256=hashlib.sha256(content).hexdigest(),
    )


def load_plate_bytes(
    content: bytes,
    *,
    source_file: str,
    plate_id: Optional[str] = None,
) -> pd.DataFrame:
    """Load uploaded CSV bytes without first writing a temporary file."""
    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")
    name = _source_name(source_file)
    text = _decode_csv_bytes(content, name)
    raw = _read_rectangular_text(text, name)
    return _parse_plate_table(
        raw,
        source_file=name,
        plate_id=plate_id or Path(name).stem,
        source_sha256=hashlib.sha256(content).hexdigest(),
    )


def load_plate_csv(path: Union[str, Path], plate_id: Optional[str] = None) -> pd.DataFrame:
    """Load one CSV path and return tidy, acquisition-aware measurement data."""
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)
    return load_plate_bytes(csv_path.read_bytes(), source_file=csv_path.name, plate_id=plate_id)


def _unique_plate_id(base: str, used_casefold: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate.casefold() in used_casefold:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used_casefold.add(candidate.casefold())
    return candidate


def load_plate_csvs(
    paths: Iterable[Union[str, Path]],
    *,
    duplicate_policy: str = "reject",
    existing_data: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Load paths with globally unique IDs and explicit duplicate handling.

    ``duplicate_policy`` is one of ``"reject"`` (default), ``"skip"``, or
    ``"allow"``.  Duplicates are identified by SHA-256 content rather than by
    filename, which also catches the same upload under a different name.
    Pass the already-loaded tidy table as ``existing_data`` to enforce the
    same hash and plate-ID rules across incremental upload batches.
    """
    if duplicate_policy not in {"reject", "skip", "allow"}:
        raise ValueError("duplicate_policy must be 'reject', 'skip', or 'allow'")
    frames: list[pd.DataFrame] = []
    used_ids: set[str] = set()
    seen_hashes: dict[str, str] = {}
    if existing_data is not None and not existing_data.empty:
        if "plate_id" in existing_data.columns:
            used_ids.update(existing_data["plate_id"].dropna().astype(str).str.casefold())
        if "source_sha256" in existing_data.columns:
            source_names = (
                existing_data["source_file"].astype(str)
                if "source_file" in existing_data.columns
                else pd.Series("existing data", index=existing_data.index)
            )
            for fingerprint, source in zip(existing_data["source_sha256"], source_names):
                if pd.notna(fingerprint) and str(fingerprint).strip():
                    seen_hashes.setdefault(str(fingerprint), str(source))
    for path in paths:
        csv_path = Path(path)
        if not csv_path.exists():
            raise FileNotFoundError(csv_path)
        content = csv_path.read_bytes()
        fingerprint = hashlib.sha256(content).hexdigest()
        first_source = seen_hashes.get(fingerprint)
        if first_source is not None and duplicate_policy != "allow":
            if duplicate_policy == "skip":
                continue
            raise ValueError(
                f"Duplicate plate upload: {csv_path.name!r} has the same SHA-256 content "
                f"as {first_source!r}; pass duplicate_policy='skip' or 'allow' explicitly"
            )
        seen_hashes.setdefault(fingerprint, csv_path.name)
        plate_id = _unique_plate_id(csv_path.stem, used_ids)
        frames.append(load_plate_bytes(content, source_file=csv_path.name, plate_id=plate_id))
    if not frames:
        return pd.DataFrame(columns=_TIDY_COLUMNS)
    return pd.concat(frames, ignore_index=True)
