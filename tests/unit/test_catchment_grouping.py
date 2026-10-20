import geopandas as gpd
import networkx as nx
import polars as pl
import pytest
import shapely
from pydantic import ValidationError
from shapely.geometry import Polygon, box

from risk_map.spatial.catchment import CatchmentDelineation
from risk_map.spatial.grouping import SubcatchmentGrouping, group_subcatchments


def make_delineation(
    areas: dict[int, float], pour_points: dict[int, int] | None = None
) -> CatchmentDelineation:
    """Adjacent square watersheds keyed by pour point; nodes default to their own pour point."""
    geometries = []
    x_offset = 0.0
    for area in areas.values():
        width = area**0.5
        geometries.append(box(x_offset, 0.0, x_offset + width, width) if area else Polygon())
        x_offset += width
    watersheds = gpd.GeoDataFrame(
        {"pour_point_id": list(areas), "area_m2": [float(a) for a in areas.values()]},
        geometry=geometries,
        crs="EPSG:27700",
    )
    pour_points = pour_points or {pour_point_id: pour_point_id for pour_point_id in areas}
    membership = pl.DataFrame(
        {"node_id": list(pour_points), "pour_point_id": list(pour_points.values())}
    )
    return CatchmentDelineation(watersheds=watersheds, membership=membership)


def make_graph(edges: list[tuple[int, int]], extra_nodes: tuple[int, ...] = ()) -> nx.DiGraph:
    graph = nx.DiGraph()
    for node_id in sorted({node for edge in edges for node in edge} | set(extra_nodes)):
        graph.add_node((float(node_id), 0.0), nodeID=node_id)
    for upstream, downstream in edges:
        graph.add_edge((float(upstream), 0.0), (float(downstream), 0.0))
    return graph


def members(grouping: SubcatchmentGrouping, subcatchment_id: int) -> set[int]:
    selected = grouping.membership.filter(pl.col("subcatchment_id") == subcatchment_id)
    return set(selected["node_id"].to_list())


def subcatchment_of(grouping: SubcatchmentGrouping, node_id: int) -> int:
    return grouping.membership.filter(pl.col("node_id") == node_id)["subcatchment_id"].item()


def by_id(grouping: SubcatchmentGrouping) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(grouping.subcatchments.set_index("subcatchment_id"))


# Merge rule


def test_accumulates_small_watersheds_downstream_until_minimum():
    delineation = make_delineation({1: 3.0, 2: 4.0, 3: 5.0, 4: 10.0})
    graph = make_graph([(1, 2), (2, 3), (3, 4)])

    grouping = group_subcatchments(delineation, graph, min_area_m2=10.0)

    subcatchments = by_id(grouping)
    assert set(subcatchments.index) == {3, 4}
    assert members(grouping, 3) == {1, 2, 3}
    assert members(grouping, 4) == {4}
    assert subcatchments["area_m2"].to_list() == pytest.approx([12.0, 10.0])
    assert not subcatchments["below_minimum"].any()


def test_combines_undersized_tributaries_at_confluence():
    delineation = make_delineation({1: 4.0, 2: 4.0, 3: 3.0, 4: 10.0})
    graph = make_graph([(1, 3), (2, 3), (3, 4)])

    grouping = group_subcatchments(delineation, graph, min_area_m2=10.0)

    assert members(grouping, 3) == {1, 2, 3}
    assert members(grouping, 4) == {4}
    assert by_id(grouping).loc[3, "area_m2"] == pytest.approx(11.0)


def test_keeps_and_flags_whole_component_below_minimum():
    delineation = make_delineation({1: 2.0, 2: 3.0})
    graph = make_graph([(1, 2)])

    grouping = group_subcatchments(delineation, graph, min_area_m2=10.0)

    subcatchments = by_id(grouping)
    assert list(subcatchments.index) == [2]
    assert subcatchments.loc[2, "area_m2"] == pytest.approx(5.0)
    assert bool(subcatchments.loc[2, "below_minimum"])
    assert members(grouping, 2) == {1, 2}


