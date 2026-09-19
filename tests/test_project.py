from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from folding_practical.project import (
    GroupAssignment,
    build_group_dataframe,
    build_group_spectrum_matrix,
    build_spectrum_dataframe,
    concentration_labels,
    export_group_csv,
    export_group_spectrum_csv,
    inspect_group_map,
    load_group_map_assignments,
    normalize_fluorescence,
    summarize_group_observations,
)


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


def _spectrum_frame():
    rows = []
    for well, scale in (("A1", 1.0), ("A2", 0.5), ("A3", 0.25)):
        for wavelength, value in ((500.0, 100.0), (501.0, 200.0), (502.0, 150.0)):
            rows.append(
                {
                    "plate_id": "P1",
                    "well": well,
                    "measurement": "Emission spectrum (Ex 472 nm)",
                    "wavelength_nm": wavelength,
                    "value": value * scale,
                }
            )
    return pd.DataFrame(rows)


def _spectrum_assignment(concentrations=(0.0, 0.4, 0.8)):
    return GroupAssignment(
        name="A1",
        plate_id="P1",
        wells=["A1", "A2", "A3"],
        concentrations=list(concentrations),
        measurement="Emission spectrum (Ex 472 nm)",
        wavelength_nm=501.0,
    )


def test_concentration_labels_number_repeats():
    assert concentration_labels([0, 0.4, 0.4, 6]) == ["0M", "0.4M", "0.4M (2)", "6M"]


def test_group_spectrum_matrix_is_wavelength_by_concentration():
    output = build_group_spectrum_matrix(_spectrum_frame(), _spectrum_assignment())
    assert output.columns.tolist() == ["wavelength (nm)", "0M", "0.4M", "0.8M"]
    assert output["wavelength (nm)"].tolist() == [500.0, 501.0, 502.0]
    assert output["0M"].tolist() == [100.0, 200.0, 150.0]
    assert output["0.8M"].tolist() == [25.0, 50.0, 37.5]


def test_group_spectrum_matrix_follows_assignment_order():
    assignment = _spectrum_assignment()
    assignment.wells = ["A3", "A2", "A1"]
    output = build_group_spectrum_matrix(_spectrum_frame(), assignment)
    assert output["0M"].tolist() == [25.0, 50.0, 37.5]


def test_group_spectrum_matrix_rejects_a_single_readout():
    data = _spectrum_frame()
    data["wavelength_nm"] = float("nan")
    with pytest.raises(ValueError, match="no wavelength-resolved data"):
        build_group_spectrum_matrix(data, _spectrum_assignment())


def test_group_spectrum_matrix_names_the_wells_it_cannot_find():
    assignment = _spectrum_assignment()
    assignment.wells = ["A1", "A2", "H12"]
    with pytest.raises(ValueError, match="H12"):
        build_group_spectrum_matrix(_spectrum_frame(), assignment)


def test_inspect_group_map_reports_counts_without_plate_data(tmp_path):
    path = tmp_path / "groups.csv"
    path.write_text(
        "group name,plate number,well ranges\nA1,P1,A1-A4\nA2,P1,B1-B4\n",
        encoding="utf-8",
    )
    info = inspect_group_map(path)
    assert info["group_count"] == 2
    assert info["well_count"] == 4
    assert info["same_count"] is True
    assert info["missing_concentrations"] is True
    assert info["names"] == ["A1", "A2"]


def test_group_map_records_failures_instead_of_aborting(tmp_path):
    data = _spectrum_frame()
    path = tmp_path / "groups.csv"
    path.write_text(
        "group name,plate number,well ranges\nA1,P1,A1-A3\nGhost,P9,A1-A3\n",
        encoding="utf-8",
    )
    failures = {}
    assignments = load_group_map_assignments(
        data,
        path,
        default_concentrations=[0.0, 0.4, 0.8],
        failures=failures,
    )
    assert list(assignments) == ["A1"]
    assert "Ghost" in failures and "P9" in failures["Ghost"]


