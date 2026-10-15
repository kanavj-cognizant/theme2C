import geopandas as gpd
import numpy as np
import shapely
from shapely.geometry import LineString
from shapely.ops import substring

from risk_map.config.models import DEFAULT_CONFIG


def split_rivers(
    rivers: gpd.GeoDataFrame,
    resolution: float = DEFAULT_CONFIG.river_resolution,
) -> gpd.GeoDataFrame:
    """Split river LineStrings into smaller segments up to resolution length.

    Preserves original bends, path length, direction, and river attributes.
    """
    if resolution <= 0:
        raise ValueError(f"resolution must be positive, got {resolution}")

    rivers_copy = rivers.copy()
    if rivers_copy.geometry.apply(
        lambda x: not isinstance(x, (LineString, shapely.MultiLineString))
    ).any():
        raise TypeError(
            "rivers GeoDataFrame must contain only LineString or MultiLineString geometries"
        )

    rivers_exploded = rivers_copy.explode(index_parts=False)

    def _split_line(line: LineString) -> list[LineString]:
        if line.length <= resolution:
            return [line]
        return [
            substring(line, start, min(start + resolution, line.length))
            for start in np.arange(0, line.length, resolution)
        ]

    # Apply splitting: explode the list of lines in pandas first before converting to GeoDataFrame
    exploded_geoms = rivers_exploded.geometry.apply(_split_line)
    result_df = rivers_exploded.drop(columns=["geometry"]).copy()
    result_df["geometry"] = exploded_geoms
    exploded_df = result_df.explode("geometry", ignore_index=True)
    return gpd.GeoDataFrame(exploded_df, geometry="geometry", crs=rivers.crs)


def snap_points_to_rivers(
    points: gpd.GeoDataFrame,
    rivers: gpd.GeoDataFrame,
    max_distance: float = DEFAULT_CONFIG.max_outfall_distance,
) -> gpd.GeoDataFrame:
    """Snap point sources to the nearest river reach within max_distance.

    Returns GeoDataFrame with original columns plus:
      - river_index: index position in the rivers GeoDataFrame (or None if beyond max distance)
      - snapped_point: Point geometry projected onto the nearest river line
      - snap_distance: Euclidean distance from point to river
    """
    if max_distance <= 0:
        raise ValueError(f"max_distance must be positive, got {max_distance}")

    points_df = points.copy()
    rivers_df = rivers.reset_index(drop=True)

    joined = gpd.sjoin_nearest(
        points_df,
        rivers_df[["geometry"]],
        max_distance=max_distance,
        how="left",
        distance_col="snap_distance",
    )

    # De-duplicate if a point is equidistant to multiple edges
    joined = joined[~joined.index.duplicated(keep="first")].sort_index()

    matched_mask = joined["index_right"].notna()
    right_indices = joined.loc[matched_mask, "index_right"].astype(int).to_numpy()

    river_indices: list[int | None] = [None] * len(joined)
    snapped_geoms = np.full(len(joined), None, dtype=object)

    if len(right_indices) > 0:
        matched_lines = rivers_df.geometry.iloc[right_indices].to_numpy()
        matched_points = joined.loc[matched_mask, "geometry"].to_numpy()

        connecting_lines = shapely.shortest_line(matched_points, matched_lines)
        snapped = shapely.get_point(connecting_lines, 1)

        matched_pos = np.where(matched_mask)[0]
        for pos, r_idx, s_geom in zip(matched_pos, right_indices, snapped, strict=True):
            river_indices[pos] = int(r_idx)
            snapped_geoms[pos] = s_geom

    joined["river_index"] = river_indices
    joined["snapped_point"] = gpd.GeoSeries(snapped_geoms, crs=rivers.crs)
    if "index_right" in joined.columns:
        joined.drop(columns=["index_right"], inplace=True)

    return joined
