from pydantic import BaseModel, PositiveFloat

from risk_map.config.enums import PollutantType


class PollutantParameters(BaseModel):
    """Consolidated physical constants, guidelines, and fill values in SI units (kg, m, s)."""

    pollutant: PollutantType
    guideline: PositiveFloat  # kg/m3
    degradation_rate: PositiveFloat  # 1/s (s^-1)
    wwtw_fill_value: PositiveFloat  # kg/m3
    cso_fill_value: PositiveFloat  # kg/m3
    road_urban_emc: PositiveFloat  # kg/m3
    road_rural_emc: PositiveFloat  # kg/m3
    building_emc: PositiveFloat  # kg/m3


# P parameters converted to SI:
# Guideline: 0.87 mg/L = 8.7e-4 kg/m3
# Degradation: 0.04 day^-1 = 0.04 / 86400 s^-1
# WwTW fill: 5.0 mg/L = 5.0e-3 kg/m3
# CSO fill: 3.0 mg/L = 3.0e-3 kg/m3
# EMCs: 0.1 mg/L = 1.0e-4 kg/m3
P_CONSTANTS = PollutantParameters(
    pollutant=PollutantType.P,
    guideline=8.7e-4,
    degradation_rate=0.04 / 86400.0,
    wwtw_fill_value=5.0e-3,
    cso_fill_value=3.0e-3,
    road_urban_emc=1.0e-4,
    road_rural_emc=1.0e-4,
    building_emc=1.0e-4,
)

# NH4 parameters converted to SI:
# Guideline: 6.0 mg/L = 6.0e-3 kg/m3
# Degradation: 0.42 day^-1 = 0.42 / 86400 s^-1
# WwTW fill: 2.6 mg/L = 2.6e-3 kg/m3
# CSO fill: 13.0 mg/L = 1.3e-2 kg/m3
# EMCs: 0.8, 0.65, 0.2 mg/L = 8.0e-4, 6.5e-4, 2.0e-4 kg/m3
NH4_CONSTANTS = PollutantParameters(
    pollutant=PollutantType.NH4,
    guideline=6.0e-3,
    degradation_rate=0.42 / 86400.0,
    wwtw_fill_value=2.6e-3,
    cso_fill_value=1.3e-2,
    road_urban_emc=8.0e-4,
    road_rural_emc=6.5e-4,
    building_emc=2.0e-4,
)

POLLUTANT_LOOKUP: dict[PollutantType, PollutantParameters] = {
    PollutantType.P: P_CONSTANTS,
    PollutantType.NH4: NH4_CONSTANTS,
}
