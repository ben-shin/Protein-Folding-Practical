"""tools for importing organizing fitting and plotting GFP denaturation data."""

from .models import FitResult, fit_four_parameter_logistic, fit_two_state_denaturation
from .plate_io import load_plate_csv, load_plate_csvs
from .project import GroupAssignment, build_group_dataframe

__all__ = [
  "FitResult",
  "GroupAssignment",
  "build_group_dataframe",
  "fit_four_parameter_logistic",
  "load_plate_csv",
  "load_plate_csvs"
]

__version__ = "0.1.0"
