"""Curve fitting for GFP chemical denaturation data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
from scipy.optimize import curve_fit

R_KJ_PER_MOL_K = 0.008314462618


@dataclass
class FitResult:
    model_name: str
    success: bool
    parameters: dict[str, float] = field(default_factory=dict)
    standard_errors: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    predicted: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    message: str = ""
    parameter_order: tuple[str, ...] = ()
    covariance: Optional[np.ndarray] = None
    prediction_function: Optional[Callable[[np.ndarray], np.ndarray]] = None

    def predict(self, x: np.ndarray) -> np.ndarray:
        if self.prediction_function is None:
            raise RuntimeError("No prediction function is available for this fit")
        return np.asarray(self.prediction_function(np.asarray(x, dtype=float)), dtype=float)


def _validate_xy(x: np.ndarray, y: np.ndarray, minimum_points: int) -> tuple[np.ndarray, np.ndarray]:
    x_array = np.asarray(x, dtype=float)
    y_array = np.asarray(y, dtype=float)
    valid = np.isfinite(x_array) & np.isfinite(y_array)
    x_array = x_array[valid]
    y_array = y_array[valid]
    if len(x_array) < minimum_points:
        raise ValueError(f"At least {minimum_points} finite points are required")
    order = np.argsort(x_array)
    return x_array[order], y_array[order]


def _fit_metrics(y: np.ndarray, predicted: np.ndarray, parameter_count: int) -> dict[str, float]:
    residuals = y - predicted
    rss = float(np.sum(residuals**2))
    n = len(y)
    rmse = float(np.sqrt(rss / n))
    total = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = float(1.0 - rss / total) if total > 0 else float("nan")
    safe_rss = max(rss, np.finfo(float).tiny)
    aic = float(n * np.log(safe_rss / n) + 2 * parameter_count)
    bic = float(n * np.log(safe_rss / n) + parameter_count * np.log(n))
    if n > parameter_count + 1:
        aicc = float(aic + (2 * parameter_count * (parameter_count + 1)) / (n - parameter_count - 1))
    else:
        aicc = float("inf")
    return {"rss": rss, "rmse": rmse, "r_squared": r_squared, "aic": aic, "aicc": aicc, "bic": bic}


def four_parameter_logistic(
    concentration: np.ndarray,
    low_denaturant_signal: float,
    high_denaturant_signal: float,
    midpoint_m: float,
    width_m: float,
) -> np.ndarray:
    exponent = np.clip((concentration - midpoint_m) / width_m, -700, 700)
    fraction_high_denaturant = 1.0 / (1.0 + np.exp(-exponent))
    return low_denaturant_signal + (high_denaturant_signal - low_denaturant_signal) * fraction_high_denaturant


def fit_four_parameter_logistic(x: np.ndarray, y: np.ndarray) -> FitResult:
    """Fit a four-parameter logistic curve for descriptive/QC purposes."""
    try:
        x_array, y_array = _validate_xy(x, y, minimum_points=5)
        x_range = max(float(np.ptp(x_array)), 1e-3)
        y_range = max(float(np.ptp(y_array)), 1e-9)
        low_guess = float(np.mean(y_array[: max(2, len(y_array) // 5)]))
        high_guess = float(np.mean(y_array[-max(2, len(y_array) // 5) :]))
        midpoint_guess = float(x_array[np.argmin(np.abs(y_array - (low_guess + high_guess) / 2.0))])
        width_guess = max(x_range / 8.0, 0.05)
        lower = [float(np.min(y_array) - 5 * y_range), float(np.min(y_array) - 5 * y_range), float(np.min(x_array) - x_range), 1e-4]
        upper = [float(np.max(y_array) + 5 * y_range), float(np.max(y_array) + 5 * y_range), float(np.max(x_array) + x_range), 10 * x_range]
        popt, covariance = curve_fit(
            four_parameter_logistic,
            x_array,
            y_array,
            p0=[low_guess, high_guess, midpoint_guess, width_guess],
            bounds=(lower, upper),
            maxfev=100_000,
        )
        predicted = four_parameter_logistic(x_array, *popt)
        names = ("low_denaturant_signal", "high_denaturant_signal", "midpoint_m", "width_m")
        standard_errors = np.sqrt(np.clip(np.diag(covariance), 0, np.inf))
        parameters = dict(zip(names, map(float, popt)))
        errors = dict(zip(names, map(float, standard_errors)))
        return FitResult(
            model_name="4PL logistic",
            success=True,
            parameters=parameters,
            standard_errors=errors,
            metrics=_fit_metrics(y_array, predicted, len(popt)),
            predicted=predicted,
            parameter_order=names,
            covariance=covariance,
            prediction_function=lambda values: four_parameter_logistic(np.asarray(values, dtype=float), *popt),
        )
    except Exception as exc:  # fitting failures need to be reported rather than crash the GUI
        return FitResult(model_name="4PL logistic", success=False, message=str(exc))


def two_state_denaturation_signal(
    concentration: np.ndarray,
    folded_intercept: float,
    folded_slope: float,
    unfolded_intercept: float,
    unfolded_slope: float,
    delta_g_h2o_kj_mol: float,
    m_value_kj_mol_m: float,
    temperature_k: float,
) -> np.ndarray:
    folded_baseline = folded_intercept + folded_slope * concentration
    unfolded_baseline = unfolded_intercept + unfolded_slope * concentration
    delta_g = delta_g_h2o_kj_mol - m_value_kj_mol_m * concentration
    fraction_unfolded = 1.0 / (1.0 + np.exp(np.clip(delta_g / (R_KJ_PER_MOL_K * temperature_k), -700, 700)))
    return folded_baseline * (1.0 - fraction_unfolded) + unfolded_baseline * fraction_unfolded


def fit_two_state_denaturation(x: np.ndarray, y: np.ndarray, temperature_k: float = 298.15) -> FitResult:
    """Fit a two-state linear-extrapolation model.

    ``delta_g_h2o_kj_mol`` is the unfolding free energy extrapolated to zero
    denaturant and ``m_value_kj_mol_m`` is the denaturant dependence. The model
    includes linear folded and unfolded fluorescence baselines.
    """
    try:
        if not 260.0 <= float(temperature_k) <= 330.0:
            raise ValueError("Temperature must be supplied in kelvin and lie between 260 and 330 K")
        x_array, y_array = _validate_xy(x, y, minimum_points=8)
        x_range = max(float(np.ptp(x_array)), 1e-3)
        y_range = max(float(np.ptp(y_array)), 1e-9)
        edge_count = max(2, min(4, len(x_array) // 4))

        folded_slope, folded_intercept = np.polyfit(x_array[:edge_count], y_array[:edge_count], 1)
        unfolded_slope, unfolded_intercept = np.polyfit(x_array[-edge_count:], y_array[-edge_count:], 1)
        midpoint_guess = float(np.median(x_array))
        m_guess = 6.0
        dg_guess = max(midpoint_guess * m_guess, 1.0)

        def model(values: np.ndarray, *parameters: float) -> np.ndarray:
            return two_state_denaturation_signal(values, *parameters, temperature_k=float(temperature_k))

        baseline_low = float(np.min(y_array) - 10 * y_range)
        baseline_high = float(np.max(y_array) + 10 * y_range)
        max_slope = 20 * y_range / x_range
        lower = [baseline_low, -max_slope, baseline_low, -max_slope, 0.01, 0.01]
        upper = [baseline_high, max_slope, baseline_high, max_slope, 150.0, 60.0]
        p0 = [folded_intercept, folded_slope, unfolded_intercept, unfolded_slope, dg_guess, m_guess]
        p0 = np.minimum(np.maximum(np.asarray(p0, dtype=float), np.asarray(lower) + 1e-9), np.asarray(upper) - 1e-9)

        popt, covariance = curve_fit(
            model,
            x_array,
            y_array,
            p0=p0,
            bounds=(lower, upper),
            maxfev=200_000,
        )
        predicted = model(x_array, *popt)
        names = (
            "folded_intercept",
            "folded_slope",
            "unfolded_intercept",
            "unfolded_slope",
            "delta_g_h2o_kj_mol",
            "m_value_kj_mol_m",
        )
        standard_errors = np.sqrt(np.clip(np.diag(covariance), 0, np.inf))
        parameters = dict(zip(names, map(float, popt)))
        errors = dict(zip(names, map(float, standard_errors)))

        dg = parameters["delta_g_h2o_kj_mol"]
        m_value = parameters["m_value_kj_mol_m"]
        cm = dg / m_value
        parameters["cm_m"] = float(cm)
        parameters["delta_g_folding_h2o_kj_mol"] = float(-dg)
        covariance_dg_m = covariance[np.ix_([4, 5], [4, 5])]
        gradient = np.array([1.0 / m_value, -dg / (m_value**2)], dtype=float)
        cm_variance = float(gradient @ covariance_dg_m @ gradient.T)
        errors["cm_m"] = float(np.sqrt(max(cm_variance, 0.0)))
        errors["delta_g_folding_h2o_kj_mol"] = errors["delta_g_h2o_kj_mol"]

        return FitResult(
            model_name="Two-state LEM",
            success=True,
            parameters=parameters,
            standard_errors=errors,
            metrics=_fit_metrics(y_array, predicted, len(popt)),
            predicted=predicted,
            parameter_order=names,
            covariance=covariance,
            prediction_function=lambda values: model(np.asarray(values, dtype=float), *popt),
        )
    except Exception as exc:
        return FitResult(model_name="Two-state LEM", success=False, message=str(exc))


def choose_best_fit(results: list[FitResult]) -> Optional[FitResult]:
    successful = [result for result in results if result.success]
    if not successful:
        return None
    return min(successful, key=lambda result: (result.metrics.get("aicc", float("inf")), result.metrics.get("aic", float("inf"))))
