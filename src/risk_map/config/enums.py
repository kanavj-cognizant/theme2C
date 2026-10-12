from enum import Enum


class PollutantType(str, Enum):
    """Supported water quality pollutants."""

    P = "P"  # Phosphorus / Orthophosphate
    NH4 = "NH4"  # Ammoniacal Nitrogen as N


class SourceType(str, Enum):
    """Categorized point and diffuse pollutant sources."""

    CSO = "CSO"
    WWTW = "WWTW"
    AGRICULTURE = "AGRICULTURE"
    LIVESTOCK = "LIVESTOCK"
    URBAN_ROAD = "URBAN_ROAD"
    URBAN_BUILDING = "URBAN_BUILDING"


class AggregationMethod(str, Enum):
    """Method to combine multiple source risks or pollutant risks."""

    SUM = "sum"
    MEAN = "mean"
    FLOW_WEIGHTED = "flow_weighted"
