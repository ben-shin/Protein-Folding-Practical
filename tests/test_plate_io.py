from pathlib import Path

import pandas as pd
import pytest

from folding_practical.plate_io import (
    load_plate_bytes,
    load_plate_csv,
    load_plate_csvs,
    load_plate_text,
)


def test_load_long_format(tmp_path: Path):
    path = tmp_path / "plate.csv"
    path.write_text(
        "Metadata line\n"
        "Well,Sample,Fluorescence,Absorbance\n"
        "A1,G1,1000,0.1\n"
        "A2,G1,800,0.2\n"
        "A3,G1,200,0.3\n",
        encoding="utf-8",
    )
    result = load_plate_csv(path)
    assert set(result["measurement"]) == {"Fluorescence", "Absorbance"}
    fluorescence = result[result["measurement"] == "Fluorescence"]
    assert fluorescence["well"].tolist() == ["A1", "A2", "A3"]
    assert fluorescence["value"].tolist() == [1000.0, 800.0, 200.0]
    assert fluorescence["source_row"].tolist() == [3, 4, 5]


def test_load_plate_grid(tmp_path: Path):
    path = tmp_path / "grid.csv"
    path.write_text(
        ",1,2,3,4\n"
        "A,10,20,30,40\n"
        "B,50,60,70,80\n",
        encoding="utf-8",
    )
    result = load_plate_csv(path)
    assert result["well"].tolist() == ["A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4"]
    assert result["source_row"].tolist() == [2, 2, 2, 2, 3, 3, 3, 3]


def test_load_clariostar_emission_spectrum(tmp_path: Path):
    path = tmp_path / "spectrum.csv"
    path.write_text(
        "User: USER,,,,\n"
        ",Raw Data (Em Spectrum) 472-16 / 500-10,,,\n"
        ",1,2,3\n"
        "A,10,20,30\n"
        "B,40,50,60\n"
        ",,,,\n"
        ",Raw Data (Em Spectrum) 472-16 / 501-10,,,\n"
        ",1,2,3\n"
        "A,11,21,31\n"
        "B,41,51,61\n",
        encoding="utf-8",
    )
    result = load_plate_csv(path)
    assert len(result) == 12
    assert result["measurement"].unique().tolist() == ["Emission spectrum (Ex 472 nm)"]
    assert result["wavelength_nm"].unique().tolist() == [500.0, 501.0]
    assert result["well"].nunique() == 6
    a2_501 = result.loc[(result["well"] == "A2") & (result["wavelength_nm"] == 501.0), "value"]
    assert a2_501.iloc[0] == 21.0
    assert result.loc[
        (result["well"] == "A2") & (result["wavelength_nm"] == 501.0),
        "source_row",
    ].iloc[0] == 9


def test_bytes_and_text_entrypoints_include_provenance():
    text = "Well,Fluorescence\nA1,10\nA2,20\nA3,30\n"
    from_text = load_plate_text(text, source_file="upload.csv")
    from_bytes = load_plate_bytes(text.encode("utf-8"), source_file="upload.csv")
    assert from_text.equals(from_bytes)
    assert from_text["source_file"].unique().tolist() == ["upload.csv"]
    assert len(from_text["source_sha256"].unique()[0]) == 64
    assert from_text["acquisition_id"].unique().tolist() == ["long_1"]


def test_repeated_spectrum_preserves_acquisitions_and_missing_observations():
    text = (
        ",Raw Data (Em Spectrum) 472-16 / 500-10,,,\n"
        ",1,2,3\nA,10,,30\nB,40,50,60\n"
        ",Raw Data (Em Spectrum) 472-16 / 501-10,,,\n"
        ",1,2,3\nA,11,21,31\nB,41,51,61\n"
        ",Raw Data (Em Spectrum) 472-16 / 500-10,,,\n"
        ",1,2,3\nA,110,120,130\nB,140,150,160\n"
        ",Raw Data (Em Spectrum) 472-16 / 501-10,,,\n"
        ",1,2,3\nA,111,121,131\nB,141,151,161\n"
    )
    result = load_plate_text(text, source_file="repeated-spectrum.csv")
    assert result["acquisition_id"].unique().tolist() == ["spectrum_1", "spectrum_2"]
    missing = result.loc[
        (result["acquisition_id"] == "spectrum_1")
        & (result["wavelength_nm"] == 500.0)
        & (result["well"] == "A2"),
        "value",
    ]
    assert len(missing) == 1 and pd.isna(missing.iloc[0])
    assert result.loc[
        (result["acquisition_id"] == "spectrum_2") & (result["well"] == "A2"), "value"
    ].tolist() == [120.0, 121.0]


