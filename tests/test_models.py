import numpy as np

from folding_practical.models import (
    fit_four_parameter_logistic,
    fit_two_state_denaturation,
    four_parameter_logistic,
    two_state_denaturation_signal,
)


def test_logistic_fit_recovers_midpoint():
    x = np.linspace(0.0, 6.0, 16)
    y = four_parameter_logistic(x, 1000.0, 100.0, 3.1, 0.35)
    result = fit_four_parameter_logistic(x, y)
    assert result.success, result.message
    assert abs(result.parameters["midpoint_m"] - 3.1) < 0.05
    assert result.metrics["r_squared"] > 0.999


def test_two_state_fit_recovers_thermodynamics():
    x = np.linspace(0.0, 6.0, 20)
    true_dg = 24.0
    true_m = 7.5
    y = two_state_denaturation_signal(
        x,
        folded_intercept=1000.0,
        folded_slope=-12.0,
        unfolded_intercept=130.0,
        unfolded_slope=8.0,
        delta_g_h2o_kj_mol=true_dg,
        m_value_kj_mol_m=true_m,
        temperature_k=298.15,
    )
    result = fit_two_state_denaturation(x, y, temperature_k=298.15)
    assert result.success, result.message
    assert abs(result.parameters["delta_g_h2o_kj_mol"] - true_dg) < 0.5
    assert abs(result.parameters["m_value_kj_mol_m"] - true_m) < 0.2
    assert abs(result.parameters["cm_m"] - true_dg / true_m) < 0.05
    assert result.metrics["r_squared"] > 0.999
