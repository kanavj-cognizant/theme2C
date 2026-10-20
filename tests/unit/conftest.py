from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Point

CELL_SIZE = 10.0
# Off-grid origin so tests can tell whether vertices are snapped to the 0.01 m grid.
ORIGIN = (400_000.004, 500_200.007)
DEFAULT_NODATA = -32768.0

RasterWriter = Callable[..., Path]


def write_geotiff(
    path: Path,
    array: np.ndarray,
    *,
    crs: str = "EPSG:27700",
    nodata: float | None = DEFAULT_NODATA,
) -> Path:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=array.shape[0],
        width=array.shape[1],
        count=1,
        dtype="float64",
        crs=crs,
        transform=from_origin(*ORIGIN, CELL_SIZE, CELL_SIZE),
        nodata=nodata,
    ) as dataset:
        dataset.write(array.astype("float64"), 1)
    return path


@pytest.fixture
def write_raster() -> RasterWriter:
    return write_geotiff


@pytest.fixture
def cell_centre() -> Callable[[int, int], Point]:
    def centre(row: int, col: int) -> Point:
        return Point(ORIGIN[0] + (col + 0.5) * CELL_SIZE, ORIGIN[1] - (row + 0.5) * CELL_SIZE)

    return centre
