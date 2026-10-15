from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
from shapely.geometry import Point, Polygon

from risk_map.spatial.catchment import delineate_catchments


class FakeRaster:
    def to_tif(self, path: str) -> None:
        Path(path).write_bytes(b"raster")


class FakeWhitebox:
    instances: list["FakeWhitebox"] = []

    def __init__(self) -> None:
        self.verbose = True
        self._working_directory = ""
        self.instances.append(self)
        self.hydrology = SimpleNamespace(
            depressions_storage=SimpleNamespace(fill_depressions=lambda dem: dem),
            flow_routing=SimpleNamespace(
                d8_pointer=lambda dem: dem,
                d8_flow_accum=lambda **kwargs: object(),
            ),
            jenson_snap_pour_points=lambda **kwargs: object(),
            watersheds_basins=SimpleNamespace(watershed=lambda **kwargs: FakeRaster()),
        )
        self.streams = SimpleNamespace(
            network_extraction=SimpleNamespace(extract_streams=lambda **kwargs: object())
        )
        self.conversion = SimpleNamespace(
            raster_vector_conversion=SimpleNamespace(
                raster_to_vector_polygons=self._raster_to_vector_polygons
            )
        )

    @property
    def working_directory(self) -> str:
        return self._working_directory

    @working_directory.setter
    def working_directory(self, value: str) -> None:
        self._working_directory = value

    def read_raster(self, path: str, file_mode: str) -> str:
        assert file_mode == "m"
        return path

    def read_vector(self, path: str) -> str:
        return path

    def write_raster(self, raster: FakeRaster, output_path: str) -> None:
        raster.to_tif(output_path)

    def write_vector(self, vector: object, output_path: str) -> None:
        pass

    def _raster_to_vector_polygons(self, input: str) -> object:
        raster_path = Path(input)
        assert raster_path.is_absolute()
        assert raster_path.is_file()
        return object()


def test_delineation_uses_isolated_wbe_workspaces_and_raster_path(monkeypatch, tmp_path):
    import risk_map.spatial.catchment as catchment

    FakeWhitebox.instances.clear()
    monkeypatch.setattr(catchment, "WbEnvironment", FakeWhitebox)
    monkeypatch.setattr(catchment.elevatr, "get_elev_raster", lambda *args, **kwargs: FakeRaster())
    monkeypatch.setattr(gpd.GeoDataFrame, "to_file", lambda *args, **kwargs: None)
    polygons = gpd.GeoDataFrame(
        {"VALUE": [1]}, geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])], crs="EPSG:4326"
    )
    monkeypatch.setattr(gpd, "read_file", lambda *args, **kwargs: polygons.copy())
    risk_map = gpd.GeoDataFrame(geometry=[Point(400000, 500000)], crs="EPSG:27700")

    first = delineate_catchments(risk_map, tile_folder=tmp_path / "tiles", workspace_root=tmp_path)
    second = delineate_catchments(risk_map, tile_folder=tmp_path / "tiles", workspace_root=tmp_path)

    assert first.crs == risk_map.crs
    assert second.crs == risk_map.crs
    workspaces = [Path(instance.working_directory) for instance in FakeWhitebox.instances]
    assert len(workspaces) == 2
    assert workspaces[0] != workspaces[1]
    assert all(not path.exists() for path in workspaces)
