"""Protein folding practical analysis package."""

from __future__ import annotations

from typing import Any

__version__ = "0.4.0"

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


def __getattr__(name: str) -> Any:
    if name in {"FitResult", "fit_four_parameter_logistic", "fit_two_state_denaturation"}:
        from .models import FitResult, fit_four_parameter_logistic, fit_two_state_denaturation

        return {
            "FitResult": FitResult,
            "fit_four_parameter_logistic": fit_four_parameter_logistic,
            "fit_two_state_denaturation": fit_two_state_denaturation,
        }[name]
    if name in {
        "GroupAssignment",
        "build_group_dataframe",
        "build_spectrum_dataframe",
        "load_group_map_assignments",
    }:
        from .project import (
            GroupAssignment,
            build_group_dataframe,
            build_spectrum_dataframe,
            load_group_map_assignments,
        )

        return {
            "GroupAssignment": GroupAssignment,
            "build_group_dataframe": build_group_dataframe,
            "build_spectrum_dataframe": build_spectrum_dataframe,
            "load_group_map_assignments": load_group_map_assignments,
        }[name]
    if name in {"load_plate_csv", "load_plate_csvs"}:
        from .plate_io import load_plate_csv, load_plate_csvs

        return {"load_plate_csv": load_plate_csv, "load_plate_csvs": load_plate_csvs}[name]
    raise AttributeError(name)
