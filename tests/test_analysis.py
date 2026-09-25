import csv
import hashlib
import io
import json

import pytest

from folding_practical.analysis import MAX_TEXT_BYTES, dispatch


def ok(request):
    response = dispatch(request)
    assert response["ok"], response.get("error")
    json.dumps(response, allow_nan=False)
    return response["data"]


def synthetic_series_text():
    return (
        "GuHCl concentration (M),raw fluorescence values,normalized fluorescence values\n"
        "0,1002,0.99\n"
        "0,998,1.00\n"
        "0.8,970,0.97\n"
        "1.6,900,0.88\n"
        "2.4,710,0.66\n"
        "3.2,430,0.36\n"
        "4.0,220,0.13\n"
        "4.8,130,0.03\n"
        "5.6,102,0.00\n"
        "6.0,,0.00\n"
    )


def import_series():
    return ok(
        {
            "action": "import_series",
            "filename": "student.csv",
            "text": synthetic_series_text(),
            "settings": {"temperature_k": 298.15, "group_name": "Group A"},
        }
    )["project"]


def repeated_spectrum_text():
    return (
        "Synthetic repeated spectrum,,,,\n"
        ",Raw Data (Em Spectrum) 472-16 / 508-10,,,\n"
        ",1,2,3\n"
        "A,100,80,40\n"
        "H,10,12,8\n"
        ",Raw Data (Em Spectrum) 472-16 / 508-10,,,\n"
        ",1,2,3\n"
        "A,110,90,50\n"
        "H,11,13,9\n"
    )


def imported_repeated_plate():
    return ok(
        {
            "action": "import_plate",
            "filename": "repeat.csv",
            "text": repeated_spectrum_text(),
        }
    )["plate"]


def prepare_request(plate, policy):
    return {
        "action": "prepare_group",
        "plate": plate,
        "selection": {
            "group_name": "Replicate group",
            "measurement": "Emission spectrum (Ex 472 nm)",
            "wavelength_nm": 508,
            "excitation_nm": 472,
            "repeat_policy": policy,
        },
        "wells": [
            {"well": "A1", "concentration_m": 0.0},
            {"well": "A2", "concentration_m": 1.0},
            {"well": "A3", "concentration_m": 2.0},
        ],
        "blank_correction": {
            "enabled": True,
            "method": "mean",
            "blank_wells": ["H1", "H2", "H3"],
        },
    }


def test_dispatch_rejects_unknown_action_and_is_always_strict_json():
    response = dispatch({"action": "not_real"})
    assert response["ok"] is False
    assert response["error"]["code"] == "unknown_action"
    json.dumps(response, allow_nan=False)


def test_import_series_preserves_replicates_excludes_missing_and_fits_raw():
    original_bytes_digest = "a" * 64
    data = ok(
        {
            "action": "import_series",
            "filename": "student.csv",
            "text": synthetic_series_text(),
            "source_bytes_sha256": original_bytes_digest,
            "source_encoding": "windows-1252",
        }
    )
    project = data["project"]
    assert project["source"]["name"] == "student.csv"
    assert project["source"]["sha256"] == hashlib.sha256(
        synthetic_series_text().encode("utf-8")
    ).hexdigest()
    assert project["source"]["bytes_sha256"] == original_bytes_digest
    assert project["source"]["encoding"] == "windows-1252"
    assert project["settings"]["fit_signal"] == "raw_signal"
    assert [row["concentration_m"] for row in project["observations"][:2]] == [0.0, 0.0]
    assert project["observations"][:2][0]["row_id"] != project["observations"][:2][1]["row_id"]
    missing = project["observations"][-1]
    assert missing["excluded"] is True
    assert missing["raw_signal"] is None
    assert "missing raw signal" in missing["exclusion_reason"]
    assert project["exclusions"] == [
        {"row_id": missing["row_id"], "rationale": missing["exclusion_reason"]}
    ]
    assert any("normalized column was not used" in item for item in data["warnings"])


