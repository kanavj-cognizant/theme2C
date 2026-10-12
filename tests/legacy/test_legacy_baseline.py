import pathlib

import geopandas as gpd
import numpy as np
import pytest

import RiskMap
from pollutant_parameters import NH4_values, P_values
from utils import consentsRisk
from utils.agricultureRisk import (
    agriculture_land_cover_load,
    agriculture_livestock_load,
)
from utils.rivers import linear_flow
from utils.urbanRisk import urban_buildings, urban_road

FIXTURES_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "browney"
GOLDEN_BASELINE_PATH = FIXTURES_DIR / "golden_riskmap.parquet"
CATCHMENT_GEOJSON = FIXTURES_DIR / "catchment.geojson"


def get_browney_mask() -> gpd.GeoDataFrame:
    cmnt = gpd.read_file(CATCHMENT_GEOJSON)
    cmnt["geometry"] = cmnt.make_valid()
    cmnt = cmnt[["geometry"]][cmnt.geometry.type == "Polygon"]
    cmnt.to_crs(epsg=27700, inplace=True)
    return gpd.GeoDataFrame(geometry=[cmnt.geometry.union_all()], crs=cmnt.crs)


def run_legacy_browney_pipeline() -> gpd.GeoDataFrame:
    """Run the exact legacy Exploration.ipynb pipeline on Browney catchment."""
    cmnt_mask = get_browney_mask()

    data_dir = pathlib.Path("data")
    rivers_all = gpd.read_parquet(data_dir / "rivers.parquet")
    cso_all = gpd.read_parquet(data_dir / "cso.parquet")
    wwtw_all = gpd.read_parquet(data_dir / "wwtw.parquet")

    rivers = gpd.overlay(rivers_all, cmnt_mask, how="intersection", keep_geom_type=True)
    cso = gpd.clip(cso_all, cmnt_mask)
    wwtw = gpd.clip(wwtw_all, cmnt_mask)

    river_resolution = 300.0
    max_outfall_dist = 700.0
    max_risk_dist = np.inf

    # 1. Initialize RiskMap and delineate catchments
    riskmap = RiskMap.RiskMap(
        riversdf=rivers,
        id_col="RivID",
        resolution=river_resolution,
        river_flow_func=lambda x: linear_flow(x["US_Accum"]),
        catchment_mask=cmnt_mask,
    )
    riskmap.delineate_catchments()

    # 2. Add CSO and WwTW point outfalls
    outfall_cso = riskmap.add_point_info_to_map(
        cso.index,
        cso["geometry"],
        "csoInfo",
        max_risk_dist,
        max_outfall_dist,
    )
    cso = cso.assign(outfallLocation=outfall_cso)

    outfall_wwtw = riskmap.add_point_info_to_map(
        wwtw.index,
        wwtw["geometry"],
        "wwtwInfo",
        max_risk_dist,
        max_outfall_dist,
    )
    wwtw = wwtw.assign(outfallLocation=outfall_wwtw)

    # 3. Add Diffuse loads (Agriculture & Livestock)
    riskmap.add_diffuse_info_to_map(
        info_col_name="agri_P_load",
        load_method=agriculture_land_cover_load,
        parameter="P",
    )
    riskmap.add_diffuse_info_to_map(
        info_col_name="livestock_P_load",
        load_method=agriculture_livestock_load,
        parameter="P",
    )
    riskmap.add_diffuse_info_to_map(
        info_col_name="agri_NH4_load",
        load_method=agriculture_land_cover_load,
        parameter="NH4",
    )
    riskmap.add_diffuse_info_to_map(
        info_col_name="livestock_NH4_load",
        load_method=agriculture_livestock_load,
        parameter="NH4",
    )

    # 4. Add Diffuse loads (Urban roads & buildings)
    riskmap.add_diffuse_info_to_map(
        info_col_name="urban_road_P_load",
        load_method=urban_road,
        parameter="P",
    )
    riskmap.add_diffuse_info_to_map(
        info_col_name="urban_build_P_load",
        load_method=urban_buildings,
        parameter="P",
    )
    riskmap.add_diffuse_info_to_map(
        info_col_name="urban_road_NH4_load",
        load_method=urban_road,
        parameter="NH4",
    )
    riskmap.add_diffuse_info_to_map(
        info_col_name="urban_build_NH4_load",
        load_method=urban_buildings,
        parameter="NH4",
    )

    # 5. Calculate P_Risk
    riskmap.add_all_risk(
        [
            {
                "type": "point",
                "info_col": "csoInfo",
                "risk_name": "cso_P_Risk",
                "risk_method": consentsRisk.add_cso_risk("P"),
                "distance_scaling": P_values.p_distance_scaling,
            },
            {
                "type": "point",
                "info_col": "wwtwInfo",
                "risk_name": "wwtw_P_Risk",
                "risk_method": consentsRisk.add_wwtw_risk("P"),
                "distance_scaling": P_values.p_distance_scaling,
            },
            {
                "type": "diffuse",
                "info_col": "agri_P_load",
                "risk_name": "agri_P_Risk",
                "risk_method": lambda x: x / P_values.P_GUIDELINE * 100,
                "distance_scaling": P_values.p_distance_scaling,
            },
            {
                "type": "diffuse",
                "info_col": "livestock_P_load",
                "risk_name": "livestock_P_Risk",
                "risk_method": lambda x: x / P_values.P_GUIDELINE * 100,
                "distance_scaling": P_values.p_distance_scaling,
            },
            {
                "type": "diffuse",
                "info_col": "urban_road_P_load",
                "risk_name": "urban_road_P_Risk",
                "risk_method": lambda x: x / P_values.P_GUIDELINE * 100,
                "distance_scaling": P_values.p_distance_scaling,
            },
            {
                "type": "diffuse",
                "info_col": "urban_build_P_load",
                "risk_name": "urban_build_P_Risk",
                "risk_method": lambda x: x / P_values.P_GUIDELINE * 100,
                "distance_scaling": P_values.p_distance_scaling,
            },
        ],
        risk_name="P_Risk",
    )

    # 6. Calculate NH4_Risk
    riskmap.add_all_risk(
        [
            {
                "type": "point",
                "info_col": "csoInfo",
                "risk_name": "cso_NH4_Risk",
                "risk_method": consentsRisk.add_cso_risk("NH4"),
                "distance_scaling": NH4_values.nh4_distance_scaling,
            },
            {
                "type": "point",
                "info_col": "wwtwInfo",
                "risk_name": "wwtw_NH4_Risk",
                "risk_method": consentsRisk.add_wwtw_risk("NH4"),
                "distance_scaling": NH4_values.nh4_distance_scaling,
            },
            {
                "type": "diffuse",
                "info_col": "agri_NH4_load",
                "risk_name": "agri_NH4_Risk",
                "risk_method": lambda x: x / NH4_values.NH4_GUIDELINE * 100,
                "distance_scaling": NH4_values.nh4_distance_scaling,
            },
            {
                "type": "diffuse",
                "info_col": "livestock_NH4_load",
                "risk_name": "livestock_NH4_Risk",
                "risk_method": lambda x: x / NH4_values.NH4_GUIDELINE * 100,
                "distance_scaling": NH4_values.nh4_distance_scaling,
            },
            {
                "type": "diffuse",
                "info_col": "urban_road_NH4_load",
                "risk_name": "urban_road_NH4_Risk",
                "risk_method": lambda x: x / NH4_values.NH4_GUIDELINE * 100,
                "distance_scaling": NH4_values.nh4_distance_scaling,
            },
            {
                "type": "diffuse",
                "info_col": "urban_build_NH4_load",
                "risk_name": "urban_build_NH4_Risk",
                "risk_method": lambda x: x / NH4_values.NH4_GUIDELINE * 100,
                "distance_scaling": NH4_values.nh4_distance_scaling,
            },
        ],
        risk_name="NH4_Risk",
    )

    # 7. Aggregate overall Risk
    riskmap.add_all_risk(
        [
            {"type": "simple", "risk_name": "P_Risk"},
            {"type": "simple", "risk_name": "NH4_Risk"},
        ],
        risk_name="Risk",
        aggregation_method=np.mean,
    )

    return riskmap.get_risk_map()


