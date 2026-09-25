"""Browser group distribution tests, including plate identity and privacy boundaries."""

import base64
import csv
import io
import zipfile

import pytest

from folding_practical.analysis import dispatch


def ok(request):
    result = dispatch(request)
    assert result["ok"], result.get("error")
    return result["data"]


def plate_file(name="P1.csv", offset=0):
    rows = ["Synthetic plate", ",Raw Data (Em Spectrum) 472-16 / 508-10", "," + ",".join(map(str, range(1, 13)))]
    for row_index, row in enumerate("ABCDEFGH"):
        values = [1000 + offset - (row_index * 12 + col) * 5 for col in range(12)]
        rows.append(row + "," + ",".join(map(str, values)))
    return {"filename": name, "text": "\n".join(rows) + "\n"}


def import_plates(*files):
    return ok({"action": "import_plates", "files": list(files) or [plate_file()]})["plate"]


def prepare(plate, text, **kwargs):
    return ok({"action": "prepare_groups", "plate": plate, "text": text, **kwargs})


def test_multi_plate_group_list_zip_reopens_each_16_point_csv():
    plate = import_plates(plate_file(), plate_file("P2.csv", 500))
    data = prepare(plate, "group name,plate number,well ranges\nGroup A,P1,A1-B4\nGroup B,P2,A1-B4\n")
    assert data["failures"] == {}
    assert len(data["projects"]) == 2
    a, b = data["projects"]
    assert [row["well"] for row in a["observations"]] == [*[f"A{i}" for i in range(1, 13)], "B1", "B2", "B3", "B4"]
    assert [row["concentration_m"] for row in a["observations"]] == pytest.approx([i * 0.4 for i in range(16)])
    assert b["observations"][0]["raw_signal"] - a["observations"][0]["raw_signal"] == 500
    exported = ok({"action": "export_group_csvs", "projects": data["projects"]})
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(exported["base64"]))) as archive:
        assert archive.namelist() == ["Group_A.csv", "Group_B.csv"]
        for name in archive.namelist():
            text = archive.read(name).decode("utf-8")
            records = list(csv.DictReader(io.StringIO(text)))
            assert len(records) == 16
            assert float(records[0]["normalized fluorescence values"]) == 1
            assert float(records[-1]["normalized fluorescence values"]) == 0
            restored = ok({"action": "import_series", "filename": name, "text": text})["project"]
            assert len(restored["observations"]) == 16
            assert not any(row["excluded"] for row in restored["observations"])


def test_one_bad_group_does_not_prevent_other_exports():
    data = prepare(import_plates(), "group,plate,wells\nGood,P1,A1-B4\nMissing,P9,A1-B4\nOverlap,P1,A3-B6\n")
    assert [p["settings"]["group_name"] for p in data["projects"]] == ["Good"]
    assert "P9" in data["failures"]["Missing"]
    assert "already assigned" in data["failures"]["Overlap"]


def test_explicit_concentrations_override_the_16_point_default_in_order():
    data = prepare(import_plates(), 'group,plate,wells,concentrations\nReverse,P1,A3-A1,"6,3,0"\n')
    rows = data["projects"][0]["observations"]
    assert [row["well"] for row in rows] == ["A3", "A2", "A1"]
    assert [row["concentration_m"] for row in rows] == [6, 3, 0]


@pytest.mark.parametrize("text", [
    "group,plate,wells\nA,P1,A1-B4\n a ,P1,B5-C8\n",
    "group,plate,wells,wells\nA,P1,A1-B4,B5-C8\n",
    "group,plate,wells,concentrations\nA,P1,A1-B4,0,0.4,0.8\n",
])
def test_ambiguous_group_csvs_are_rejected_before_preparation(text):
    response = dispatch({"action": "prepare_groups", "plate": import_plates(), "text": text})
    assert not response["ok"]
    assert response["error"]["code"] == "invalid_group_map"


def test_duplicate_plate_ids_are_rejected():
    response = dispatch({"action": "import_plates", "files": [plate_file(), plate_file()]})
    assert not response["ok"]
    assert response["error"]["code"] == "duplicate_plate"


def test_existing_group_names_are_not_silently_overwritten():
    data = prepare(import_plates(), "group,plate,wells\nGroup A,P1,A1-B4\n", existing_names=["group a"])
    assert data["projects"] == []
    assert "already prepared" in data["failures"]["Group A"]


def test_archive_filenames_are_unique_and_cannot_escape_archive():
    data = prepare(import_plates(), "group,plate,wells\nGroup A,P1,A1-B4\nGroup_A,P1,B5-C8\n../../Group A,P1,C9-D12\n")
    exported = ok({"action": "export_group_csvs", "projects": data["projects"]})
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(exported["base64"]))) as archive:
        assert archive.namelist() == ["Group_A.csv", "Group_A_2.csv", "Group_A_3.csv"]


def test_repeated_reads_require_explicit_selection():
    first, second = plate_file(), plate_file(offset=1000)
    plate = import_plates({"filename": "P1.csv", "text": first["text"] + second["text"]})
    data = prepare(plate, "group,plate,wells\nAmbiguous,P1,A1-B4\n")
    assert not data["projects"]
    assert "repeated" in data["failures"]["Ambiguous"]
    data = prepare(plate, "group,plate,wells,repeat_policy,acquisition_id\nSelected,P1,A1-B4,select,spectrum_2\n")
    assert data["failures"] == {}
    assert data["projects"][0]["observations"][0]["raw_signal"] == 2000


def test_shared_repeat_policy_is_a_fallback_and_csv_rows_can_override_it():
    first, second = plate_file(), plate_file(offset=1000)
    plate = import_plates({"filename": "P1.csv", "text": first["text"] + second["text"]})
    data = prepare(
        plate,
        "group,plate,wells,repeat_policy,acquisition_id\nDefault,P1,A1-B4,,\nOverride,P1,B5-C8,select,spectrum_2\n",
        repeat_policy={"mode": "mean", "technical_replicates": True, "label": "Repeat pair"},
    )
    assert data["failures"] == {}
    first, second = data["projects"]
    assert first["observations"][0]["raw_signal"] == 1500
    assert first["settings"]["repeat_policy"]["label"] == "Repeat pair"
    assert second["observations"][0]["raw_signal"] == 1920
    assert second["settings"]["repeat_policy"]["mode"] == "select"


def test_observation_limit_includes_already_prepared_groups():
    data = prepare(import_plates(), "group,plate,wells\nExtra,P1,A1-B4\n", existing_observation_count=4990)
    assert data["projects"] == []
    assert "5000 observations" in data["failures"]["Extra"]
