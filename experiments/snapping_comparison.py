"""Compare metre-based Jenson delineation with the legacy Browney baseline.

Bounded experiment for refactor/04-spatial.md §6.7; not part of the package API.
Stream burning is deferred (WBE 2.0.6 rasterize_streams is row-flipped).
Runs on a DEM clipped to the catchment mask at several stream thresholds.

    .venv/bin/python experiments/snapping_comparison.py

Writes GeoPackages and snapping_comparison.png to experiments/output/ (gitignored).
"""

import pathlib
import sys
import tempfile
import time

import elevatr
import geopandas as gpd
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
import rasterio
import rasterio.features
import shapely
from whitebox_workflows import Raster, WbEnvironment

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import RiskMap  # noqa: E402
from utils.rivers import linear_flow  # noqa: E402

FIXTURES_DIR = ROOT / "tests" / "fixtures" / "browney"
LEGACY_DIR = FIXTURES_DIR / "legacy_catchments"
OUTPUT_DIR = pathlib.Path(__file__).resolve().parent / "output"
TILE_DIR = ROOT / "data" / "tiles"
CRS = "EPSG:27700"
BUFFER_DEGREES = 0.15
STREAM_THRESHOLDS_M2 = (240_000.0, 480_000.0, 960_000.0)
SNAP_DISTANCE_M = 100.0
REPROJECTION_FILL = -32768.0
OUTLIER_RATIO = 0.5


def browney_mask() -> gpd.GeoDataFrame:
    cmnt = gpd.read_file(FIXTURES_DIR / "catchment.geojson")
    cmnt["geometry"] = cmnt.make_valid()
    cmnt = cmnt[["geometry"]][cmnt.geometry.type == "Polygon"].to_crs(CRS)
    return gpd.GeoDataFrame(geometry=[cmnt.geometry.union_all()], crs=CRS)


def legacy_network(rivers: gpd.GeoDataFrame, mask: gpd.GeoDataFrame):
    riskmap = RiskMap.RiskMap(
        riversdf=rivers,
        id_col="RivID",
        resolution=300.0,
        river_flow_func=lambda x: linear_flow(x["US_Accum"]),
        catchment_mask=mask,
    )
    graph = riskmap.get_river_network()
    nodes = gpd.GeoDataFrame(
        {"node_id": [data["nodeID"] for _, data in graph.nodes(data=True)]},
        geometry=[shapely.Point(key) for key in graph.nodes],
        crs=CRS,
    )
    edges = gpd.GeoDataFrame(
        {"downstream_node_id": [graph.nodes[v]["nodeID"] for _, v in graph.edges]},
        geometry=[data["geometry"] for _, _, data in graph.edges(data=True)],
        crs=CRS,
    )
    return graph, nodes, edges


def fetch_dem(nodes: gpd.GeoDataFrame, path: pathlib.Path) -> None:
    bounds = nodes.to_crs(epsg=4326).total_bounds
    bounds[:2] -= BUFFER_DEGREES
    bounds[2:] += BUFFER_DEGREES
    elevation = elevatr.get_elev_raster(
        tuple(bounds),
        zoom=12,
        use_cache=True,
        delete_cache=False,
        crs=CRS,
        cache_folder=str(TILE_DIR),
        verbose=False,
    )
    elevation.to_tif(path.as_posix())  # ty: ignore[unresolved-attribute]
    # elevatr declares nodata -9999 but fills reprojected corners with -32768.
    with rasterio.open(path, "r+") as dataset:
        dataset.nodata = REPROJECTION_FILL


def clip_dem(source: pathlib.Path, target: pathlib.Path, mask: gpd.GeoDataFrame) -> None:
    with rasterio.open(source) as src:
        data = src.read(1)
        profile = src.profile
        outside = rasterio.features.geometry_mask(
            mask.geometry, out_shape=data.shape, transform=src.transform
        )
    data[outside] = REPROJECTION_FILL
    with rasterio.open(target, "w", **profile) as dst:
        dst.write(data, 1)


def cells(raster: Raster, points: gpd.GeoSeries) -> np.ndarray:
    return np.array(
        [(raster.get_row_from_y(p.y), raster.get_column_from_x(p.x)) for p in points], dtype=int
    )


def values_at(array: np.ndarray, rc: np.ndarray) -> np.ndarray:
    rows = np.clip(rc[:, 0], 0, array.shape[0] - 1)
    cols = np.clip(rc[:, 1], 0, array.shape[1] - 1)
    inside = (rc[:, 0] == rows) & (rc[:, 1] == cols)
    return np.where(inside, array[rows, cols], np.nan)


