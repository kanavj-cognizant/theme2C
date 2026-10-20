from collections import Counter, defaultdict
from typing import Any

import geopandas as gpd
import networkx as nx
import polars as pl
import shapely
from pydantic import BaseModel, ConfigDict, model_validator

from risk_map.config.models import DEFAULT_CONFIG
from risk_map.spatial.catchment import TARGET_CRS, CatchmentDelineation

_WEIGHT_TOLERANCE = 1e-9


class SubcatchmentGrouping(BaseModel):
    """Subcatchments built from merged watersheds, and their river node membership.

    ``subcatchments`` has ``subcatchment_id``, ``area_m2`` (m2), ``below_minimum``
    and ``geometry`` in EPSG:27700. ``membership`` has one row per river node with
    ``subcatchment_id``, ``node_id`` and ``allocation_weight``; weights sum to 1
    per subcatchment.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    subcatchments: gpd.GeoDataFrame
    membership: pl.DataFrame

    @model_validator(mode="after")
    def _check_consistency(self) -> "SubcatchmentGrouping":
        missing = {"subcatchment_id", "area_m2", "below_minimum", "geometry"} - set(
            self.subcatchments.columns
        )
        if missing:
            raise ValueError(f"subcatchments is missing columns {sorted(missing)}")
        missing = {"subcatchment_id", "node_id", "allocation_weight"} - set(self.membership.columns)
        if missing:
            raise ValueError(f"membership is missing columns {sorted(missing)}")
        if self.subcatchments.crs is None or self.subcatchments.crs.to_epsg() != 27700:
            raise ValueError("subcatchments must use EPSG:27700")
        if self.subcatchments["subcatchment_id"].duplicated().any():
            raise ValueError("subcatchments contains duplicate subcatchment_id values")
        unknown = set(self.membership["subcatchment_id"].to_list()) - set(
            self.subcatchments["subcatchment_id"].tolist()
        )
        if unknown:
            raise ValueError(
                f"membership references unknown subcatchment_id values {sorted(unknown)}"
            )
        if self.membership["node_id"].is_duplicated().any():
            raise ValueError("membership contains duplicate node_id values")
        if (self.membership["allocation_weight"] <= 0).any():
            raise ValueError("membership contains nonpositive allocation_weight values")
        totals = self.membership.group_by("subcatchment_id").agg(pl.col("allocation_weight").sum())
        unbalanced = totals.filter((pl.col("allocation_weight") - 1).abs() > _WEIGHT_TOLERANCE)
        if unbalanced.height:
            raise ValueError(
                "allocation_weight does not sum to 1 for subcatchment_id values "
                f"{sorted(unbalanced['subcatchment_id'].to_list())}"
            )
        return self


def group_subcatchments(
    delineation: CatchmentDelineation,
    river_graph: nx.DiGraph,
    *,
    min_area_m2: float = DEFAULT_CONFIG.min_catchment_area,
    graph_node_id_attribute: str = "nodeID",
) -> SubcatchmentGrouping:
    """Merge watersheds downstream into subcatchments of at least ``min_area_m2``.

    Pour points are the grouping unit: the downstream-directed river graph is
    collapsed to one vertex per pour point, and area accumulates downstream until
    a group reaches the minimum. An undersized outlet remainder joins the nearest
    upstream group; a component that never reaches the minimum is kept and
    flagged ``below_minimum``. Each subcatchment is named after its most
    downstream pour point, and its member nodes share its load equally.
    """
    if not min_area_m2 > 0:
        raise ValueError(f"min_area_m2 must be positive, got {min_area_m2}")
    pour_point_of = dict(
        zip(
            delineation.membership["node_id"].to_list(),
            delineation.membership["pour_point_id"].to_list(),
            strict=True,
        )
    )
    nodes_of: dict[Any, list[Any]] = defaultdict(list)
    for node_id, pour_point_id in sorted(pour_point_of.items()):
        nodes_of[pour_point_id].append(node_id)

    watersheds = delineation.watersheds
    pour_point_ids = watersheds["pour_point_id"].tolist()
    orphans = sorted(set(pour_point_ids) - set(nodes_of))
    if orphans:
        raise ValueError(f"Watersheds referenced by no river node: pour_point_id {orphans}")
    collapsed = _collapsed_graph(river_graph, pour_point_of, nodes_of, graph_node_id_attribute)
    areas = dict(zip(pour_point_ids, watersheds["area_m2"].tolist(), strict=True))
    geometries = dict(zip(pour_point_ids, watersheds.geometry.to_numpy(), strict=True))
    groups = _merge_downstream(collapsed, areas, min_area_m2)

    subcatchment_ids = sorted(groups)
    subcatchments = gpd.GeoDataFrame(
        {"subcatchment_id": subcatchment_ids},
        geometry=[
            # Delineation already snapped watersheds to the precision grid.
            shapely.union_all([geometries[p] for p in groups[s]])
            for s in subcatchment_ids
        ],
        crs=TARGET_CRS,
    )
    subcatchments["area_m2"] = subcatchments.area
    subcatchments["below_minimum"] = subcatchments["area_m2"] < min_area_m2

    rows = []
    for subcatchment_id in subcatchment_ids:
        nodes = sorted(node for p in groups[subcatchment_id] for node in nodes_of[p])
        rows.extend((subcatchment_id, node, 1.0 / len(nodes)) for node in nodes)
    membership = pl.DataFrame(
        rows, schema=["subcatchment_id", "node_id", "allocation_weight"], orient="row"
    )
    return SubcatchmentGrouping(
        subcatchments=subcatchments[["subcatchment_id", "area_m2", "below_minimum", "geometry"]],
        membership=membership,
    )


def _collapsed_graph(
    river_graph: nx.DiGraph,
    pour_point_of: dict[Any, Any],
    nodes_of: dict[Any, list[Any]],
    id_attribute: str,
) -> nx.DiGraph:
    """River graph with one vertex per pour point; must be a forest of in-trees."""
    node_ids = {}
    for node, attributes in river_graph.nodes(data=True):
        if id_attribute not in attributes:
            raise ValueError(f"Graph node {node!r} lacks the {id_attribute!r} attribute")
        node_ids[node] = attributes[id_attribute]
    duplicated = sorted(i for i, count in Counter(node_ids.values()).items() if count > 1)
    if duplicated:
        raise ValueError(f"Duplicate graph node IDs: {duplicated}")
    unknown = sorted(set(node_ids.values()) - set(pour_point_of))
    if unknown:
        raise ValueError(f"Graph node IDs missing from delineation membership: {unknown}")
    absent = sorted(set(pour_point_of) - set(node_ids.values()))
    if absent:
        raise ValueError(f"Membership node IDs missing from river_graph: {absent}")

    collapsed = nx.DiGraph()
    collapsed.add_nodes_from(sorted(nodes_of))
    for upstream, downstream in river_graph.edges:
        source, target = pour_point_of[node_ids[upstream]], pour_point_of[node_ids[downstream]]
        if source != target:
            collapsed.add_edge(source, target)

    branching = []
    for pour_point_id in collapsed:
        targets = sorted(collapsed.successors(pour_point_id))
        if len(targets) > 1:
            branching.append(
                f"pour point {pour_point_id} (nodes {nodes_of[pour_point_id]}) "
                f"-> pour points {targets} (nodes {[nodes_of[t] for t in targets]})"
            )
    if branching:
        raise ValueError(
            "Pour points with more than one downstream neighbour: " + "; ".join(branching)
        )
    try:
        cycle = [source for source, _ in nx.find_cycle(collapsed)]
    except nx.NetworkXNoCycle:
        return collapsed
    nodes = sorted(node for pour_point_id in cycle for node in nodes_of[pour_point_id])
    raise ValueError(f"Pour points {cycle} form a cycle through river nodes {nodes}")


def _merge_downstream(
    collapsed: nx.DiGraph, areas: dict[Any, float], min_area_m2: float
) -> dict[Any, list[Any]]:
    """Pour point groups keyed by their most downstream pour point."""
    groups: dict[Any, list[Any]] = {}
    group_of: dict[Any, Any] = {}
    pending: dict[Any, list[Any]] = defaultdict(list)
    # Hops to, and key of, the nearest finalized group upstream of each vertex.
    nearest: dict[Any, tuple[int, Any]] = {}

    for vertex in nx.topological_sort(collapsed):
        candidates = []
        for parent in collapsed.predecessors(vertex):
            if parent in group_of:
                candidates.append((1, group_of[parent]))
            elif parent in nearest:
                hops, key = nearest[parent]
                candidates.append((hops + 1, key))
        if candidates:
            nearest[vertex] = min(candidates)

        members = [*pending.pop(vertex, []), vertex]
        if sum(areas[member] for member in members) < min_area_m2:
            downstream = next(iter(collapsed.successors(vertex)), None)
            if downstream is not None:
                pending[downstream].extend(members)
                continue
            if vertex in nearest:
                members = groups.pop(nearest[vertex][1]) + members
        groups[vertex] = members
        group_of.update(dict.fromkeys(members, vertex))
    return groups
