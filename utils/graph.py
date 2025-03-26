import networkx as nx
import numpy as np
import shapely
from collections.abc import Callable
from RiskMap import RiskMap
from collections import defaultdict


def add_to_graph(
    graph: nx.DiGraph,
    fieldName: str,
    ID: int | str | tuple[str, str],
    p: shapely.Point,
    edge: tuple,
    distance_cutoff: float,
    directed: bool,
    both_ways: bool = True,
    is_node: bool = False,
) -> None:
    """add_to_graph takes in points and adds their distance from the nodes to the graph on the nodes of the graph.
    This is achieved through a BFS and works for directed and undirected graphs.
    For each node, there will be a set of tuples of (ID,Dist) associated with the input point.

    Parameters
    ----------
    graph : nx.DiGraph
        The graph to add the information on
    fieldName : str
        The field in the node attributes to add the information on
    ID : int | str
        The ID of the point
    p : shapely.Point
        A point object representing the location of the point. It is assumed the point is on an edge
    edge : tuple
        The edge corresponding to that point in the graph
    distance_cutoff : float
        The distance cutoff to perform the BFS with
    directed : bool
        To indicate whether the graph is directed or not
    both_ways : bool, optional
        To indicate for a directed graph, to perform 'downstream' or both 'downstream' and 'upstream', by default True
    is_node : bool, optional
        To indicate if the point is a node of the graph, by default False
    """
    if (edge is None or p is None) and not is_node:
        return

    if directed:
        if is_node:
            next_node = (p.x, p.y)
            next_dist = 0
        else:
            # Loop over successors, add data
            next_node = edge[1]
            # Find distance between geometry and first point
            next_dist = p.distance(shapely.Point(next_node))

        # Check if distance lesser than threshold
        if next_dist < distance_cutoff:
            # Get prev data
            dataBefore = graph.nodes(data=fieldName)[next_node]
            # If exists, add data
            if dataBefore is not None and isinstance(dataBefore, dict):
                dataBefore[ID] = next_dist
            # If doesn't exist, add new dict
            else:
                nx.set_node_attributes(
                    graph, {next_node: {ID: next_dist}}, fieldName)

            # Loop over children nodes
            directed_graph_bfs(graph, next_node, fieldName,
                               distance_cutoff, ID, True)

        if not both_ways:
            return

        # Loop over predecessors
        if is_node:
            prev_node = (p.x, p.y)
            prev_dist = 0
        else:
            prev_node = edge[0]
            prev_dist = -p.distance(shapely.Point(prev_node))
        if prev_dist > -distance_cutoff:
            dataBefore = graph.nodes(data=fieldName)[prev_node]
            if dataBefore is not None and isinstance(dataBefore, dict):
                dataBefore[ID] = prev_dist
            else:
                nx.set_node_attributes(
                    graph, {prev_node: {ID: prev_dist}}, fieldName)
            directed_graph_bfs(graph, prev_node, fieldName,
                               distance_cutoff, ID, False)
        return

    else:
        traversed = set()

        curr_nodes = []
        if is_node:
            next_node = (p.x, p.y)
            next_dist = 0
        else:
            next_node = edge[1]
            next_dist = p.distance(shapely.Point(next_node))
        # Check if distance lesser than threshold
        if next_dist < distance_cutoff:
            # Get prev data
            dataBefore = graph.nodes(data=fieldName)[next_node]
            # If exists, add data
            if dataBefore is not None and isinstance(dataBefore, dict):
                dataBefore[ID] = next_dist
            # If doesn't exist, add new dict
            else:
                nx.set_node_attributes(
                    graph, {next_node: {ID: next_dist}}, fieldName)
            curr_nodes.append(next_node)
            traversed.add(next_node)
        prev_node = edge[0]
        prev_dist = p.distance(shapely.Point(prev_node))
        if prev_dist < distance_cutoff:
            dataBefore = graph.nodes(data=fieldName)[prev_node]
            if dataBefore is not None and isinstance(dataBefore, dict):
                dataBefore[ID] = prev_dist
            else:
                nx.set_node_attributes(
                    graph, {prev_node: {ID: prev_dist}}, fieldName)
            curr_nodes.append(prev_node)
            traversed.add(prev_node)
        curr_nodes = directed_graph_bfs(
            graph, curr_nodes,  fieldName, distance_cutoff, ID, traversed)
        return


