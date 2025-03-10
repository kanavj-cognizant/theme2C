import networkx as nx
import shapely
from collections.abc import Callable
from collections import defaultdict


def add_to_graph(
    graph: nx.DiGraph,
    colName: str,
    ID: int | str,
    p: shapely.Point,
    edge: tuple,
    distance_cutoff: float,
    directed: bool,
    both_ways: bool = True,
) -> None:
    if edge is None:
        return

    if directed:
        # Loop over successors, add data
        next_node = edge[1]
        # Find distance between geometry and first point
        next_dist = p.distance(shapely.Point(next_node))

        # Check if distance lesser than threshold
        if next_dist < distance_cutoff:
            # Get prev data
            dataBefore = graph.nodes(data=colName)[next_node]
            # If exists, add data
            if dataBefore is not None and isinstance(dataBefore, dict):
                dataBefore[ID] = next_dist
            # If doesn't exist, add new dict
            else:
                nx.set_node_attributes(
                    graph, {next_node: {ID: next_dist}}, colName)

            # Loop over children nodes
            directed_graph_bfs(graph, next_node, colName,
                               distance_cutoff, ID, True)

        if not both_ways:
            return

        # Loop over predecessors
        prev_node = edge[0]
        prev_dist = -p.distance(shapely.Point(prev_node))
        if prev_dist > -distance_cutoff:
            dataBefore = graph.nodes(data=colName)[prev_node]
            if dataBefore is not None and isinstance(dataBefore, dict):
                dataBefore[ID] = prev_dist
            else:
                nx.set_node_attributes(
                    graph, {prev_node: {ID: prev_dist}}, colName)
            directed_graph_bfs(graph, prev_node, colName,
                               distance_cutoff, ID, False)
        return

    else:
        traversed = set()

        curr_nodes = []
        next_node = edge[1]
        next_dist = p.distance(shapely.Point(next_node))
        # Check if distance lesser than threshold
        if next_dist < distance_cutoff:
            # Get prev data
            dataBefore = graph.nodes(data=colName)[next_node]
            # If exists, add data
            if dataBefore is not None and isinstance(dataBefore, dict):
                dataBefore[ID] = next_dist
            # If doesn't exist, add new dict
            else:
                nx.set_node_attributes(
                    graph, {next_node: {ID: next_dist}}, colName)
            curr_nodes.append(next_node)
            traversed.add(next_node)
        prev_node = edge[0]
        prev_dist = p.distance(shapely.Point(prev_node))
        if prev_dist < distance_cutoff:
            dataBefore = graph.nodes(data=colName)[prev_node]
            if dataBefore is not None and isinstance(dataBefore, dict):
                dataBefore[ID] = prev_dist
            else:
                nx.set_node_attributes(
                    graph, {prev_node: {ID: prev_dist}}, colName)
            curr_nodes.append(prev_node)
            traversed.add(prev_node)
        curr_nodes = directed_graph_bfs(
            graph, curr_nodes, traversed, colName, distance_cutoff, ID)
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
    downstream : bool, optional
        Indicates whether to propagate risk downstream. Default is True.
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


def undirected_graph_bfs(graph: nx.Graph, current_nodes: list[tuple], traversed: set[tuple], colName: str, distance_cutoff: float, ID: int | str):
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
    network: nx.Graph,
    info_col: str,
    risk_name: str,
    risk_method: Callable,
    distance_scaling: Callable,
    aggregation_method: Callable = sum,
    ids_to_ignore: list = [],
    inplace=True,
) -> dict[dict[float]] | None:

    found = False
    if not inplace:
        retdict: dict[dict[float]] = defaultdict(dict)

    for node in network.nodes:
        if info_col in network.nodes[node]:
            found = True
            rsk = []
            for id, dist in network.nodes[node][info_col].items():
                if id in ids_to_ignore:
                    continue
                idrisk = risk_method(id)
                if idrisk:
                    rsk.append(idrisk*distance_scaling(dist))
            if inplace:
                network.nodes[node][risk_name] = aggregation_method(rsk)
            else:
                retdict[node][risk_name] = aggregation_method(rsk)
        else:
            if inplace:
                network.nodes[node][risk_name] = 0
            else:
                retdict[node][risk_name] = 0
    if not found:
        raise ValueError(f"Column {info_col} not found in network nodes")

    if not inplace:
        return retdict
    return
