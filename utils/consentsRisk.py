import pandas as pd
import geopandas as gpd
import math
from utils.rivers import linear_flow

WWTW_PHOSPHORUS_FILL_VALUE = 5.0    # mg/l
WWTW_DWF_FILL_VALUE = 20.0      # m3/day

CSO_PHOSPHORUS_FILL_VALUE = 3.0    # mg/l
CSO_WEIR_SETTING_FILL_VALUE = 20    # l/s

P_GUIDLINE = 0.87   # mg/l
P_DEGRADATION_COEFF = 4E-2  # /day

FLOW_SPEED = 0.55  # m/s

determinands = pd.read_parquet(
    "gs://rdmai_dev_data/cleansed/Theme2CD/determinands.parquet")

################################################################################################################
wwtw = gpd.read_parquet(
    "gs://rdmai_dev_data/cleansed/Theme2CD/WWTW_consents.parquet")

wwtw_join = wwtw.join(determinands[determinands["DETE"].str.contains("Phosphorus") & (
    determinands["CODE"] == "MEAN VALUE")]["VALUE"], how="left")
wwtw_join.rename(columns={"VALUE": "P"}, inplace=True)

wwtw_join = wwtw_join.join(determinands[(determinands["DETE"].str.contains("Flow : Dry Weather :- {DWF}")) & (
    determinands["CODE"] == "MAXIMUM VALUE")]["VALUE"], how="left")
wwtw_join.rename(columns={"VALUE": "DWF"}, inplace=True)

wwtw_join["P"].fillna(WWTW_PHOSPHORUS_FILL_VALUE, inplace=True)
wwtw_join["DWF"].fillna(WWTW_DWF_FILL_VALUE, inplace=True)

################################################################################################################

cso_join = gpd.read_parquet(
    "gs://rdmai_dev_data/cleansed/Theme2CD/CSO_Annual_2024_Weir.parquet")
cso_join["Weir Setting"].fillna(CSO_WEIR_SETTING_FILL_VALUE, inplace=True)

################################################################################################################


def add_wwtw_P_risk(ID: str, riverInfo: dict) -> float:
    """add_wwtw_P_risk which is the risk calculated from the Phosphorus emissions
    by the waste water treatement works

    Parameters
    ----------
    ID : str
        The ID of the treatement work
    riverInfo : dict
        A dict of river info at the point of inflow into the river network

    Returns
    -------
    float
        Returns the value of phosphorus after dilution divided by the standard guidline and scaled between
        0 and 100.
    """
    accum_len = riverInfo["Upstream Accumulated Length"]
    p = wwtw_join.loc[ID, "P"]
    dwf = wwtw_join.loc[ID, "DWF"]
    p_dilution = p * dwf/(linear_flow(accum_len)*86400)
    rsk = p_dilution/P_GUIDLINE*100
    return rsk

def add_cso_P_risk(ID: str, riverInfo: dict) -> float:
    """add_cso_P_risk which is the risk calculated from the CSOs
    based on their weir setting and spill duration

    Parameters
    ----------
    ID : str
        The Unique ID of the CSO in question
    riverInfo : dict
        The riverInfo dict containing the upstream accumulated length

    Returns
    -------
    float
        Returns the value of phosphorus after dilution divided by the standard guidline and scaled between
        0 and 100.
    """
    accum_len = riverInfo["Upstream Accumulated Length"]
    p = CSO_PHOSPHORUS_FILL_VALUE
    weir_setting = cso_join.loc[ID, "Weir Setting"] # l/s
    p_dilution = p * weir_setting/(linear_flow(accum_len)*1000)*(cso_join.loc[ID, "Spill Duration"].total_seconds()/(365*24*60*60))
    rsk = p_dilution/P_GUIDLINE*100
    return rsk

def P_distance_scaling(x: float) -> float:
    return math.e**(-(P_DEGRADATION_COEFF/86400)*x/FLOW_SPEED)