def test_import_series_rejects_oversized_upload():
    response = dispatch(
        {
            "action": "import_series",
            "filename": "large.csv",
            "text": "x" * (MAX_TEXT_BYTES + 1),
        }
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "upload_too_large"


def test_plate_import_returns_provenance_selections_and_acquisitions():
    plate = imported_repeated_plate()
    assert plate["schema_version"] == "plate-1.0"
    assert plate["source"]["sha256"] == hashlib.sha256(
        repeated_spectrum_text().encode("utf-8")
    ).hexdigest()
    assert plate["plate_ids"] == ["repeat"]
    assert {row["acquisition_id"] for row in plate["rows"]} == {"spectrum_1", "spectrum_2"}
    summary = plate["measurements"][0]
    assert summary["wavelengths_nm"] == [508.0]
    assert summary["excitations_nm"] == [472.0]
    assert summary["acquisition_ids"] == ["spectrum_1", "spectrum_2"]
    assert all(row["source_row"] is not None for row in plate["rows"])
    assert {row["source_row"] for row in plate["rows"] if row["well"].startswith("A")} == {
        4,
        8,
    }
    assert {row["source_row"] for row in plate["rows"] if row["well"].startswith("H")} == {
        5,
        9,
    }


def test_prepare_group_rejects_implicit_repeat_choice_and_first_policy():
    plate = imported_repeated_plate()
    repeated = dispatch(prepare_request(plate, {"mode": "reject"}))
    assert repeated["ok"] is False
    assert repeated["error"]["code"] == "repeated_measurement"

    first = dispatch(prepare_request(plate, {"mode": "first"}))
    assert first["ok"] is False
    assert first["error"]["code"] == "invalid_repeat_policy"


@pytest.mark.parametrize(
    "policy",
    [
        {"mode": "select", "selected_acquisition_id": "spectrum_1"},
        {
            "mode": "mean",
            "technical_replicates": True,
            "label": "duplicate reads",
        },
    ],
)
def test_prepare_group_rejects_duplicate_well_rows_within_one_acquisition(policy):
    plate = imported_repeated_plate()
    duplicate = dict(
        next(
            row
            for row in plate["rows"]
            if row["well"] == "A1" and row["acquisition_id"] == "spectrum_1"
        )
    )
    duplicate["source_row"] = 99
    duplicate["value"] = 101.0
    plate["rows"].append(duplicate)
    fingerprint_content = {
        "source": plate["source"],
        "plate_ids": plate["plate_ids"],
        "rows": plate["rows"],
    }
    plate["content_fingerprint"] = hashlib.sha256(
        json.dumps(
            fingerprint_content,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    response = dispatch(prepare_request(plate, policy))
    assert response["ok"] is False
    assert response["error"]["code"] == "repeated_measurement"
    assert response["error"]["details"] == {
        "well": "A1",
        "acquisition_id": "spectrum_1",
        "row_count": 2,
        "source_rows": [4, 99],
    }


def test_prepare_group_selects_acquisition_and_applies_explicit_blank_correction():
    plate = imported_repeated_plate()
    data = ok(
        prepare_request(
            plate,
            {
                "mode": "select",
                "selected_acquisition_id": "spectrum_2",
                "technical_replicates": True,
                "label": "stale UI value",
            },
        )
    )
    project = data["project"]
    settings = project["settings"]
    assert settings["wavelength_nm"] == 508.0
    assert settings["excitation_nm"] == 472.0
    assert settings["requested_selection"] == {
        "plate_id": None,
        "measurement": "Emission spectrum (Ex 472 nm)",
        "wavelength_nm": 508.0,
        "excitation_nm": 472.0,
        "acquisition_id": "spectrum_2",
    }
    assert settings["fit_signal"] == "blank_corrected_signal"
    assert settings["blank_correction"]["blank_value"] == 11.0
    assert settings["blank_correction"]["blank_observation_count"] == 3
    assert settings["repeat_policy"]["technical_replicates"] is False
    assert settings["repeat_policy"]["label"] is None
    assert [row["raw_signal"] for row in project["observations"]] == [110.0, 90.0, 50.0]
    assert [row["blank_corrected_signal"] for row in project["observations"]] == [99.0, 79.0, 39.0]
    assert all(
        row["acquisition"]["acquisition_id"] == "spectrum_2"
        for row in project["observations"]
    )


def test_prepare_group_mean_and_all_require_declared_technical_replicates():
    plate = imported_repeated_plate()
    unsafe = dispatch(prepare_request(plate, {"mode": "mean"}))
    assert unsafe["ok"] is False
    assert unsafe["error"]["code"] == "invalid_repeat_policy"

    mean_project = ok(
        prepare_request(
            plate,
            {
                "mode": "mean",
                "selected_acquisition_id": "spectrum_1",
                "technical_replicates": True,
                "label": "duplicate reads",
            },
        )
    )["project"]
    assert [row["raw_signal"] for row in mean_project["observations"]] == [105.0, 85.0, 45.0]
    assert all(row["acquisition"]["replicate_count"] == 2 for row in mean_project["observations"])
    assert mean_project["settings"]["repeat_policy"]["selected_acquisition_id"] is None
    assert mean_project["settings"]["requested_selection"]["acquisition_id"] is None

    all_project = ok(
        prepare_request(
            plate,
            {"mode": "all", "technical_replicates": True, "label": "duplicate reads"},
        )
    )["project"]
    assert len(all_project["observations"]) == 6
    assert [row["concentration_m"] for row in all_project["observations"][:2]] == [0.0, 0.0]
    assert {row["acquisition"]["acquisition_id"] for row in all_project["observations"]} == {
        "spectrum_1",
        "spectrum_2",
    }


def test_prepare_group_requires_explicit_available_wavelength():
    plate = imported_repeated_plate()
    request = prepare_request(
        plate,
        {"mode": "select", "selected_acquisition_id": "spectrum_1"},
    )
    request["selection"]["wavelength_nm"] = 999
    response = dispatch(request)
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_group"

    no_wavelength_plate = ok(
        {
            "action": "import_plate",
            "filename": "long.csv",
            "text": "Well,Fluorescence\nA1,10\nA2,20\n",
        }
    )["plate"]
    unavailable = dispatch(
        {
            "action": "prepare_group",
            "plate": no_wavelength_plate,
            "selection": {"measurement": "Fluorescence", "wavelength_nm": 508},
            "wells": [{"well": "A1", "concentration_m": 0.0}],
        }
    )
    assert unavailable["ok"] is False
    assert unavailable["error"]["code"] == "invalid_group"


def test_fit_returns_plot_payload_and_strict_json():
    project = ok({"action": "example_plate"})["project"]
    data = ok({"action": "fit", "project": project, "model": "compare"})
    result = data["result"]
    assert data["project"]["result"] == result
    assert result["project_fingerprint"] == project["content_fingerprint"]
    assert result["signal_field"] == "blank_corrected_signal"
    assert {fit["model_name"] for fit in result["fits"]} == {"Two-state LEM", "4PL logistic"}
    for fit in result["fits"]:
        assert set(fit["observed"]) == {
            "row_ids",
            "x",
            "y",
            "predicted",
            "residuals",
            "retained_indices",
            "excluded_indices",
        }
        assert set(fit["curve"]) == {"x", "y"}
        assert len(fit["observed"]["row_ids"]) == 16
        assert fit["interpretation_status"] in {
            "interpretable",
            "caution",
            "insufficient_information",
            "fit_failed",
        }
    assert "descriptive" in result["preferred_model_note"]
    json.dumps(data, allow_nan=False)
    report = ok({"action": "export_report", "project": data["project"]})["text"]
    assert "## Scientific assumptions and limits" in report
    assert "equilibrium, reversibility" in report
    assert "residual-variance parameter" in report
    assert "#### Fit metrics" in report
    assert "#### Diagnostics" in report
    assert "#### Parameter covariance matrix" in report


def test_validate_and_load_discard_stale_or_untrusted_results():
    project = import_series()
    fitted = ok({"action": "fit", "project": project, "model": "logistic"})["project"]
    assert fitted["result"] is not None

    validated = ok({"action": "validate_project", "project": fitted})
    assert validated["project"]["result"] is None
    assert any("re-fit" in item.lower() for item in validated["warnings"])

    stale = json.loads(json.dumps(fitted, allow_nan=False))
    stale["observations"][0]["raw_signal"] += 50
    loaded = ok(
        {
            "action": "load_project",
            "filename": "student.folding.json",
            "text": json.dumps(stale, allow_nan=False),
        }
    )
    assert loaded["project"]["result"] is None
    assert loaded["project"]["content_fingerprint"] != fitted["content_fingerprint"]
    assert any("stale" in item.lower() for item in loaded["warnings"])


def test_load_project_rejects_duplicate_json_keys_and_nonfinite_json():
    duplicate = dispatch(
        {
            "action": "load_project",
            "filename": "bad.json",
            "text": '{"schema_version":"1.0","schema_version":"1.0"}',
        }
    )
    assert duplicate["ok"] is False
    assert duplicate["error"]["code"] == "invalid_json"

    nonfinite = dispatch(
        {
            "action": "load_project",
            "filename": "bad.json",
            "text": '{"value": NaN}',
        }
    )
    assert nonfinite["ok"] is False
    assert nonfinite["error"]["code"] == "invalid_json"


def test_exports_preserve_raw_rows_and_normalize_only_when_explicit():
    project = import_series()
    plain = ok({"action": "export_legacy_csv", "project": project})
    rows = list(csv.reader(io.StringIO(plain["text"])))
    assert rows[1][0] == rows[2][0] == "0.0"
    assert all(row[2] == "" for row in rows[1:])
    assert plain["normalization"] == "none"
    assert plain["omitted_exclusions"] == 1

    normalized = ok(
        {
            "action": "export_legacy_csv",
            "project": project,
            "normalization": "minmax",
        }
    )
    normalized_rows = list(csv.reader(io.StringIO(normalized["text"])))
    assert any(row[2] != "" for row in normalized_rows[1:])

    constant_project = import_series()
    for row in constant_project["observations"]:
        if row["raw_signal"] is not None:
            row["raw_signal"] = 7.0
    constant = ok(
        {
            "action": "export_legacy_csv",
            "project": constant_project,
            "normalization": "minmax",
        }
    )
    constant_rows = list(csv.reader(io.StringIO(constant["text"])))
    assert all(row[2] == "" for row in constant_rows[1:])
    assert any("no_dynamic_range" in warning for warning in constant["warnings"])

    project_export = ok({"action": "export_project", "project": project})
    assert project_export["mime"] == "application/json"
    json.loads(project_export["text"])
    report = ok({"action": "export_report", "project": project})
    assert "## Observations" in report["text"]
    assert "scientific interpretability" in report["text"]
    assert "Decoded UTF-8 text SHA-256" in report["text"]
    assert "Requested plate selection" in report["text"]
    assert "Repeat policy" in report["text"]
    assert "Blank correction" in report["text"]


def test_practical_round_trip_validates_every_project_and_discards_results():
    first = ok(
        {"action": "fit", "project": ok({"action": "example"})["project"], "model": "logistic"}
    )["project"]
    second = import_series()
    exported = ok(
        {
            "action": "export_practical",
            "title": "Instructor set",
            "projects": [first, second],
            "selected_index": 1,
            "instructor_locks": {"temperature_k": True, "model": "compare"},
        }
    )
    practical = exported["practical"]
    assert practical["schema_version"] == "practical-1.0"
    assert practical["selected_index"] == 1
    assert practical["projects"][0]["result"] is not None

    loaded = ok(
        {
            "action": "load_practical",
            "filename": exported["filename"],
            "text": exported["text"],
        }
    )
    assert len(loaded["practical"]["projects"]) == 2
    assert all(project["result"] is None for project in loaded["practical"]["projects"])
    assert any("re-fit" in item.lower() for item in loaded["warnings"])


def test_practical_rejects_casefold_duplicate_group_names_and_invalid_locks():
    first = import_series()
    second = ok({"action": "example"})["project"]
    first["settings"]["group_name"] = "Straße Group"
    second["settings"]["group_name"] = "STRASSE GROUP"
    duplicate = dispatch(
        {
            "action": "export_practical",
            "projects": [first, second],
            "instructor_locks": {},
        }
    )
    assert duplicate["ok"] is False
    assert duplicate["error"]["code"] == "invalid_practical"
    assert duplicate["error"]["details"]["project_indices"] == [0, 1]

    valid_project = import_series()
    for locks in (
        {"model": "polynomial"},
        {"model": True},
        {"temperature_k": "298.15"},
        {"temperature_k": 259.9},
        {"temperature_k": 330.1},
    ):
        rejected = dispatch(
            {
                "action": "export_practical",
                "projects": [valid_project],
                "instructor_locks": locks,
            }
        )
        assert rejected["ok"] is False
        assert rejected["error"]["code"] == "invalid_practical"

    for temperature in (True, False, 260, 298.15, 330):
        accepted = ok(
            {
                "action": "export_practical",
                "projects": [valid_project],
                "instructor_locks": {
                    "temperature_k": temperature,
                    "model": "compare",
                },
            }
        )
        assert accepted["practical"]["instructor_locks"]["temperature_k"] == temperature
        assert accepted["practical"]["instructor_locks"]["model"] == "compare"


def test_example_plate_is_explicitly_synthetic_and_self_contained():
    data = ok({"action": "example_plate"})
    assert data["filename"] == "synthetic_teaching_plate.csv"
    assert "Synthetic teaching example only" in data["text"]
    assert "Synthetic teaching example" in data["project"]["notes"]
    assert data["project"]["settings"]["wavelength_nm"] == 508.0
    assert data["project"]["settings"]["excitation_nm"] == 472.0
    assert data["prepare_group_request"]["action"] == "prepare_group"
    observations = data["project"]["observations"]
    assert len(observations) == 16
    assert [row["well"] for row in observations] == [
        *[f"A{index}" for index in range(1, 13)],
        *[f"B{index}" for index in range(1, 5)],
    ]
    assert [row["concentration_m"] for row in observations] == [index * 4 / 10 for index in range(16)]
    assert all(row["raw_signal"] is not None and not row["excluded"] for row in observations)
    assert data["prepare_group_request"]["blank_correction"]["blank_wells"] == ["H1", "H2", "H3", "H4"]
    assert ok({"action": "example"})["project"]["observations"] == observations