@pytest.fixture(scope="session")
def golden_baseline() -> gpd.GeoDataFrame:
    """Fixture to ensure the golden baseline exists and returns it."""
    if not GOLDEN_BASELINE_PATH.exists():
        gdf = run_legacy_browney_pipeline()
        # Parquet cannot serialize nested list/dict columns easily, so flatten or drop info dicts for the baseline table
        columns_to_save = [
            col
            for col in gdf.columns
            if not isinstance(gdf[col].iloc[0] if len(gdf) else None, (list, dict))
        ]
        gdf[columns_to_save].to_parquet(GOLDEN_BASELINE_PATH)
    return gpd.read_parquet(GOLDEN_BASELINE_PATH)


@pytest.mark.legacy
def test_legacy_baseline_structure(golden_baseline: gpd.GeoDataFrame):
    """Sanity check the structure of the golden baseline."""
    assert len(golden_baseline) == 426
    expected_cols = [
        "flow",
        "P_Risk",
        "NH4_Risk",
        "Risk",
        "cso_P_Risk",
        "wwtw_P_Risk",
        "agri_P_Risk",
        "livestock_P_Risk",
        "urban_road_P_Risk",
        "urban_build_P_Risk",
        "cso_NH4_Risk",
        "wwtw_NH4_Risk",
        "agri_NH4_Risk",
        "livestock_NH4_Risk",
        "urban_road_NH4_Risk",
        "urban_build_NH4_Risk",
        "agri_P_load",
        "livestock_P_load",
        "urban_road_P_load",
        "urban_build_P_load",
        "geometry",
    ]
    for col in expected_cols:
        assert col in golden_baseline.columns, f"Missing expected column {col}"

    # Assert values are reasonable numbers
    assert golden_baseline["flow"].min() >= 0
    assert not golden_baseline["Risk"].isna().all()
    assert (golden_baseline["Risk"] >= 0).all()