def directed_graph_bfs(graph: nx.DiGraph, start_node: tuple, colName: str, distance_cutoff: float, ID: int | str, along_direction: bool = True) -> None:
    """
    Perform a breadth-first search (BFS) on a directed river network graph to propagate risk information.

    Parameters
    ----------
    graph : nx.DiGraph
        The directed graph representing the river network.
    start_node : tuple
        The starting node for the BFS.
    col_name : str
        Name of the attribute in the graph nodes where the risk information is stored.
    distance_cutoff : float
        Maximum distance for risk propagation.
    id : int | str
        Identifier for the point location.
    along_direction : bool, optional
        Indicates whether to propagate risk along the direction of the graph. Default is True.
    """
    curr_nodes = [start_node]

    while len(curr_nodes) > 0:
        new_curr = []
        for node in curr_nodes:
            # Check children
            if along_direction:
                children = graph.successors(node)
            else:
                children = graph.predecessors(node)

            for child in children:
                # Check if already added
                dataBefore = graph.nodes(data=colName)[child]
                if dataBefore is not None and ID in dataBefore:
                    continue

                # Check distance
                if along_direction:
                    total_dist = graph.nodes(data=colName)[
                        node][ID] + graph[node][child]["mm_len"]
                else:
                    total_dist = graph.nodes(data=colName)[
                        node][ID] - graph[child][node]["mm_len"]

                if total_dist >= distance_cutoff or total_dist <= -distance_cutoff:
                    continue

                # Add data
                if dataBefore is not None and isinstance(dataBefore, dict):
                    dataBefore[ID] = total_dist
                    nx.set_node_attributes(
                        graph, {child: dataBefore}, colName)

                # If doesn't exist, add new dict
                else:
                    nx.set_node_attributes(
                        graph, {child: {ID: total_dist}}, colName)

                # Add to new_curr
                new_curr.append(child)
        curr_nodes = new_curr
    return


def undirected_graph_bfs(graph: nx.Graph, current_nodes: list[tuple], colName: str, distance_cutoff: float, ID: int | str, traversed: set[tuple],):
    """
    Perform a breadth-first search (BFS) on an undirected river network graph to propagate risk information.

    Parameters
    ----------
    graph : nx.Graph
        The undirected graph representing the river network.
    current_nodes : list[tuple]
        List of current nodes to process.
    traversed : set[tuple]
        Set of nodes that have already been processed.
    col_name : str
        Name of the attribute in the graph nodes where the risk information is stored.
    distance_cutoff : float
        Maximum distance for risk propagation.
    id : int | str
        Identifier for the point location.

    Returns
    -------
    list[tuple]
        List of nodes processed in the current BFS iteration.
    """
    while len(current_nodes) > 0:
        new_current_nodes = []
        for node in current_nodes:
            children = [c for c in graph.neighbors(
                node) if c not in traversed]
            for child in children:
                dataBefore = graph.nodes(data=colName)[child]
                if dataBefore is not None and ID in dataBefore:
                    continue

                # Check distance
                total_dist = graph.nodes(data=colName)[
                    node][ID] + graph[node][child]["mm_len"]

                if total_dist >= distance_cutoff:
                    continue

                # Add data
                if dataBefore is not None and isinstance(dataBefore, dict):
                    dataBefore[ID] = total_dist
                    nx.set_node_attributes(
                        graph, {child: dataBefore}, colName)

                # If doesn't exist, add new dict
                else:
                    nx.set_node_attributes(
                        graph, {child: {ID: total_dist}}, colName)

                # Add to new_curr
                traversed.add(child)
                new_current_nodes.append(child)
        current_nodes = new_current_nodes
    return


