import math

import geopandas as gpd
import networkx as nx
import pytest
from shapely.geometry import LineString

from risk_map.config.constants import P_CONSTANTS
from risk_map.config.enums import PollutantType, SourceType
from risk_map.graph.network import build_river_graph
from risk_map.graph.propagation import InjectedLoads, propagate_loads
from risk_map.models.uncertainty import Measurement
from risk_map.spatial.flow import LinearAccumulationFlowEstimator


@pytest.mark.parametrize(
    ("edges", "message"),
    [
        ([(1, 1)], "cycle"),
        ([(1, 2), (2, 1)], "cycle"),
        ([(1, 2), (2, 3), (3, 1)], "cycle"),
        ([(1, 2), (1, 3)], "downstream split"),
        ([(1, 2), (1, 3), (2, 4), (3, 4)], "downstream split"),
    ],
    ids=["self-loop", "two-node-cycle", "three-node-cycle", "split", "split-rejoin"],
)
def test_propagation_rejects_invalid_topology_before_calculation(edges, message, monkeypatch):
    import risk_map.graph.propagation as propagation

    graph = nx.DiGraph()
    graph.add_node((-1.0, 0.0), flow=2.0)
    for upstream, downstream in edges:
        graph.add_edge(
            (float(upstream), 0.0),
            (float(downstream), 0.0),
            length=100.0,
            flow_speed=1.0,
        )
    before = graph.copy()

    def unexpected_calculation(*args, **kwargs):
        pytest.fail("Topology must be validated before numerical propagation")

    monkeypatch.setattr(propagation, "calculate_concentration", unexpected_calculation)
    with pytest.raises(ValueError, match=message):
        propagate_loads(graph, {}, P_CONSTANTS)

    assert nx.utils.graphs_equal(graph, before)


def test_propagation_accepts_confluences_and_disconnected_catchments():
    graph = nx.DiGraph()
    graph.add_edges_from(
        [
            ((1.0, 0.0), (3.0, 0.0)),
            ((2.0, 0.0), (3.0, 0.0)),
            ((4.0, 0.0), (5.0, 0.0)),
        ],
        length=100.0,
        flow_speed=1.0,
    )
    graph.add_node((6.0, 0.0))
    nx.set_node_attributes(graph, 2.0, "flow")
    before = graph.copy()
    injections: InjectedLoads = {
        (1.0, 0.0): {SourceType.CSO: {"upstream-cso": 1.0}},
        (2.0, 0.0): {SourceType.WWTW: {"upstream-wwtw": 2.0}},
        (4.0, 0.0): {SourceType.CSO: {"separate-cso": 3.0}},
    }

    results = propagate_loads(graph, injections, P_CONSTANTS)

    decay = math.exp(-P_CONSTANTS.degradation_rate * 100.0)
    assert set(results) == set(graph)
    assert results[(3.0, 0.0)].total_load.nominal == pytest.approx(3.0 * decay)
    assert results[(3.0, 0.0)].source_loads[SourceType.CSO][
        "upstream-cso"
    ].nominal == pytest.approx(decay)
    assert results[(5.0, 0.0)].total_load.nominal == pytest.approx(3.0 * decay)
    assert results[(6.0, 0.0)].total_load.nominal == 0.0
    assert nx.utils.graphs_equal(graph, before)


def test_build_river_graph_structure():
    """Verify directed river graph construction from segmented reaches."""
    # 2 connected reaches: Reach 0 (0,0 -> 100,0), Reach 1 (100,0 -> 200,0)
    rivers = gpd.GeoDataFrame(
        {
            "riverID": [10, 20],
            "US_Accum": [1000.0, 2000.0],
            "geometry": [
                LineString([(0, 0), (100, 0)]),
                LineString([(100, 0), (200, 0)]),
            ],
        },
        crs="EPSG:27700",
    )

    estimator = LinearAccumulationFlowEstimator(
        coeff=1.476e-5, accum_col="US_Accum", flow_speed=0.77
    )
    graph = build_river_graph(rivers, flow_estimator=estimator)

    assert isinstance(graph, nx.DiGraph)
    # 3 nodes: (0, 0), (100, 0), (200, 0)
    assert graph.number_of_nodes() == 3
    assert graph.number_of_edges() == 2

    # Edges should carry length, flow_speed, and riverID
    edge_data = graph[(100, 0)][(200, 0)]
    assert edge_data["length"] == 100.0
    assert edge_data["riverID"] == 20
    assert edge_data["flow_speed"] == 0.77

    # Nodes carry flow and nodeID
    assert (0, 0) in graph.nodes
    assert (200, 0) in graph.nodes
    assert graph.nodes[(200, 0)]["flow"] == 2000.0 * 1.476e-5


def test_missing_flow_speed_raises_error():
    """Verify that build_river_graph raises KeyError if flow_speed is not provided."""
    rivers = gpd.GeoDataFrame(
        {
            "riverID": [10],
            "geometry": [LineString([(0, 0), (100, 0)])],
        },
        crs="EPSG:27700",
    )
    with pytest.raises(KeyError, match="flow_speed"):
        build_river_graph(rivers, flow_estimator=None)