def watersheds_from_cells(wbe, d8, node_cells: np.ndarray, workspace: pathlib.Path, name: str):
    """Deduplicate pour-point cells, label with our own IDs, run raster-pour-point watershed."""
    unique_cells, pour_point_index = np.unique(node_cells, axis=0, return_inverse=True)
    pour_point_ids = pour_point_index.ravel() + 1
    grid = np.zeros((d8.metadata().rows, d8.metadata().columns), dtype=np.float64)
    grid[unique_cells[:, 0], unique_cells[:, 1]] = np.arange(1, len(unique_cells) + 1)
    pour_path = workspace / f"{name}_pour_points.tif"
    Raster.from_numpy(grid, d8, output_path=pour_path.as_posix())
    labels_raster = wbe.hydrology.watershed_from_raster_pour_points(
        d8_pointer=d8, pour_points=wbe.read_raster(pour_path.as_posix())
    )
    labels = labels_raster.to_numpy().astype(np.float64)
    labels[labels == labels_raster.metadata().nodata] = np.nan

    raster_path = workspace / f"{name}_watersheds.tif"
    vector_path = workspace / f"{name}_watersheds.shp"
    wbe.write_raster(raster=labels_raster, output_path=raster_path.as_posix())
    polygons = wbe.conversion.raster_vector_conversion.raster_to_vector_polygons(
        input=raster_path.as_posix()
    )
    wbe.write_vector(vector=polygons, output_path=vector_path.as_posix())
    watersheds = gpd.read_file(vector_path).set_crs(CRS, allow_override=True)
    # WBE raster_to_vector_polygons emits invalid rings; repair before dissolving.
    watersheds["geometry"] = watersheds.make_valid()
    watersheds = (
        watersheds[watersheds["VALUE"] > 0]
        .dissolve(by="VALUE", as_index=False)
        .rename(columns={"VALUE": "pour_point_id"})[["pour_point_id", "geometry"]]
    )
    watersheds["geometry"] = watersheds.make_valid()
    return pour_point_ids, labels, watersheds


def metrics(name, nodes, edges, pour_point_ids, on_stream, labels, dem, watersheds, mask, seconds):
    node_pp = dict(zip(nodes["node_id"], pour_point_ids, strict=True))
    shared_counts = pd.Series(pour_point_ids).value_counts()
    midpoints = edges.geometry.interpolate(0.5, normalized=True)
    midpoint_labels = values_at(labels, cells(dem, midpoints))
    expected = edges["downstream_node_id"].map(node_pp).to_numpy()
    total_area = watersheds.area.sum()
    outside = watersheds.geometry.difference(mask.geometry.iloc[0]).area.sum()
    return {
        "method": name,
        "nodes": len(nodes),
        "pour_points": int(len(shared_counts)),
        "M1_nodes_sharing_cell": int(shared_counts[shared_counts > 1].sum()),
        "M2_nodes_off_stream": int((~on_stream).sum()),
        "M3_wrong_branch_segments": int((midpoint_labels != expected).sum()),
        "M3_segments": len(edges),
        "M4_leakage_fraction": round(float(outside / total_area), 4),
        "total_area_km2": round(float(total_area / 1e6), 2),
        "M5_seconds": round(seconds, 1),
    }


def save(name, nodes, pour_point_ids, watersheds, comparison):
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / f"snapping_{name}.gpkg"
    watersheds.to_file(path, layer="watersheds", driver="GPKG")
    nodes.assign(pour_point_id=pour_point_ids).merge(comparison, on="node_id").to_file(
        path, layer="nodes", driver="GPKG"
    )


def cumulative_area(graph, node_label: dict, label_area: dict) -> dict:
    """Sum distinct watershed areas over each node and all its upstream nodes."""
    result = {}
    for key in graph.nodes:
        members = nx.ancestors(graph, key) | {key}
        labels = {node_label.get(graph.nodes[m]["nodeID"]) for m in members} - {None}
        result[graph.nodes[key]["nodeID"]] = sum(label_area.get(label, 0.0) for label in labels)
    return result


