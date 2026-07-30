from pathlib import Path

from folding_practical.plate_io import load_plate_csv


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