def test_topological_propagation_analytical_mass_balance():
    """Analytical verification of 1D advection-decay and source attribution.

    Synthetic Reach:
      Node A (0,0) ---- 1000m ----> Node B (1000, 0) ---- 1000m ----> Node C (2000, 0)
      Flow Q = 2.0 m3/s, Flow Speed v = 1.0 m/s
      Degradation rate k = 4.6296e-7 s^-1 (P degradation in SI)
      Point source injection at Node A: CSO_1 = 10.0 kg/s ± 1.0 kg/s
      Point source injection at Node B: CSO_2 = 4.0 kg/s ± 0.5 kg/s
    """
    rivers = gpd.GeoDataFrame(
        {
            "riverID": [1, 2],
            "US_Accum": [2.0 / 1.476e-5, 2.0 / 1.476e-5],  # Yields exactly 2.0 m3/s flow
            "geometry": [
                LineString([(0, 0), (1000, 0)]),
                LineString([(1000, 0), (2000, 0)]),
            ],
        },
        crs="EPSG:27700",
    )

    estimator = LinearAccumulationFlowEstimator(
        coeff=1.476e-5, accum_col="US_Accum", flow_speed=1.0
    )
    graph = build_river_graph(rivers, flow_estimator=estimator)

    node_a = (0.0, 0.0)
    node_b = (1000.0, 0.0)
    node_c = (2000.0, 0.0)

    # Define nested point injections: {node: {SourceType: {source_id: Measurement}}}
    injected_loads: InjectedLoads = {
        node_a: {SourceType.CSO: {"CSO_1": Measurement(nominal=10.0, std_dev=1.0)}},
        node_b: {SourceType.CSO: {"CSO_2": Measurement(nominal=4.0, std_dev=0.5)}},
    }

    # Run single-pass topological propagation (flow_speed is read per edge directly from graph)
    results = propagate_loads(
        graph=graph,
        injected_loads=injected_loads,
        parameters=P_CONSTANTS,
    )

    # 1. Verification at Node A (upstream outfall)
    # Load: CSO_1 = 10 kg/s
    # Concentration = 10 / 2.0 = 5.0 kg/m3
    res_a = results[node_a]
    assert res_a.pollutant == PollutantType.P
    assert math.isclose(res_a.total_load.nominal, 10.0, rel_tol=1e-5)
    assert math.isclose(res_a.concentration.nominal, 5.0, rel_tol=1e-5)
    assert res_a.attribution[SourceType.CSO]["CSO_1"].nominal == 100.0
    assert res_a.attribution_by_type[SourceType.CSO].nominal == 100.0

    # 2. Verification at Node B (downstream reach + local injection CSO_2)
    # Travel time from A to B: 1000m / 1.0 m/s = 1000 s
    decay_ab = math.exp(-P_CONSTANTS.degradation_rate * 1000.0)
    expected_cso1_at_b = 10.0 * decay_ab
    expected_total_load_at_b = expected_cso1_at_b + 4.0

    res_b = results[node_b]
    assert math.isclose(
        res_b.source_loads[SourceType.CSO]["CSO_1"].nominal, expected_cso1_at_b, rel_tol=1e-5
    )
    assert math.isclose(res_b.source_loads[SourceType.CSO]["CSO_2"].nominal, 4.0, rel_tol=1e-5)
    assert math.isclose(res_b.total_load.nominal, expected_total_load_at_b, rel_tol=1e-5)
    assert math.isclose(res_b.concentration.nominal, expected_total_load_at_b / 2.0, rel_tol=1e-5)

    # Verify attribution percentages sum strictly to 100%
    cso1_pct = res_b.attribution[SourceType.CSO]["CSO_1"].nominal
    cso2_pct = res_b.attribution[SourceType.CSO]["CSO_2"].nominal
    assert math.isclose(cso1_pct + cso2_pct, 100.0, rel_tol=1e-4)
    assert math.isclose(res_b.attribution_by_type[SourceType.CSO].nominal, 100.0, rel_tol=1e-4)

    # 3. Verification at Node C (further downstream decay of both sources)
    decay_bc = math.exp(-P_CONSTANTS.degradation_rate * 1000.0)
    expected_cso1_at_c = expected_cso1_at_b * decay_bc
    expected_cso2_at_c = 4.0 * decay_bc

    res_c = results[node_c]
    assert math.isclose(
        res_c.source_loads[SourceType.CSO]["CSO_1"].nominal, expected_cso1_at_c, rel_tol=1e-5
    )
    assert math.isclose(
        res_c.source_loads[SourceType.CSO]["CSO_2"].nominal, expected_cso2_at_c, rel_tol=1e-5
    )
    assert math.isclose(
        res_c.total_load.nominal, expected_cso1_at_c + expected_cso2_at_c, rel_tol=1e-5
    )
    assert math.isclose(res_c.attribution_by_type[SourceType.CSO].nominal, 100.0, rel_tol=1e-4)
