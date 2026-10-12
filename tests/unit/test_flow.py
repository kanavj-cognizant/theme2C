import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import LineString

from risk_map.spatial.flow import FlowEstimator, LinearAccumulationFlowEstimator


def test_linear_flow_estimator_protocol():
    """Verify LinearAccumulationFlowEstimator implements FlowEstimator protocol."""
    estimator = LinearAccumulationFlowEstimator(coeff=1.476e-5, flow_speed=0.77)
    assert isinstance(estimator, FlowEstimator)


def test_linear_flow_and_velocity_estimation():
    """Verify calculation of flow (m3/s) and flow speed (m/s) as Polars Series / float."""
    # Create sample river reach with US_Accum lengths
    rivers = gpd.GeoDataFrame(
        {
            "riverID": [101, 102, 103],
            "US_Accum": [0.0, 100000.0, 200000.0],  # meters of upstream accumulation
            "geometry": [
                LineString([(0, 0), (100, 0)]),
                LineString([(100, 0), (200, 0)]),
                LineString([(200, 0), (300, 0)]),
            ],
        },
        crs="EPSG:27700",
    )

    estimator = LinearAccumulationFlowEstimator(
        coeff=1.476e-5, accum_col="US_Accum", flow_speed=0.77
    )
    flows = estimator.estimate_flow(rivers)

    # Verify return type is polars.Series
    assert isinstance(flows, pl.Series)
    assert len(flows) == 3
    assert flows[0] == 0.0
    assert pytest.approx(flows[1], rel=1e-5) == 100000.0 * 1.476e-5  # 1.476 m3/s
    assert pytest.approx(flows[2], rel=1e-5) == 200000.0 * 1.476e-5  # 2.952 m3/s

    # Flow speed check: float | pl.Series
    speeds = estimator.estimate_flow_speed(rivers)
    if isinstance(speeds, pl.Series):
        assert (speeds == 0.77).all()
    else:
        assert isinstance(speeds, float)
        assert speeds == 0.77
