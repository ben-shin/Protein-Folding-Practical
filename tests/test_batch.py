from pathlib import Path

import pytest

from folding_practical.batch import (
    default_concentrations_for,
    resolve_batch_concentrations,
    run_batch_export,
)

WAVELENGTHS = (500.0, 501.0, 502.0)


def write_spectrum_plate(path: Path, offset: float = 0.0) -> None:
    """Write a miniature CLARIOstar emission-spectrum export."""
    lines = ["Test Name: synthetic,,,,,,,,,,,,", ""]
    for wavelength in WAVELENGTHS:
        lines.append(f",Raw Data (Em Spectrum) 472-16 / {wavelength:g}-10,,,,,,,,,,,")
        lines.append("," + ",".join(str(column) for column in range(1, 13)))
        for row_index, row_letter in enumerate("ABCDEFGH"):
            values = [
                offset + wavelength + row_index * 100 + column
                for column in range(1, 13)
            ]
            lines.append(row_letter + "," + ",".join(f"{value:g}" for value in values))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture()
def practical(tmp_path: Path) -> dict[str, object]:
    write_spectrum_plate(tmp_path / "P1.csv")
    write_spectrum_plate(tmp_path / "P2.csv", offset=1000.0)
    group_map = tmp_path / "groups.csv"
    group_map.write_text(
        "group name,plate number,well ranges\n"
        "A1,P1,A1-A4\n"
        "A2,P1,B1-B4\n"
        "B1,P2,A1-A4\n",
        encoding="utf-8",
    )
    return {
        "plates": [tmp_path / "P1.csv", tmp_path / "P2.csv"],
        "group_map": group_map,
        "out": tmp_path / "exports",
    }


def test_default_concentrations_span_the_range():
    assert default_concentrations_for(4, 0.0, 6.0) == [0.0, 2.0, 4.0, 6.0]


def test_resolve_concentrations_from_well_count(practical):
    values = resolve_batch_concentrations(practical["group_map"], start=0.0, stop=3.0)
    assert values == [0.0, 1.0, 2.0, 3.0]


def test_resolve_concentrations_prefers_an_explicit_list(practical):
    assert resolve_batch_concentrations(practical["group_map"], [0, 1, 2, 9]) == [0.0, 1.0, 2.0, 9.0]


def test_resolve_concentrations_rejects_mixed_well_counts(tmp_path: Path):
    group_map = tmp_path / "mixed.csv"
    group_map.write_text(
        "group name,plate number,well ranges\nA1,P1,A1-A4\nA2,P1,B1-B8\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="same number of wells"):
        resolve_batch_concentrations(group_map)


def test_batch_writes_one_file_pair_per_group(practical):
    result = run_batch_export(
        practical["plates"],
        practical["group_map"],
        practical["out"],
        start=0.0,
        stop=1.2,
    )
    assert result.failures == {}
    assert sorted(path.name for path in result.spectrum_files.values()) == [
        "A1_spectra.csv",
        "A2_spectra.csv",
        "B1_spectra.csv",
    ]
    assert sorted(path.name for path in result.curve_files.values()) == ["A1.csv", "A2.csv", "B1.csv"]

    header = (practical["out"] / "A1_spectra.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header == "wavelength (nm),0M,0.4M,0.8M,1.2M"


def test_batch_rows_are_one_row_per_wavelength(practical):
    run_batch_export(practical["plates"], practical["group_map"], practical["out"], start=0.0, stop=1.2)
    lines = (practical["out"] / "A1_spectra.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1 + len(WAVELENGTHS)
    # Wells A1-A4 at 500 nm are 501, 502, 503, 504 by construction.
    assert lines[1] == "500.0,501.0,502.0,503.0,504.0"


def test_batch_skips_a_bad_row_and_exports_the_rest(practical, tmp_path: Path):
    group_map = tmp_path / "with_bad_row.csv"
    group_map.write_text(
        "group name,plate number,well ranges\n"
        "A1,P1,A1-A4\n"
        "Missing,P9,A1-A4\n"
        "B1,P2,A1-A4\n",
        encoding="utf-8",
    )
    result = run_batch_export(practical["plates"], group_map, practical["out"], start=0.0, stop=1.2)
    assert sorted(result.spectrum_files) == ["A1", "B1"]
    assert "Missing" in result.failures
    assert "P9" in result.failures["Missing"]


def test_batch_ignores_the_group_map_in_the_plate_selection(practical):
    result = run_batch_export(
        [*practical["plates"], practical["group_map"]],
        practical["group_map"],
        practical["out"],
        start=0.0,
        stop=1.2,
    )
    assert result.plate_ids == ["P1", "P2"]
    assert result.failures == {}


def test_batch_keeps_the_imported_data_for_reuse(practical):
    result = run_batch_export(practical["plates"], practical["group_map"], practical["out"])
    assert not result.data.empty
    assert sorted(result.assignments) == ["A1", "A2", "B1"]
