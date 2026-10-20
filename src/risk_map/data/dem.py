import shutil
from pathlib import Path
from typing import Protocol, runtime_checkable

import elevatr
import rasterio
from pyproj import CRS
from pyproj.transformer import Transformer

DEM_NODATA = -32768.0

Bounds = tuple[float, float, float, float]


@runtime_checkable
class DemProvider(Protocol):
    """Source of digital elevation models for hydrological delineation."""

    def fetch(self, bounds: Bounds, crs: str, output_path: Path) -> Path:
        """Write a GeoTIFF in ``crs`` covering ``bounds`` with its nodata value declared."""
        ...


class LocalDemProvider:
    """Serve a pre-existing GeoTIFF, e.g. a fixture or a pre-downloaded DEM.

    The file is copied unchanged; it must already use the requested CRS, declare
    nodata, and cover the requested bounds.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def fetch(self, bounds: Bounds, crs: str, output_path: Path) -> Path:
        with rasterio.open(self.path) as dem:
            source_crs = dem.crs
            nodata = dem.nodata
        if source_crs is None or not CRS.from_user_input(source_crs.to_wkt()).equals(
            CRS.from_user_input(crs), ignore_axis_order=True
        ):
            raise ValueError(f"DEM CRS {source_crs} does not match requested CRS {crs}")
        if nodata is None:
            raise ValueError(f"DEM {self.path} does not declare a nodata value")
        shutil.copyfile(self.path, output_path)
        return output_path


class ElevatrDemProvider:
    """Download (and cache) terrain tiles with elevatr, reprojected to the requested CRS."""

    def __init__(self, tile_folder: Path, zoom: int = 12) -> None:
        self.tile_folder = Path(tile_folder)
        self.zoom = zoom

    def fetch(self, bounds: Bounds, crs: str, output_path: Path) -> Path:
        self.tile_folder.mkdir(parents=True, exist_ok=True)
        wgs84_bounds = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform_bounds(
            *bounds
        )
        elevation = elevatr.get_elev_raster(
            wgs84_bounds,
            zoom=self.zoom,
            crs=crs,
            use_cache=True,
            delete_cache=False,
            cache_folder=str(self.tile_folder),
            verbose=False,
        )
        if elevation is None:
            raise RuntimeError(f"Elevation data could not be retrieved for bounds {bounds}")
        elevation.to_tif(Path(output_path).as_posix())
        # elevatr declares -9999 but fills reprojected areas with -32768.
        with rasterio.open(output_path, "r+") as dataset:
            dataset.nodata = DEM_NODATA
        return output_path