def add_risk(
    riskmap: RiskMap,
    info_col: str,
    risk_name: str,
    risk_method: str,
    distance_scaling: Callable,
    aggregation_method: str = 'sum',
    ids_to_ignore: list = [],
    inplace=True,
) -> dict[dict[float]] | None:
    """add_risk adds the risk to a riskmap containing a river graph

    Parameters
    ----------
    riskmap : RiskMap
        The riskmap to add the risk to
    info_col : str
        The name of the info column associated with the risk
    risk_name : str
        The name of the risk to be stored in the output
    risk_method : Callable
        The function to calculate risk given an ID
    distance_scaling : Callable
        The distance scaling function to apply to the risk
    aggregation_method : str, optional
        The aggregation method of different risks, by default 'sum'
    ids_to_ignore : list, optional
        The IDs to ignore while calculating the risk, by default []
    inplace : bool, optional
        Whether to return a dict of risks or just do in inplace on the graph, by default True

    Returns
    -------
    dict[dict[float]] | None
        Returns a dict on the nodes of the risk values as floats to be assigned to the graph or None

    Raises
    ------
    NotImplementedError
        If the risk aggregation method is not implemented
    ValueError
        If the risk info column isn't present on the graph
    """
    network = riskmap.get_river_network(copy=False)
    rivers = riskmap.get_rivers(copy=False)

    found = False
    if not inplace:
        retdict: dict[dict[float]] = defaultdict(dict)

    for node in network.nodes:
        rsk = []
        weights = []
        if info_col in network.nodes[node]:
            found = True
            for id, dist in network.nodes[node][info_col].items():
                if id[0] in ids_to_ignore:
                    continue
                riverInfo = rivers.loc[id[1]].to_dict()
                idrisk = risk_method(id[0], riverInfo)
                if idrisk:
                    rsk.append(idrisk*distance_scaling(dist))
                    weights.append(riverInfo["Upstream Accumulated Length"])

        if aggregation_method == 'sum':
            agg_risk = np.sum(rsk)
        elif aggregation_method == 'avg':
            if not len(rsk):
                agg_risk = 0
            else:
                agg_risk = np.mean(rsk)
        elif aggregation_method == 'w_avg':
            riverIDs = list(network.nodes[node]["riverID"])
            accum_len_node = rivers.loc[riverIDs,
                                        "Upstream Accumulated Length"].max()
            agg_risk = sum([w*r for w, r in zip(weights, rsk)])/accum_len_node
        else:
            raise NotImplementedError(
                "aggregation_method not in ['sum','avg','w_avg']")

        if inplace:
            network.nodes[node][risk_name] = agg_risk
        else:
            retdict[node][risk_name] = agg_risk

    if not found:
        raise ValueError(f"Column {info_col} not found in network nodes")

    if not inplace:
        return retdict
    return


def integrate_field(graph: nx.DiGraph, field: str, outputName: str = None) -> None:
    """integrate_field integrates a given field on the nodes over the edges of the graph
    and puts the vlaue on the [field+"_int"] field on the edges

    Parameters
    ----------
    graph : nx.DiGraph
        Graph with the field values present on every node
    field : str
        The key name of the field to integrate
    """
    if outputName is None:
        outputName = field+"_int"
    for node in graph.nodes:
        for child in graph.successors(node):
            graph[node][child][outputName] = (graph.nodes[child].get(
                field, 0) + graph.nodes[node].get(field, 0))/2 * graph[node][child]["mm_len"]
    return


def integrate_field_product(graph: nx.DiGraph, field1: str, field2: str, outputName: str = None) -> None:
    """integrate_field_product takes 2 fields and calculates the integral of their product
    assuming a linear appoximation of the 2 fields between nodes and puts them on the
    [ field1 + "_" + field2 + "_" + "int"] field on the edges.

    Parameters
    ----------
    graph : nx.DiGraph
        Graph with the field values present on every node
    field1 : str
        The key name of field 1
    field2 : str
        The key name of field 2
    """
    if outputName is None:
        outputName = field1 + "_" + field2 + "_int"
    for node in graph.nodes:
        for child in graph.successors(node):
            f1, f2 = graph.nodes[child].get(
                field1, 0), graph.nodes[node].get(field1, 0)
            g1, g2 = graph.nodes[child].get(
                field2, 0), graph.nodes[node].get(field2, 0)
            graph[node][child][outputName] = (
                f1*g1/3 + f1*g2/6 + f2*g1/6 + f2*g2/3)*graph[node][child]["mm_len"]
    return
