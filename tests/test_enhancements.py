from pathlib import Path

import pytest

from folding_practical.enhancements import (
    _inspect_group_map,
    _parse_concentration_order,
    _sortable_value,
)
from folding_practical.wells import expand_well_spec


def test_parse_concentration_order_accepts_common_separators():
    values = _parse_concentration_order("0, 0.4; 0.8 1.2", 4)
    assert values == [0.0, 0.4, 0.8, 1.2]


def test_parse_concentration_order_checks_count():
    with pytest.raises(ValueError, match="exactly 3"):
        _parse_concentration_order("0, 1", 3)


def test_inspect_group_map_detects_shared_well_count(tmp_path: Path):
    path = tmp_path / "groups.csv"
    path.write_text(
        "group name,plate number,well ranges\n"
        "A1,P1,A1-B4\n"
        "A2,P2,C1-D4\n",
        encoding="utf-8",
    )
    result = _inspect_group_map(str(path), expand_well_spec)
    assert result["group_count"] == 2
    assert result["same_count"] is True
    assert result["well_count"] == 16
    assert result["missing_concentrations"] is True


def test_inspect_group_map_detects_mixed_counts(tmp_path: Path):
    path = tmp_path / "groups.csv"
    path.write_text(
        "group name,plate number,well ranges\n"
        "A1,P1,A1-A8\n"
        "A2,P2,B1-B12\n",
        encoding="utf-8",
    )
    result = _inspect_group_map(str(path), expand_well_spec)
    assert result["same_count"] is False


def test_sortable_value_prefers_numbers_then_text_then_blanks():
    values = ["", "10", "2", "Failed"]
    assert sorted(values, key=_sortable_value) == ["2", "10", "Failed", ""]
