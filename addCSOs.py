# %% [markdown]
# # Theme 2C

# %% [markdown]
# The idea behind Theme 2C is to use open source sewer outfall data(and other sources hopefully) to estimate a risk index on the river network to optimize placement of new sensors

# %%
import numpy as np
import geopandas as gpd
import shapely
import momepy
import networkx as nx
import pandas as pd
from collections import defaultdict

# %% [markdown]
# ## Calculations

# %% [markdown]
# ### Helper Functions

# %% [markdown]
# #### Splitting rivers into segments

# %%


def split_rivers(rivers: gpd.GeoDataFrame, division_distance: float) -> gpd.GeoDataFrame:
    def split_linestring(l: shapely.LineString) -> list[shapely.LineString]:
        points: np.ndarray[shapely.Point] = shapely.line_interpolate_point(l,
                                                                           np.arange(
                                                                               0, l.length, division_distance),
                                                                           # np.linspace(0,1,n_divisions),
                                                                           # normalized=True,
                                                                           )

        endpoint = shapely.Point(l.coords[-1])
        if endpoint not in points:
            points = np.append(points, endpoint)

        return [shapely.LineString([p1, p2]) for p1, p2 in zip(points[:-1], points[1:])]

    riverExpl = rivers.copy()
    riverExpl["geometry2"] = riverExpl.geometry.apply(split_linestring)

    riverExpl = riverExpl[[
        "riverID", "geometry2"]].explode("geometry2")
    riverExpl.rename(
        columns={"geometry2": "geometry"}, inplace=True)

    riverExpl.geometry = riverExpl.geometry.astype("geometry")
    riverExpl = gpd.GeoDataFrame(riverExpl)
    riverExpl.geometry = riverExpl.geometry.set_crs(rivers.crs)
    riverExpl.reset_index(inplace=True)
    return riverExpl

# %% [markdown]
# #### Adding the closest edge data to the CSOs

# %%


def return_coords(x, df, cso_location="geometry"):
    if pd.isna(x):
        return None
    else:
        return tuple(df.loc[x, cso_location].coords)


