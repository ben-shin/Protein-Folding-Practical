import numpy as np
import pytest

from folding_practical.models import (
    FitResult,
    choose_best_fit,
    fit_four_parameter_logistic,
    fit_two_state_denaturation,
    four_parameter_logistic,
    two_state_denaturation_signal,
)


def _two_state_signal(x):
    return two_state_denaturation_signal(
        np.asarray(x, dtype=float),
        folded_intercept=1000.0,
        folded_slope=-12.0,
        unfolded_intercept=130.0,
        unfolded_slope=8.0,
        delta_g_h2o_kj_mol=24.0,
        m_value_kj_mol_m=7.5,
        temperature_k=298.15,
    )


def test_success_is_separate_from_flat_curve_interpretability():
    x = np.linspace(0.0, 6.0, 16)

    logistic = fit_four_parameter_logistic(x, np.full_like(x, 500.0))
    two_state = fit_two_state_denaturation(x, np.full_like(x, 500.0))

    for result in (logistic, two_state):
        assert result.success, result.message
        assert result.interpretation_status == "insufficient_information"
        assert result.diagnostics["response_dynamic_range"] == 0.0
        assert any("flat" in warning.lower() for warning in result.warnings)


def test_partial_curve_is_not_reported_as_interpretable():
    # These points sample only the folded side of a transition centred at 3.2 M.
    x = np.linspace(0.0, 2.0, 14)
    result = fit_two_state_denaturation(x, _two_state_signal(x))

    assert result.success, result.message
    assert result.interpretation_status == "insufficient_information"
    assert not result.diagnostics["midpoint_within_observed_range"]
    assert not result.diagnostics["high_denaturant_baseline_covered"]
    assert not result.diagnostics["transition_covered"]


def test_noisy_curve_exposes_residual_diagnostics_and_caution():
    rng = np.random.default_rng(19)
    x = np.linspace(0.0, 6.0, 30)
    y = four_parameter_logistic(x, 1000.0, 100.0, 3.1, 0.35) + rng.normal(0.0, 400.0, x.size)

    result = fit_four_parameter_logistic(x, y)

    assert result.success, result.message
    assert result.interpretation_status == "caution"
    assert result.residuals.shape == y.shape
    np.testing.assert_allclose(result.residuals, result.retained_y - result.predicted)
    assert result.metrics["rmse"] > 0.0
    assert result.diagnostics["residual_rmse_fraction_dynamic_range"] > 0.15
    assert any("Residual error" in warning for warning in result.warnings)


def test_replicates_do_not_inflate_concentration_coverage():
    rng = np.random.default_rng(7)
    unique_x = np.linspace(0.0, 6.0, 16)
    x = np.repeat(unique_x, 3)
    y = _two_state_signal(x) + rng.normal(0.0, 5.0, x.size)

    result = fit_two_state_denaturation(x, y)

    assert result.success, result.message
    assert result.interpretation_status == "interpretable"
    assert result.diagnostics["retained_count"] == 48
    assert result.diagnostics["distinct_concentration_count"] == 16
    assert result.diagnostics["replicate_observation_count"] == 32
    assert result.diagnostics["replicate_group_count"] == 16
    assert result.diagnostics["replicate_noise_sigma"] > 0.0
    assert result.diagnostics["low_denaturant_baseline_distinct_count"] < 16


def test_nonfinite_pairs_are_excluded_with_original_indices_retained():
    x = np.linspace(0.0, 6.0, 10)
    y = four_parameter_logistic(x, 1000.0, 100.0, 3.0, 0.4)
    x[2] = np.nan
    y[7] = np.inf

    result = fit_four_parameter_logistic(x, y)

    assert result.success, result.message
    np.testing.assert_array_equal(result.excluded_indices, [2, 7])
    np.testing.assert_array_equal(result.retained_indices, [0, 1, 3, 4, 5, 6, 8, 9])
    assert result.diagnostics["input_count"] == 10
    assert result.diagnostics["retained_count"] == 8
    assert result.diagnostics["excluded_nonfinite_count"] == 2
    assert any("original indices [2, 7]" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    ("x", "y", "message_fragment"),
    [
        ([0.0, 1.0], [1.0], "same number"),
        ([[0.0, 1.0]], [[1.0, 2.0]], "one-dimensional"),
        ([0.0, 1.0, np.nan, np.inf], [1.0, 2.0, 3.0, 4.0], "finite paired"),
    ],
)
def test_malformed_inputs_fail_cleanly(x, y, message_fragment):
    result = fit_four_parameter_logistic(np.asarray(x), np.asarray(y))

    assert not result.success
    assert result.interpretation_status == "fit_failed"
    assert message_fragment in result.message
    assert result.predicted.size == 0