def legacy_comparison(graph, nodes, pour_point_ids, watersheds, mask):
    baseline_nodes = gpd.read_parquet(LEGACY_DIR / "nodes.parquet").set_index("node_id")
    aligned = baseline_nodes.geometry.loc[nodes["node_id"]].reset_index(drop=True)
    if not aligned.geom_equals_exact(nodes.geometry, tolerance=1e-6).all():
        raise RuntimeError("Experiment nodes do not match the legacy baseline nodes")

    legacy = gpd.read_parquet(LEGACY_DIR / "wbe_polygons.parquet").to_crs(CRS)
    legacy["geometry"] = legacy.make_valid()
    legacy = legacy.dissolve(by="VALUE", as_index=False)
    legacy_area = dict(zip(legacy["VALUE"] - 1, legacy.area, strict=True))
    new_area = dict(zip(watersheds["pour_point_id"], watersheds.area, strict=True))

    comparison = pd.DataFrame(
        {
            "node_id": nodes["node_id"],
            "legacy_area_m2": nodes["node_id"].map(legacy_area).fillna(0.0),
            "new_area_m2": pd.Series(pour_point_ids).map(new_area).fillna(0.0),
        }
    )
    comparison["ratio"] = comparison["new_area_m2"] / comparison["legacy_area_m2"]
    outlier = (comparison["ratio"] - 1).abs() > OUTLIER_RATIO
    comparison["outlier"] = outlier | comparison["legacy_area_m2"].eq(0)

    legacy_labels = {node_id: node_id for node_id in legacy_area}
    new_labels = dict(zip(nodes["node_id"], pour_point_ids, strict=True))
    legacy_cumulative = cumulative_area(graph, legacy_labels, legacy_area)
    new_cumulative = cumulative_area(graph, new_labels, new_area)
    comparison["legacy_cumulative_m2"] = comparison["node_id"].map(legacy_cumulative)
    comparison["new_cumulative_m2"] = comparison["node_id"].map(new_cumulative)
    comparison["cumulative_ratio"] = (
        comparison["new_cumulative_m2"] / comparison["legacy_cumulative_m2"]
    )
    comparison["cumulative_outlier"] = (comparison["cumulative_ratio"] - 1).abs() > OUTLIER_RATIO

    mask_geometry = mask.geometry.iloc[0]
    summary = {
        "legacy_total_km2": legacy.area.sum() / 1e6,
        "new_total_km2": watersheds.area.sum() / 1e6,
        "legacy_outside_mask": legacy.difference(mask_geometry).area.sum() / legacy.area.sum(),
        "new_outside_mask": watersheds.difference(mask_geometry).area.sum() / watersheds.area.sum(),
        "legacy_nodes_without_polygon": int(comparison["legacy_area_m2"].eq(0).sum()),
        "new_nodes_sharing_pour_point": int(pd.Series(pour_point_ids).duplicated(keep=False).sum()),
        "median_area_ratio": comparison["ratio"].replace(np.inf, np.nan).median(),
        f"nodes_differing_>{OUTLIER_RATIO:.0%}": int(comparison["outlier"].sum()),
        "median_cumulative_ratio": comparison["cumulative_ratio"].median(),
        f"cumulative_differing_>{OUTLIER_RATIO:.0%}": int(comparison["cumulative_outlier"].sum()),
    }
    return comparison, summary


def run_jenson(wbe, dem, nodes, workspace, name, threshold_m2):
    filled = wbe.hydrology.depressions_storage.fill_depressions(dem=dem)
    d8 = wbe.hydrology.flow_routing.d8_pointer(dem=filled)
    accumulation = wbe.hydrology.flow_routing.d8_flow_accum(
        input=d8, out_type="cells", clip=False, input_is_pointer=True
    )
    meta = dem.metadata()
    threshold_cells = threshold_m2 / (meta.resolution_x * meta.resolution_y)
    streams = wbe.streams.network_extraction.extract_streams(
        flow_accumulation=accumulation, threshold=threshold_cells
    )
    points_path = workspace / f"{name}_nodes.shp"
    snapped_path = workspace / f"{name}_snapped.shp"
    nodes.to_file(points_path)
    snapped = wbe.hydrology.jenson_snap_pour_points(
        pour_pts=wbe.read_vector(points_path.as_posix()),
        streams=streams,
        snap_dist=SNAP_DISTANCE_M,
    )
    wbe.write_vector(vector=snapped, output_path=snapped_path.as_posix())
    snapped_points = gpd.read_file(snapped_path)
    if "node_id" in snapped_points.columns:
        snapped_points = snapped_points.set_index("node_id").loc[nodes["node_id"]]
    node_cells = cells(dem, snapped_points.geometry)
    stream_values = values_at(streams.to_numpy().astype(np.float64), node_cells)
    on_stream = np.isfinite(stream_values) & (stream_values > 0)
    on_stream &= stream_values != streams.metadata().nodata
    return d8, node_cells, on_stream


