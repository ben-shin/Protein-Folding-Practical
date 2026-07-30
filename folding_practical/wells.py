"""Utilities for validating and expanding 96-well plate identifiers."""

from __future__ import annotations

import re
from typing import Iterable, Union

PLATE_ROWS = "ABCDEFGH"
PLATE_COLUMNS = tuple(range(1, 13))
_WELL_RE = re.compile(r"^([A-Ha-h])\s*0*([1-9]|1[0-2])$")


def normalize_well(value: str) -> str:
    """Return a canonical well name such as ``A1`` or raise ``ValueError``."""
    match = _WELL_RE.match(str(value).strip())
    if not match:
        raise ValueError(f"Invalid 96-well identifier: {value!r}")
    return f"{match.group(1).upper()}{int(match.group(2))}"


def well_to_indices(well: str) -> tuple[int, int]:
    canonical = normalize_well(well)
    return PLATE_ROWS.index(canonical[0]), int(canonical[1:]) - 1


def indices_to_well(row_index: int, column_index: int) -> str:
    if not 0 <= row_index < len(PLATE_ROWS):
        raise ValueError("Row index must be between 0 and 7")
    if not 0 <= column_index < len(PLATE_COLUMNS):
        raise ValueError("Column index must be between 0 and 11")
    return f"{PLATE_ROWS[row_index]}{column_index + 1}"


def well_sort_key(well: str, order: str = "row-major") -> int:
    row, column = well_to_indices(well)
    if order == "row-major":
        return row * 12 + column
    if order == "column-major":
        return column * 8 + row
    raise ValueError("order must be 'row-major' or 'column-major'")


def consecutive_wells(start_well: str, count: int, order: str = "row-major") -> list[str]:
    """Return ``count`` wells beginning at ``start_well`` in the requested order."""
    if count < 1:
        raise ValueError("count must be at least 1")
    start_index = well_sort_key(start_well, order)
    plate_size = 96
    if start_index + count > plate_size:
        raise ValueError("Requested wells run past the end of the 96-well plate")

    wells: list[str] = []
    for index in range(start_index, start_index + count):
        if order == "row-major":
            row, column = divmod(index, 12)
        else:
            column, row = divmod(index, 8)
        wells.append(indices_to_well(row, column))
    return wells


def _expand_range(start: str, end: str) -> list[str]:
    start_well = normalize_well(start)
    end_well = normalize_well(end)
    start_row, start_col = well_to_indices(start_well)
    end_row, end_col = well_to_indices(end_well)

    if start_row == end_row:
        step = 1 if end_col >= start_col else -1
        return [indices_to_well(start_row, col) for col in range(start_col, end_col + step, step)]
    if start_col == end_col:
        step = 1 if end_row >= start_row else -1
        return [indices_to_well(row, start_col) for row in range(start_row, end_row + step, step)]

    start_index = well_sort_key(start_well, "row-major")
    end_index = well_sort_key(end_well, "row-major")
    step = 1 if end_index >= start_index else -1
    output: list[str] = []
    for index in range(start_index, end_index + step, step):
        row, column = divmod(index, 12)
        output.append(indices_to_well(row, column))
    return output


def expand_well_spec(specification: Union[str, Iterable[str]]) -> list[str]:
    """Expand a flexible well specification while preserving order.

    Examples
    --------
    ``A1:A12``
        Twelve wells across row A.
    ``A1:H1``
        Eight wells down column 1.
    ``A1, A3, B2``
        An explicit ordered list.
    """
    if isinstance(specification, str):
        tokens = [token for token in re.split(r"[,;\s]+", specification.strip()) if token]
    else:
        tokens = [str(token).strip() for token in specification if str(token).strip()]

    wells: list[str] = []
    for token in tokens:
        if ":" in token:
            start, end = token.split(":", maxsplit=1)
            wells.extend(_expand_range(start, end))
        else:
            wells.append(normalize_well(token))

    duplicates = {well for well in wells if wells.count(well) > 1}
    if duplicates:
        duplicate_text = ", ".join(sorted(duplicates, key=well_sort_key))
        raise ValueError(f"Well specification contains duplicates: {duplicate_text}")
    return wells
