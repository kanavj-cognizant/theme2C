import os
import pathlib

import elevatr
import geopandas as gpd
from whitebox_workflows import WbEnvironment

rmap_before = None


def delineate_catchments(
    rmap: gpd.GeoDataFrame,
    wbe_folder: pathlib.Path,
    tile_folder: pathlib.Path,
) -> gpd.GeoDataFrame:
    """
    Delineate catchments for the provided risk map using WhiteboxTools.

    This function uses WhiteboxTools to fill depressions, calculate flow
    directions, and extract watersheds based on the provided risk map.

    It requires the WhiteboxTools environment to be set up and uses
    elevatr to obtain elevation data for the area of interest.

    Parameters
    ----------
    rmap : gpd.GeoDataFrame
        GeoDataFrame containing the risk map with geometry and river index.
    wbe_folder : pathlib.Path
        Path to the folder where WhiteboxTools will store its working files.
    tile_folder : pathlib.Path
        Path to the folder where elevation tiles will be stored.
    Returns
    -------
    gpd.GeoDataFrame
        GeoDataFrame containing the delineated catchments as polygons.

    """
    global rmap_before
    if rmap_before is not None and rmap_before.equals(rmap):
        return gpd.read_file(pathlib.Path.joinpath(wbe_folder, "polygons.shp").as_posix()).to_crs(
            rmap.crs
        )
    rmap_before = rmap.copy()

    os.makedirs(wbe_folder, exist_ok=True)
    wbe = WbEnvironment()
    wbe.verbose = False  # ty: ignore[invalid-assignment]
    wbe.working_directory = wbe_folder.as_posix()  # ty: ignore[invalid-assignment]
    os.makedirs(tile_folder, exist_ok=True)

    buffer = 0.15
    bounds = rmap.to_crs(epsg=4326).total_bounds
    bounds[0] -= buffer
    bounds[1] -= buffer
    bounds[2] += buffer
    bounds[3] += buffer
    rs = elevatr.get_elev_raster(
        tuple(bounds),
        zoom=12,
        use_cache=True,
        delete_cache=False,
        crs="EPSG:4326",
        cache_folder=str(tile_folder),
        verbose=False,
    )
    if rs is None:
        raise Exception("wtf")
    rs.to_tif(pathlib.Path.joinpath(wbe_folder, "raster.tif").as_posix())
    rs = wbe.read_raster("raster.tif", file_mode="m")
    filled = wbe.hydrology.depressions_storage.fill_depressions(dem=rs)
    d8s = wbe.hydrology.flow_routing.d8_pointer(dem=filled)
    accum = wbe.hydrology.flow_routing.d8_flow_accum(
        input=d8s, out_type="cells", clip=False, input_is_pointer=True
    )
    streams = wbe.streams.network_extraction.extract_streams(
        flow_accumulation=accum, threshold=1000.0
    )
    rmap.to_crs(epsg=4326)["geometry"].to_file(
        pathlib.Path.joinpath(wbe_folder, "riskmap.shp").as_posix()
    )
    points = wbe.read_vector("riskmap.shp")
    snapped = wbe.hydrology.jenson_snap_pour_points(
        pour_pts=points, streams=streams, snap_dist=0.001
    )
    watersheds = wbe.hydrology.watersheds_basins.watershed(
        d8_pntr=d8s, pour_pts=snapped, esri_pntr=False
    )
    watershed_path = wbe_folder.joinpath("watersheds.tif")
    wbe.write_raster(raster=watersheds, output_path=watershed_path.as_posix())
    polygons = wbe.conversion.raster_vector_conversion.raster_to_vector_polygons(
        input=watershed_path.as_posix()  # ty: ignore[invalid-argument-type]
    )
    wbe.write_vector(vector=polygons, output_path="polygons.shp")
    polygons = gpd.read_file(pathlib.Path.joinpath(wbe_folder, "polygons.shp").as_posix()).to_crs(
        rmap.crs
    )
    return polygons
