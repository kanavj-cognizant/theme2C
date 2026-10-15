import geopandas as gpd
import pandas as pd
import pytest
from geopandas.testing import assert_geodataframe_equal
from shapely.geometry import LineString, MultiLineString, Point

from risk_map.graph.network import build_river_graph
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
    # Default resolution (300m) -> segments of 300, 300, 300, 100m -> 4 segments
    split = split_rivers(rivers)

    assert len(split) == 4
    assert (split["riverID"] == 1).all()
    # Total length should be strictly preserved (1000m)
    assert pytest.approx(split.geometry.length.sum(), rel=1e-5) == 1000.0
    for geom in split.geometry:
        assert geom.length <= 300.0001


@pytest.mark.parametrize("reverse", [False, True], ids=["downstream", "reversed"])
def test_split_rivers_preserves_bends_length_and_direction(reverse):
    coordinates = [(0, 0), (200, 0), (200, 400), (400, 400)]
    line = LineString(coordinates[::-1] if reverse else coordinates)
    rivers = gpd.GeoDataFrame(
        {"riverID": [42], "flow": [2.0], "geometry": [line]},
        index=[17],
        crs="EPSG:27700",
    )
    before = rivers.copy(deep=True)

    segments = split_rivers(rivers, resolution=300.0)

    assert segments.geometry.length.to_list() == pytest.approx([300.0, 300.0, 200.0])
    assert segments.geometry.length.sum() == pytest.approx(line.length)
    assert segments.crs == rivers.crs
    assert segments.index.to_list() == [0, 1, 2]
    assert segments["riverID"].to_list() == [42, 42, 42]
    assert segments["flow"].to_list() == [2.0, 2.0, 2.0]
    for position, segment in enumerate(segments.geometry):
        assert line.covers(segment)
        assert Point(segment.coords[0]).equals(line.interpolate(position * 300.0))
        assert Point(segment.coords[-1]).equals(
            line.interpolate(min((position + 1) * 300.0, 800.0))
        )
    assert_geodataframe_equal(rivers, before)


def test_split_rivers_bends_do_not_add_graph_nodes():
    line = LineString([(0, 0), (200, 0), (200, 400), (400, 400)])
    rivers = gpd.GeoDataFrame(
        {"riverID": [42], "flow": [2.0], "flow_speed": [1.0], "geometry": [line]},
        crs="EPSG:27700",
    )

    segments = split_rivers(rivers, resolution=300.0)
    graph = build_river_graph(segments)

    assert graph.number_of_edges() == 3
    assert set(graph) == {(0.0, 0.0), (200.0, 100.0), (200.0, 400.0), (400.0, 400.0)}
    assert (200.0, 0.0) not in graph
    assert graph[(0.0, 0.0)][(200.0, 100.0)]["length"] == pytest.approx(300.0)
    assert sum(edge["length"] for _, _, edge in graph.edges(data=True)) == pytest.approx(800.0)


def test_split_rivers_multipart_preserves_short_and_exact_length_parts():
    multiline = MultiLineString(
        [[(0, 0), (200, 0), (200, 100)], [(1000, 0), (1050, 0), (1050, 50)]]
    )
    rivers = gpd.GeoDataFrame({"riverID": [7], "geometry": [multiline]}, crs="EPSG:27700")

    segments = split_rivers(rivers, resolution=300.0)

    assert len(segments) == 2
    assert segments.geometry.length.to_list() == pytest.approx([300.0, 100.0])
    for segment, original in zip(segments.geometry, multiline.geoms, strict=True):
        assert segment.equals_exact(original, tolerance=1e-9)
    assert segments["riverID"].to_list() == [7, 7]


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
