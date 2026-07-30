import pytest

from folding_practical.wells import consecutive_wells, expand_well_spec, normalize_well


def test_normalize_well():
    assert normalize_well("a01") == "A1"
    assert normalize_well("H12") == "H12"
    with pytest.raises(ValueError):
        normalize_well("I1")


def test_expand_well_ranges():
    assert expand_well_spec("A1:A4") == ["A1", "A2", "A3", "A4"]
    assert expand_well_spec("A1:D1") == ["A1", "B1", "C1", "D1"]
    assert expand_well_spec("A1, B2, H12") == ["A1", "B2", "H12"]


def test_consecutive_wells():
    assert consecutive_wells("A11", 4, "row-major") == ["A11", "A12", "B1", "B2"]
    assert consecutive_wells("G1", 4, "column-major") == ["G1", "H1", "A2", "B2"]


def test_expand_hyphenated_well_range():
    assert expand_well_spec("A1-B4") == [
        "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8",
        "A9", "A10", "A11", "A12", "B1", "B2", "B3", "B4",
    ]
