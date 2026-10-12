from collections import defaultdict

import geopandas as gpd
import momepy
import networkx as nx
import polars as pl
from shapely.geometry import LineString

from risk_map.spatial.flow import FlowEstimator


def build_river_graph(
    rivers: gpd.GeoDataFrame,
    flow_estimator: FlowEstimator | None = None,
) -> nx.DiGraph:
    """Construct a directed river graph from segmented river reaches.

    Each node is represented by its (x, y) coordinate tuple.
    Edges point downstream (in the direction of digitised LineString vertices).
    Edge attributes include 'length' (m), 'flow_speed' (m/s), and 'riverID'.
    Node attributes include 'flow' (m3/s) and 'nodeID'.
    """
    rivers_gdf = rivers.copy()
    if flow_estimator is not None:
        if "flow" not in rivers_gdf.columns:
            flow_series = flow_estimator.estimate_flow(rivers_gdf)
            rivers_gdf["flow"] = flow_series.to_numpy()
        if "flow_speed" not in rivers_gdf.columns:
            speed_val = flow_estimator.estimate_flow_speed(rivers_gdf)
            if isinstance(speed_val, pl.Series):
                rivers_gdf["flow_speed"] = speed_val.to_numpy()
            else:
                rivers_gdf["flow_speed"] = float(speed_val)

    # Use momepy to construct directed primal network
    graph: nx.DiGraph = momepy.gdf_to_nx(
        rivers_gdf,
        approach="primal",
        directed=True,
        multigraph=False,
    )

    # Standardize edge attributes
    for u, v, data in graph.edges(data=True):
        if "mm_len" in data:
            data["length"] = float(data["mm_len"])
        elif "geometry" in data and isinstance(data["geometry"], LineString):
            data["length"] = float(data["geometry"].length)
        else:
            data["length"] = float(LineString([u, v]).length)
        if "flow_speed" not in data:
            raise KeyError(
                f"Edge ({u}, {v}) is missing 'flow_speed'. "
                "Ensure a FlowEstimator is provided or 'flow_speed' column exists in rivers."
            )
        data["flow_speed"] = float(data["flow_speed"])

    # Propagate maximum incident reach flow to nodes
    node_flows = defaultdict(float)
    node_river_ids = defaultdict(set)

    for u, v, data in graph.edges(data=True):
        edge_flow = float(data.get("flow", 0.0))
        edge_id = data.get("riverID")

        node_flows[u] = max(node_flows[u], edge_flow)
        node_flows[v] = max(node_flows[v], edge_flow)
        if edge_id is not None:
            node_river_ids[u].add(edge_id)
            node_river_ids[v].add(edge_id)

    # Assign node attributes
    for idx, (node, data) in enumerate(graph.nodes(data=True)):
        data["nodeID"] = idx
        data["flow"] = node_flows[node]
        data["riverID"] = node_river_ids[node]

    return graph
