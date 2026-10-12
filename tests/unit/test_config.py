import pytest
from pydantic import ValidationError

from risk_map.config.constants import NH4_CONSTANTS, P_CONSTANTS, PollutantParameters
from risk_map.config.enums import AggregationMethod, PollutantType, SourceType
from risk_map.config.models import PipelineConfig


def test_pollutant_type_enum():
    """Verify PollutantType enum members and string representations."""
    assert PollutantType.P == "P"
    assert PollutantType.NH4 == "NH4"
    assert PollutantType("P") == PollutantType.P
    assert PollutantType("NH4") == PollutantType.NH4

    with pytest.raises(ValueError):
        PollutantType("INVALID_POLLUTANT")


def test_source_type_enum():
    """Verify all point and diffuse SourceType members."""
    expected_sources = {
        "CSO",
        "WWTW",
        "AGRICULTURE",
        "LIVESTOCK",
        "URBAN_ROAD",
        "URBAN_BUILDING",
    }
    actual_sources = {s.value for s in SourceType}
    assert actual_sources == expected_sources


def test_aggregation_method_enum():
    """Verify supported aggregation methods."""
    assert AggregationMethod.SUM == "sum"
    assert AggregationMethod.MEAN == "mean"
    assert AggregationMethod.FLOW_WEIGHTED == "flow_weighted"


def test_pipeline_config_defaults_and_validation():
    """Verify PipelineConfig defaults, paths, and positive constraints."""
    config = PipelineConfig()
    assert config.river_resolution == 300.0
    assert config.max_outfall_distance == 700.0
    assert config.min_catchment_area == 20000.0

    # Test positive constraint validation
    with pytest.raises(ValidationError):
        PipelineConfig(river_resolution=-10.0)

    with pytest.raises(ValidationError):
        PipelineConfig(river_resolution=0.0)


def test_pollutant_physical_constants():
    """Verify consolidated physical constants match expected domain values in SI units."""
    assert isinstance(P_CONSTANTS, PollutantParameters)
    assert P_CONSTANTS.pollutant == PollutantType.P
    assert P_CONSTANTS.guideline == 8.7e-4
    assert pytest.approx(P_CONSTANTS.degradation_rate, rel=1e-5) == 0.04 / 86400.0
    assert P_CONSTANTS.wwtw_fill_value == 5.0e-3
    assert P_CONSTANTS.cso_fill_value == 3.0e-3

    assert isinstance(NH4_CONSTANTS, PollutantParameters)
    assert NH4_CONSTANTS.pollutant == PollutantType.NH4
    assert NH4_CONSTANTS.guideline == 6.0e-3
    assert pytest.approx(NH4_CONSTANTS.degradation_rate, rel=1e-5) == 0.42 / 86400.0
    assert NH4_CONSTANTS.wwtw_fill_value == 2.6e-3
    assert NH4_CONSTANTS.cso_fill_value == 1.3e-2
