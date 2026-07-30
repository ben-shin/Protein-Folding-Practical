"""Tools for importing, organizing, fitting, and plotting GFP denaturation data."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__version__ = "0.3.0"

__all__ = [
    "FitResult",
    "GroupAssignment",
    "build_group_dataframe",
    "build_spectrum_dataframe",
    "fit_four_parameter_logistic",
    "fit_two_state_denaturation",
    "load_group_map_assignments",
    "load_plate_csv",
    "load_plate_csvs",
]

_LAZY_IMPORTS = {
    "FitResult": (".models", "FitResult"),
    "fit_four_parameter_logistic": (".models", "fit_four_parameter_logistic"),
    "fit_two_state_denaturation": (".models", "fit_two_state_denaturation"),
    "load_plate_csv": (".plate_io", "load_plate_csv"),
    "load_plate_csvs": (".plate_io", "load_plate_csvs"),
    "GroupAssignment": (".project", "GroupAssignment"),
    "build_group_dataframe": (".project", "build_group_dataframe"),
    "build_spectrum_dataframe": (".project", "build_spectrum_dataframe"),
    "load_group_map_assignments": (".project", "load_group_map_assignments"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _LAZY_IMPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value
