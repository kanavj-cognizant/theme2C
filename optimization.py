import geopandas as gpd
import pyproj
from collections.abc import Callable

def greedy_optimization(n_sensors: int, risk: gpd.GeoDataFrame, riskfunc: Callable) -> gpd.GeoDataFrame:
    sensors = gpd.GeoDataFrame(
        data={"csoCovered": []}, geometry=[])
    risk_after_sensor = risk.copy()

    for i in range(n_sensors):
        if risk_after_sensor["Risk"].max() == 0:
            print(f"All risk accounted for by {i} sensors")
            break
        max_risk_idx = risk_after_sensor["Risk"].idxmax()

        sensor_loc = risk_after_sensor.loc[max_risk_idx].geometry
        csos_covered = [cso for cso,
                        _ in risk_after_sensor.loc[max_risk_idx, "csoInfo"]]

        sensors.loc[-1] = [csos_covered, sensor_loc]
        sensors.index = sensors.index+1

        # all_csos_covered.extend(csos_covered)

        risk_after_sensor["csoInfo"] = risk_after_sensor["csoInfo"].apply(
            lambda x: [(cso, dist) for cso, dist in x if cso not in csos_covered])
        risk_after_sensor["Risk"] = riskfunc(risk_after_sensor)

    sensors = sensors.set_crs(risk.crs)
    return sensors
