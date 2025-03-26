import geopandas as gpd
import networkx as nx
import shapely
import numpy as np
from collections.abc import Callable
import momepy
import pandas as pd
from copy import deepcopy

import utils.rivers
import utils.graph as graph


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

        # Check if column in rivers
        if not id_col in riversdf.columns:
            raise ValueError(
                f"River id column [{id_col}] not in river dataframe columns")
        riversdf = riversdf.to_crs(epsg=3857).rename(
            columns={id_col: "riverID"})
        self.__rivers: gpd.GeoDataFrame = riversdf

        self.__rivers_split: gpd.GeoDataFrame = utils.rivers.split_rivers(
            riversdf, resolution, id_col)

        self.__rivers.set_index("riverID", verify_integrity=True, inplace=True)

        self.__directed = directed
        self.__risk_map: gpd.GeoDataFrame | None = None
        self.__river_network: nx.Graph | None = None

    def __repr__(self) -> str | None:
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
            return self.__risk_map.__repr__()

    # Getter functions
    def get_rivers(self, copy: bool = True) -> gpd.GeoDataFrame:
        """get_rivers returns the rivers from the RiskMap

        Parameters
        ----------
        copy : bool, optional
            Whether to return a copy or the original GeoDataFrame, by default True

        Returns
        -------
        gpd.GeoDataFrame
            The rivers dataframe
        """
        if copy:
            return self.__rivers.copy()
        else:
            return self.__rivers

    def get_river_network(self, copy: bool = True) -> nx.Graph | None:
        """get_river_network returns the river network as a NetworkX graph.

        Parameters
        ----------
        copy: bool, optional
            Whether to return a copy of the network or the original, by default True

        Returns
        -------
        nx.Graph | None
            The river network graph if constructed, otherwise None.
        """
        if self.__river_network is None:
            return None
        if copy:
            return self.__river_network.copy()
        else:
            return self.__river_network

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
        return self.__risk_map

    # Constructor functions
    def construct_river_graph(self) -> None:
        """
        Construct a river network graph from the processed river data.

        The graph is constructed using the `momepy` library, creating a primal graph where each node
        represents a river segment intersection and edges represent river segments.
        """
        self.__river_network = utils.rivers.river_to_graph(
            self.__rivers_split, self.__directed)

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
    def add_point_info_to_map(
        self,
        ids: gpd.GeoSeries,
        geometry: gpd.GeoSeries,
        col_name_graph: str,
        max_risk_distance: float,
        max_dist_from_river: float,
        include_upstream: bool = False,
    ) -> gpd.GeoSeries:
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

        Returns
        -------
        gpd.GeoSeries
            GeoSeries of the closest points on the river network
        """
        if geometry.crs != self.__rivers_split.crs:
            raise ValueError(
                "Points are not in EPSG:3857 CRS which is used by the class")

        closestPoint, closestEdge, riverID = utils.rivers.add_location_on_river_and_closest_edge(self.__rivers_split,
                                                                                                 geometry, max_dist_from_river)
        df = pd.DataFrame(
            {"ids": ids, "closestPoint": closestPoint, "closestEdge": closestEdge})

        df["idTups"] = list(zip(df['ids'], riverID))
        df.apply(
            lambda x:
            graph.add_to_graph(
                self.__river_network,
                col_name_graph,
                x["idTups"],
                x["closestPoint"],
                x["closestEdge"],
                distance_cutoff=max_risk_distance,
                directed=self.__directed,
                both_ways=include_upstream
            ), axis=1
        )
        return closestPoint

    # Calculate risk from info on map
    def add_risk(self,
                 info_col: str,
                 risk_name: str,
                 risk_method: Callable,
                 distance_scaling: Callable,
                 aggregation_method: Callable = sum,
                 ids_to_ignore: list = [],
                 inplace: bool = True,
                 ) -> gpd.GeoDataFrame | None:
        """add_risk adds risk about a certain column in the riskMap

        Parameters
        ----------
        info_col : str
            The name of the column in the risk map containing tuples of (ID,dist) from river points
        risk_name : str
            The column name of the risk to be assigned in the risk map
        risk_method : Callable
            A method from riskID -> float that gives a risk value for each point
        distance_scaling : Callable
            A method from dist -> float which gives the distance scaling of that risk
        aggregation_method : Callable, optional
            The method iterable -> float to aggregate different risks together, by default sum
        ids_to_ignore : list
            The IDs to ignore from the risk calculation, by default []
        inplace : bool, optional
            Whether to add the risk to the risk map inplace or return a new risk map, by default True

        Returns
        -------
        gpd.GeoDataFrame | None
            The risk values for each point in the risk map if inplace is False, otherwise None

        Raises
        ------
        Exception
            If the col_name_risk doesn't exist in the riskmap, it asks the user to generate it
        """

        Risk = graph.add_risk(
            self,
            info_col,
            risk_name,
            risk_method,
            distance_scaling,
            aggregation_method,
            ids_to_ignore,
            inplace
        )
        if inplace:
            return
        else:
            gdf = gpd.GeoDataFrame.from_dict(Risk, orient="index")
            gdf = gdf.reset_index()
            gdf.set_geometry(gdf.apply(lambda x: shapely.Point(
                x["level_0"], x["level_1"]), axis=1), inplace=True)
            gdf.set_crs(epsg=3857, inplace=True)
            gdf.drop(columns=["level_0", "level_1"], inplace=True)
            return gdf

    def add_all_risk(self, risk_list: list[dict], aggregation_method: Callable = sum, inplace: bool = True) -> gpd.GeoDataFrame | None:
        """add_all_risk takes in a list of dictionaries and aggregates the risks together
        and adds it to the "Risk" column in the riskmap

        Parameters
        ----------
        risk_list : list[dict]
            A list of dictionaries consisting of parameters for the RiskMap.add_risk() method

            info_col: str

            risk_name: str

            risk_method: Callable

            distance_scaling: Callable

            aggregation_method: Callable (optional)

            ids_to_ignore: list
        aggregation_method : Callable, optional
            The aggregation method for all the risks together list[float] -> float, by default sum
        inplace : bool, optional
            Whether to add the risk to the risk map inplace or return a new risk map, by default True

        Returns
        -------

        gpd.GeoDataFrame | None
            The risk map with the aggregated risk if inplace is False, otherwise None

        Raises
        ------
        ValueError
            If one of the risk_name is "Risk"
        """
        if not inplace:
            risks = gpd.GeoDataFrame({"geometry": []}, geometry="geometry")
        for item in risk_list:
            if item["risk_name"] == "Risk":
                raise ValueError("Cannot have the risk name be Risk")
            if inplace:
                self.add_risk(**item)
            else:
                risks = risks.set_index("geometry").join(self.add_risk(
                    **item, inplace=False).set_index("geometry"), how="outer").reset_index()

        if inplace:
            for node in self.__river_network.nodes:
                self.__river_network.nodes[node]["Risk"] = aggregation_method(
                    [self.__river_network.nodes[node][item["risk_name"]] for item in risk_list])
            return
        else:
            risks["Risk"] = risks.apply(lambda x: aggregation_method(
                [x[item["risk_name"]] for item in risk_list]), axis=1)
            return gpd.GeoDataFrame(risks, geometry="geometry", crs="EPSG:3857")

    # Optimization

    def optimization_greedy(
        self,
        n_sensors: float,
        riskList: list[dict],
        sensor_coverage_dist: float,
        hist: bool = True
    ) -> list[tuple[gpd.GeoDataFrame, pd.Series]] | tuple[gpd.GeoDataFrame, pd.Series]:
        """optimization_greedy is a greedy optimization algorithm to find the best sensor locations

        Parameters
        ----------
        n_sensors : float
            The number of sensors to be placed
        riskList : list[dict]
            A list of dictionaries consisting of parameters for the RiskMap.add_risk() method

            info_col: str

            risk_name: str

            risk_method: Callable

            distance_scaling: Callable

            aggregation_method: Callable (optional)

            ids_to_ignore: list
        sensor_coverage_dist : float
            The maximum distance a sensor can cover
        hist : bool, optional
            Whether to store the information and return it as a list or not, by default True

        Returns
        -------
        list[tuple[gpd.GeoDataFrame,pd.Series]] | tuple[gpd.GeoDataFrame,pd.Series]
            A list of tuples containing the sensor locations and the risk map at that point if hist is 
            True, otherwise a tuple containing the sensor locations and the risk map at that point
        """

        sensors = gpd.GeoDataFrame(
            data={}, geometry=[])
        allrisk = self.add_all_risk(riskList, inplace=False)
        newrisk = allrisk["Risk"]
        if hist:
            risk_hist = []
            risk_hist.append((gpd.GeoDataFrame(), allrisk.copy()))

        riskList2 = deepcopy(riskList)
        for i in range(n_sensors):
            if newrisk.max() == 0:
                print(f"All risk accounted for by {i} sensors")
                break

            max_risk_idx = newrisk.idxmax()
            sensor_loc: shapely.Point = allrisk.loc[max_risk_idx].geometry

            # Add the csos within a certain distance of the sensor point
            covered = {}
            for item in riskList2:
                idscovered = [id for id, dist in self.__river_network.nodes[(
                    sensor_loc.x, sensor_loc.y)][item["info_col"]].items() if dist <= sensor_coverage_dist]
                if "ids_to_ignore" not in item:
                    item["ids_to_ignore"] = idscovered
                else:
                    item["ids_to_ignore"] = item["ids_to_ignore"] + idscovered
                covered[item["info_col"]+"_covered"] = [idscovered]
            # Add sensor to the list
            covered["geometry"] = sensor_loc

            sensors = pd.concat([sensors, gpd.GeoDataFrame(
                covered, geometry="geometry", crs="EPSG:3857")], ignore_index=True)
            sensors = sensors.set_crs(epsg=3857)
            sensors.index = sensors.index+1

            # Remove the CSOs from the risk calculation
            allrisk = self.add_all_risk(riskList2, inplace=False)
            newrisk = allrisk["Risk"]
            if hist:
                risk_hist.append((sensors.copy(), allrisk.copy()))
        if hist:
            return risk_hist
        else:
            return sensors, allrisk
