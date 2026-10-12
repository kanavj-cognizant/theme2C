from __future__ import annotations

import math
import warnings
from typing import Any

from uncertainties import ufloat
from uncertainties.core import AffineScalarFunc

# Filter the benign warning from uncertainties when std_dev is exactly 0
warnings.filterwarnings("ignore", message="Using UFloat objects with std_dev==0.*")


class Measurement:
    """A quantity with a nominal value and standard deviation (uncertainty).

    Wraps uncertainties.ufloat to provide typed access and domain helpers.
    """

    __slots__ = ("_val",)
    _val: Any

    def __init__(self, nominal: float, std_dev: float = 0.0) -> None:
        if std_dev < 0:
            raise ValueError(f"std_dev must be non-negative, got {std_dev}")
        self._val = ufloat(float(nominal), float(std_dev))

    @classmethod
    def from_ufloat(cls, val: Any) -> Measurement:
        obj = cls.__new__(cls)
        obj._val = val
        return obj

    @property
    def nominal(self) -> float:
        return float(self._val.nominal_value)

    @property
    def std_dev(self) -> float:
        return float(self._val.std_dev)

    @property
    def uval(self) -> Any:
        return self._val

    def __repr__(self) -> str:
        return f"Measurement({self.nominal:.4g} ± {self.std_dev:.4g})"

    def __add__(self, other: Any) -> Measurement:
        o = to_measurement(other)
        return Measurement.from_ufloat(self._val + o._val)

    def __radd__(self, other: Any) -> Measurement:
        return self.__add__(other)

    def __sub__(self, other: Any) -> Measurement:
        o = to_measurement(other)
        return Measurement.from_ufloat(self._val - o._val)

    def __rsub__(self, other: Any) -> Measurement:
        o = to_measurement(other)
        return Measurement.from_ufloat(o._val - self._val)

    def __mul__(self, other: Any) -> Measurement:
        o = to_measurement(other)
        return Measurement.from_ufloat(self._val * o._val)

    def __rmul__(self, other: Any) -> Measurement:
        return self.__mul__(other)

    def __truediv__(self, other: Any) -> Measurement:
        o = to_measurement(other)
        return Measurement.from_ufloat(self._val / o._val)

    def __rtruediv__(self, other: Any) -> Measurement:
        o = to_measurement(other)
        return Measurement.from_ufloat(o._val / self._val)


MeasurementLike = Measurement | AffineScalarFunc | float | int


def to_measurement(val: MeasurementLike) -> Measurement:
    """Convert any number or ufloat into a typed Measurement."""
    if isinstance(val, Measurement):
        return val
    if isinstance(val, AffineScalarFunc):
        return Measurement.from_ufloat(val)
    if isinstance(val, (int, float)):
        return Measurement(float(val), 0.0)
    raise TypeError(f"Cannot convert {type(val)} to Measurement")


def calculate_decay(
    load: MeasurementLike,
    length: float,
    flow_speed: float,
    degradation_rate: float,
) -> Measurement:
    """Calculate exponential pollutant decay over a river segment"""
    m_load = to_measurement(load)
    if flow_speed <= 0 or length <= 0:
        return m_load

    # travel time in seconds: length (m) / flow_speed (m/s) = s
    travel_time = length / flow_speed
    decay_fraction = math.exp(-degradation_rate * travel_time)
    return m_load * decay_fraction


def calculate_concentration(
    load: MeasurementLike,
    river_flow: MeasurementLike,
    min_flow_threshold: float = 1e-6,
) -> Measurement:
    """Calculate concentration (kg/m3) from load (kg/s) and river flow (m3/s).

    Gracefully handles dry or zero-flow reaches to prevent ZeroDivisionError.
    """
    m_load = to_measurement(load)
    m_flow = to_measurement(river_flow)

    if m_flow.nominal <= min_flow_threshold:
        return Measurement(0.0, 0.0)

    return m_load / m_flow


def calculate_risk(
    concentration: MeasurementLike,
    guideline: float,
) -> Measurement:
    """Calculate risk percentage as (concentration / guideline) * 100 in SI units."""
    if guideline <= 0:
        raise ValueError(f"Guideline must be positive, got {guideline}")
    m_conc = to_measurement(concentration)
    return (m_conc / guideline) * 100.0
