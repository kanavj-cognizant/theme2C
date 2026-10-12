import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from risk_map.spatial.rivers import snap_points_to_rivers, split_rivers


def test_split_rivers_discretization():
    """Verify that split_rivers splits LineStrings into segments of target maximum resolution."""
    # 1000m straight line
    rivers = gpd.GeoDataFrame(
        {
            "riverID": [1],
            "geometry": [LineString([(0, 0), (1000, 0)])],
        },
        crs="EPSG:27700",
    )
    # Resolution of 300m -> segments of 300, 300, 300, 100m -> 4 segments
    split = split_rivers(rivers, resolution=300.0)

    assert len(split) == 4
    assert (split["riverID"] == 1).all()
    # Total length should be strictly preserved (1000m)
    assert pytest.approx(split.geometry.length.sum(), rel=1e-5) == 1000.0
    for geom in split.geometry:
        assert geom.length <= 300.0001


def test_snap_points_to_rivers_including_index_zero():
    """Verify point snapping assigns points correctly including to segment at index 0."""
    rivers = gpd.GeoDataFrame(
        {
            "riverID": [10, 20],
            "geometry": [
                LineString([(0, 0), (100, 0)]),  # index 0
                LineString([(100, 0), (200, 0)]),  # index 1
            ],
        },
        crs="EPSG:27700",
    )

    # Point near index 0: (50, 10) -> closest point on river is (50, 0)
    # Point near index 1: (150, 15) -> closest point on river is (150, 0)
    # Point too far away: (50, 800) -> should NOT snap (None / unassigned)
    points = gpd.GeoDataFrame(
        {
            "source_id": ["P0", "P1", "P_FAR"],
            "geometry": [
                Point(50, 10),
                Point(150, 15),
                Point(50, 800),
            ],
        },
        crs="EPSG:27700",
    )

    snapped = snap_points_to_rivers(
        points=points,
        rivers=rivers,
        max_distance=500.0,
    )

    assert len(snapped) == 3
    # P0 must snap to index 0 (verifying index 0 is not ignored!)
    assert snapped.loc[0, "river_index"] == 0
    assert snapped.loc[0, "snapped_point"].equals_exact(Point(50, 0), tolerance=1e-3)
    assert pytest.approx(snapped.loc[0, "snap_distance"], rel=1e-3) == 10.0

    # P1 must snap to index 1
    assert snapped.loc[1, "river_index"] == 1
    assert snapped.loc[1, "snapped_point"].equals_exact(Point(150, 0), tolerance=1e-3)
    assert pytest.approx(snapped.loc[1, "snap_distance"], rel=1e-3) == 15.0

    # P_FAR must be None / NaN because it exceeds 500m
    assert snapped.loc[2, "river_index"] is None or pd.isna(snapped.loc[2, "river_index"])
