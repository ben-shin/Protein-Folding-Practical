"""Validate the derivatives used to accelerate both bounded curve fits."""
import numpy as np
import pytest

from folding_practical.models import (
    _four_parameter_logistic_jacobian,
    _two_state_denaturation_jacobian,
    four_parameter_logistic,
    two_state_denaturation_signal,
)


def _central_difference(model, x, parameters):
    parameters = np.asarray(parameters, dtype=float)
    columns = []
    for index, value in enumerate(parameters):
        step = np.cbrt(np.finfo(float).eps) * max(abs(value), 1.0)
        offset = np.zeros_like(parameters)
        offset[index] = step
        columns.append((model(x, *(parameters + offset)) - model(x, *(parameters - offset))) / (2 * step))
    return np.column_stack(columns)


@pytest.mark.parametrize("parameters", [
    (1000.0, 100.0, 3.2, 0.35),
    (100.0, 1000.0, 2.5, 0.6),
    (500.0, 500.0, 3.0, 0.5),
    (1000.0, 100.0, 3.1, 0.0001),
])
def test_logistic_derivatives_match_independent_numerical_difference(parameters):
    x = np.linspace(0.0, 6.0, 16)
    actual = _four_parameter_logistic_jacobian(x, *parameters)
    expected = _central_difference(four_parameter_logistic, x, parameters)
    assert np.isfinite(actual).all()
    np.testing.assert_allclose(actual, expected, rtol=2e-6, atol=2e-7)


@pytest.mark.parametrize("temperature", [260.0, 298.15, 330.0])
@pytest.mark.parametrize("parameters", [
    (1000.0, -12.0, 130.0, 8.0, 24.0, 7.5),
    (130.0, 8.0, 1000.0, -12.0, 24.0, 7.5),
    (500.0, 0.0, 500.0, 0.0, 24.0, 7.5),
])
def test_two_state_derivatives_match_independent_numerical_difference(temperature, parameters):
    x = np.linspace(0.0, 6.0, 16)
    model = lambda values, *params: two_state_denaturation_signal(values, *params, temperature_k=temperature)
    actual = _two_state_denaturation_jacobian(x, *parameters, temperature_k=temperature)
    expected = _central_difference(model, x, parameters)
    assert np.isfinite(actual).all()
    np.testing.assert_allclose(actual, expected, rtol=2e-6, atol=2e-7)
