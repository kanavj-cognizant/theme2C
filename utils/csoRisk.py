import geopandas as gpd
import os
import pandas as pd

EDM_PATH = os.getenv("CSO_DATA_OUTPUT")
edm = pd.read_pickle(EDM_PATH)

freq: pd.Series = edm.groupby("Monitor_ID").count()["Event_Start"]

dur = edm["Event_End"] - edm["Event_Start"]
duration: pd.Series = dur.groupby("Monitor_ID").sum()


def add_frequency_risk(ID: str) -> float | None:
    if ID in freq.index:
        return freq[ID]
    else:
        return None

def add_duration_risk(ID: str) -> float | None:
    if ID in duration.index:
        return duration[ID].total_seconds()/60/60
    else:
        return None

def add_const_risk(ID:str) -> float:
    return 10