def test_information_criteria_use_estimated_gaussian_variance_parameter():
    rng = np.random.default_rng(4)
    x = np.linspace(0.0, 6.0, 24)
    y = four_parameter_logistic(x, 900.0, 120.0, 2.9, 0.45) + rng.normal(0.0, 8.0, x.size)
    result = fit_four_parameter_logistic(x, y)

    assert result.success, result.message
    n = x.size
    k = 5  # four mean parameters plus estimated residual variance
    sigma_squared = result.metrics["rss"] / n
    negative_twice_log_likelihood = n * (np.log(2.0 * np.pi) + 1.0 + np.log(sigma_squared))
    expected_aic = negative_twice_log_likelihood + 2.0 * k
    expected_bic = negative_twice_log_likelihood + k * np.log(n)
    expected_aicc = expected_aic + (2.0 * k * (k + 1)) / (n - k - 1)

    assert result.metrics["information_parameter_count"] == 5.0
    assert result.metrics["aic"] == pytest.approx(expected_aic)
    assert result.metrics["bic"] == pytest.approx(expected_bic)
    assert result.metrics["aicc"] == pytest.approx(expected_aicc)


def test_aicc_is_nan_when_small_sample_correction_is_undefined():
    x = np.linspace(0.0, 6.0, 6)
    y = four_parameter_logistic(x, 1000.0, 100.0, 3.0, 0.4)
    result = fit_four_parameter_logistic(x, y)

    assert result.success, result.message
    assert np.isnan(result.metrics["aicc"])


def test_multistart_work_is_bounded_and_quality_is_reported():
    x = np.linspace(0.0, 6.0, 20)
    result = fit_two_state_denaturation(x, _two_state_signal(x))

    assert result.success, result.message
    assert 1 <= result.diagnostics["multistart_attempt_count"] <= 12
    assert 1 <= result.diagnostics["multistart_success_count"] <= result.diagnostics["multistart_attempt_count"]
    assert isinstance(result.diagnostics["multistart_consistent"], bool)
    assert "covariance_status" in result.diagnostics
    assert "bound_hit_parameters" in result.diagnostics


def test_choose_best_fit_requires_identical_retained_data_and_response():
    x = np.linspace(0.0, 6.0, 20)
    first = fit_four_parameter_logistic(x, four_parameter_logistic(x, 1000.0, 100.0, 3.0, 0.4))
    second = fit_four_parameter_logistic(x, four_parameter_logistic(x, 1000.0, 100.0, 3.2, 0.4))
    assert first.success and second.success

    with pytest.raises(ValueError, match="different retained"):
        choose_best_fit([first, second])


def test_choose_best_fit_accepts_models_fitted_to_the_same_data():
    x = np.linspace(0.0, 6.0, 20)
    y = _two_state_signal(x)
    logistic = fit_four_parameter_logistic(x, y)
    two_state = fit_two_state_denaturation(x, y)

    best = choose_best_fit([logistic, two_state])

    assert best in (logistic, two_state)
    assert logistic.data_fingerprint == two_state.data_fingerprint


def test_legacy_fit_result_fields_remain_constructible():
    result = FitResult(model_name="legacy", success=False, message="not fitted")

    assert result.model_name == "legacy"
    assert not result.success
    assert result.parameters == {}
    assert result.metrics == {}
    assert result.interpretation_status == "fit_failed"