def test_empty_watershed_merges_downstream():
    delineation = make_delineation({1: 10.0, 2: 0.0, 3: 10.0})
    graph = make_graph([(1, 2), (2, 3)])

    grouping = group_subcatchments(delineation, graph, min_area_m2=10.0)

    assert members(grouping, 1) == {1}
    assert members(grouping, 3) == {2, 3}
    weight = grouping.membership.filter(pl.col("node_id") == 2)["allocation_weight"].item()
    assert weight == pytest.approx(0.5)


def test_outlet_remainder_joins_nearest_upstream_group_and_takes_outlet_id():
    delineation = make_delineation({1: 10.0, 2: 2.0})
    graph = make_graph([(1, 2)])

    grouping = group_subcatchments(delineation, graph, min_area_m2=10.0)

    subcatchments = by_id(grouping)
    assert list(subcatchments.index) == [2]
    assert subcatchments.loc[2, "area_m2"] == pytest.approx(12.0)
    assert not subcatchments.loc[2, "below_minimum"]
    assert members(grouping, 2) == {1, 2}
    assert grouping.membership["allocation_weight"].to_list() == pytest.approx([0.5, 0.5])


# Shared pour points


def test_nodes_sharing_a_pour_point_stay_together_and_count_area_once():
    delineation = make_delineation({10: 15.0, 30: 15.0}, pour_points={1: 10, 2: 10, 3: 30})
    graph = make_graph([(1, 2), (2, 3)])

    grouping = group_subcatchments(delineation, graph, min_area_m2=10.0)

    assert subcatchment_of(grouping, 1) == subcatchment_of(grouping, 2) == 10
    assert by_id(grouping).loc[10, "area_m2"] == pytest.approx(15.0)
    assert members(grouping, 30) == {3}


def test_pour_point_shared_across_branches_is_counted_once():
    delineation = make_delineation(
        {10: 4.0, 30: 4.0, 40: 10.0}, pour_points={1: 10, 2: 10, 3: 30, 4: 40}
    )
    graph = make_graph([(1, 3), (2, 3), (3, 4)])

    grouping = group_subcatchments(delineation, graph, min_area_m2=10.0)

    subcatchments = by_id(grouping)
    assert list(subcatchments.index) == [40]
    assert subcatchments.loc[40, "area_m2"] == pytest.approx(18.0)
    assert members(grouping, 40) == {1, 2, 3, 4}
    assert grouping.membership["allocation_weight"].to_list() == pytest.approx([0.25] * 4)


def test_cycle_created_by_shared_pour_point_raises():
    delineation = make_delineation({10: 5.0, 20: 5.0}, pour_points={1: 10, 2: 20, 3: 10})
    graph = make_graph([(1, 2), (2, 3)])

    with pytest.raises(ValueError, match="cycle"):
        group_subcatchments(delineation, graph, min_area_m2=10.0)


def test_shared_pour_point_with_two_downstream_neighbours_raises():
    delineation = make_delineation(
        {10: 5.0, 20: 5.0, 40: 5.0}, pour_points={1: 10, 3: 10, 2: 20, 4: 40}
    )
    graph = make_graph([(1, 2), (3, 4)])

    with pytest.raises(ValueError, match="downstream"):
        group_subcatchments(delineation, graph, min_area_m2=10.0)


# Output


def test_every_node_once_with_weights_summing_to_one():
    delineation = make_delineation({1: 4.0, 2: 4.0, 3: 3.0, 4: 10.0, 5: 12.0})
    graph = make_graph([(1, 3), (2, 3), (3, 4), (4, 5)])

    grouping = group_subcatchments(delineation, graph, min_area_m2=10.0)

    membership = grouping.membership
    assert sorted(membership["node_id"].to_list()) == [1, 2, 3, 4, 5]
    sizes = membership.group_by("subcatchment_id").agg(
        pl.len().alias("size"), pl.col("allocation_weight").sum().alias("total")
    )
    assert sizes["total"].to_list() == pytest.approx([1.0] * sizes.height)
    for row in membership.join(sizes, on="subcatchment_id").iter_rows(named=True):
        assert row["allocation_weight"] == pytest.approx(1 / row["size"])


