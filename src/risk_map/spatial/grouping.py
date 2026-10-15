from collections import defaultdict
from typing import Any

import geopandas as gpd
import networkx as nx
import polars as pl
import shapely
from shapely.geometry.base import BaseGeometry

from risk_map.config.models import DEFAULT_CONFIG


def merge_subcatchments_by_area(
    node_catchments: gpd.GeoDataFrame,
    river_graph: nx.DiGraph,
    min_area: float = DEFAULT_CONFIG.min_catchment_area,
    node_id_column: str = "node_id",
    graph_node_id_attribute: str = "nodeID",
) -> tuple[gpd.GeoDataFrame, pl.DataFrame]:
    """Merge adjacent node catchments downstream until groups reach min_area.

    Input polygons must be non-overlapping WBE watershed zones. Missing or
    empty node polygons contribute zero area. River graph edges must point
    downstream, and each node may have at most one downstream successor.

    Returns one row per merged subcatchment plus a membership table. Each
    member node receives an equal allocation weight so its group's weights sum
    to one.
    """
    if min_area <= 0:
        raise ValueError("min_area must be positive")
    if node_catchments.crs is None or not node_catchments.crs.is_projected:
        raise ValueError("node_catchments must use a projected CRS in metres")
    if any(axis.unit_conversion_factor != 1.0 for axis in node_catchments.crs.axis_info):
        raise ValueError("node_catchments CRS axes must use metres")
    if node_id_column not in node_catchments.columns:
        raise ValueError(f"Missing node identifier column: {node_id_column}")
    if not nx.is_directed_acyclic_graph(river_graph):
        raise ValueError("river_graph must be a directed acyclic graph")
    if any(river_graph.out_degree(node) > 1 for node in river_graph):
        raise ValueError("Each river node must have at most one downstream successor")

    node_by_id: dict[Any, Any] = {}
    for node, attributes in river_graph.nodes(data=True):
        if graph_node_id_attribute not in attributes:
            raise ValueError(f"Graph node {node!r} lacks {graph_node_id_attribute!r}")
        node_id = attributes[graph_node_id_attribute]
        if node_id in node_by_id:
            raise ValueError(f"Duplicate graph node ID: {node_id!r}")
        node_by_id[node_id] = node

    grouped_polygons = node_catchments.groupby(node_id_column, sort=False).geometry.agg(
        lambda geometries: shapely.union_all(geometries.fillna(shapely.Polygon()).to_numpy())
    )
    polygon_ids = set(grouped_polygons.index)
    graph_ids = set(node_by_id)
    unknown = polygon_ids - graph_ids
    if unknown:
        raise ValueError(f"Catchment polygons reference unknown graph node IDs: {unknown}")

    node_geometry = grouped_polygons.to_dict()
    for node_id in graph_ids - polygon_ids:
        node_geometry[node_id] = shapely.Polygon()
    node_areas = {node_id: float(geometry.area) for node_id, geometry in node_geometry.items()}
    topological_nodes = list(nx.topological_sort(river_graph))
    pending_members: dict[Any, list[Any]] = defaultdict(list)
    pending_areas: dict[Any, float] = defaultdict(float)
    pending_geometries: dict[Any, list[BaseGeometry]] = defaultdict(list)

    groups: dict[Any, dict[str, Any]] = {}
    group_for_node: dict[Any, Any] = {}
    nearest_group: dict[Any, tuple[int, Any]] = {}

    def finalize(
        group_id: Any, members: list[Any], area: float, geometries: list[BaseGeometry]
    ) -> None:
        groups[group_id] = {
            "members": list(members),
            "area": area,
            "geometries": list(geometries),
            "below_minimum": area < min_area,
        }
        for member in members:
            group_for_node[member] = group_id

    for graph_node in topological_nodes:
        node_id = river_graph.nodes[graph_node][graph_node_id_attribute]
        nearest_candidates = []
        for parent in river_graph.predecessors(graph_node):
            parent_id = river_graph.nodes[parent][graph_node_id_attribute]
            if parent_id in group_for_node:
                nearest_candidates.append((1, group_for_node[parent_id]))
            elif parent in nearest_group:
                distance, group_id = nearest_group[parent]
                nearest_candidates.append((distance + 1, group_id))
        closest_group = (
            min(nearest_candidates, key=lambda item: (item[0], str(item[1])))
            if nearest_candidates
            else None
        )
        if closest_group is not None:
            nearest_group[graph_node] = closest_group

        members = [*pending_members.pop(node_id, []), node_id]
        area = pending_areas.pop(node_id, 0.0) + node_areas[node_id]
        geometries = [*pending_geometries.pop(node_id, []), node_geometry[node_id]]

        if area >= min_area:
            finalize(node_id, members, area, geometries)
            continue

        downstream = next(iter(river_graph.successors(graph_node)), None)
        if downstream is not None:
            downstream_id = river_graph.nodes[downstream][graph_node_id_attribute]
            pending_members[downstream_id].extend(members)
            pending_areas[downstream_id] += area
            pending_geometries[downstream_id].extend(geometries)
            continue

        upstream_group_id = closest_group[1] if closest_group is not None else None
        if upstream_group_id is not None:
            group_id = upstream_group_id
            group = groups[group_id]
            group["members"].extend(members)
            group["area"] += area
            group["geometries"].extend(geometries)
            group["below_minimum"] = group["area"] < min_area
            for member in members:
                group_for_node[member] = group_id
        else:
            finalize(node_id, members, area, geometries)

    subcatchment_rows = []
    mapping_rows = []
    for subcatchment_id, group in groups.items():
        merged_geometry = shapely.union_all(group["geometries"])
        members = group["members"]
        subcatchment_rows.append(
            {
                "subcatchment_id": subcatchment_id,
                "area": float(merged_geometry.area),
                "below_minimum": group["below_minimum"],
                "geometry": merged_geometry,
            }
        )
        weight = 1.0 / len(members)
        mapping_rows.extend(
            {
                "subcatchment_id": subcatchment_id,
                "node_id": member,
                "allocation_weight": weight,
            }
            for member in members
        )

    merged = gpd.GeoDataFrame(subcatchment_rows, geometry="geometry", crs=node_catchments.crs)
    mapping = pl.DataFrame(mapping_rows)
    return merged, mapping