def add_closest_edge(csoDf: gpd.GeoDataFrame, riversDf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:

    # Find nearest river
    nearest = gpd.sjoin_nearest(
        csoDf, riversDf[["geometry", "riverID"]], how="left")

    # Remove duplicates
    nearest = nearest[~nearest.index.duplicated(keep="first")]

    # Add the edge info
    nearest["closestEdge"] = nearest["index_right"].apply(
        lambda x: return_coords(x, riversDf, "geometry"))

    # Remove unused columns
    nearest.drop(columns=["index_right", "riverID"], inplace=True)

    return nearest

# %% [markdown]
# #### Add the closest point as the outfall location

# %%


def find_shortest_line(p: shapely.Point, idx: float | None, riversDf: gpd.GeoDataFrame) -> shapely.LineString | None:
    if not idx > 0:
        return None
    return shapely.shortest_line(p, riversDf.iloc[int(idx)].geometry)


def get_line_endpoint(l: shapely.LineString | None) -> shapely.Point | None:
    if l is None:
        return None
    return shapely.Point(l.coords[-1])


def return_linestring_coords(x: shapely.LineString | None):
    if pd.isna(x):
        return None
    else:
        return tuple(x.coords)


def cso_add_outfall_location_and_edge(csoDf: gpd.GeoDataFrame, riversDf: gpd.GeoDataFrame, max_distance: float) -> gpd.GeoDataFrame:
    riversDf["closestEdge"] = riversDf.geometry.copy()
    nearest_point_info = gpd.sjoin_nearest(
        csoDf,
        riversDf[["geometry", "riverID", "closestEdge"]],
        max_distance=max_distance,
        how="left"
    )
    nearest_point_info = nearest_point_info[~nearest_point_info.index.duplicated(
        keep='first')]

    connectingLines = nearest_point_info.apply(lambda x: find_shortest_line(
        x["geometry"], x["index_right"], riversDf), axis=1).set_crs(nearest_point_info.crs)
    nearest_point_info.drop(columns=["index_right"], inplace=True)

    nearest_point_info["outfallLocation"] = connectingLines.apply(
        get_line_endpoint).set_crs(nearest_point_info.crs)

    # nearest_point_info["outfallLocation"] = connectingLines.apply(
    #     lambda x: shapely.line_interpolate_point(x, 1, normalized=True)).set_crs(nearest_point_info.crs)

    nearest_point_info["closestEdge"] = nearest_point_info["closestEdge"].apply(
        return_linestring_coords)

    return nearest_point_info

# %% [markdown]
# #### BFS on graph

# %%


def directed_graph_bfs(graph: nx.DiGraph, start_node: tuple, csoInfoName: str, distance_cutoff: float, csoID: int | str, downstream: bool = True):

    curr_nodes = [start_node]

    while len(curr_nodes) > 0:
        new_curr = []
        for node in curr_nodes:
            # Check children
            if downstream:
                children = graph.successors(node)
            else:
                children = graph.predecessors(node)

            for child in children:
                # Check if already added
                dataBefore = graph.nodes(data=csoInfoName)[child]
                if dataBefore is not None and csoID in dataBefore:
                    continue

                # Check distance
                if downstream:
                    total_dist = graph.nodes(data=csoInfoName)[
                        node][csoID] + graph[node][child]["mm_len"]
                else:
                    total_dist = graph.nodes(data=csoInfoName)[
                        node][csoID] - graph[child][node]["mm_len"]

                if total_dist >= distance_cutoff or total_dist <= -distance_cutoff:
                    continue

                # Add data
                if dataBefore is not None and isinstance(dataBefore, dict):
                    dataBefore[csoID] = total_dist
                    nx.set_node_attributes(
                        graph, {child: dataBefore}, csoInfoName)

                # If doesn't exist, add new dict
                else:
                    nx.set_node_attributes(
                        graph, {child: {csoID: total_dist}}, csoInfoName)

                # Add to new_curr
                new_curr.append(child)
        curr_nodes = new_curr
    return


def undirected_graph_bfs_step(graph: nx.Graph, current_nodes: list[tuple], traversed: set[tuple], csoInfoName: str, distance_cutoff: float, csoID: int | str) -> list[tuple]:
    new_current_nodes = []
    for node in current_nodes:
        children = [c for c in graph.neighbors(node) if c not in traversed]
        for child in children:
            dataBefore = graph.nodes(data=csoInfoName)[child]
            if dataBefore is not None and csoID in dataBefore:
                continue

            # Check distance
            total_dist = graph.nodes(data=csoInfoName)[
                node][csoID] + graph[node][child]["mm_len"]

            if total_dist >= distance_cutoff:
                continue

            # Add data
            if dataBefore is not None and isinstance(dataBefore, dict):
                dataBefore[csoID] = total_dist
                nx.set_node_attributes(
                    graph, {child: dataBefore}, csoInfoName)

            # If doesn't exist, add new dict
            else:
                nx.set_node_attributes(
                    graph, {child: {csoID: total_dist}}, csoInfoName)

            # Add to new_curr
            traversed.add(child)
            new_current_nodes.append(child)
    return new_current_nodes

# %% [markdown]
# #### Doing BFS on graph for each cso

# %%


def add_cso_to_graph(graph: nx.DiGraph, p: shapely.Point, ID: int | str, edge: tuple, distance_cutoff: float, directed: bool, include_upstream: bool = True) -> None:
    if edge is None:
        return

    csoInfoName = "csoInfo"
    if directed:
        # Loop over successors, add data
        next_node = edge[1]
        # Find distance between CSO and first point
        next_dist = p.distance(shapely.Point(next_node))

        # Check if distance lesser than threshold
        if next_dist < distance_cutoff:
            # Get prev data
            dataBefore = graph.nodes(data=csoInfoName)[next_node]
            # If exists, add data
            if dataBefore is not None and isinstance(dataBefore, dict):
                dataBefore[ID] = next_dist
            # If doesn't exist, add new dict
            else:
                nx.set_node_attributes(
                    graph, {next_node: {ID: next_dist}}, csoInfoName)

            # Loop over children nodes
            directed_graph_bfs(graph, next_node, csoInfoName,
                               distance_cutoff, ID, True)

        if not include_upstream:
            return

        # Loop over predecessors
        prev_node = edge[0]
        prev_dist = -p.distance(shapely.Point(prev_node))
        if prev_dist > -distance_cutoff:
            dataBefore = graph.nodes(data=csoInfoName)[prev_node]
            if dataBefore is not None and isinstance(dataBefore, dict):
                dataBefore[ID] = prev_dist
            else:
                nx.set_node_attributes(
                    graph, {prev_node: {ID: prev_dist}}, csoInfoName)
            directed_graph_bfs(graph, prev_node, csoInfoName,
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
            dataBefore = graph.nodes(data=csoInfoName)[next_node]
            # If exists, add data
            if dataBefore is not None and isinstance(dataBefore, dict):
                dataBefore[ID] = next_dist
            # If doesn't exist, add new dict
            else:
                nx.set_node_attributes(
                    graph, {next_node: {ID: next_dist}}, csoInfoName)
            curr_nodes.append(next_node)
            traversed.add(next_node)
        prev_node = edge[0]
        prev_dist = p.distance(shapely.Point(prev_node))
        if prev_dist < distance_cutoff:
            dataBefore = graph.nodes(data=csoInfoName)[prev_node]
            if dataBefore is not None and isinstance(dataBefore, dict):
                dataBefore[ID] = prev_dist
            else:
                nx.set_node_attributes(
                    graph, {prev_node: {ID: prev_dist}}, csoInfoName)
            curr_nodes.append(prev_node)
            traversed.add(prev_node)
        while len(curr_nodes) > 0:
            curr_nodes = undirected_graph_bfs_step(
                graph, curr_nodes, traversed, csoInfoName, distance_cutoff, ID)
        return

# %% [markdown]
# #### Converting rivers to a graph and adding node attributes

# %%


def river_to_directed_graph(rivers: gpd.GeoDataFrame, directed: bool) -> nx.DiGraph | nx.Graph:
    riverGraph = momepy.gdf_to_nx(
        rivers,
        approach="primal",
        directed=directed,  # To indicate flow direction
        multigraph=False,
    )

    # Create a dictionary to store edge IDs for each node
    node_edge_ids = defaultdict(set)

    # Populate the dictionary by iterating over edges
    for u, v, data in riverGraph.edges(data=True):
        edge_id = data.get('riverID')
        node_edge_ids[u].add(edge_id)
        node_edge_ids[v].add(edge_id)
    nx.set_node_attributes(riverGraph, node_edge_ids, name="riverID")
    return riverGraph

# %% [markdown]
# #### Convert river graph points data to gdf

# %%


def convert_to_list(x: dict):
    if isinstance(x, dict):
        return list(zip(x.keys(), x.values()))
    elif isinstance(x, set):
        return list(x)
    elif isinstance(x, list):
        return x
    elif pd.isna(x):
        return []
    else:
        return x


def graph_to_gdf(graph: nx.Graph) -> gpd.GeoDataFrame:
    points_data = momepy.nx_to_gdf(graph, points=True, lines=False)
    points_data.drop(columns=["x", "y", "nodeID"], inplace=True)

    points_data["csoInfo"] = points_data["csoInfo"].apply(convert_to_list)
    points_data["riverID"] = points_data["riverID"].apply(convert_to_list)
    return points_data

# %% [markdown]
# ### Main Function

# %%


def calculate_risk_factors_cso(
    cso_df: gpd.GeoDataFrame,
    river_df: gpd.GeoDataFrame,
    cso_df_id_col: str = "OBJECTID",
    river_df_id_col: str = "OBJECTID",
    river_resolution: float = 125,
    max_outfall_dist_from_river: float = 700,
    calc_outfall_location: bool = True,
    max_risk_distance: float = 3000,
    river_directed=True,
    include_upstream=False,
) -> gpd.GeoDataFrame | tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Calculate risk factors for Combined Sewer Overflows (CSOs) based on their proximity to rivers.
    Parameters:
    cso_df (gpd.GeoDataFrame): GeoDataFrame containing CSO data.
    river_df (gpd.GeoDataFrame): GeoDataFrame containing river data.
    cso_df_id_col (str, optional): Column name for CSO IDs in cso_df. Defaults to "OBJECTID".
    river_df_id_col (str, optional): Column name for river IDs in river_df. Defaults to "OBJECTID".
    river_resolution (float, optional): Resolution for splitting river segments. Defaults to 125.
    max_outfall_dist_from_river (float, optional): Maximum distance from river to consider for outfall location. Defaults to 700.
    calc_outfall_location (bool, optional): Whether to calculate outfall location. Defaults to True.
    max_risk_distance (float, optional): Maximum distance to consider for risk calculation. Defaults to 3000.
    river_directed (bool, optional): Whether the river is directed. Defaults to True.
    include_upstream (bool, optional): Whether to include upstream in risk calculation. Defaults to False.
    Returns:
    gpd.GeoDataFrame | tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]: Returns a GeoDataFrame with river data. If calc_outfall_location is True, also returns a GeoDataFrame with CSO data. 
    """

    if "riverID" in river_df.columns and river_df_id_col != "riverID":
        raise ValueError("please change riverID column name to something else")
    if "csoID" in cso_df.columns and cso_df_id_col != "csoID":
        raise ValueError("please change csoID column name to something else")

    cso_renamed = cso_df.rename(columns={cso_df_id_col: "csoID"})
    river_renamed = river_df.rename(columns={river_df_id_col: "riverID"})

    riverSplit = split_rivers(
        river_renamed, river_resolution)

    if calc_outfall_location:
        cso_with_edge = cso_add_outfall_location_and_edge(
            cso_renamed, riverSplit, max_outfall_dist_from_river)
        outfall_col = "outfallLocation"
    else:
        cso_with_edge = add_closest_edge(cso_renamed, riverSplit)
        outfall_col = "geometry"

    riverGraph = river_to_directed_graph(riverSplit, directed=river_directed)

    cso_with_edge.apply(lambda x: add_cso_to_graph(
        riverGraph, x[outfall_col], x["csoID"], x["closestEdge"], distance_cutoff=max_risk_distance, directed=river_directed, include_upstream=include_upstream), axis=1)

    river_data = graph_to_gdf(riverGraph)

    if calc_outfall_location:
        return river_data, cso_with_edge

    return river_data
