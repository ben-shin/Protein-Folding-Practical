"""Protein folding practical analysis package."""

from __future__ import annotations

from typing import Any

from ._version import __version__

__all__ = [
    "FitResult",
    "GroupAssignment",
    "build_group_dataframe",
    "build_group_spectrum_matrix",
    "build_spectrum_dataframe",
    "concentration_labels",
    "export_group_spectrum_csv",
    "fit_four_parameter_logistic",
    "fit_two_state_denaturation",
    "inspect_group_map",
    "load_group_map_assignments",
    "load_plate_csv",
    "load_plate_csvs",
    "run_batch_export",
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
        "build_group_spectrum_matrix",
        "build_spectrum_dataframe",
        "concentration_labels",
        "export_group_spectrum_csv",
        "inspect_group_map",
        "load_group_map_assignments",
    }:
        from . import project

        return getattr(project, name)
    if name == "run_batch_export":
        from .batch import run_batch_export

        return run_batch_export
    if name in {"load_plate_csv", "load_plate_csvs"}:
        from .plate_io import load_plate_csv, load_plate_csvs

        return {"load_plate_csv": load_plate_csv, "load_plate_csvs": load_plate_csvs}[name]
    raise AttributeError(name)
