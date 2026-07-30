"""curve fitting for GFP denaturation data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from scipy.optimize import curve_fit

R_KJ_PER_MOL_K = 0.008314462618

@dataclass(slots=True)
class FitResult:
  model_name: str
  success: bool
  parameters: dict[str, float] = field(default_factory=dict)
  standard_errors: dict[str, float] = field(default_factory=dict)
  metrics: dict[str, float] = field(default_factory=dict)
  predicted: np.ndarray = field(default_factory=lambda : np.array([], dtype=float))
  message: str = ""
  parameter_order: tuple[str, ...] = ()
  covariance: np.ndarray | None = None
  prediction_function: Callable[[np.ndarray], np.ndarray] | None = None

  def predict(self, x: np.ndarray) -> np.ndarray:
    if self.prediction_function is None:
      raise RuntimeError("no prediction is available for this fit")
    return np.asarray(self.prediction_function(np.asarray(x, dtype=float)), dtype=float)
