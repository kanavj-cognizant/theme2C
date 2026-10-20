"""Capture the legacy Browney catchment-delineation baseline.

Run explicitly from the repository root; tests never regenerate this baseline:

    .venv/bin/python scripts/capture_legacy_catchments.py [--force]

Uses cached elevation tiles in data/tiles and writes legacy scratch files to data/wbe.
"""

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone
from importlib.metadata import version

import geopandas as gpd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import RiskMap  # noqa: E402
from utils.rivers import linear_flow  # noqa: E402

FIXTURES_DIR = ROOT / "tests" / "fixtures" / "browney"
OUTPUT_DIR = FIXTURES_DIR / "legacy_catchments"
RIVER_RESOLUTION = 300.0
MIN_AREA = 20000.0


def browney_mask() -> gpd.GeoDataFrame:
    cmnt = gpd.read_file(FIXTURES_DIR / "catchment.geojson")
    cmnt["geometry"] = cmnt.make_valid()
    cmnt = cmnt[["geometry"]][cmnt.geometry.type == "Polygon"].to_crs(epsg=27700)
    return gpd.GeoDataFrame(geometry=[cmnt.geometry.union_all()], crs=cmnt.crs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="overwrite an existing baseline")
    args = parser.parse_args()
    if OUTPUT_DIR.exists() and not args.force:
        sys.exit(f"{OUTPUT_DIR} exists; pass --force to overwrite the baseline")

    mask = browney_mask()
    rivers = gpd.read_parquet(FIXTURES_DIR / "rivers.parquet")
    riskmap = RiskMap.RiskMap(
        riversdf=rivers,
        id_col="RivID",
        resolution=RIVER_RESOLUTION,
        river_flow_func=lambda x: linear_flow(x["US_Accum"]),
        catchment_mask=mask,
    )
    riskmap.delineate_catchments(min_area=MIN_AREA)

    nodes = riskmap.get_risk_map()[["geometry"]].rename_axis("node_id").reset_index()
    raw_polygons = gpd.read_file(ROOT / "data" / "wbe" / "polygons.shp")
    joined, map_poly_join, mapping_points = riskmap.get_joined_catchments()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    nodes.to_parquet(OUTPUT_DIR / "nodes.parquet")
    raw_polygons.to_parquet(OUTPUT_DIR / "wbe_polygons.parquet")
    gpd.GeoDataFrame(mapping_points, geometry="geometry", crs=nodes.crs).to_parquet(
        OUTPUT_DIR / "mapping_points.parquet"
    )
    map_poly_join.astype("float64").to_parquet(OUTPUT_DIR / "map_poly_join.parquet")
    joined.reset_index().to_parquet(OUTPUT_DIR / "joined_catchments.parquet")

    metadata = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "river_resolution_m": RIVER_RESOLUTION,
        "min_area_m2": MIN_AREA,
        "elevation_zoom": 12,
        "buffer_degrees": 0.15,
        "stream_threshold_cells": 1000.0,
        "snap_distance_degrees": 0.001,
        "tile_count": len(list((ROOT / "data" / "tiles").glob("*"))),
        "packages": {
            name: version(name)
            for name in ("whitebox-workflows", "elevatr", "geopandas", "shapely", "momepy")
        },
    }
    (OUTPUT_DIR / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Wrote legacy catchment baseline to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
