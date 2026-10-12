import math

from uncertainties import ufloat

from risk_map.models.uncertainty import (
    Measurement,
    calculate_concentration,
    calculate_decay,
    calculate_risk,
    to_measurement,
)


def test_measurement_creation_and_properties():
    """Verify Measurement creation, nominal value, std dev, and math operations."""
    m = Measurement(nominal=100.0, std_dev=10.0)
    assert m.nominal == 100.0
    assert m.std_dev == 10.0

    # Test arithmetic via wrapped ufloat
    m2 = Measurement(nominal=50.0, std_dev=5.0)
    res = m + m2
    assert res.nominal == 150.0
    # Independent error propagation: sqrt(10^2 + 5^2) = sqrt(125) ~ 11.18
    assert math.isclose(res.std_dev, math.sqrt(125), rel_tol=1e-3)


def test_to_measurement_helper():
    """Verify conversion from float, int, ufloat, or Measurement."""
    m_from_float = to_measurement(10.0)
    assert m_from_float.nominal == 10.0
    assert m_from_float.std_dev == 0.0

    u = ufloat(20.0, 2.0)
    m_from_u = to_measurement(u)
    assert m_from_u.nominal == 20.0
    assert m_from_u.std_dev == 2.0

    m_from_m = to_measurement(m_from_u)
    assert m_from_m is m_from_u


def test_calculate_decay():
    """Verify first-order exponential decay in SI units with uncertainty propagation."""
    # Length: 1000 m, Velocity: 0.77 m/s, degradation_rate: (0.04 / 86400) s^-1
    # travel time = 1000 / 0.77 s
    initial_load = Measurement(nominal=100.0, std_dev=10.0)
    k_si = 0.04 / 86400.0
    decayed = calculate_decay(
        load=initial_load,
        length=1000.0,
        flow_speed=0.77,
        degradation_rate=k_si,
    )
    expected_decay = math.exp(-k_si * (1000.0 / 0.77))
    assert math.isclose(decayed.nominal, 100.0 * expected_decay, rel_tol=1e-4)
    assert math.isclose(decayed.std_dev, 10.0 * expected_decay, rel_tol=1e-4)


def test_calculate_concentration_and_safe_zero_division():
    """Verify concentration calculation (kg/m3) and graceful handling of zero or negative flow."""
    load = Measurement(nominal=10.0, std_dev=1.0)  # kg/s
    flow = Measurement(nominal=2.0, std_dev=0.2)  # m3/s

    # Conc = 10 kg/s / 2 m3/s = 5 kg/m3
    conc = calculate_concentration(load=load, river_flow=flow)
    assert math.isclose(conc.nominal, 5.0, rel_tol=1e-4)
    # Ratio relative error: sqrt((1/10)^2 + (0.2/2)^2) = sqrt(0.01 + 0.01) = sqrt(0.02) ~ 0.1414
    # std_dev = 5.0 * 0.1414 ~ 0.707
    assert math.isclose(conc.std_dev, 5.0 * math.sqrt(0.02), rel_tol=1e-3)

    # Safe zero division check
    zero_flow = Measurement(nominal=0.0, std_dev=0.0)
    dry_conc = calculate_concentration(load=load, river_flow=zero_flow)
    assert dry_conc.nominal == 0.0
    assert dry_conc.std_dev == 0.0


def test_calculate_risk():
    """Verify risk calculation against environmental quality standards (EQS)."""
    # Concentration: 8.7e-4 kg/m3, Guideline: 8.7e-4 kg/m3 -> 100%
    conc = Measurement(nominal=8.7e-4, std_dev=8.7e-5)
    risk = calculate_risk(concentration=conc, guideline=8.7e-4)
    assert math.isclose(risk.nominal, 100.0, rel_tol=1e-4)
    assert math.isclose(risk.std_dev, 10.0, rel_tol=1e-3)
