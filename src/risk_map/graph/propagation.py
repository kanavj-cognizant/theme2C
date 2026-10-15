from collections import defaultdict
from dataclasses import dataclass, field

import networkx as nx

from risk_map.config.constants import PollutantParameters
from risk_map.config.enums import PollutantType, SourceType
from risk_map.config.models import DEFAULT_CONFIG
from risk_map.models.uncertainty import (
    Measurement,
    calculate_concentration,
    calculate_decay,
    calculate_risk,
    to_measurement,
)


@dataclass
class ReachResult:
    """Water quality assessment result for a single river reach/node."""

    node: tuple[float, float]
    flow: Measurement
    pollutant: PollutantType
    source_loads: dict[SourceType, dict[str, Measurement]] = field(default_factory=dict)
    total_load: Measurement = field(default_factory=lambda: Measurement(0.0, 0.0))
    concentration: Measurement = field(default_factory=lambda: Measurement(0.0, 0.0))
    risk: Measurement = field(default_factory=lambda: Measurement(0.0, 0.0))
    attribution: dict[SourceType, dict[str, Measurement]] = field(default_factory=dict)
    attribution_by_type: dict[SourceType, Measurement] = field(default_factory=dict)


NodeKey = tuple[float | int, float | int]
InjectedLoads = dict[NodeKey, dict[SourceType, dict[str, Measurement | float]]]


def propagate_loads(
    graph: nx.DiGraph,
    injected_loads: InjectedLoads,
    parameters: PollutantParameters,
    min_flow_threshold: float = DEFAULT_CONFIG.min_flow_threshold,
) -> dict[tuple[float, float], ReachResult]:
    """Single-pass 1D topological advection-decay mass propagation.

    Propagates individual source loads down the river network organized by SourceType
    while retaining full per-source attribution, type rollups, and first-order uncertainty.
    Uses reach-specific length and flow_speed defined on river graph edges.

    Parameters
    ----------
    graph : nx.DiGraph
        Directed river network graph with edges directed downstream.
    injected_loads : dict
        Mapping of node (x, y) to {SourceType: {source_id: load_measurement}}.
    parameters : PollutantParameters
        Physical constants including guideline and degradation_rate (s^-1).
    min_flow_threshold : float
        Discharge cutoff below which reach is treated as dry (default from PipelineConfig).

    Returns
    -------
    dict[tuple[float, float], ReachResult]
        Water quality concentration, risk, and source attribution at each node.

    Raises
    ------
    ValueError
        If the graph contains a directed cycle or a downstream split.
    """
    try:
        topo_order = list(nx.topological_sort(graph))
    except nx.NetworkXUnfeasible as error:
        raise ValueError("River graph contains a directed cycle") from error

    for node, degree in graph.out_degree():
        if degree > 1:
            raise ValueError(
                f"River graph contains a downstream split at node {node!r}; "
                "each node must have at most one downstream successor"
            )

    results: dict[tuple[float, float], ReachResult] = {}
    accumulated_loads: dict[tuple[float, float], dict[SourceType, dict[str, Measurement]]] = (
        defaultdict(dict)
    )

    for node in topo_order:
        node_flow_val = float(graph.nodes[node].get("flow", 0.0))
        node_flow = to_measurement(node_flow_val)

        # 1. Decay and accumulate loads from all upstream predecessors
        incoming_sources: dict[SourceType, dict[str, Measurement]] = defaultdict(dict)

        for parent in graph.predecessors(node):
            edge = graph[parent][node]
            edge_length = float(edge.get("length", 0.0))
            if "flow_speed" not in edge:
                raise KeyError(
                    f"Edge ({parent}, {node}) is missing required 'flow_speed' attribute"
                )
            edge_flow_speed = float(edge["flow_speed"])
            parent_sources = accumulated_loads[parent]

            for s_type, s_dict in parent_sources.items():
                for source_id, parent_load in s_dict.items():
                    decayed = calculate_decay(
                        load=parent_load,
                        length=edge_length,
                        flow_speed=edge_flow_speed,
                        degradation_rate=parameters.degradation_rate,
                    )
                    curr = incoming_sources[s_type].get(source_id, Measurement(0.0, 0.0))
                    incoming_sources[s_type][source_id] = curr + decayed

        # 2. Add local mass injections at this node
        local_sources = injected_loads.get(node, {})
        for s_type, s_dict in local_sources.items():
            for source_id, load_val in s_dict.items():
                m_load = to_measurement(load_val)
                curr = incoming_sources[s_type].get(source_id, Measurement(0.0, 0.0))
                incoming_sources[s_type][source_id] = curr + m_load

        # Store snapshot for downstream traversal
        frozen_incoming: dict[SourceType, dict[str, Measurement]] = {
            st: dict(ids) for st, ids in incoming_sources.items()
        }
        accumulated_loads[node] = frozen_incoming

        # 3. Calculate total load, concentration, risk, and percentage attribution
        total_load = Measurement(0.0, 0.0)
        type_totals: dict[SourceType, Measurement] = defaultdict(lambda: Measurement(0.0, 0.0))

        for s_type, s_dict in frozen_incoming.items():
            for s_load in s_dict.values():
                total_load = total_load + s_load
                type_totals[s_type] = type_totals[s_type] + s_load

        concentration = calculate_concentration(
            load=total_load, river_flow=node_flow, min_flow_threshold=min_flow_threshold
        )
        risk = calculate_risk(concentration=concentration, guideline=parameters.guideline)

        # Calculate attribution percentage per source and rollup by SourceType
        attribution: dict[SourceType, dict[str, Measurement]] = defaultdict(dict)
        attribution_by_type: dict[SourceType, Measurement] = {}

        if total_load.nominal > 0:
            for s_type, s_dict in frozen_incoming.items():
                attribution_by_type[s_type] = (type_totals[s_type] / total_load) * 100.0
                for source_id, s_load in s_dict.items():
                    attribution[s_type][source_id] = (s_load / total_load) * 100.0

        results[node] = ReachResult(
            node=node,
            flow=node_flow,
            pollutant=parameters.pollutant,
            source_loads=frozen_incoming,
            total_load=total_load,
            concentration=concentration,
            risk=risk,
            attribution=dict(attribution),
            attribution_by_type=attribution_by_type,
        )

    return results