def plot(results, nodes, mask, rivers):
    fig, axes = plt.subplots(len(results), 2, figsize=(14, 6.5 * len(results)), squeeze=False)
    for row, (name, (watersheds, comparison)) in enumerate(results.items()):
        map_ax, scatter_ax = axes[row]
        mask.boundary.plot(ax=map_ax, color="black", linewidth=1.2)
        watersheds.boundary.plot(ax=map_ax, color="tab:blue", linewidth=0.3)
        rivers.plot(ax=map_ax, color="tab:cyan", linewidth=0.6)
        flagged = nodes.merge(comparison, on="node_id")
        flagged[~flagged["cumulative_outlier"]].plot(ax=map_ax, color="grey", markersize=4)
        flagged[flagged["cumulative_outlier"]].plot(ax=map_ax, color="red", markersize=12)
        map_ax.set_xlim(*mask.total_bounds[[0, 2]] + np.array([-3000, 3000]))
        map_ax.set_ylim(*mask.total_bounds[[1, 3]] + np.array([-3000, 3000]))
        map_ax.set_title(f"{name}: watersheds, red = cumulative area off by >50%")
        map_ax.set_axis_off()

        legacy = comparison["legacy_cumulative_m2"] / 1e6
        new = comparison["new_cumulative_m2"] / 1e6
        scatter_ax.scatter(
            legacy, new, s=8, c=comparison["cumulative_outlier"].map({True: "red", False: "grey"})
        )
        limits = np.array([1e-4, max(legacy.max(), new.max()) * 2])
        scatter_ax.plot(limits, limits, color="black", linewidth=0.8)
        scatter_ax.fill_between(limits, limits * 0.5, limits * 1.5, color="grey", alpha=0.15)
        scatter_ax.set(
            xscale="log",
            yscale="log",
            xlim=limits,
            ylim=limits,
            xlabel="legacy cumulative upstream area (km²)",
            ylabel="new cumulative upstream area (km²)",
            title=f"{name}: per node, band = ±50%",
        )
    fig.tight_layout()
    path = OUTPUT_DIR / "snapping_comparison.png"
    fig.savefig(path, dpi=110)
    print(f"Saved {path}")


def main() -> None:
    mask = browney_mask()
    rivers = gpd.read_parquet(FIXTURES_DIR / "rivers.parquet").to_crs(CRS)
    graph, nodes, edges = legacy_network(rivers, mask)

    rows = []
    summaries = {}
    results = {}
    with tempfile.TemporaryDirectory(prefix="snapping_experiment_") as temp_dir:
        workspace = pathlib.Path(temp_dir)
        wbe = WbEnvironment()
        wbe.verbose = False  # ty: ignore[invalid-assignment]
        wbe.working_directory = workspace.as_posix()  # ty: ignore[invalid-assignment]

        dem_path = workspace / "dem.tif"
        clipped_path = workspace / "dem_clipped.tif"
        fetch_dem(nodes, dem_path)
        clip_dem(dem_path, clipped_path, mask)
        dem = wbe.read_raster(clipped_path.as_posix())

        for threshold_m2 in STREAM_THRESHOLDS_M2:
            name = f"clipped_{threshold_m2 / 1e3:.0f}k_m2"
            start = time.perf_counter()
            d8, node_cells, on_stream = run_jenson(wbe, dem, nodes, workspace, name, threshold_m2)
            pour_point_ids, labels, watersheds = watersheds_from_cells(
                wbe, d8, node_cells, workspace, name
            )
            seconds = time.perf_counter() - start
            rows.append(
                metrics(
                    name,
                    nodes,
                    edges,
                    pour_point_ids,
                    on_stream,
                    labels,
                    dem,
                    watersheds,
                    mask,
                    seconds,
                )
            )
            comparison, summaries[name] = legacy_comparison(
                graph, nodes, pour_point_ids, watersheds, mask
            )
            save(name, nodes, pour_point_ids, watersheds, comparison)
            results[name] = (watersheds, comparison)

    print(pd.DataFrame(rows).set_index("method").T.to_string())
    print(pd.DataFrame(summaries).round(3).to_string())
    for name, (_, comparison) in results.items():
        worst = comparison.assign(deviation=(comparison["cumulative_ratio"] - 1).abs())
        columns = ["node_id", "legacy_cumulative_m2", "new_cumulative_m2", "cumulative_ratio"]
        print(f"\n{name}: largest cumulative-area differences")
        print(
            worst.sort_values("deviation", ascending=False)[columns].head(10).round(2).to_string()
        )
    plot(results, nodes, mask, rivers)


if __name__ == "__main__":
    main()
