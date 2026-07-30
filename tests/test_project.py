import pandas as pd

from folding_practical.project import GroupAssignment, build_group_dataframe, build_spectrum_dataframe


def test_group_export_columns_and_order():
    data = pd.DataFrame(
        {
            "plate_id": ["plate"] * 4,
            "well": ["A1", "A2", "A3", "A4"],
            "measurement": ["Fluorescence"] * 4,
            "value": [100.0, 80.0, 40.0, 20.0],
        }
    )
    assignment = GroupAssignment(
        name="Group 1",
        plate_id="plate",
        wells=["A1", "A2", "A3", "A4"],
        concentrations=[0.0, 1.0, 2.0, 3.0],
        measurement="Fluorescence",
    )
    output = build_group_dataframe(data, assignment)
    assert output.columns.tolist() == [
        "GuHCl concentration (M)",
        "raw fluorescence values",
        "normalized fluorescence values",
    ]
    assert output["raw fluorescence values"].tolist() == [100.0, 80.0, 40.0, 20.0]
    assert output["normalized fluorescence values"].tolist() == [1.0, 0.75, 0.25, 0.0]


def test_group_can_select_one_wavelength_from_spectrum():
    data = pd.DataFrame(
        {
            "plate_id": ["plate"] * 8,
            "well": ["A1", "A2", "A3", "A4"] * 2,
            "measurement": ["Emission spectrum (Ex 472 nm)"] * 8,
            "wavelength_nm": [508.0] * 4 + [509.0] * 4,
            "value": [100.0, 80.0, 40.0, 20.0, 999.0, 999.0, 999.0, 999.0],
        }
    )
    assignment = GroupAssignment(
        name="Group 1",
        plate_id="plate",
        wells=["A1", "A2", "A3", "A4"],
        concentrations=[0.0, 1.0, 2.0, 3.0],
        measurement="Emission spectrum (Ex 472 nm)",
        wavelength_nm=508.0,
    )
    output = build_group_dataframe(data, assignment)
    assert output["raw fluorescence values"].tolist() == [100.0, 80.0, 40.0, 20.0]


def test_build_spectrum_dataframe():
    data = pd.DataFrame(
        {
            "plate_id": ["plate"] * 6,
            "well": ["A1", "A1", "A1", "B1", "B1", "B1"],
            "measurement": ["Emission spectrum"] * 6,
            "wavelength_nm": [500.0, 501.0, 502.0] * 2,
            "value": [5.0, 10.0, 5.0, 2.0, 4.0, 1.0],
        }
    )
    output = build_spectrum_dataframe(
        data,
        plate_id="plate",
        measurement="Emission spectrum",
        wells=["B1", "A1"],
    )
    assert output["well"].tolist()[:3] == ["B1", "B1", "B1"]
    assert output.loc[output["well"] == "A1", "peak-normalized fluorescence values"].tolist() == [0.5, 1.0, 0.5]


def test_group_rejects_unselected_multiwavelength_signal():
    data = pd.DataFrame(
        {
            "plate_id": ["plate"] * 6,
            "well": ["A1", "A2", "A3"] * 2,
            "measurement": ["Emission spectrum"] * 6,
            "wavelength_nm": [508.0] * 3 + [509.0] * 3,
            "value": [10.0, 8.0, 2.0, 11.0, 9.0, 3.0],
        }
    )
    assignment = GroupAssignment(
        name="Group 1",
        plate_id="plate",
        wells=["A1", "A2", "A3"],
        concentrations=[0.0, 1.0, 2.0],
        measurement="Emission spectrum",
    )
    import pytest

    with pytest.raises(ValueError, match="select one emission wavelength"):
        build_group_dataframe(data, assignment)


def test_load_group_map_assignments(tmp_path):
    from folding_practical.project import load_group_map_assignments

    wells = [f"A{column}" for column in range(1, 13)] + [f"B{column}" for column in range(1, 5)]
    rows = []
    for wavelength in (508.0, 509.0):
        for index, well in enumerate(wells):
            rows.append(
                {
                    "plate_id": "P1(Microplate End point)",
                    "source_file": "P1(Microplate End point).csv",
                    "well": well,
                    "measurement": "Emission spectrum (Ex 472 nm)",
                    "wavelength_nm": wavelength,
                    "value": 1000.0 - index * 40.0 + wavelength,
                }
            )
    data = pd.DataFrame(rows)
    map_path = tmp_path / "groups.csv"
    map_path.write_text(
        "group name,plate number,well ranges\n"
        "A1,P1,A1-B4\n",
        encoding="utf-8",
    )
    concentrations = [value * 0.4 for value in range(16)]
    assignments = load_group_map_assignments(
        data,
        map_path,
        default_concentrations=concentrations,
        default_measurement="Emission spectrum (Ex 472 nm)",
        default_wavelength_nm=508.0,
    )
    assignment = assignments["A1"]
    assert assignment.plate_id == "P1(Microplate End point)"
    assert assignment.wells == wells
    assert assignment.concentrations == concentrations
    assert assignment.wavelength_nm == 508.0


def test_group_map_rejects_overlapping_wells(tmp_path):
    from folding_practical.project import load_group_map_assignments

    wells = [f"A{column}" for column in range(1, 13)] + [f"B{column}" for column in range(1, 12)]
    data = pd.DataFrame(
        {
            "plate_id": ["P1"] * len(wells),
            "source_file": ["P1.csv"] * len(wells),
            "well": wells,
            "measurement": ["Fluorescence"] * len(wells),
            "value": list(range(len(wells))),
        }
    )
    map_path = tmp_path / "groups.csv"
    map_path.write_text(
        "group name,plate number,well ranges\n"
        "Group 1,P1,A1-A12\n"
        "Group 2,P1,A12-B11\n",
        encoding="utf-8",
    )
    import pytest

    with pytest.raises(ValueError, match="already assigned"):
        load_group_map_assignments(
            data,
            map_path,
            default_concentrations=[float(value) for value in range(12)],
        )
