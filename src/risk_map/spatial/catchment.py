from pathlib import Path
from tempfile import TemporaryDirectory

import geopandas as gpd
import numpy as np
import polars as pl
import rasterio
import rasterio.features
import rasterio.transform
import shapely
from pydantic import BaseModel, ConfigDict, model_validator
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from whitebox_workflows import WbEnvironment

from risk_map.data.dem import DemProvider

TARGET_CRS = "EPSG:27700"
PRECISION_M = 0.01
_ORDER_FIELD = "pp_order"


class CatchmentDelineation(BaseModel):
    """Watersheds keyed by pour point, and the river node to pour point membership.

    ``watersheds`` has ``pour_point_id``, ``area_m2`` (m2) and ``geometry`` in
    EPSG:27700. ``membership`` has one row per river node with ``node_id`` and
    ``pour_point_id``; several nodes may share a pour point.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    watersheds: gpd.GeoDataFrame
    membership: pl.DataFrame

    @model_validator(mode="after")
    def _check_consistency(self) -> "CatchmentDelineation":
        missing = {"pour_point_id", "area_m2", "geometry"} - set(self.watersheds.columns)
        if missing:
            raise ValueError(f"watersheds is missing columns {sorted(missing)}")
        missing = {"node_id", "pour_point_id"} - set(self.membership.columns)
        if missing:
            raise ValueError(f"membership is missing columns {sorted(missing)}")
        if self.watersheds.crs is None or self.watersheds.crs.to_epsg() != 27700:
            raise ValueError("watersheds must use EPSG:27700")
        if self.watersheds["pour_point_id"].duplicated().any():
            raise ValueError("watersheds contains duplicate pour_point_id values")
        if self.membership["pour_point_id"].null_count():
            raise ValueError("membership contains null pour_point_id values")
        if self.membership["node_id"].is_duplicated().any():
            raise ValueError("membership contains duplicate node_id values")
        unknown = set(self.membership["pour_point_id"].to_list()) - set(
            self.watersheds["pour_point_id"].tolist()
        )
        if unknown:
            raise ValueError(
                f"membership references unknown pour_point_id values {sorted(unknown)}"
            )
        return self


def delineate_catchments(
    river_nodes: gpd.GeoDataFrame,
    dem_provider: DemProvider,
    *,
    node_id_column: str = "node_id",
    mask: gpd.GeoDataFrame | None = None,
    buffer_m: float = 10_000.0,
    stream_threshold_m2: float = 240_000.0,
    snap_distance_m: float = 100.0,
    workspace_root: Path | None = None,
) -> CatchmentDelineation:
    """Delineate one watershed per distinct snapped pour point of the river nodes.

    Nodes are Jenson-snapped onto DEM-derived streams; nodes landing in the same
    cell share a pour point. A node whose snapped cell has no DEM data is assigned
    the watershed at its own location. With ``mask``, the DEM is clipped to it
    before hydrology and the watersheds are clipped to it afterwards; without it,
    the DEM covers the nodes plus ``buffer_m``. Each call runs Whitebox in its own
    temporary workspace under ``workspace_root`` (system temp by default).
    """
    nodes = _validated_nodes(river_nodes, node_id_column)
    for name, value in (
        ("buffer_m", buffer_m),
        ("stream_threshold_m2", stream_threshold_m2),
        ("snap_distance_m", snap_distance_m),
    ):
        if not value > 0:
            raise ValueError(f"{name} must be positive, got {value}")

    mask_geometry = None
    if mask is not None:
        if mask.crs is None:
            raise ValueError("mask must have a CRS")
        mask_geometry = shapely.union_all(mask.to_crs(TARGET_CRS).geometry.to_numpy())
        minx, miny, maxx, maxy = (float(v) for v in mask_geometry.bounds)
        bounds = (minx, miny, maxx, maxy)
    else:
        minx, miny, maxx, maxy = (float(v) for v in nodes.total_bounds)
        bounds = (minx - buffer_m, miny - buffer_m, maxx + buffer_m, maxy + buffer_m)

    if workspace_root is not None:
        workspace_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="risk_map_catchment_", dir=workspace_root) as temp_dir:
        workspace = Path(temp_dir)
        dem_path = Path(dem_provider.fetch(bounds, TARGET_CRS, workspace / "dem.tif"))
        if mask_geometry is not None:
            dem_path = _clip_dem(dem_path, mask_geometry, workspace / "dem_clipped.tif")

        with rasterio.open(dem_path) as dem:
            elevation = dem.read(1)
            profile = dem.profile
            transform = dem.transform
            dem_nodata = dem.nodata
        cell_area = abs(transform.a * transform.e)
        has_data = np.isfinite(elevation) & (elevation != dem_nodata)

        wbe = WbEnvironment()
        wbe.verbose = False  # ty: ignore[invalid-assignment]
        wbe.working_directory = workspace.as_posix()  # ty: ignore[invalid-assignment]
        d8_pointer, snapped = _snap_pour_points(
            wbe, dem_path, nodes, workspace, stream_threshold_m2 / cell_area, snap_distance_m
        )

        snapped_cells = _cells(transform, snapped)
        usable = _inside(snapped_cells, elevation.shape)
        usable[usable] = has_data[snapped_cells[usable, 0], snapped_cells[usable, 1]]
        unique_cells, inverse = np.unique(snapped_cells[usable], axis=0, return_inverse=True)
        pour_point_ids = np.zeros(len(nodes), dtype=np.int64)
        pour_point_ids[usable] = inverse.ravel() + 1

        pour_grid = np.zeros(elevation.shape, dtype=np.int32)
        pour_grid[unique_cells[:, 0], unique_cells[:, 1]] = np.arange(1, len(unique_cells) + 1)
        pour_path = workspace / "pour_points.tif"
        with rasterio.open(
            pour_path, "w", **{**profile, "dtype": "int32", "nodata": 0, "count": 1}
        ) as target:
            target.write(pour_grid, 1)

        labels_path = workspace / "watersheds.tif"
        watershed = wbe.hydrology.watershed_from_raster_pour_points(
            d8_pointer=d8_pointer, pour_points=wbe.read_raster(pour_path.as_posix())
        )
        wbe.write_raster(raster=watershed, output_path=labels_path.as_posix())
        with rasterio.open(labels_path) as source:
            raw_labels = source.read(1)
            labels_nodata = source.nodata

    labelled = np.isfinite(raw_labels) & (raw_labels > 0)
    if labels_nodata is not None:
        labelled &= raw_labels != labels_nodata
    labels = np.where(labelled, raw_labels, 0).astype(np.int32)

    unassigned = pour_point_ids == 0
    if unassigned.any():
        own_cells = _cells(transform, nodes.geometry)[unassigned]
        own_labels = np.zeros(len(own_cells), dtype=np.int64)
        inside = _inside(own_cells, labels.shape)
        own_labels[inside] = labels[own_cells[inside, 0], own_cells[inside, 1]]
        pour_point_ids[unassigned] = own_labels
    outside = nodes.loc[pour_point_ids == 0, "node_id"].tolist()
    if outside:
        raise ValueError(f"River nodes lie outside every watershed: {outside}")

    watersheds = _watershed_polygons(labels, transform, len(unique_cells), mask_geometry)
    _check_no_gaps(watersheds, mask_geometry, cell_area / 2)
    membership = pl.DataFrame(
        {
            "node_id": nodes["node_id"].to_numpy(),
            "pour_point_id": pour_point_ids,
        }
    )
    return CatchmentDelineation(watersheds=watersheds, membership=membership)


def _validated_nodes(river_nodes: gpd.GeoDataFrame, node_id_column: str) -> gpd.GeoDataFrame:
    if river_nodes.crs is None:
        raise ValueError("river_nodes must have a CRS")
    if river_nodes.empty:
        raise ValueError("river_nodes must contain at least one node")
    if node_id_column not in river_nodes.columns:
        raise ValueError(f"river_nodes has no column {node_id_column!r}")
    if not (river_nodes.geom_type == "Point").all():
        raise ValueError("river_nodes geometries must all be Points")
    node_ids = river_nodes[node_id_column]
    if node_ids.isna().any():
        raise ValueError("river_nodes contains null node IDs")
    duplicated = node_ids[node_ids.duplicated()].unique().tolist()
    if duplicated:
        raise ValueError(f"Duplicate node IDs: {duplicated}")
    return gpd.GeoDataFrame(
        {"node_id": node_ids.to_numpy()},
        geometry=river_nodes.geometry.to_crs(TARGET_CRS).to_numpy(),
        crs=TARGET_CRS,
    )


def _clip_dem(source: Path, mask_geometry: BaseGeometry, target: Path) -> Path:
    with rasterio.open(source) as dem:
        values = dem.read(1)
        profile = dem.profile
        outside = rasterio.features.geometry_mask(
            [mask_geometry], out_shape=values.shape, transform=dem.transform
        )
    values[outside] = profile["nodata"]
    with rasterio.open(target, "w", **profile) as clipped:
        clipped.write(values, 1)
    return target


def _snap_pour_points(
    wbe: WbEnvironment,
    dem_path: Path,
    nodes: gpd.GeoDataFrame,
    workspace: Path,
    threshold_cells: float,
    snap_distance_m: float,
):
    filled = wbe.hydrology.depressions_storage.fill_depressions(
        dem=wbe.read_raster(dem_path.as_posix())
    )
    d8_pointer = wbe.hydrology.flow_routing.d8_pointer(dem=filled)
    accumulation = wbe.hydrology.flow_routing.d8_flow_accum(
        input=d8_pointer, out_type="cells", clip=False, input_is_pointer=True
    )
    streams = wbe.streams.network_extraction.extract_streams(
        flow_accumulation=accumulation, threshold=threshold_cells
    )

    nodes_path = workspace / "nodes.shp"
    snapped_path = workspace / "snapped.shp"
    nodes.assign(**{_ORDER_FIELD: np.arange(len(nodes))})[[_ORDER_FIELD, "geometry"]].to_file(
        nodes_path
    )
    snapped = wbe.hydrology.jenson_snap_pour_points(
        pour_pts=wbe.read_vector(nodes_path.as_posix()),
        streams=streams,
        snap_dist=snap_distance_m,
    )
    wbe.write_vector(vector=snapped, output_path=snapped_path.as_posix())
    snapped_points = gpd.read_file(snapped_path)
    if len(snapped_points) != len(nodes):
        raise RuntimeError(
            f"Jenson snapping returned {len(snapped_points)} points for {len(nodes)} nodes"
        )
    if _ORDER_FIELD in snapped_points.columns:
        snapped_points = snapped_points.sort_values(_ORDER_FIELD)
    return d8_pointer, snapped_points.geometry


def _cells(transform, points: gpd.GeoSeries) -> np.ndarray:
    rows, cols = rasterio.transform.rowcol(transform, points.x.to_numpy(), points.y.to_numpy())
    return np.column_stack([rows, cols]).astype(np.int64)


def _inside(cells: np.ndarray, grid_shape: tuple[int, ...]) -> np.ndarray:
    rows, cols = cells[:, 0], cells[:, 1]
    return (rows >= 0) & (rows < grid_shape[0]) & (cols >= 0) & (cols < grid_shape[1])


def _polygonal(geometry: BaseGeometry) -> BaseGeometry:
    parts = [
        part
        for part in shapely.get_parts(shapely.make_valid(geometry))
        if part.geom_type in ("Polygon", "MultiPolygon")
    ]
    return shapely.union_all(parts, grid_size=PRECISION_M) if parts else shapely.Polygon()


def _watershed_polygons(
    labels: np.ndarray,
    transform,
    pour_point_count: int,
    mask_geometry: BaseGeometry | None,
) -> gpd.GeoDataFrame:
    pieces: dict[int, list[BaseGeometry]] = {}
    for geometry, value in rasterio.features.shapes(
        labels, mask=labels > 0, transform=transform, connectivity=4
    ):
        pieces.setdefault(int(value), []).append(shape(geometry))

    geometries = []
    for pour_point_id in range(1, pour_point_count + 1):
        merged = shapely.union_all(pieces.get(pour_point_id, []), grid_size=PRECISION_M)
        if mask_geometry is not None:
            merged = shapely.intersection(merged, mask_geometry, grid_size=PRECISION_M)
        geometries.append(_polygonal(merged))

    watersheds = gpd.GeoDataFrame(
        {"pour_point_id": np.arange(1, pour_point_count + 1)},
        geometry=geometries,
        crs=TARGET_CRS,
    )
    watersheds["area_m2"] = watersheds.area
    return watersheds[["pour_point_id", "area_m2", "geometry"]]


def _check_no_gaps(
    watersheds: gpd.GeoDataFrame, mask_geometry: BaseGeometry | None, min_area: float
) -> None:
    union = shapely.union_all(watersheds.geometry.to_numpy(), grid_size=PRECISION_M)
    holes = [
        shapely.Polygon(ring)
        for polygon in shapely.get_parts(union)
        if polygon.geom_type == "Polygon"
        for ring in polygon.interiors
    ]
    if mask_geometry is not None:
        holes = [hole.intersection(mask_geometry) for hole in holes]
    gaps = [hole for hole in holes if hole.area > min_area]
    if gaps:
        raise ValueError(
            f"Watersheds leave {len(gaps)} gap(s) totalling "
            f"{sum(gap.area for gap in gaps):.1f} m2 that drain to no pour point"
        )
