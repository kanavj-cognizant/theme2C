from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import numpy as np
import polars as pl
import pytest
import rasterio
import shapely
from pydantic import ValidationError
from shapely.geometry import LineString, Point, box

import risk_map.spatial.catchment as catchment
from risk_map.data.dem import LocalDemProvider
from risk_map.spatial.catchment import CatchmentDelineation, delineate_catchments

SHAPE = (20, 20)
CELL_AREA = 100.0
NODATA = -32768.0


class FakeWhitebox:
    """Stand-in for WBE hydrology; each test scripts snapping and watershed labels."""

    instances: list["FakeWhitebox"] = []

    def __init__(self) -> None:
        self.verbose = True
        self.working_directory = ""
        self.filled_dem: str | None = None
        self.stream_threshold: float | None = None
        self.snap_distance: float | None = None
        FakeWhitebox.instances.append(self)
        self.hydrology = SimpleNamespace(
            depressions_storage=SimpleNamespace(fill_depressions=self._fill_depressions),
            flow_routing=SimpleNamespace(
                d8_pointer=lambda **kwargs: "d8",
                d8_flow_accum=lambda **kwargs: "accumulation",
            ),
            jenson_snap_pour_points=self._jenson_snap_pour_points,
            watershed_from_raster_pour_points=self._watershed,
        )
        self.streams = SimpleNamespace(
            network_extraction=SimpleNamespace(extract_streams=self._extract_streams)
        )

    @staticmethod
    def snap(points: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        return points

    @staticmethod
    def label(pour_grid: np.ndarray) -> np.ndarray:
        raise NotImplementedError("each test sets FakeWhitebox.label")

    def read_raster(self, file_name: str, file_mode: str = "r") -> str:
        return file_name

    def read_vector(self, file_name: str) -> str:
        return file_name

    def _fill_depressions(self, dem: str, **kwargs) -> str:
        self.filled_dem = dem
        return dem

    def _extract_streams(self, flow_accumulation: str, threshold: float, **kwargs) -> str:
        self.stream_threshold = threshold
        return "streams"

    def _jenson_snap_pour_points(self, pour_pts: str, streams: str, snap_dist: float, **kwargs):
        self.snap_distance = snap_dist
        return ("snapped", pour_pts)

    def _watershed(self, d8_pointer: str, pour_points: str, **kwargs):
        return ("labels", pour_points)

    def write_vector(self, vector, output_path: str, **kwargs) -> None:
        _, source = vector
        type(self).snap(gpd.read_file(source)).to_file(Path(output_path))

    def write_raster(self, raster, output_path: str, **kwargs) -> None:
        _, pour_path = raster
        with rasterio.open(pour_path) as source:
            pour_grid = source.read(1)
            profile = source.profile
        labels = np.asarray(type(self).label(pour_grid), dtype="float64")
        profile.update(dtype="float64", nodata=NODATA)
        with rasterio.open(output_path, "w", **profile) as target:
            target.write(labels, 1)


@pytest.fixture
def fake_wbe(monkeypatch):
    FakeWhitebox.instances = []
    monkeypatch.setattr(FakeWhitebox, "snap", staticmethod(lambda points: points))
    monkeypatch.setattr(catchment, "WbEnvironment", FakeWhitebox)
    return FakeWhitebox


def set_labels(fake_wbe, monkeypatch, label) -> None:
    monkeypatch.setattr(fake_wbe, "label", staticmethod(label))


def elevation(nodata_column: int | None = None) -> np.ndarray:
    values = np.tile(np.arange(SHAPE[0], 0, -1, dtype=float)[:, None], (1, SHAPE[1]))
    if nodata_column is not None:
        values[:, nodata_column] = NODATA
    return values


@pytest.fixture
def dem_provider(tmp_path, write_raster):
    return LocalDemProvider(write_raster(tmp_path / "source_dem.tif", elevation()))


def make_nodes(points: dict[int, Point], crs: str = "EPSG:27700") -> gpd.GeoDataFrame:
    nodes = gpd.GeoDataFrame(
        {"node_id": list(points)}, geometry=list(points.values()), crs="EPSG:27700"
    )
    return nodes.to_crs(crs)


def pour_id(pour_grid: np.ndarray, row: int, col: int) -> float:
    value = float(pour_grid[row, col])
    assert value > 0, f"no pour point written at cell ({row}, {col})"
    return value


def split_rows(upstream: tuple[int, int], downstream: tuple[int, int], boundary: int = 10):
    def label(pour_grid: np.ndarray) -> np.ndarray:
        labels = np.full(pour_grid.shape, pour_id(pour_grid, *downstream))
        labels[:boundary] = pour_id(pour_grid, *upstream)
        return labels

    return label


def move(moves: dict[Point, Point]):
    lookup = {(round(p.x, 3), round(p.y, 3)): q for p, q in moves.items()}

    def snap(points: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        points = points.copy()
        points["geometry"] = [
            lookup.get((round(g.x, 3), round(g.y, 3)), g) for g in points.geometry
        ]
        return points

    return snap


def pour_point_of(result: CatchmentDelineation, node_id: int):
    return result.membership.filter(pl.col("node_id") == node_id)["pour_point_id"].item()


def watersheds_by_id(result: CatchmentDelineation) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(result.watersheds.set_index("pour_point_id"))


# Result model


def model_inputs(
    pour_point_ids=(1, 2),
    membership=((10, 1), (20, 2)),
    crs="EPSG:27700",
):
    watersheds = gpd.GeoDataFrame(
        {"pour_point_id": list(pour_point_ids), "area_m2": [100.0] * len(pour_point_ids)},
        geometry=[box(i * 10, 0, i * 10 + 10, 10) for i in range(len(pour_point_ids))],
        crs=crs,
    )
    node_ids, memberships = zip(*membership, strict=True)
    return {
        "watersheds": watersheds,
        "membership": pl.DataFrame({"node_id": node_ids, "pour_point_id": memberships}),
    }


def test_result_model_accepts_consistent_tables():
    result = CatchmentDelineation(**model_inputs())

    assert result.membership.height == 2
    assert len(result.watersheds) == 2


@pytest.mark.parametrize(
    ("inputs", "message"),
    [
        (model_inputs(pour_point_ids=(1, 1)), "pour_point_id"),
        (model_inputs(membership=((10, 1), (20, 3))), "pour_point_id"),
        (model_inputs(membership=((10, 1), (20, None))), "pour_point_id"),
        (model_inputs(membership=((10, 1), (10, 2))), "node_id"),
        (model_inputs(crs="EPSG:4326"), "27700"),
    ],
    ids=["duplicate-watershed", "unknown-pour-point", "null-pour-point", "duplicate-node", "crs"],
)
def test_result_model_rejects_inconsistent_tables(inputs, message):
    with pytest.raises(ValueError, match=message):
        CatchmentDelineation(**inputs)


def test_result_model_is_frozen():
    result = CatchmentDelineation(**model_inputs())

    with pytest.raises(ValidationError):
        result.watersheds = result.watersheds.iloc[:1]  # ty: ignore[invalid-assignment]


# Input validation


def without_crs(nodes: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(nodes.drop(columns="geometry"), geometry=list(nodes.geometry))


def as_lines(nodes: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    lines = [LineString([(p.x, p.y), (p.x + 1, p.y)]) for p in nodes.geometry]
    return gpd.GeoDataFrame(nodes.drop(columns="geometry"), geometry=lines, crs=nodes.crs)


def with_ids(ids):
    def change(nodes: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(nodes.assign(node_id=ids))

    return change


@pytest.mark.parametrize(
    ("change_nodes", "kwargs", "message"),
    [
        (without_crs, {}, "CRS"),
        (lambda nodes: nodes.iloc[0:0], {}, "empty|at least one"),
        (as_lines, {}, "[Pp]oint"),
        (lambda nodes: nodes, {"node_id_column": "missing"}, "missing"),
        (with_ids([1, 1]), {}, "[Dd]uplicate"),
        (with_ids([1, None]), {}, "[Nn]ull|missing"),
        (lambda nodes: nodes, {"buffer_m": 0.0}, "buffer_m"),
        (lambda nodes: nodes, {"stream_threshold_m2": 0.0}, "stream_threshold_m2"),
        (lambda nodes: nodes, {"snap_distance_m": -1.0}, "snap_distance_m"),
    ],
    ids=[
        "no-crs",
        "empty",
        "not-points",
        "missing-id-column",
        "duplicate-ids",
        "null-ids",
        "buffer",
        "threshold",
        "snap-distance",
    ],
)
def test_invalid_inputs_raise_before_hydrology(
    fake_wbe, dem_provider, cell_centre, tmp_path, change_nodes, kwargs, message
):
    nodes = change_nodes(make_nodes({1: cell_centre(5, 10), 2: cell_centre(15, 10)}))

    with pytest.raises(ValueError, match=message):
        delineate_catchments(nodes, dem_provider, workspace_root=tmp_path, **kwargs)
    assert fake_wbe.instances == []


# Delineation behaviour with scripted hydrology


def test_each_node_maps_to_its_own_watershed(
    fake_wbe, monkeypatch, dem_provider, cell_centre, tmp_path
):
    set_labels(fake_wbe, monkeypatch, split_rows((5, 10), (15, 10)))
    nodes = make_nodes({10: cell_centre(5, 10), 20: cell_centre(15, 10)})

    result = delineate_catchments(nodes, dem_provider, workspace_root=tmp_path)

    upstream, downstream = pour_point_of(result, 10), pour_point_of(result, 20)
    watersheds = watersheds_by_id(result)
    assert sorted(result.membership["node_id"].to_list()) == [10, 20]
    assert upstream != downstream
    assert set(watersheds.index) == {upstream, downstream}
    assert watersheds.crs.to_epsg() == 27700
    assert watersheds.is_valid.all()
    assert watersheds.geom_type.isin(["Polygon", "MultiPolygon"]).all()
    assert watersheds.loc[upstream, "area_m2"] == pytest.approx(200 * CELL_AREA, rel=1e-3)
    assert watersheds["area_m2"].to_numpy() == pytest.approx(watersheds.area.to_numpy())
    assert watersheds.geometry.loc[upstream].contains(cell_centre(5, 10))
    union = shapely.union_all(watersheds.geometry.to_numpy())
    assert union.area == pytest.approx(watersheds.area.sum())


def test_stream_threshold_and_snap_distance_are_converted_from_metres(
    fake_wbe, monkeypatch, dem_provider, cell_centre, tmp_path
):
    set_labels(fake_wbe, monkeypatch, split_rows((5, 10), (15, 10)))
    nodes = make_nodes({1: cell_centre(5, 10), 2: cell_centre(15, 10)})

    delineate_catchments(
        nodes,
        dem_provider,
        stream_threshold_m2=240_000.0,
        snap_distance_m=100.0,
        workspace_root=tmp_path,
    )

    [wbe] = fake_wbe.instances
    assert wbe.stream_threshold == pytest.approx(240_000.0 / CELL_AREA)
    assert wbe.snap_distance == pytest.approx(100.0)


def test_nodes_in_the_same_cell_share_one_pour_point(
    fake_wbe, monkeypatch, dem_provider, cell_centre, tmp_path
):
    set_labels(fake_wbe, monkeypatch, split_rows((5, 10), (15, 10)))
    centre = cell_centre(5, 10)
    nodes = make_nodes({1: centre, 2: Point(centre.x + 2, centre.y - 2), 3: cell_centre(15, 10)})

    result = delineate_catchments(nodes, dem_provider, workspace_root=tmp_path)

    assert result.membership.height == 3
    assert pour_point_of(result, 1) == pour_point_of(result, 2)
    assert len(result.watersheds) == 2


def test_nodes_snapped_to_the_same_cell_share_one_pour_point(
    fake_wbe, monkeypatch, dem_provider, cell_centre, tmp_path
):
    set_labels(fake_wbe, monkeypatch, split_rows((5, 10), (15, 10)))
    monkeypatch.setattr(
        fake_wbe, "snap", staticmethod(move({cell_centre(6, 10): cell_centre(5, 10)}))
    )
    nodes = make_nodes({1: cell_centre(5, 10), 2: cell_centre(6, 10), 3: cell_centre(15, 10)})

    result = delineate_catchments(nodes, dem_provider, workspace_root=tmp_path)

    assert pour_point_of(result, 1) == pour_point_of(result, 2)
    assert pour_point_of(result, 3) != pour_point_of(result, 1)


def test_enclosed_upstream_watershed_leaves_a_filled_hole(
    fake_wbe, monkeypatch, dem_provider, cell_centre, tmp_path
):
    def nested(pour_grid: np.ndarray) -> np.ndarray:
        labels = np.full(pour_grid.shape, pour_id(pour_grid, 15, 10))
        labels[5:10, 5:10] = pour_id(pour_grid, 7, 7)
        return labels

    set_labels(fake_wbe, monkeypatch, nested)
    nodes = make_nodes({1: cell_centre(7, 7), 2: cell_centre(15, 10)})

    result = delineate_catchments(nodes, dem_provider, workspace_root=tmp_path)

    watersheds = watersheds_by_id(result)
    downstream = watersheds.geometry.loc[pour_point_of(result, 2)]
    assert len(downstream.interiors) == 1
    assert watersheds.loc[pour_point_of(result, 1), "area_m2"] == pytest.approx(
        25 * CELL_AREA, rel=1e-3
    )
    union = shapely.union_all(watersheds.geometry.to_numpy())
    assert union.geom_type == "Polygon"
    assert len(union.interiors) == 0


def test_gap_inside_watersheds_raises(fake_wbe, monkeypatch, dem_provider, cell_centre, tmp_path):
    def with_gap(pour_grid: np.ndarray) -> np.ndarray:
        labels = np.full(pour_grid.shape, pour_id(pour_grid, 15, 10))
        labels[2:5, 2:5] = NODATA
        return labels

    set_labels(fake_wbe, monkeypatch, with_gap)
    nodes = make_nodes({1: cell_centre(15, 10)})

    with pytest.raises(ValueError, match="gap"):
        delineate_catchments(nodes, dem_provider, workspace_root=tmp_path)


def test_diagonally_connected_label_becomes_one_watershed_row(
    fake_wbe, monkeypatch, dem_provider, cell_centre, tmp_path
):
    def diagonal(pour_grid: np.ndarray) -> np.ndarray:
        labels = np.full(pour_grid.shape, pour_id(pour_grid, 15, 10))
        labels[2, 2] = labels[3, 3] = pour_id(pour_grid, 2, 2)
        return labels

    set_labels(fake_wbe, monkeypatch, diagonal)
    nodes = make_nodes({1: cell_centre(2, 2), 2: cell_centre(15, 10)})

    result = delineate_catchments(nodes, dem_provider, workspace_root=tmp_path)

    watersheds = watersheds_by_id(result)
    assert len(watersheds) == 2
    upstream = watersheds.loc[pour_point_of(result, 1)]
    assert upstream.geometry.geom_type == "MultiPolygon"
    assert upstream["area_m2"] == pytest.approx(2 * CELL_AREA, rel=1e-2)


def test_node_snapped_onto_nodata_uses_label_at_its_own_location(
    fake_wbe, monkeypatch, tmp_path, write_raster, cell_centre
):
    provider = LocalDemProvider(write_raster(tmp_path / "dem.tif", elevation(nodata_column=19)))
    set_labels(fake_wbe, monkeypatch, split_rows((5, 10), (15, 10)))
    monkeypatch.setattr(
        fake_wbe, "snap", staticmethod(move({cell_centre(5, 18): cell_centre(5, 19)}))
    )
    nodes = make_nodes({1: cell_centre(5, 10), 2: cell_centre(15, 10), 3: cell_centre(5, 18)})

    result = delineate_catchments(nodes, provider, workspace_root=tmp_path)

    assert pour_point_of(result, 3) == pour_point_of(result, 1)


def test_node_outside_every_watershed_raises_with_its_id(
    fake_wbe, monkeypatch, tmp_path, write_raster, cell_centre
):
    provider = LocalDemProvider(write_raster(tmp_path / "dem.tif", elevation(nodata_column=19)))

    def unlabelled_edge(pour_grid: np.ndarray) -> np.ndarray:
        labels = split_rows((5, 10), (15, 10))(pour_grid)
        labels[:, 19] = NODATA
        return labels

    set_labels(fake_wbe, monkeypatch, unlabelled_edge)
    nodes = make_nodes({1: cell_centre(5, 10), 2: cell_centre(15, 10), 47: cell_centre(5, 19)})

    with pytest.raises(ValueError, match="47"):
        delineate_catchments(nodes, provider, workspace_root=tmp_path)


def test_mask_clips_dem_before_hydrology_and_clips_watersheds(
    fake_wbe, monkeypatch, dem_provider, cell_centre, tmp_path
):
    set_labels(fake_wbe, monkeypatch, split_rows((5, 5), (15, 5)))
    left = cell_centre(0, 0).x - 5.0
    mask = gpd.GeoDataFrame(
        geometry=[box(left, cell_centre(19, 0).y - 5.0, left + 100.0, cell_centre(0, 0).y + 5.0)],
        crs="EPSG:27700",
    )
    nodes = make_nodes({1: cell_centre(5, 5), 2: cell_centre(15, 5)})

    result = delineate_catchments(nodes, dem_provider, mask=mask, workspace_root=tmp_path)

    [wbe] = fake_wbe.instances
    assert wbe.filled_dem is not None
    assert not wbe.filled_dem.endswith("source_dem.tif")
    watersheds = watersheds_by_id(result)
    # Vertices on the mask edge may move by up to the 0.01 m precision grid.
    assert watersheds.within(mask.geometry.iloc[0].buffer(0.01)).all()
    assert watersheds["area_m2"].to_numpy() == pytest.approx([100 * CELL_AREA] * 2, rel=1e-3)


def test_mask_clipped_dem_has_nodata_outside_mask(
    fake_wbe, monkeypatch, dem_provider, cell_centre, tmp_path
):
    clipped = {}

    def capture_fill(self, dem, **kwargs):
        with rasterio.open(dem) as dataset:
            clipped["values"] = dataset.read(1)
            clipped["nodata"] = dataset.nodata
        return dem

    monkeypatch.setattr(FakeWhitebox, "_fill_depressions", capture_fill)
    set_labels(fake_wbe, monkeypatch, split_rows((5, 5), (15, 5)))
    left = cell_centre(0, 0).x - 5.0
    mask = gpd.GeoDataFrame(
        geometry=[box(left, cell_centre(19, 0).y - 5.0, left + 100.0, cell_centre(0, 0).y + 5.0)],
        crs="EPSG:27700",
    )
    nodes = make_nodes({1: cell_centre(5, 5), 2: cell_centre(15, 5)})

    delineate_catchments(nodes, dem_provider, mask=mask, workspace_root=tmp_path)

    values = clipped["values"]
    assert (values[:, 10:] == clipped["nodata"]).all()
    assert (values[:, :10] != clipped["nodata"]).all()


class SpyProvider:
    def __init__(self, inner: LocalDemProvider) -> None:
        self.inner = inner
        self.calls: list[tuple[tuple[float, float, float, float], str]] = []

    def fetch(self, bounds, crs, output_path):
        self.calls.append((tuple(bounds), crs))
        return self.inner.fetch(bounds, crs, output_path)


def test_provider_receives_buffered_node_bounds_without_mask(
    fake_wbe, monkeypatch, dem_provider, cell_centre, tmp_path
):
    set_labels(fake_wbe, monkeypatch, split_rows((5, 10), (15, 10)))
    nodes = make_nodes({1: cell_centre(5, 10), 2: cell_centre(15, 10)})
    spy = SpyProvider(dem_provider)

    delineate_catchments(nodes, spy, buffer_m=500.0, workspace_root=tmp_path)

    [(bounds, crs)] = spy.calls
    minx, miny, maxx, maxy = nodes.total_bounds
    assert crs == "EPSG:27700"
    assert bounds == pytest.approx((minx - 500.0, miny - 500.0, maxx + 500.0, maxy + 500.0))


def test_provider_receives_mask_bounds_with_mask(
    fake_wbe, monkeypatch, dem_provider, cell_centre, tmp_path
):
    set_labels(fake_wbe, monkeypatch, split_rows((5, 5), (15, 5)))
    left = cell_centre(0, 0).x - 5.0
    mask = gpd.GeoDataFrame(
        geometry=[box(left, cell_centre(19, 0).y - 5.0, left + 100.0, cell_centre(0, 0).y + 5.0)],
        crs="EPSG:27700",
    )
    nodes = make_nodes({1: cell_centre(5, 5), 2: cell_centre(15, 5)})
    spy = SpyProvider(dem_provider)

    delineate_catchments(nodes, spy, mask=mask, workspace_root=tmp_path)

    [(bounds, _)] = spy.calls
    assert bounds == pytest.approx(tuple(mask.total_bounds))


def test_nodes_in_another_crs_are_reprojected(
    fake_wbe, monkeypatch, dem_provider, cell_centre, tmp_path
):
    set_labels(fake_wbe, monkeypatch, split_rows((5, 10), (15, 10)))
    nodes = make_nodes({1: cell_centre(5, 10), 2: cell_centre(15, 10)}, crs="EPSG:4326")

    result = delineate_catchments(nodes, dem_provider, workspace_root=tmp_path)

    watersheds = watersheds_by_id(result)
    assert watersheds.crs.to_epsg() == 27700
    assert watersheds.geometry.loc[pour_point_of(result, 1)].contains(cell_centre(5, 10))
    assert watersheds.geometry.loc[pour_point_of(result, 2)].contains(cell_centre(15, 10))


def test_result_does_not_depend_on_row_order_or_index(
    fake_wbe, monkeypatch, dem_provider, cell_centre, tmp_path
):
    set_labels(fake_wbe, monkeypatch, split_rows((5, 10), (15, 10)))
    nodes = make_nodes({1: cell_centre(5, 10), 2: cell_centre(15, 10), 3: cell_centre(16, 4)})
    shuffled = nodes.iloc[[2, 0, 1]].set_axis([70, 30, 50])

    def area_by_node(result: CatchmentDelineation) -> dict[int, float]:
        areas = watersheds_by_id(result)["area_m2"]
        return {
            row["node_id"]: areas.loc[row["pour_point_id"]]
            for row in result.membership.iter_rows(named=True)
        }

    first = delineate_catchments(nodes, dem_provider, workspace_root=tmp_path)
    second = delineate_catchments(shuffled, dem_provider, workspace_root=tmp_path)

    assert area_by_node(first) == pytest.approx(area_by_node(second))


def test_watershed_vertices_are_snapped_to_centimetre_grid(
    fake_wbe, monkeypatch, dem_provider, cell_centre, tmp_path
):
    set_labels(fake_wbe, monkeypatch, split_rows((5, 10), (15, 10)))
    nodes = make_nodes({1: cell_centre(5, 10), 2: cell_centre(15, 10)})

    result = delineate_catchments(nodes, dem_provider, workspace_root=tmp_path)

    coordinates = shapely.get_coordinates(result.watersheds.geometry.to_numpy())
    np.testing.assert_allclose(coordinates * 100, np.round(coordinates * 100), atol=1e-6)


def test_each_call_uses_and_removes_its_own_workspace(
    fake_wbe, monkeypatch, dem_provider, cell_centre, tmp_path
):
    set_labels(fake_wbe, monkeypatch, split_rows((5, 10), (15, 10)))
    nodes = make_nodes({1: cell_centre(5, 10), 2: cell_centre(15, 10)})

    delineate_catchments(nodes, dem_provider, workspace_root=tmp_path)
    delineate_catchments(nodes, dem_provider, workspace_root=tmp_path)

    workspaces = [instance.working_directory for instance in fake_wbe.instances]
    assert len(workspaces) == 2
    assert workspaces[0] != workspaces[1]
    assert not any(Path(workspace).exists() for workspace in workspaces)
    assert all(Path(workspace).parent == tmp_path for workspace in workspaces)
