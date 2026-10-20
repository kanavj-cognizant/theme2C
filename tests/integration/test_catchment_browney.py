import pathlib

import geopandas as gpd
import networkx as nx
import pytest

import RiskMap
from risk_map.data.dem import ElevatrDemProvider
from risk_map.spatial.catchment import delineate_catchments
from risk_map.spatial.grouping import group_subcatchments
from utils.rivers import linear_flow

pytestmark = pytest.mark.integration

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "browney"
LEGACY_DIR = FIXTURES_DIR / "legacy_catchments"
TILE_DIR = ROOT / "data" / "tiles"
CRS = "EPSG:27700"


def browney_mask() -> gpd.GeoDataFrame:
    cmnt = gpd.read_file(FIXTURES_DIR / "catchment.geojson")
    cmnt["geometry"] = cmnt.make_valid()
    cmnt = cmnt[["geometry"]][cmnt.geometry.type == "Polygon"].to_crs(CRS)
    return gpd.GeoDataFrame(geometry=[cmnt.geometry.union_all()], crs=CRS)


def legacy_graph(mask: gpd.GeoDataFrame) -> nx.DiGraph:
    riskmap = RiskMap.RiskMap(
        riversdf=gpd.read_parquet(FIXTURES_DIR / "rivers.parquet"),
        id_col="RivID",
        resolution=300.0,
        river_flow_func=lambda x: linear_flow(x["US_Accum"]),
        catchment_mask=mask,
    )
    return riskmap.get_river_network()


def cumulative_area(graph: nx.DiGraph, label_of: dict, area_of: dict) -> dict:
    result = {}
    for key in graph.nodes:
        members = nx.ancestors(graph, key) | {key}
        labels = {label_of.get(graph.nodes[m]["nodeID"]) for m in members} - {None}
        result[graph.nodes[key]["nodeID"]] = sum(area_of.get(label, 0.0) for label in labels)
    return result


@pytest.fixture(scope="module")
def browney(tmp_path_factory):
    if not any(TILE_DIR.glob("*")):
        pytest.skip("No cached elevation tiles in data/tiles")
    mask = browney_mask()
    nodes = gpd.read_parquet(LEGACY_DIR / "nodes.parquet")
    result = delineate_catchments(
        nodes,
        ElevatrDemProvider(TILE_DIR),
        mask=mask,
        workspace_root=tmp_path_factory.mktemp("wbe"),
    )
    legacy = gpd.read_parquet(LEGACY_DIR / "wbe_polygons.parquet").to_crs(CRS)
    legacy["geometry"] = legacy.make_valid()
    legacy = legacy.dissolve(by="VALUE", as_index=False)
    return mask, nodes, result, legacy


def test_every_browney_node_has_a_watershed(browney):
    _, nodes, result, _ = browney

    assert result.membership.height == len(nodes) == 426
    assert result.membership["pour_point_id"].null_count() == 0


def test_browney_total_area_matches_legacy_within_3_percent(browney):
    _, _, result, legacy = browney

    assert result.watersheds.area.sum() == pytest.approx(legacy.area.sum(), rel=0.03)


def test_browney_watersheds_stay_inside_mask(browney):
    mask, _, result, _ = browney

    outside = result.watersheds.difference(mask.geometry.iloc[0]).area.sum()
    assert outside / result.watersheds.area.sum() < 0.01


def test_browney_large_nodes_match_legacy_cumulative_area(browney):
    mask, _, result, legacy = browney
    graph = legacy_graph(mask)
    legacy_area = dict(zip(legacy["VALUE"] - 1, legacy.area, strict=True))
    new_area = dict(zip(result.watersheds["pour_point_id"], result.watersheds.area, strict=True))
    new_label = dict(
        zip(result.membership["node_id"], result.membership["pour_point_id"], strict=True)
    )

    legacy_cumulative = cumulative_area(graph, {n: n for n in legacy_area}, legacy_area)
    new_cumulative = cumulative_area(graph, new_label, new_area)

    def matches(node) -> bool:
        return abs(new_cumulative[node] / legacy_cumulative[node] - 1) <= 0.5

    downstream = {graph.nodes[u]["nodeID"]: graph.nodes[v]["nodeID"] for u, v in graph.edges}
    large = [node for node, area in legacy_cumulative.items() if area > 5e6]
    assert large
    # A tributary credited one node apart is accepted if the next node downstream agrees.
    mismatched = [
        node
        for node in large
        if not matches(node) and not (node in downstream and matches(downstream[node]))
    ]
    assert mismatched == []


def test_browney_grouping_meets_minimum_area_and_keeps_every_node(browney):
    mask, nodes, result, _ = browney

    grouping = group_subcatchments(result, legacy_graph(mask))

    subcatchments = grouping.subcatchments
    assert grouping.membership.height == len(nodes)
    assert (subcatchments.loc[~subcatchments["below_minimum"], "area_m2"] >= 20_000.0).all()
    assert subcatchments["area_m2"].sum() == pytest.approx(
        result.watersheds["area_m2"].sum(), rel=1e-6
    )
