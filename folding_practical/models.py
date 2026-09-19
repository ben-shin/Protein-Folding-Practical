"""Curve fitting and fit-quality diagnostics for GFP denaturation data.

The equations in this module are intentionally small and explicit.  Optimizer
success is reported separately from whether the data identify a scientifically
interpretable transition: a numerical curve can be fitted to a flat or partial
trace, but that does not make its midpoint or thermodynamic parameters useful.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import warnings as python_warnings
from typing import Callable, Optional

import numpy as np
from scipy.optimize import OptimizeWarning, curve_fit

R_KJ_PER_MOL_K = 0.008314462618


@dataclass
class FitResult:
    """Result of a model fit.

    The fields that pre-date the diagnostic API remain unchanged.  ``success``
    means that the numerical optimization produced a finite solution.
    ``interpretation_status`` is the independent scientific quality judgement.
    """

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

    # Additive diagnostic API.  Defaults keep construction of legacy results
    # source compatible.
    interpretation_status: str = "fit_failed"
    diagnostics: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    retained_indices: np.ndarray = field(default_factory=lambda: np.array([], dtype=int))
    excluded_indices: np.ndarray = field(default_factory=lambda: np.array([], dtype=int))
    retained_x: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    retained_y: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    residuals: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    data_fingerprint: str = ""

    def predict(self, x: np.ndarray) -> np.ndarray:
        if self.prediction_function is None:
            raise RuntimeError("No prediction function is available for this fit")
        return np.asarray(self.prediction_function(np.asarray(x, dtype=float)), dtype=float)


@dataclass
class _PreparedData:
    x: np.ndarray
    y: np.ndarray
    retained_indices: np.ndarray
    excluded_indices: np.ndarray
    input_count: int
    fingerprint: str


@dataclass
class _MultiStartResult:
    parameters: np.ndarray
    covariance: np.ndarray
    diagnostics: dict[str, object]
    optimizer_warnings: list[str]


def _data_fingerprint(x: np.ndarray, y: np.ndarray) -> str:
    """Return an exact, deterministic identity for the retained paired data."""
    digest = hashlib.sha256()
    for values in (x, y):
        canonical = np.ascontiguousarray(values, dtype="<f8")
        digest.update(np.asarray(canonical.shape, dtype="<i8").tobytes())
        digest.update(canonical.tobytes())
    return digest.hexdigest()


def _prepare_xy(x: np.ndarray, y: np.ndarray) -> _PreparedData:
    x_array = np.asarray(x, dtype=float)
    y_array = np.asarray(y, dtype=float)
    if x_array.ndim != 1 or y_array.ndim != 1:
        raise ValueError("Concentration and response must both be one-dimensional")
    if x_array.shape != y_array.shape:
        raise ValueError("Concentration and response must contain the same number of values")
    if x_array.size == 0:
        raise ValueError("Concentration and response cannot be empty")

    original_indices = np.arange(x_array.size, dtype=int)
    valid = np.isfinite(x_array) & np.isfinite(y_array)
    retained_indices = original_indices[valid]
    excluded_indices = original_indices[~valid]
    retained_x = x_array[valid]
    retained_y = y_array[valid]
    order = np.argsort(retained_x, kind="mergesort")
    retained_x = retained_x[order]
    retained_y = retained_y[order]
    retained_indices = retained_indices[order]
    return _PreparedData(
        x=retained_x,
        y=retained_y,
        retained_indices=retained_indices,
        excluded_indices=excluded_indices,
        input_count=int(x_array.size),
        fingerprint=_data_fingerprint(retained_x, retained_y),
    )


def _data_diagnostics(data: _PreparedData) -> dict[str, object]:
    x = data.x
    y = data.y
    diagnostics: dict[str, object] = {
        "input_count": data.input_count,
        "retained_count": int(x.size),
        "excluded_nonfinite_count": int(data.excluded_indices.size),
        "distinct_concentration_count": int(np.unique(x).size),
        "replicate_observation_count": int(x.size - np.unique(x).size),
    }
    if x.size:
        diagnostics.update(
            {
                "concentration_min_m": float(np.min(x)),
                "concentration_max_m": float(np.max(x)),
                "concentration_span_m": float(np.ptp(x)),
                "response_min": float(np.min(y)),
                "response_max": float(np.max(y)),
                "response_dynamic_range": float(np.ptp(y)),
            }
        )

        unique_x, inverse, counts = np.unique(x, return_inverse=True, return_counts=True)
        replicate_ss = 0.0
        replicate_df = 0
        replicate_groups = 0
        for group_index, count in enumerate(counts):
            if count > 1:
                values = y[inverse == group_index]
                replicate_ss += float(np.sum((values - np.mean(values)) ** 2))
                replicate_df += int(count - 1)
                replicate_groups += 1
        diagnostics["replicate_group_count"] = replicate_groups
        if replicate_df:
            replicate_noise = float(np.sqrt(replicate_ss / replicate_df))
            diagnostics["replicate_noise_sigma"] = replicate_noise
            diagnostics["dynamic_range_to_replicate_noise"] = (
                float(np.ptp(y) / replicate_noise) if replicate_noise > 0 else None
            )
        else:
            diagnostics["replicate_noise_sigma"] = None
            diagnostics["dynamic_range_to_replicate_noise"] = None
        # Keep this assignment so static tools know unique_x is intentionally used
        # only to count independent concentration locations above.
        del unique_x
    return diagnostics


def _fit_metrics(y: np.ndarray, predicted: np.ndarray, parameter_count: int) -> dict[str, float]:
    """Calculate residual and Gaussian information-criterion metrics.

    AIC/BIC use the maximized Gaussian iid likelihood with residual variance
    estimated from the data.  The variance is therefore counted as one further
    parameter.  AICc is undefined (NaN) when ``n <= k + 1``.
    """
    residuals = y - predicted
    rss = float(np.sum(residuals**2))
    n = int(y.size)
    rmse = float(np.sqrt(rss / n))
    total = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = float(1.0 - rss / total) if total > 0 else float("nan")

    residual_variance = float(rss / n)
    safe_variance = max(residual_variance, np.finfo(float).tiny)
    negative_twice_log_likelihood = float(n * (np.log(2.0 * np.pi) + 1.0 + np.log(safe_variance)))
    information_parameter_count = int(parameter_count + 1)  # fitted mean + residual variance
    aic = float(negative_twice_log_likelihood + 2.0 * information_parameter_count)
    bic = float(negative_twice_log_likelihood + information_parameter_count * np.log(n))
    if n > information_parameter_count + 1:
        aicc = float(
            aic
            + (2.0 * information_parameter_count * (information_parameter_count + 1))
            / (n - information_parameter_count - 1)
        )
    else:
        aicc = float("nan")

    centered_residuals = residuals - np.mean(residuals)
    residual_std = float(np.sqrt(np.mean(centered_residuals**2)))
    return {
        "rss": rss,
        "rmse": rmse,
        "r_squared": r_squared,
        "aic": aic,
        "aicc": aicc,
        "bic": bic,
        "log_likelihood": float(-0.5 * negative_twice_log_likelihood),
        "residual_variance_mle": residual_variance,
        "residual_mean": float(np.mean(residuals)),
        "residual_std": residual_std,
        "max_abs_residual": float(np.max(np.abs(residuals))),
        "observation_count": float(n),
        "model_parameter_count": float(parameter_count),
        "information_parameter_count": float(information_parameter_count),
    }


def _deduplicate_starts(starts: list[np.ndarray]) -> list[np.ndarray]:
    unique: list[np.ndarray] = []
    seen: set[tuple[float, ...]] = set()
    for start in starts:
        key = tuple(np.round(np.asarray(start, dtype=float), decimals=12))
        if key not in seen:
            seen.add(key)
            unique.append(np.asarray(start, dtype=float))
    return unique


def _bounded_multistart(
    model: Callable[..., np.ndarray],
    x: np.ndarray,
    y: np.ndarray,
    starts: list[np.ndarray],
    lower: np.ndarray,
    upper: np.ndarray,
    *,
    maxfev_per_start: int,
) -> _MultiStartResult:
    """Run a deterministic, explicitly bounded number of bounded fits."""
    starts = _deduplicate_starts(starts)
    candidates: list[tuple[float, np.ndarray, np.ndarray, np.ndarray]] = []
    warning_messages: list[str] = []
    failure_messages: list[str] = []

    for start in starts:
        clipped = np.minimum(np.maximum(start, lower + 1e-10), upper - 1e-10)
        try:
            with python_warnings.catch_warnings(record=True) as caught:
                python_warnings.simplefilter("always", OptimizeWarning)
                popt, covariance = curve_fit(
                    model,
                    x,
                    y,
                    p0=clipped,
                    bounds=(lower, upper),
                    maxfev=maxfev_per_start,
                )
            warning_messages.extend(str(item.message) for item in caught)
            predicted = np.asarray(model(x, *popt), dtype=float)
            if not np.all(np.isfinite(popt)) or not np.all(np.isfinite(predicted)):
                raise RuntimeError("optimizer returned non-finite parameters or predictions")
            rss = float(np.sum((y - predicted) ** 2))
            candidates.append((rss, np.asarray(popt, dtype=float), np.asarray(covariance, dtype=float), predicted))
        except Exception as exc:  # one failed start must not discard other viable starts
            failure_messages.append(str(exc))

    if not candidates:
        detail = failure_messages[-1] if failure_messages else "no optimizer result"
        raise RuntimeError(f"All {len(starts)} bounded optimization starts failed: {detail}")

    candidates.sort(key=lambda item: item[0])
    best_rss, best_parameters, best_covariance, _ = candidates[0]
    response_scale = max(float(np.ptp(y)), float(np.max(np.abs(y))), 1.0)
    near_tolerance = max(best_rss * 0.01, y.size * response_scale**2 * 1e-12, 1e-24)
    near_best = [candidate for candidate in candidates if candidate[0] <= best_rss + near_tolerance]

    bound_span = np.maximum(upper - lower, np.finfo(float).eps)
    normalized_parameters = np.vstack([candidate[1] / bound_span for candidate in near_best])
    parameter_spread = float(np.max(np.ptp(normalized_parameters, axis=0))) if len(near_best) > 1 else 0.0
    prediction_matrix = np.vstack([candidate[3] for candidate in near_best])
    prediction_spread = (
        float(np.max(np.ptp(prediction_matrix, axis=0)) / max(float(np.ptp(y)), response_scale * 1e-12))
        if len(near_best) > 1
        else 0.0
    )
    consistent = bool(parameter_spread <= 0.05 and prediction_spread <= 0.02)

    diagnostics: dict[str, object] = {
        "multistart_attempt_count": len(starts),
        "multistart_success_count": len(candidates),
        "multistart_failure_count": len(starts) - len(candidates),
        "multistart_near_best_count": len(near_best),
        "multistart_parameter_spread_fraction": parameter_spread,
        "multistart_prediction_spread_fraction": prediction_spread,
        "multistart_consistent": consistent,
        "optimizer_warning_count": len(warning_messages),
    }
    return _MultiStartResult(
        parameters=best_parameters,
        covariance=best_covariance,
        diagnostics=diagnostics,
        optimizer_warnings=sorted(set(warning_messages)),
    )


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


def _fraction_coverage_diagnostics(x: np.ndarray, fraction: np.ndarray) -> dict[str, object]:
    # Coverage counts refer to distinct concentration locations, not replicate
    # rows, so replication cannot masquerade as experimental coverage.
    unique_x = np.unique(x)
    unique_fraction = np.interp(unique_x, x, fraction)
    low_count = int(np.count_nonzero(unique_fraction <= 0.1))
    high_count = int(np.count_nonzero(unique_fraction >= 0.9))
    transition_count = int(np.count_nonzero((unique_fraction > 0.1) & (unique_fraction < 0.9)))
    central_count = int(np.count_nonzero((unique_fraction >= 0.2) & (unique_fraction <= 0.8)))
    return {
        "low_denaturant_baseline_distinct_count": low_count,
        "high_denaturant_baseline_distinct_count": high_count,
        "transition_distinct_count": transition_count,
        "central_transition_distinct_count": central_count,
        "low_denaturant_baseline_covered": bool(low_count > 0),
        "high_denaturant_baseline_covered": bool(high_count > 0),
        "transition_covered": bool(transition_count > 0),
        "fraction_unfolded_min": float(np.min(unique_fraction)),
        "fraction_unfolded_max": float(np.max(unique_fraction)),
    }


def _covariance_diagnostics(
    covariance: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    residual_variance: float,
    response_scale: float,
) -> dict[str, object]:
    parameter_count = lower.size
    if covariance.shape != (parameter_count, parameter_count) or not np.all(np.isfinite(covariance)):
        return {
            "covariance_status": "nonfinite",
            "covariance_rank": None,
            "covariance_condition_number": None,
            "covariance_well_conditioned": False,
        }
    if residual_variance <= max(response_scale**2 * 1e-24, np.finfo(float).tiny):
        return {
            "covariance_status": "zero_residual_variance",
            "covariance_rank": int(np.linalg.matrix_rank(covariance)),
            "covariance_condition_number": None,
            "covariance_well_conditioned": None,
        }
    scale = np.maximum(upper - lower, np.finfo(float).eps)
    scaled = covariance / np.outer(scale, scale)
    rank = int(np.linalg.matrix_rank(scaled))
    condition = float(np.linalg.cond(scaled))
    well_conditioned = bool(rank == parameter_count and np.isfinite(condition) and condition <= 1e12)
    return {
        "covariance_status": "finite",
        "covariance_rank": rank,
        "covariance_condition_number": condition if np.isfinite(condition) else None,
        "covariance_well_conditioned": well_conditioned,
    }


def _bound_diagnostics(
    parameters: np.ndarray,
    names: tuple[str, ...],
    lower: np.ndarray,
    upper: np.ndarray,
) -> dict[str, object]:
    span = np.maximum(upper - lower, np.finfo(float).eps)
    proximity = np.minimum((parameters - lower) / span, (upper - parameters) / span)
    hit_names = [name for name, value in zip(names, proximity) if value <= 1e-3]
    return {
        "bound_hit": bool(hit_names),
        "parameter_bounds": {
            name: {"lower": float(lo), "upper": float(hi)}
            for name, lo, hi in zip(names, lower, upper)
        },
        "bound_hit_parameters": hit_names,
        "minimum_relative_distance_to_bound": float(np.min(proximity)),
    }


def _lag_one_residual_correlation(residuals: np.ndarray) -> Optional[float]:
    if residuals.size < 4:
        return None
    left = residuals[:-1] - np.mean(residuals[:-1])
    right = residuals[1:] - np.mean(residuals[1:])
    denominator = float(np.sqrt(np.sum(left**2) * np.sum(right**2)))
    if denominator <= np.finfo(float).tiny:
        return None
    return float(np.sum(left * right) / denominator)


def _interpretation(
    *,
    data: _PreparedData,
    diagnostics: dict[str, object],
    parameters: np.ndarray,
    names: tuple[str, ...],
    lower: np.ndarray,
    upper: np.ndarray,
    covariance: np.ndarray,
    metrics: dict[str, float],
    fraction_unfolded: np.ndarray,
    midpoint_m: float,
    transition_width_m: float,
    transition_signal_separation: float,
    critical_parameters: set[str],
) -> tuple[str, list[str], dict[str, object]]:
    warnings: list[str] = []
    insufficient = False
    caution = False
    x = data.x
    y = data.y
    y_scale = max(float(np.max(np.abs(y))), 1.0)
    dynamic_range = float(np.ptp(y))
    flat_tolerance = max(y_scale * 1e-6, 1e-12)

    coverage = _fraction_coverage_diagnostics(x, fraction_unfolded)
    bound_info = _bound_diagnostics(parameters, names, lower, upper)
    covariance_info = _covariance_diagnostics(
        covariance,
        lower,
        upper,
        metrics["residual_variance_mle"],
        y_scale,
    )

    diagnostics.update(coverage)
    diagnostics.update(bound_info)
    diagnostics.update(covariance_info)
    diagnostics["fitted_midpoint_m"] = float(midpoint_m)
    diagnostics["transition_width_m"] = float(transition_width_m)
    diagnostics["transition_signal_separation"] = float(transition_signal_separation)
    diagnostics["midpoint_within_observed_range"] = bool(np.min(x) <= midpoint_m <= np.max(x))
    diagnostics["transition_width_fraction_of_span"] = float(
        transition_width_m / max(float(np.ptp(x)), np.finfo(float).eps)
    )
    diagnostics["residual_rmse_fraction_dynamic_range"] = (
        float(metrics["rmse"] / dynamic_range) if dynamic_range > 0 else None
    )

    if data.excluded_indices.size:
        warnings.append(
            f"Excluded {data.excluded_indices.size} non-finite paired observation(s) at original indices "
            f"{data.excluded_indices.tolist()}."
        )
    if diagnostics["distinct_concentration_count"] < len(names) + 1:
        insufficient = True
        warnings.append("Too few distinct concentration values to identify all fitted model parameters.")
    if float(diagnostics.get("concentration_span_m", 0.0)) <= 1e-12:
        insufficient = True
        warnings.append("The concentration span is effectively zero.")
    if dynamic_range <= flat_tolerance:
        insufficient = True
        warnings.append("The response is effectively flat; transition parameters are not identifiable.")
    noise_ratio = diagnostics.get("dynamic_range_to_replicate_noise")
    if isinstance(noise_ratio, float) and np.isfinite(noise_ratio):
        if noise_ratio < 3.0:
            insufficient = True
            warnings.append("The observed response range is less than three times the replicate noise.")
        elif noise_ratio < 6.0:
            caution = True
            warnings.append("The observed response range is only modestly larger than replicate noise.")

    if not diagnostics["midpoint_within_observed_range"]:
        insufficient = True
        warnings.append("The fitted transition midpoint lies outside the observed concentration range.")
    if not coverage["low_denaturant_baseline_covered"]:
        insufficient = True
        warnings.append("The data do not cover the low-denaturant baseline.")
    if not coverage["high_denaturant_baseline_covered"]:
        insufficient = True
        warnings.append("The data do not cover the high-denaturant baseline.")
    if not coverage["transition_covered"]:
        insufficient = True
        warnings.append("The data do not sample the fitted transition region.")
    if (
        coverage["low_denaturant_baseline_distinct_count"] < 2
        or coverage["high_denaturant_baseline_distinct_count"] < 2
        or coverage["transition_distinct_count"] < 2
    ):
        caution = True
        warnings.append("One or more baseline/transition regions contain fewer than two distinct concentrations.")
    if transition_width_m > max(float(np.ptp(x)), np.finfo(float).eps):
        insufficient = True
        warnings.append("The fitted transition is broader than the observed concentration span.")
    elif transition_width_m > 0.5 * float(np.ptp(x)):
        caution = True
        warnings.append("The fitted transition occupies more than half of the observed concentration span.")
    if transition_signal_separation <= flat_tolerance:
        insufficient = True
        warnings.append("The fitted baselines have negligible separation at the transition midpoint.")

    hit_names = set(bound_info["bound_hit_parameters"])
    if hit_names:
        if hit_names & critical_parameters:
            insufficient = True
            warnings.append("A transition-defining parameter is at or extremely near an optimization bound.")
        else:
            caution = True
            warnings.append("At least one fitted baseline parameter is at or extremely near an optimization bound.")
    if covariance_info["covariance_status"] == "nonfinite":
        caution = True
        warnings.append("The parameter covariance matrix is non-finite.")
    elif covariance_info["covariance_well_conditioned"] is False:
        caution = True
        warnings.append("The parameter covariance matrix is rank-deficient or ill-conditioned.")
    if diagnostics.get("multistart_consistent") is False:
        caution = True
        warnings.append("Near-optimal multistart solutions are not parameter-consistent.")
    residual_fraction = diagnostics["residual_rmse_fraction_dynamic_range"]
    if isinstance(residual_fraction, float):
        if residual_fraction > 0.35:
            insufficient = True
            warnings.append("Residual error is large relative to the observed response range.")
        elif residual_fraction > 0.15:
            caution = True
            warnings.append("Residual error is appreciable relative to the observed response range.")

    if insufficient:
        return "insufficient_information", warnings, diagnostics
    if caution:
        return "caution", warnings, diagnostics
    return "interpretable", warnings, diagnostics


def _failure_result(model_name: str, exc: Exception, data: Optional[_PreparedData]) -> FitResult:
    diagnostics = _data_diagnostics(data) if data is not None else {}
    return FitResult(
        model_name=model_name,
        success=False,
        message=str(exc),
        interpretation_status="fit_failed",
        diagnostics=diagnostics,
        warnings=[str(exc)],
        retained_indices=(data.retained_indices.copy() if data is not None else np.array([], dtype=int)),
        excluded_indices=(data.excluded_indices.copy() if data is not None else np.array([], dtype=int)),
        retained_x=(data.x.copy() if data is not None else np.array([], dtype=float)),
        retained_y=(data.y.copy() if data is not None else np.array([], dtype=float)),
        data_fingerprint=(data.fingerprint if data is not None else ""),
    )


def fit_four_parameter_logistic(x: np.ndarray, y: np.ndarray) -> FitResult:
    """Fit a four-parameter logistic curve for descriptive/QC purposes."""
    data: Optional[_PreparedData] = None
    try:
        data = _prepare_xy(x, y)
        if data.x.size < 5:
            raise ValueError("At least 5 finite paired points are required")
        x_array = data.x
        y_array = data.y
        diagnostics = _data_diagnostics(data)
        x_range = max(float(np.ptp(x_array)), 1e-3)
        y_range = max(float(np.ptp(y_array)), max(float(np.max(np.abs(y_array))), 1.0) * 1e-9)
        edge_count = max(2, min(4, len(x_array) // 4))
        low_guess = float(np.mean(y_array[:edge_count]))
        high_guess = float(np.mean(y_array[-edge_count:]))
        half_signal = (low_guess + high_guess) / 2.0
        midpoint_crossing = float(x_array[np.argmin(np.abs(y_array - half_signal))])

        lower = np.asarray(
            [np.min(y_array) - 5 * y_range, np.min(y_array) - 5 * y_range, np.min(x_array) - x_range, 1e-4],
            dtype=float,
        )
        upper = np.asarray(
            [np.max(y_array) + 5 * y_range, np.max(y_array) + 5 * y_range, np.max(x_array) + x_range, 10 * x_range],
            dtype=float,
        )
        midpoint_guesses = [midpoint_crossing, *np.quantile(x_array, [0.25, 0.5, 0.75])]
        width_guesses = [max(x_range / 12.0, 0.02), max(x_range / 4.0, 0.05)]
        baseline_guesses = [
            (low_guess, high_guess),
            (float(np.max(y_array)), float(np.min(y_array))),
            (float(np.min(y_array)), float(np.max(y_array))),
        ]
        starts: list[np.ndarray] = []
        for baseline_low, baseline_high in baseline_guesses:
            for midpoint in midpoint_guesses:
                for width in width_guesses:
                    starts.append(np.asarray([baseline_low, baseline_high, midpoint, width], dtype=float))
        starts = starts[:16]

        multi = _bounded_multistart(
            four_parameter_logistic,
            x_array,
            y_array,
            starts,
            lower,
            upper,
            maxfev_per_start=20_000,
        )
        popt = multi.parameters
        covariance = multi.covariance
        predicted = four_parameter_logistic(x_array, *popt)
        residuals = y_array - predicted
        names = ("low_denaturant_signal", "high_denaturant_signal", "midpoint_m", "width_m")
        standard_errors = np.sqrt(np.clip(np.diag(covariance), 0, np.inf))
        parameters = dict(zip(names, map(float, popt)))
        errors = dict(zip(names, map(float, standard_errors)))
        metrics = _fit_metrics(y_array, predicted, len(popt))
        diagnostics.update(multi.diagnostics)
        diagnostics["residual_lag1_correlation"] = _lag_one_residual_correlation(residuals)
        fraction = 1.0 / (1.0 + np.exp(-np.clip((x_array - popt[2]) / popt[3], -700, 700)))
        status, quality_warnings, diagnostics = _interpretation(
            data=data,
            diagnostics=diagnostics,
            parameters=popt,
            names=names,
            lower=lower,
            upper=upper,
            covariance=covariance,
            metrics=metrics,
            fraction_unfolded=fraction,
            midpoint_m=float(popt[2]),
            transition_width_m=float(popt[3]),
            transition_signal_separation=float(abs(popt[1] - popt[0])),
            critical_parameters={"midpoint_m", "width_m"},
        )
        quality_warnings.extend(f"Optimizer warning: {item}" for item in multi.optimizer_warnings)
        return FitResult(
            model_name="4PL logistic",
            success=True,
            parameters=parameters,
            standard_errors=errors,
            metrics=metrics,
            predicted=predicted,
            parameter_order=names,
            covariance=covariance,
            prediction_function=lambda values: four_parameter_logistic(np.asarray(values, dtype=float), *popt),
            interpretation_status=status,
            diagnostics=diagnostics,
            warnings=quality_warnings,
            retained_indices=data.retained_indices.copy(),
            excluded_indices=data.excluded_indices.copy(),
            retained_x=x_array.copy(),
            retained_y=y_array.copy(),
            residuals=residuals,
            data_fingerprint=data.fingerprint,
        )
    except Exception as exc:  # fitting failures need to be reported rather than crash the GUI
        return _failure_result("4PL logistic", exc, data)


def fit_two_state_denaturation(x: np.ndarray, y: np.ndarray, temperature_k: float = 298.15) -> FitResult:
    """Fit a two-state linear-extrapolation model with linear baselines."""
    data: Optional[_PreparedData] = None
    try:
        if not 260.0 <= float(temperature_k) <= 330.0:
            raise ValueError("Temperature must be supplied in kelvin and lie between 260 and 330 K")
        data = _prepare_xy(x, y)
        if data.x.size < 8:
            raise ValueError("At least 8 finite paired points are required")
        x_array = data.x
        y_array = data.y
        diagnostics = _data_diagnostics(data)
        x_range = max(float(np.ptp(x_array)), 1e-3)
        y_range = max(float(np.ptp(y_array)), max(float(np.max(np.abs(y_array))), 1.0) * 1e-9)
        edge_count = max(2, min(4, len(x_array) // 4))

        folded_slope, folded_intercept = np.polyfit(x_array[:edge_count], y_array[:edge_count], 1)
        unfolded_slope, unfolded_intercept = np.polyfit(x_array[-edge_count:], y_array[-edge_count:], 1)

        def model(values: np.ndarray, *parameters: float) -> np.ndarray:
            return two_state_denaturation_signal(values, *parameters, temperature_k=float(temperature_k))

        baseline_low = float(np.min(y_array) - 10 * y_range)
        baseline_high = float(np.max(y_array) + 10 * y_range)
        max_slope = 20 * y_range / x_range
        lower = np.asarray([baseline_low, -max_slope, baseline_low, -max_slope, 0.01, 0.01], dtype=float)
        upper = np.asarray([baseline_high, max_slope, baseline_high, max_slope, 150.0, 60.0], dtype=float)

        baseline_guesses = [
            (folded_intercept, folded_slope, unfolded_intercept, unfolded_slope),
            (float(np.mean(y_array[:edge_count])), 0.0, float(np.mean(y_array[-edge_count:])), 0.0),
        ]
        midpoint_guesses = np.quantile(x_array, [0.25, 0.5, 0.75])
        m_guesses = (4.0, 8.0)
        starts: list[np.ndarray] = []
        for folded_i, folded_s, unfolded_i, unfolded_s in baseline_guesses:
            for midpoint in midpoint_guesses:
                for m_guess in m_guesses:
                    dg_guess = max(float(midpoint) * m_guess, 0.1)
                    starts.append(
                        np.asarray([folded_i, folded_s, unfolded_i, unfolded_s, dg_guess, m_guess], dtype=float)
                    )

        multi = _bounded_multistart(
            model,
            x_array,
            y_array,
            starts,
            lower,
            upper,
            maxfev_per_start=30_000,
        )
        popt = multi.parameters
        covariance = multi.covariance
        predicted = model(x_array, *popt)
        residuals = y_array - predicted
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

        metrics = _fit_metrics(y_array, predicted, len(popt))
        diagnostics.update(multi.diagnostics)
        diagnostics["residual_lag1_correlation"] = _lag_one_residual_correlation(residuals)
        delta_g_at_x = popt[4] - popt[5] * x_array
        fraction = 1.0 / (
            1.0 + np.exp(np.clip(delta_g_at_x / (R_KJ_PER_MOL_K * float(temperature_k)), -700, 700))
        )
        folded_at_cm = popt[0] + popt[1] * cm
        unfolded_at_cm = popt[2] + popt[3] * cm
        status, quality_warnings, diagnostics = _interpretation(
            data=data,
            diagnostics=diagnostics,
            parameters=popt,
            names=names,
            lower=lower,
            upper=upper,
            covariance=covariance,
            metrics=metrics,
            fraction_unfolded=fraction,
            midpoint_m=float(cm),
            transition_width_m=float(R_KJ_PER_MOL_K * float(temperature_k) / m_value),
            transition_signal_separation=float(abs(unfolded_at_cm - folded_at_cm)),
            critical_parameters={"delta_g_h2o_kj_mol", "m_value_kj_mol_m"},
        )
        quality_warnings.extend(f"Optimizer warning: {item}" for item in multi.optimizer_warnings)
        return FitResult(
            model_name="Two-state LEM",
            success=True,
            parameters=parameters,
            standard_errors=errors,
            metrics=metrics,
            predicted=predicted,
            parameter_order=names,
            covariance=covariance,
            prediction_function=lambda values: model(np.asarray(values, dtype=float), *popt),
            interpretation_status=status,
            diagnostics=diagnostics,
            warnings=quality_warnings,
            retained_indices=data.retained_indices.copy(),
            excluded_indices=data.excluded_indices.copy(),
            retained_x=x_array.copy(),
            retained_y=y_array.copy(),
            residuals=residuals,
            data_fingerprint=data.fingerprint,
        )
    except Exception as exc:
        return _failure_result("Two-state LEM", exc, data)


def choose_best_fit(results: list[FitResult]) -> Optional[FitResult]:
    """Choose by AICc (or AIC when AICc is not defined) on identical data.

    Information criteria cannot be compared when candidate models retained
    different concentrations or responses.  Refusing such a comparison is
    preferable to silently presenting a scientifically invalid winner.
    """
    successful = [result for result in results if result.success]
    if not successful:
        return None
    if len(successful) == 1:
        return successful[0]

    fingerprints: list[str] = []
    for result in successful:
        fingerprint = result.data_fingerprint
        if not fingerprint and result.retained_x.size and result.retained_y.size:
            if result.retained_x.shape != result.retained_y.shape:
                raise ValueError("Cannot compare fits with malformed retained data")
            fingerprint = _data_fingerprint(result.retained_x, result.retained_y)
        if not fingerprint:
            raise ValueError("Cannot compare fits without retained-data fingerprints")
        fingerprints.append(fingerprint)
    if len(set(fingerprints)) != 1:
        raise ValueError("Cannot compare fits produced from different retained concentration/response data")

    aicc_values = [result.metrics.get("aicc", float("nan")) for result in successful]
    if all(np.isfinite(value) for value in aicc_values):
        return min(successful, key=lambda result: float(result.metrics["aicc"]))
    return min(successful, key=lambda result: float(result.metrics.get("aic", float("inf"))))
