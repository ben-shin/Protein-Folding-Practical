"""Compare exact derivatives with SciPy finite differences on identical fits.

Run: .venv-browser/bin/python scripts/benchmark_models.py
Reports timing evidence without machine-dependent pass/fail thresholds.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import statistics
import sys
import time

import numpy as np
import scipy
from scipy import optimize

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from folding_practical import models


def measure(fitter, x, y, *, exact, repeats):
    original = optimize.curve_fit
    calls = 0

    def instrumented(model, *args, **kwargs):
        def counted(*values):
            nonlocal calls
            calls += 1
            return model(*values)

        if not exact:
            kwargs["jac"] = None
        return original(counted, *args, **kwargs)

    timings = []
    optimize.curve_fit = instrumented
    try:
        for _ in range(repeats):
            start = time.perf_counter()
            result = fitter(x, y)
            timings.append(time.perf_counter() - start)
            if not result.success:
                raise RuntimeError(result.message)
    finally:
        optimize.curve_fit = original
    return result, statistics.median(timings), calls / repeats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--sizes", type=int, nargs="+", default=[16, 48, 256, 5000])
    args = parser.parse_args()
    if args.repeats < 1 or any(size < 8 or size > 5000 for size in args.sizes):
        parser.error("Use positive repeats and observation counts between 8 and 5000")
    rows = []
    for count in args.sizes:
        x = np.linspace(0.0, 6.0, count)
        y = models.two_state_denaturation_signal(x, 1000, -12, 130, 8, 24, 7.5, 298.15)
        y += np.random.default_rng(41).normal(0.0, 5.0, count)
        for fitter in (models.fit_four_parameter_logistic, models.fit_two_state_denaturation):
            baseline, baseline_time, baseline_calls = measure(fitter, x, y, exact=False, repeats=args.repeats)
            exact, exact_time, exact_calls = measure(fitter, x, y, exact=True, repeats=args.repeats)
            rows.append({
                "observations": count,
                "model": fitter.__name__,
                "finite_difference_median_seconds": baseline_time,
                "exact_derivative_median_seconds": exact_time,
                "speedup": baseline_time / exact_time,
                "finite_difference_model_calls": baseline_calls,
                "exact_derivative_model_calls": exact_calls,
                "maximum_prediction_difference": float(np.max(np.abs(baseline.predicted - exact.predicted))),
                "rss_relative_difference": abs(baseline.metrics["rss"] - exact.metrics["rss"]) / max(baseline.metrics["rss"], np.finfo(float).tiny),
                "interpretation_match": baseline.interpretation_status == exact.interpretation_status,
            })
    print(json.dumps({
        "python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__,
        "repeats": args.repeats, "results": rows,
    }, indent=2))


if __name__ == "__main__":
    main()
