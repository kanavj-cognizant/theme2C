from pathlib import Path

import numpy as np
import pytest
import rasterio
from pyproj.transformer import Transformer

import risk_map.data.dem as dem_module
from risk_map.data.dem import DEM_NODATA, DemProvider, ElevatrDemProvider, LocalDemProvider

BOUNDS = (400_000.0, 500_000.0, 400_200.0, 500_200.0)


def test_providers_satisfy_protocol(tmp_path):
    assert isinstance(LocalDemProvider(tmp_path / "dem.tif"), DemProvider)
    assert isinstance(ElevatrDemProvider(tmp_path / "tiles"), DemProvider)


def test_local_provider_writes_geotiff_with_crs_and_declared_nodata(tmp_path, write_raster):
    elevation = np.arange(400.0).reshape(20, 20)
    source = write_raster(tmp_path / "source.tif", elevation, nodata=-9999.0)
    output = tmp_path / "dem.tif"

    result = LocalDemProvider(source).fetch(BOUNDS, "EPSG:27700", output)

    assert result == output
    with rasterio.open(output) as dem:
        assert dem.crs.to_epsg() == 27700
        assert dem.nodata == -9999.0
        np.testing.assert_array_equal(dem.read(1), elevation)


@pytest.mark.parametrize(
    ("crs", "nodata", "message"),
    [("EPSG:4326", DEM_NODATA, "CRS"), ("EPSG:27700", None, "nodata")],
    ids=["wrong-crs", "missing-nodata"],
)
def test_local_provider_rejects_wrong_crs_or_missing_nodata(
    tmp_path, write_raster, crs, nodata, message
):
    source = write_raster(tmp_path / "source.tif", np.ones((5, 5)), crs=crs, nodata=nodata)

    with pytest.raises(ValueError, match=message):
        LocalDemProvider(source).fetch(BOUNDS, "EPSG:27700", tmp_path / "dem.tif")


class FakeElevation:
    def __init__(self, write_raster) -> None:
        self._write_raster = write_raster

    def to_tif(self, path: str) -> None:
        # elevatr declares -9999 while its reprojection fill is -32768.
        self._write_raster(Path(path), np.ones((5, 5)), nodata=-9999.0)


def test_elevatr_provider_requests_buffered_wgs84_bbox_and_declares_nodata(
    monkeypatch, tmp_path, write_raster
):
    calls = {}

    def fake_get_elev_raster(bbx, **kwargs):
        calls.update(bbx=bbx, **kwargs)
        return FakeElevation(write_raster)

    monkeypatch.setattr(dem_module.elevatr, "get_elev_raster", fake_get_elev_raster)
    tiles = tmp_path / "tiles"

    output = ElevatrDemProvider(tiles, zoom=11).fetch(BOUNDS, "EPSG:27700", tmp_path / "dem.tif")

    expected_bbox = Transformer.from_crs(
        "EPSG:27700", "EPSG:4326", always_xy=True
    ).transform_bounds(*BOUNDS)
    assert tiles.is_dir()
    assert calls["bbx"] == pytest.approx(expected_bbox)
    assert calls["zoom"] == 11
    assert calls["crs"] == "EPSG:27700"
    assert calls["cache_folder"] == str(tiles)
    with rasterio.open(output) as dem:
        assert dem.nodata == DEM_NODATA


def test_elevatr_provider_raises_when_no_elevation_is_returned(monkeypatch, tmp_path):
    monkeypatch.setattr(dem_module.elevatr, "get_elev_raster", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="[Ee]levation"):
        ElevatrDemProvider(tmp_path / "tiles").fetch(BOUNDS, "EPSG:27700", tmp_path / "dem.tif")