def test_group_map_still_raises_when_no_failure_sink_is_given(tmp_path):
    path = tmp_path / "groups.csv"
    path.write_text("group name,plate number,well ranges\nGhost,P9,A1-A3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="row 2"):
        load_group_map_assignments(_spectrum_frame(), path, default_concentrations=[0.0, 0.4, 0.8])


def test_explicit_unavailable_measurement_does_not_fall_back(tmp_path):
    data = pd.DataFrame(
        {
            "plate_id": ["P1"] * 3,
            "well": ["A1", "A2", "A3"],
            "measurement": ["Fluorescence"] * 3,
            "value": [1.0, 2.0, 3.0],
        }
    )
    path = tmp_path / "groups.csv"
    path.write_text(
        "group,plate,wells,measurement\nG,P1,A1-A3,Absorbance\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Requested measurement.*unavailable"):
        load_group_map_assignments(
            data,
            path,
            default_concentrations=[0.0, 0.5, 1.0],
            default_measurement="Fluorescence",
        )


def test_explicit_unavailable_wavelength_does_not_fall_back(tmp_path):
    data = _spectrum_frame()
    path = tmp_path / "groups.csv"
    path.write_text(
        "group,plate,wells,measurement,wavelength\n"
        "G,P1,A1-A3,Emission spectrum (Ex 472 nm),999\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Requested wavelength 999.*unavailable"):
        load_group_map_assignments(
            data,
            path,
            default_concentrations=[0.0, 0.5, 1.0],
            default_wavelength_nm=501.0,
        )


def _repeated_read_frame():
    return pd.DataFrame(
        {
            "plate_id": ["P1"] * 6,
            "well": ["A1", "A2", "A3"] * 2,
            "measurement": ["Fluorescence"] * 6,
            "acquisition_id": ["read_1"] * 3 + ["read_2"] * 3,
            "value": [10.0, 20.0, 30.0, 14.0, float("nan"), 38.0],
        }
    )


def _repeat_assignment(policy="reject", acquisition_id=None):
    return GroupAssignment(
        name="Repeated",
        plate_id="P1",
        wells=["A1", "A2", "A3"],
        concentrations=[0.0, 0.5, 1.0],
        measurement="Fluorescence",
        repeat_policy=policy,
        acquisition_id=acquisition_id,
    )


def test_repeat_policy_rejects_implicit_averaging():
    with pytest.raises(ValueError, match="repeat_policy='select'.*repeat_policy='mean'"):
        build_group_dataframe(_repeated_read_frame(), _repeat_assignment())


def test_repeat_policy_selects_one_acquisition_and_preserves_missing():
    output = build_group_dataframe(
        _repeated_read_frame(),
        _repeat_assignment("select", "read_2"),
    )
    assert output.columns.tolist() == [
        "GuHCl concentration (M)",
        "raw fluorescence values",
        "normalized fluorescence values",
    ]
    assert output["raw fluorescence values"].iloc[[0, 2]].tolist() == [14.0, 38.0]
    assert pd.isna(output["raw fluorescence values"].iloc[1])
    stats = output.attrs["repeat_statistics"]
    assert stats["n"].tolist() == [1, 0, 1]


def test_declared_replicate_mean_reports_n_and_sample_std():
    assignment = _repeat_assignment("mean")
    summary = summarize_group_observations(_repeated_read_frame(), assignment)
    assert summary["value"].tolist() == [12.0, 20.0, 34.0]
    assert summary["n"].tolist() == [2, 1, 2]
    assert summary.loc[0, "std"] == pytest.approx(2.8284271247461903)
    assert pd.isna(summary.loc[1, "std"])
    output = build_group_dataframe(_repeated_read_frame(), assignment)
    assert output.shape == (3, 3)
    assert output.attrs["repeat_statistics"]["n"].tolist() == [2, 1, 2]


def test_unavailable_selected_acquisition_raises():
    with pytest.raises(ValueError, match="Requested acquisition.*unavailable"):
        build_group_dataframe(
            _repeated_read_frame(),
            _repeat_assignment("select", "ghost"),
        )


def test_selected_acquisition_must_contain_every_group_well():
    data = _repeated_read_frame().loc[
        lambda frame: ~((frame["acquisition_id"] == "read_2") & (frame["well"] == "A3"))
    ]
    with pytest.raises(ValueError, match="A3.*acquisition 'read_2'"):
        build_group_dataframe(data, _repeat_assignment("select", "read_2"))


def test_spectrum_repeat_mean_preserves_statistics():
    first = _spectrum_frame().assign(acquisition_id="scan_1")
    second = _spectrum_frame().assign(
        acquisition_id="scan_2",
        value=lambda frame: frame["value"] + 2.0,
    )
    assignment = _spectrum_assignment()
    assignment.repeat_policy = "mean"
    output = build_group_spectrum_matrix(pd.concat([first, second], ignore_index=True), assignment)
    assert output["0M"].tolist() == [101.0, 201.0, 151.0]
    stats = output.attrs["repeat_statistics"]
    assert set(stats["n"]) == {2}
    assert set(stats["std"].round(6)) == {1.414214}


def test_normalization_without_dynamic_range_is_nan():
    assert np.isnan(normalize_fluorescence([5.0, 5.0, 5.0])).all()
    assert np.isnan(normalize_fluorescence([float("nan"), float("nan")])).all()
    mixed = normalize_fluorescence([1.0, float("nan"), 3.0])
    assert mixed[[0, 2]].tolist() == [0.0, 1.0]
    assert np.isnan(mixed[1])


def test_flat_spectrum_peak_normalization_is_nan():
    data = pd.DataFrame(
        {
            "plate_id": ["P1"] * 3,
            "well": ["A1"] * 3,
            "measurement": ["Emission"] * 3,
            "wavelength_nm": [500.0, 501.0, 502.0],
            "value": [7.0, 7.0, 7.0],
        }
    )
    output = build_spectrum_dataframe(
        data,
        plate_id="P1",
        measurement="Emission",
        wells=["A1"],
    )
    assert output["peak-normalized fluorescence values"].isna().all()


def test_exports_are_case_insensitive_collision_safe_with_stable_group_suffix(tmp_path):
    data = pd.DataFrame(
        {
            "plate_id": ["P1"] * 9,
            "well": ["A1", "A1", "A1", "A2", "A2", "A2", "A3", "A3", "A3"],
            "measurement": ["Emission"] * 9,
            "wavelength_nm": [500.0, 501.0, 502.0] * 3,
            "value": [1.0, 2.0, 1.0, 2.0, 4.0, 2.0, 3.0, 6.0, 3.0],
        }
    )
    first = GroupAssignment("Group", "P1", ["A1", "A2", "A3"], [0, 0.5, 1], "Emission", 501)
    second = GroupAssignment("group", "P1", ["A1", "A2", "A3"], [0, 0.5, 1], "Emission", 501)
    assert export_group_csv(data, first, tmp_path).name == "Group.csv"
    assert export_group_spectrum_csv(data, first, tmp_path).name == "Group_spectra.csv"
    assert export_group_csv(data, second, tmp_path).name == "group_2.csv"
    assert export_group_spectrum_csv(data, second, tmp_path).name == "group_2_spectra.csv"
    assert export_group_csv(data, first, tmp_path).name == "Group_3.csv"


def test_lossy_group_names_keep_curve_and_spectrum_identity(tmp_path):
    data = pd.DataFrame(
        {
            "plate_id": ["P1"] * 9,
            "well": ["A1", "A1", "A1", "A2", "A2", "A2", "A3", "A3", "A3"],
            "measurement": ["Emission"] * 9,
            "wavelength_nm": [500.0, 501.0, 502.0] * 3,
            "value": [1.0, 2.0, 1.0, 2.0, 4.0, 2.0, 3.0, 6.0, 3.0],
        }
    )
    slash = GroupAssignment("Group/A", "P1", ["A1", "A2", "A3"], [0, 0.5, 1], "Emission", 501)
    colon = GroupAssignment("Group:A", "P1", ["A1", "A2", "A3"], [0, 0.5, 1], "Emission", 501)

    slash_curve = export_group_csv(data, slash, tmp_path)
    colon_spectrum = export_group_spectrum_csv(data, colon, tmp_path)
    slash_spectrum = export_group_spectrum_csv(data, slash, tmp_path)

    assert slash_curve.stem != colon_spectrum.stem.removesuffix("_spectra")
    assert slash_spectrum.stem.removesuffix("_spectra") == slash_curve.stem


def test_export_error_policy_refuses_overwrite(tmp_path):
    data = _repeated_read_frame().loc[lambda frame: frame["acquisition_id"] == "read_1"]
    assignment = _repeat_assignment()
    export_group_csv(data, assignment, tmp_path)
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        export_group_csv(data, assignment, tmp_path, collision_policy="error")


def test_export_claim_is_exclusive_when_file_appears_after_preflight(tmp_path, monkeypatch):
    data = _repeated_read_frame().loc[lambda frame: frame["acquisition_id"] == "read_1"]
    assignment = _repeat_assignment()
    real_open = Path.open
    raced = False

    def racing_open(path, mode="r", *args, **kwargs):
        nonlocal raced
        if mode == "x" and not raced:
            raced = True
            with real_open(path, "w", encoding="utf-8") as competitor:
                competitor.write("created by another exporter\n")
            raise FileExistsError(path)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", racing_open)
    exported = export_group_csv(data, assignment, tmp_path)

    assert exported.name == "Repeated_2.csv"
    assert (tmp_path / "Repeated.csv").read_text(encoding="utf-8") == "created by another exporter\n"
