import geopandas as gpd
from collections.abc import Callable


def greedy_optimization(n_sensors: int, risk: gpd.GeoDataFrame, riskfunc: Callable, max_sensor_coverage_dist: float) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """greedy_optimization finds the optimal locations of sensors based on minimizing the maximum risk for each sensor placed. Returns early if risk is 0.

    Parameters
    ----------
    n_sensors : int
        The number of sensors to place
    risk : gpd.GeoDataFrame
        The risk map containing information about CSOs
    riskfunc : Callable
        The risk function used to evaluate the risk from the riskmap
    max_sensor_coverage_dist : float
        The distance of the CSO upstream to be considered 'covered' by the sensor

    Returns
    -------
    tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]
        Optimal sensor locations and the risk map after they have been placed.
    """
    sensors = gpd.GeoDataFrame(
        data={"csoCovered": []}, geometry=[])
    risk_after_sensor = risk.copy()

    for i in range(n_sensors):
        # Check if risk exists or not
        if risk_after_sensor["Risk"].max() == 0:
            print(f"All risk accounted for by {i} sensors")
            break

        max_risk_idx = risk_after_sensor["Risk"].idxmax()

        sensor_loc = risk_after_sensor.loc[max_risk_idx].geometry

        # Add the csos within a certain distance of the sensor point
        csos_covered = [cso for cso,
                        dist in risk_after_sensor.loc[max_risk_idx, "csoInfo"] if dist <= max_sensor_coverage_dist]

        # Add sensor to the list
        sensors.loc[-1] = [csos_covered, sensor_loc]
        sensors.index = sensors.index+1

        # Remove the CSOs from the risk calculation
        risk_after_sensor["csoInfo"] = risk_after_sensor["csoInfo"].apply(
            lambda x: [(cso, dist) for cso, dist in x if cso not in csos_covered])

        # Calculate the risk again
        risk_after_sensor["Risk"] = riskfunc(risk_after_sensor)

    sensors = sensors.set_crs(risk.crs)
    return sensors, risk_after_sensor
