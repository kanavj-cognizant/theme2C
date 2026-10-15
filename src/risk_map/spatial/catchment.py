from pathlib import Path
from tempfile import TemporaryDirectory

import elevatr
import geopandas as gpd
from whitebox_workflows import WbEnvironment


def delineate_catchments(
    risk_map: gpd.GeoDataFrame,
    tile_folder: Path,
    workspace_root: Path | None = None,
    buffer_degrees: float = 0.15,
    stream_threshold: float = 1000.0,
    pour_point_snap_distance: float = 0.001,
) -> gpd.GeoDataFrame:
    """Delineate a watershed for each risk-map point using Whitebox Workflows.

    Each invocation gets a temporary Whitebox workspace, preventing intermediate
    files from colliding across concurrent runs. Elevation tiles are cached in
    ``tile_folder`` and retained after the workspace is removed.
    """
    if risk_map.crs is None:
        raise ValueError("risk_map must have a CRS")
    if risk_map.empty:
        raise ValueError("risk_map must contain at least one pour point")
    if buffer_degrees <= 0:
        raise ValueError("buffer_degrees must be positive")
    if stream_threshold <= 0:
        raise ValueError("stream_threshold must be positive")
    if pour_point_snap_distance <= 0:
        raise ValueError("pour_point_snap_distance must be positive")

    tile_folder.mkdir(parents=True, exist_ok=True)
    if workspace_root is not None:
        workspace_root.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix="risk_map_catchment_", dir=workspace_root) as temp_dir:
        workspace = Path(temp_dir)
        wbe = WbEnvironment()
        wbe.verbose = False  # ty: ignore[invalid-assignment]
        wbe.working_directory = workspace.as_posix()  # ty: ignore[invalid-assignment]

        bounds = risk_map.to_crs(epsg=4326).total_bounds
        bounds[0] -= buffer_degrees
        bounds[1] -= buffer_degrees
        bounds[2] += buffer_degrees
        bounds[3] += buffer_degrees
        elevation = elevatr.get_elev_raster(
            tuple(bounds),
            zoom=12,
            use_cache=True,
            delete_cache=False,
            crs="EPSG:4326",
            cache_folder=str(tile_folder),
            verbose=False,
        )
        if elevation is None:
            raise RuntimeError("Elevation data could not be retrieved for the risk map")

        elevation_path = workspace / "elevation.tif"
        risk_points_path = workspace / "risk_points.shp"
        watershed_path = workspace / "watersheds.tif"
        polygons_path = workspace / "catchments.shp"

        elevation.to_tif(elevation_path.as_posix())
        dem = wbe.read_raster(elevation_path.as_posix(), file_mode="m")
        filled = wbe.hydrology.depressions_storage.fill_depressions(dem=dem)
        d8_pointer = wbe.hydrology.flow_routing.d8_pointer(dem=filled)
        accumulation = wbe.hydrology.flow_routing.d8_flow_accum(
            input=d8_pointer,
            out_type="cells",
            clip=False,
            input_is_pointer=True,
        )
        streams = wbe.streams.network_extraction.extract_streams(
            flow_accumulation=accumulation,
            threshold=stream_threshold,
        )

        risk_map.to_crs(epsg=4326)[["geometry"]].to_file(risk_points_path.as_posix())
        points = wbe.read_vector(risk_points_path.as_posix())
        snapped_points = wbe.hydrology.jenson_snap_pour_points(
            pour_pts=points,
            streams=streams,
            snap_dist=pour_point_snap_distance,
        )
        watersheds = wbe.hydrology.watersheds_basins.watershed(
            d8_pntr=d8_pointer,
            pour_pts=snapped_points,
            esri_pntr=False,
        )

        # WBE 2's vector conversion tool accepts a raster path, not an in-memory raster.
        wbe.write_raster(raster=watersheds, output_path=watershed_path.as_posix())
        polygons = wbe.conversion.raster_vector_conversion.raster_to_vector_polygons(
            input=watershed_path.as_posix()  # ty: ignore[invalid-argument-type]
        )
        wbe.write_vector(vector=polygons, output_path=polygons_path.as_posix())
        result = gpd.read_file(polygons_path.as_posix())

    if result.crs is None:
        raise ValueError("Whitebox catchments have no CRS")
    return result.to_crs(risk_map.crs)
