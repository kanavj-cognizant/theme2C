import geopandas as gpd
import networkx as nx
import shapely
import numpy as np
from collections.abc import Callable
import momepy
import pandas as pd
import utils.rivers
import utils.graphBFS

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

    All work is done in EPSG 3857

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

    def __init__(self, riversdf: gpd.GeoDataFrame, id_col: str, resolution: float, directed: bool = True):
        riversdf = riversdf.to_crs(epsg=3857)
        self.__rivers: gpd.GeoDataFrame = utils.rivers.split_rivers(
            riversdf, resolution, id_col)
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

    # Construct rivers graph
    def construct_river_graph(self) -> None:
        """
        Construct a river network graph from the processed river data.

        The graph is constructed using the `momepy` library, creating a primal graph where each node
        represents a river segment intersection and edges represent river segments.
        """
        self.__river_network = utils.rivers.river_to_graph(
            self.__rivers, self.__directed)

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
            self.__risk_map = self.__graph_to_gdf()

    def __graph_to_gdf(self) -> gpd.GeoDataFrame:
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

    # Add points to map
    def add_point_info_to_map(self,
                              ids: gpd.GeoSeries,
                              geometry: gpd.GeoSeries,
                              col_name_graph: str,
                              max_risk_distance: float,
                              max_dist_from_river: float,
                              include_upstream: bool = False,
                              return_locations: bool = True) -> gpd.GeoSeries | None:
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
        if geometry.crs != self.__rivers.crs:
            raise ValueError(
                "Points are not in EPSG:3857 CRS which is used by the class")

        closestPoint, closestEdge = utils.rivers.add_location_on_river_and_closest_edge(self.__rivers,
                                                                                        geometry, max_dist_from_river)
        df = pd.DataFrame(
            {"ids": ids, "closestPoint": closestPoint, "closestEdge": closestEdge})
        df.apply(
            lambda x:
            utils.graphBFS.add_to_graph(
                self.__river_network,
                col_name_graph,
                x["ids"],
                x["closestPoint"],
                x["closestEdge"],
                distance_cutoff=max_risk_distance,
                directed=self.__directed,
                both_ways=include_upstream
            ), axis=1
        )
        if return_locations:
            return closestPoint

    # Add risk to map
    def add_risk(self, col_name_risk: str, risk_name: str, risk_method: Callable, distance_scaling: Callable, aggregation_method: Callable = sum) -> None:
        """add_risk adds risk about a certain column in the riskMap

        Parameters
        ----------
        col_name_risk : str
            The name of the column in the risk map containing tuples of (ID,dist) from river points
        risk_name : str
            The column name of the risk to be assigned in the risk map
        risk_method : Callable
            A method from riskID -> float that gives a risk value for each point
        distance_scaling : Callable
            A method from dist -> float which gives the distance scaling of that risk
        aggregation_method : Callable, optional
            The method iterable -> float to aggregate different risks together, by default sum

        Raises
        ------
        Exception
            If the col_name_risk doesn't exist in the riskmap, it asks the user to generate ie
        """
        if col_name_risk not in self.__risk_map.columns:
            raise Exception(
                "No " + col_name_risk + " in the risk map. Has the point info been added to the map?")

        Risk = self.__risk_map.apply(lambda x: aggregation_method(
            [risk_method(ID)*distance_scaling(dist) for ID, dist in x[col_name_risk] if risk_method(ID) is not None]), axis=1)

        self.__risk_map[risk_name] = Risk
        return
