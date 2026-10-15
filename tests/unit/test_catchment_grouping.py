import geopandas as gpd
import networkx as nx
import polars as pl
import pytest
from shapely.geometry import box

from risk_map.spatial.grouping import merge_subcatchments_by_area


def make_polygons(areas: dict[int, float]) -> gpd.GeoDataFrame:
    """Create adjacent square polygons with the requested areas."""
    rows = []
    x_offset = 0.0
    for node_id, area in areas.items():
        width = area**0.5
        rows.append(
            {
                "node_id": node_id,
                "geometry": box(x_offset, 0.0, x_offset + width, width),
            }
        )
        x_offset += width
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:27700")


def make_graph(edges: list[tuple[int, int]]) -> nx.DiGraph:
    graph = nx.DiGraph()
    for node_id in {node for edge in edges for node in edge}:
        graph.add_node((float(node_id), 0.0), nodeID=node_id)
    for upstream, downstream in edges:
        graph.add_edge((float(upstream), 0.0), (float(downstream), 0.0))
    return graph


def test_accumulates_small_nodes_to_area_threshold_downstream():
    polygons = make_polygons({1: 3.0, 2: 4.0, 3: 5.0, 4: 10.0})
    graph = make_graph([(1, 2), (2, 3), (3, 4)])

    merged, mapping = merge_subcatchments_by_area(polygons, graph, min_area=10.0)

    assert isinstance(mapping, pl.DataFrame)
    assert len(merged) == 2
    assert set(mapping.filter(pl.col("subcatchment_id") == 3)["node_id"].to_list()) == {1, 2, 3}
    assert set(mapping.filter(pl.col("subcatchment_id") == 4)["node_id"].to_list()) == {4}
    assert (merged["area"] >= 10.0).all()
    weights = mapping.group_by("subcatchment_id").agg(pl.col("allocation_weight").sum())
    assert weights.sort("subcatchment_id")["allocation_weight"].to_list() == [1.0, 1.0]


def test_combines_undersized_tributaries_at_confluence():
    polygons = make_polygons({1: 4.0, 2: 4.0, 3: 3.0, 4: 10.0})
    graph = make_graph([(1, 3), (2, 3), (3, 4)])

    merged, mapping = merge_subcatchments_by_area(polygons, graph, min_area=10.0)

    assert len(merged) == 2
    assert set(mapping.filter(pl.col("subcatchment_id") == 3)["node_id"].to_list()) == {1, 2, 3}
    assert set(mapping.filter(pl.col("subcatchment_id") == 4)["node_id"].to_list()) == {4}
    assert (merged["area"] >= 10.0).all()
    weights = mapping.group_by("subcatchment_id").agg(pl.col("allocation_weight").sum())
    assert weights.sort("subcatchment_id")["allocation_weight"].to_list() == [1.0, 1.0]


def test_marks_only_whole_small_component_below_threshold():
    polygons = make_polygons({1: 2.0, 2: 3.0})
    graph = make_graph([(1, 2)])

    merged, mapping = merge_subcatchments_by_area(polygons, graph, min_area=10.0)

    assert len(merged) == 1
    assert merged.loc[0, "area"] == pytest.approx(5.0)
    assert bool(merged.loc[0, "below_minimum"])
    assert set(mapping["node_id"].to_list()) == {1, 2}
    assert mapping["allocation_weight"].sum() == pytest.approx(1.0)


def test_missing_node_polygon_has_zero_area_and_merges_downstream():
    polygons = make_polygons({1: 10.0, 3: 10.0})
    graph = make_graph([(1, 2), (2, 3)])

    merged, mapping = merge_subcatchments_by_area(polygons, graph, min_area=10.0)

    assert len(merged) == 2
    assert set(mapping["node_id"].to_list()) == {1, 2, 3}
    downstream_members = mapping.filter(pl.col("subcatchment_id") == 3)["node_id"].to_list()
    assert set(downstream_members) == {2, 3}
    assert mapping.filter(pl.col("node_id") == 2)["allocation_weight"][0] == 0.5


def test_merges_small_outlet_remainder_into_nearest_upstream_group():
    polygons = make_polygons({1: 10.0, 2: 2.0})
    graph = make_graph([(1, 2)])

    merged, mapping = merge_subcatchments_by_area(polygons, graph, min_area=10.0)

    assert len(merged) == 1
    assert merged.loc[0, "area"] == pytest.approx(12.0)
    assert not merged.loc[0, "below_minimum"]
    assert set(mapping["node_id"].to_list()) == {1, 2}
    assert mapping["allocation_weight"].to_list() == [0.5, 0.5]
