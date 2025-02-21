import geopandas as gpd
import networkx as nx
import shapely
import numpy as np
import momepy
import pandas as pd
from collections import defaultdict

"""
RiskMap Class
=============

A class for creating risk maps based on river networks and point locations.

This class provides methods to process river data, construct river networks, and
analyze risk propagation from point locations along the river network.

"""


class RiskMap():
    """
    A class for creating risk maps based on river networks and point locations.

    This class provides methods to process river data, construct river networks, and
    analyze risk propagation from point locations along the river network.

    Parameters
    ----------
    riversdf : gpd.GeoDataFrame
        GeoDataFrame containing river data as LineStrings.
    directed : bool, optional
        Indicates whether the river network should be treated as directed. Default is True.

    Attributes
    ----------
    __rivers : gpd.GeoDataFrame
        Processed river data.
    __directed : bool
        Indicates if the river network is directed.
    __risk_map : gpd.GeoDataFrame | None
        GeoDataFrame representing the risk map.
    __river_network : nx.Graph | None
        NetworkX graph representing the river network.
    """

    def __init__(self, riversdf: gpd.GeoDataFrame, directed: bool = True):

        self.__rivers: gpd.GeoDataFrame = riversdf.copy()
        self.__rivers = self.__rivers.to_crs(epsg=3857)
        self.__directed = directed
        self.__risk_map: gpd.GeoDataFrame | None = None
        self.__river_network: nx.Graph | None = None

    def __repr__(self) -> gpd.GeoDataFrame | None:
        """
        Return the risk map as a GeoDataFrame if it has been constructed.

        Returns
        -------
        gpd.GeoDataFrame | None
            The risk map GeoDataFrame if constructed, otherwise None.
        """
        if self.__risk_map is None:
            return None
        else:
            return self.__risk_map.copy()

    def get_rivers(self) -> gpd.GeoDataFrame:
        """
        Return the processed river data.

        Returns
        -------
        gpd.GeoDataFrame
            The processed river data as a GeoDataFrame.
        """
        return self.__rivers.copy()

    def get_river_network(self) -> nx.Graph | None:
        """
        Return the river network as a NetworkX graph.

        Returns
        -------
        nx.Graph | None
            The river network graph if constructed, otherwise None.
        """
        if self.__river_network is None:
            return None
        return self.__river_network.copy(as_view=True)

    def get_risk_map(self) -> gpd.GeoDataFrame | None:
        """
        Return the risk map as a GeoDataFrame.

        Returns
        -------
        gpd.GeoDataFrame | None
            The risk map GeoDataFrame if constructed, otherwise None.
        """
        if self.__risk_map is None:
            return None
        return self.__risk_map.copy()

    # Split the rivers
    def split_rivers(self, resolution: int, river_df_id_col: str = "UniqueID",) -> None:
        """
        Split river linestrings into smaller segments based on a specified resolution.

        Parameters
        ----------
        resolution : int
            The length at which to split the river linestrings.
        river_df_id_col : str, optional
            The column name in the river GeoDataFrame used as the river ID. Default is "UniqueID".
        """
        # Check if column in rivers
        if not river_df_id_col in self.__rivers.columns:
            raise ValueError(
                f"River id column [{river_df_id_col}] not in river dataframe columns")
        self.__rivers.rename(
            columns={river_df_id_col: "riverID"}, inplace=True)

        # Check if anything given is not a LineString
        if self.__rivers.geometry.apply(lambda x: type(x) != shapely.LineString).any():
            raise Exception(
                "riversdf has other geometries. This function only works on linestrings")

        def split_linestring(l: shapely.LineString) -> list[shapely.LineString]:
            points: np.ndarray[shapely.Point] = shapely.line_interpolate_point(l,
                                                                               np.arange(
                                                                                   0, l.length, resolution),
                                                                               # np.linspace(0,1,n_divisions),
                                                                               # normalized=True,
                                                                               )

            endpoint = shapely.Point(l.coords[-1])
            if endpoint not in points:
                points = np.append(points, endpoint)

            return [shapely.LineString([p1, p2]) for p1, p2 in zip(points[:-1], points[1:])]

        riverExpl = self.__rivers.copy()
        riverExpl["geometry2"] = riverExpl.geometry.apply(split_linestring)

        riverExpl = riverExpl[[
            "riverID", "geometry2"]].explode("geometry2")
        riverExpl.rename(
            columns={"geometry2": "geometry"}, inplace=True)

        riverExpl.geometry = riverExpl.geometry.astype("geometry")
        riverExpl = gpd.GeoDataFrame(riverExpl)
        riverExpl.geometry = riverExpl.geometry.set_crs(self.__rivers.crs)
        riverExpl.reset_index(inplace=True)
        self.__rivers = riverExpl.drop("index", axis=1)
        return

    # Construct rivers graph
    def construct_river_graph(self) -> None:
        """
        Construct a river network graph from the processed river data.

        The graph is constructed using the `momepy` library, creating a primal graph where each node
        represents a river segment intersection and edges represent river segments.
        """
        self.__river_network = self._river_to_graph(
            self.__rivers, self.__directed)

    def _river_to_graph(self, rivers: gpd.GeoDataFrame, directed: bool) -> nx.DiGraph | nx.Graph:
        """
        Convert a GeoDataFrame of rivers into a NetworkX graph.

        Parameters
        ----------
        rivers : gpd.GeoDataFrame
            GeoDataFrame containing river data.
        directed : bool
            Indicates whether the resulting graph should be directed.

        Returns
        -------
        nx.Graph
            The NetworkX graph representing the river network.
        """
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

    def construct_risk_map(self) -> None:
        """
        Convert the river network graph back into a GeoDataFrame.

        Returns
        -------
        gpd.GeoDataFrame
            GeoDataFrame representing the river network graph.
        """
        if self.__river_network is None:
            raise ValueError("No river network to construct risk map from")
        else:
            self.__risk_map = self._graph_to_gdf()

    def _graph_to_gdf(self) -> gpd.GeoDataFrame:
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
        points_data = momepy.nx_to_gdf(
            self.__river_network, points=True, lines=False)
        points_data.drop(columns=["x", "y", "nodeID"], inplace=True)
        for col in points_data.columns:
            points_data[col] = points_data[col].apply(convert_to_list)
        return points_data

    # Add points to graph
    def add_point_info_to_graph(self,
                                ids: gpd.GeoSeries,
                                geometry: gpd.GeoSeries,
                                col_name_graph: str,
                                max_risk_distance: float,
                                max_dist_from_river: float,
                                include_upstream: bool = False,
                                return_locations: bool = True) -> gpd.GeoSeries | None:
        closestPoint, closestEdge = self._add_location_on_river_and_closest_edge(
            geometry, max_dist_from_river)
        """
        Add information about point locations to the river network graph.

        This method finds the closest points on the river network for given geometries and propagates
        risk information from these points along the river network.

        Parameters
        ----------
        ids : gpd.GeoSeries
            IDs of the points to be added.
        geometry : gpd.GeoSeries
            Geometries of the points to be added.
        col_name_graph : str
            Name of the attribute in the graph nodes where the point information will be stored.
        max_risk_distance : float
            Maximum distance for risk propagation along the river network.
        max_dist_from_river : float
            Maximum distance for finding the closest river segment to the points.
        include_upstream : bool, optional
            Indicates whether to include upstream propagation. Default is False.
        return_locations : bool, optional
            Indicates whether to return the closest points on the river network. Default is True.

        Returns
        -------
        gpd.GeoSeries | None
            GeoSeries of the closest points on the river network if return_locations is True, otherwise None.
        """
        df = pd.DataFrame(
            {"ids": ids, "closestPoint": closestPoint, "closestEdge": closestEdge})
        df.apply(
            lambda x:
            self._add_to_graph(
                self.__river_network,
                col_name_graph,
                x["ids"],
                x["closestPoint"],
                x["closestEdge"],
                distance_cutoff=max_risk_distance,
                directed=self.__directed,
                include_upstream=include_upstream
            ), axis=1
        )
        if return_locations:
            return closestPoint

    # Add point locations
    def _add_location_on_river_and_closest_edge(self, geometry: gpd.GeoSeries, max_dist_from_river: float) -> tuple[gpd.GeoSeries, gpd.GeoSeries]:
        """
        Find the closest points on the river network for given geometries.

        Parameters
        ----------
        geometry : gpd.GeoSeries
            Geometries of the points for which to find the closest river points.
        max_dist_from_river : float
            Maximum distance for finding the closest river segment.

        Returns
        -------
        tuple[gpd.GeoSeries, gpd.GeoSeries]
            A tuple containing GeoSeries of the closest points and edges on the river network.
        """
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

        gdf = gpd.GeoDataFrame(geometry=geometry, crs=geometry.crs)
        self.__rivers["closestEdge"] = self.__rivers.geometry.copy()
        nearest_point_info = gpd.sjoin_nearest(
            gdf,
            self.__rivers[["geometry", "riverID", "closestEdge"]],
            max_distance=max_dist_from_river,
            how="left"
        )
        self.__rivers.drop("closestEdge", axis=1)
        nearest_point_info = nearest_point_info[~nearest_point_info.index.duplicated(
            keep='first')]
        connectingLines = nearest_point_info.apply(lambda x: find_shortest_line(
            x["geometry"], x["index_right"], self.__rivers), axis=1).set_crs(nearest_point_info.crs)
        nearest_point_info.drop(columns=["index_right"], inplace=True)
        nearest_point_info["closestPoint"] = connectingLines.apply(
            get_line_endpoint).set_crs(nearest_point_info.crs)
        nearest_point_info["closestEdge"] = nearest_point_info["closestEdge"].apply(
            return_linestring_coords)
        return nearest_point_info["closestPoint"], nearest_point_info["closestEdge"]

    def _add_to_graph(self, graph: nx.DiGraph, colName: str, ID: int | str, p: shapely.Point, edge: tuple, distance_cutoff: float, directed: bool, include_upstream: bool = True) -> None:
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
                self._directed_graph_bfs(graph, next_node, colName,
                                         distance_cutoff, ID, True)

            if not include_upstream:
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
                self._directed_graph_bfs(graph, prev_node, colName,
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
            curr_nodes = self._undirected_graph_bfs(
                graph, curr_nodes, traversed, colName, distance_cutoff, ID)
            return

    def _directed_graph_bfs(self, graph: nx.DiGraph, start_node: tuple, colName: str, distance_cutoff: float, ID: int | str, downstream: bool = True) -> None:
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
                if downstream:
                    children = graph.successors(node)
                else:
                    children = graph.predecessors(node)

                for child in children:
                    # Check if already added
                    dataBefore = graph.nodes(data=colName)[child]
                    if dataBefore is not None and ID in dataBefore:
                        continue

                    # Check distance
                    if downstream:
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

    def _undirected_graph_bfs(self, graph: nx.Graph, current_nodes: list[tuple], traversed: set[tuple], colName: str, distance_cutoff: float, ID: int | str):
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