def test_geometry_is_union_of_member_watersheds():
    delineation = make_delineation({1: 3.0, 2: 4.0, 3: 5.0, 4: 10.0})
    graph = make_graph([(1, 2), (2, 3), (3, 4)])

    grouping = group_subcatchments(delineation, graph, min_area_m2=10.0)

    subcatchments = by_id(grouping)
    watersheds = delineation.watersheds.set_index("pour_point_id").geometry
    expected = shapely.union_all(watersheds.loc[[1, 2, 3]].to_numpy())
    assert subcatchments.crs.to_epsg() == 27700
    assert subcatchments.geometry.loc[3].symmetric_difference(expected).area == pytest.approx(
        0.0, abs=1e-6
    )
    assert subcatchments["area_m2"].to_numpy() == pytest.approx(subcatchments.area.to_numpy())


# Input validation


def graph_without_ids() -> nx.DiGraph:
    graph = make_graph([(1, 2)])
    del graph.nodes[(1.0, 0.0)]["nodeID"]
    return graph


def graph_with_duplicate_ids() -> nx.DiGraph:
    graph = make_graph([(1, 2)])
    graph.nodes[(2.0, 0.0)]["nodeID"] = 1
    return graph


@pytest.mark.parametrize(
    ("graph", "kwargs", "message"),
    [
        (make_graph([(1, 2)]), {"min_area_m2": 0.0}, "min_area_m2"),
        (graph_without_ids(), {}, "nodeID"),
        (graph_with_duplicate_ids(), {}, "[Dd]uplicate"),
        (make_graph([(1, 2)], extra_nodes=(3,)), {}, "3"),
        (make_graph([], extra_nodes=(1,)), {}, "2"),
    ],
    ids=[
        "min-area",
        "missing-id-attribute",
        "duplicate-graph-ids",
        "graph-node-not-in-membership",
        "membership-node-not-in-graph",
    ],
)
def test_invalid_inputs_raise(graph, kwargs, message):
    delineation = make_delineation({1: 10.0, 2: 10.0})

    with pytest.raises(ValueError, match=message):
        group_subcatchments(delineation, graph, **kwargs)


def test_watershed_referenced_by_no_node_raises():
    delineation = make_delineation({1: 10.0, 2: 10.0, 7: 5.0}, pour_points={1: 1, 2: 2})

    with pytest.raises(ValueError, match="7"):
        group_subcatchments(delineation, make_graph([(1, 2)]))


# Result model


def model_inputs(
    weights=(0.5, 0.5, 1.0),
    subcatchment_ids=(1, 2),
    member_subcatchments=(1, 1, 2),
    node_ids=(10, 11, 12),
    crs="EPSG:27700",
):
    subcatchments = gpd.GeoDataFrame(
        {
            "subcatchment_id": list(subcatchment_ids),
            "area_m2": [100.0] * len(subcatchment_ids),
            "below_minimum": [False] * len(subcatchment_ids),
        },
        geometry=[box(i * 10, 0, i * 10 + 10, 10) for i in range(len(subcatchment_ids))],
        crs=crs,
    )
    membership = pl.DataFrame(
        {
            "subcatchment_id": list(member_subcatchments),
            "node_id": list(node_ids),
            "allocation_weight": list(weights),
        }
    )
    return {"subcatchments": subcatchments, "membership": membership}


def test_result_model_accepts_consistent_tables():
    grouping = SubcatchmentGrouping(**model_inputs())

    assert grouping.membership.height == 3


@pytest.mark.parametrize(
    ("inputs", "message"),
    [
        (model_inputs(weights=(0.5, 0.25, 1.0)), "allocation_weight"),
        (model_inputs(member_subcatchments=(1, 1, 3)), "subcatchment_id"),
        (
            model_inputs(
                weights=(1 / 3, 1 / 3, 1 / 3),
                subcatchment_ids=(1, 1),
                member_subcatchments=(1, 1, 1),
            ),
            "subcatchment_id",
        ),
        (model_inputs(node_ids=(10, 10, 12)), "node_id"),
        (model_inputs(crs="EPSG:4326"), "27700"),
    ],
    ids=["weights", "unknown-subcatchment", "duplicate-subcatchment", "duplicate-node", "crs"],
)
def test_result_model_rejects_inconsistent_tables(inputs, message):
    with pytest.raises(ValueError, match=message):
        SubcatchmentGrouping(**inputs)


def test_result_model_is_frozen():
    grouping = SubcatchmentGrouping(**model_inputs())

    with pytest.raises(ValidationError):
        grouping.membership = grouping.membership.head(1)  # ty: ignore[invalid-assignment]