def test_repeated_long_reads_preserve_acquisitions_and_blank_values():
    text = (
        "Well,Fluorescence\nA1,10\nA2,\nA3,30\n"
        "Well,Fluorescence\nA1,11\nA2,21\nA3,31\n"
    )
    result = load_plate_text(text, source_file="repeated-long.csv")
    assert result["acquisition_id"].unique().tolist() == ["long_1", "long_2"]
    first_a2 = result.loc[
        (result["acquisition_id"] == "long_1") & (result["well"] == "A2"), "value"
    ]
    assert len(first_a2) == 1 and pd.isna(first_a2.iloc[0])


def test_repeated_long_reads_under_one_header_are_separate_acquisitions():
    text = (
        "Well,Fluorescence\nA1,10\nA2,\nA3,30\n"
        "A1,11\nA2,21\nA3,31\n"
    )
    result = load_plate_text(text, source_file="single-header-repeats.csv")

    assert result["acquisition_id"].unique().tolist() == ["long_1", "long_2"]
    assert result.loc[result["acquisition_id"] == "long_1", "source_row"].tolist() == [2, 3, 4]
    assert result.loc[result["acquisition_id"] == "long_2", "source_row"].tolist() == [5, 6, 7]
    assert result.loc[
        (result["acquisition_id"] == "long_2") & (result["well"] == "A1"),
        "value",
    ].tolist() == [11.0]


def test_repeated_grids_preserve_acquisitions_and_blank_values():
    text = (
        ",1,2,3\nA,10,,30\nB,40,50,60\n"
        ",1,2,3\nA,11,21,31\nB,41,51,61\n"
    )
    result = load_plate_text(text, source_file="repeated-grid.csv")
    assert result["acquisition_id"].unique().tolist() == ["grid_1", "grid_2"]
    missing = result.loc[
        (result["acquisition_id"] == "grid_1") & (result["well"] == "A2"), "value"
    ]
    assert len(missing) == 1 and pd.isna(missing.iloc[0])


def test_plate_ids_are_globally_unique_across_stem_collisions(tmp_path: Path):
    paths = []
    for directory, filename, offset in (("one", "A.csv", 0), ("two", "A.csv", 10), ("three", "A_2.csv", 20)):
        folder = tmp_path / directory
        folder.mkdir()
        path = folder / filename
        path.write_text(
            f"Well,Signal\nA1,{offset + 1}\nA2,{offset + 2}\nA3,{offset + 3}\n",
            encoding="utf-8",
        )
        paths.append(path)
    result = load_plate_csvs(paths)
    assert list(dict.fromkeys(result["plate_id"])) == ["A", "A_2", "A_2_2"]


def test_duplicate_upload_hash_requires_explicit_policy(tmp_path: Path):
    first = tmp_path / "first.csv"
    second = tmp_path / "renamed.csv"
    content = "Well,Signal\nA1,1\nA2,2\nA3,3\n"
    first.write_text(content, encoding="utf-8")
    second.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="same SHA-256"):
        load_plate_csvs([first, second])
    skipped = load_plate_csvs([first, second], duplicate_policy="skip")
    assert skipped["plate_id"].unique().tolist() == ["first"]
    allowed = load_plate_csvs([first, second], duplicate_policy="allow")
    assert allowed["plate_id"].unique().tolist() == ["first", "renamed"]
    assert allowed["source_sha256"].nunique() == 1


def test_incremental_import_uses_existing_hashes_and_plate_ids(tmp_path: Path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "A.csv"
    same_again = second_dir / "renamed.csv"
    another_a = second_dir / "a.csv"
    first.write_text("Well,Signal\nA1,1\nA2,2\nA3,3\n", encoding="utf-8")
    same_again.write_text(first.read_text(encoding="utf-8"), encoding="utf-8")
    another_a.write_text("Well,Signal\nA1,4\nA2,5\nA3,6\n", encoding="utf-8")
    existing = load_plate_csvs([first])

    with pytest.raises(ValueError, match="same SHA-256"):
        load_plate_csvs([same_again], existing_data=existing)
    imported = load_plate_csvs([another_a], existing_data=existing)
    assert imported["plate_id"].unique().tolist() == ["a_2"]
