# Spatial Hydrology & River Geometry

This document covers the spatial discretization, point-source snapping, and flow estimation layer in [src/risk_map/spatial](../src/risk_map/spatial).

---

## 1. Design & Objectives

The spatial subsystem transforms raw vector datasets into a discretized mesh suitable for graph modeling:
- River lines are discretized into uniform segments bounded by a maximum target resolution.
- Discrete point sources (CSOs, WwTWs) are projected onto their nearest river line using spatial indexes.
- River discharge ($Q$) and flow velocity ($v$) are decoupled from configuration through an abstract protocol.

---

## 2. Module Reference

### Flow Estimation Strategy ([src/risk_map/spatial/flow.py](../src/risk_map/spatial/flow.py))
- `FlowEstimator`: Protocol defining the interface for computing reach discharge and velocity:
  - `estimate_flow(rivers)`: Returns reach discharge in m³/s as a Polars Series.
  - `estimate_flow_speed(rivers)`: Returns reach flow velocity in m/s as a float or Polars Series.
- `LinearAccumulationFlowEstimator`: Baseline implementation estimating discharge from upstream accumulated river length:
  $$Q = \text{US\_Accum} \times \text{coeff}$$
  Can be replaced by gauge assimilation, Manning equation routing, or runoff models without modifying river network topology.

### River Discretization & Point Snapping ([src/risk_map/spatial/rivers.py](../src/risk_map/spatial/rivers.py))
- `split_rivers`: Cuts lines at along-river `resolution` intervals using Shapely's `substring`, preserving bends, direction, total path length, and link attributes. Bend vertices inside a segment do not become graph nodes.
- `snap_points_to_rivers`: Projects points onto their nearest river reach within a cutoff search distance:
  - Uses GeoPandas spatial nearest joins (`sjoin_nearest`) backed by C-level R-trees.
  - Uses vectorized Shapely 2.0 primitives (`shortest_line` and `get_point`) to compute perpendicular projection coordinates on the reach.
  - Avoids Python-level row iteration.
  - Assigns `river_index`, `snapped_point`, and `snap_distance` in metres.

### Catchment Delineation ([src/risk_map/spatial/catchment.py](../src/risk_map/spatial/catchment.py))
- `delineate_catchments`: Retrieves or reuses elevation tiles, runs the WBE 2 hydrology workflow, and returns watershed polygons in the input risk-map CRS.
- Each call uses a unique temporary WBE workspace to avoid collisions between concurrent runs. Elevation tiles remain in the caller-provided tile cache.
- WBE 2's `raster_to_vector_polygons` tool requires an input raster path, so the watershed raster is written to the temporary workspace before conversion.
- `workspace_root` optionally selects the parent directory for temporary workspaces; when omitted, the system temporary directory is used.
- The existing application still calls the legacy adapter at [utils/catchment_delineation.py](../utils/catchment_delineation.py) until orchestration is migrated.

#### Pour-point coverage and identity
- In the Browney notebook run with Jenson snapping and a 1,000-cell stream threshold, 426 river nodes produced 420 WBE polygon labels. Five pairs of nodes snapped to duplicate stream cells, and one node was not moved by snapping.
- WBE therefore cannot be assumed to emit exactly one polygon per river node. The legacy positional `VALUE - 1` join tolerates missing labels as empty geometries, so the pipeline completes, but mapping rows without polygon IDs are later excluded from diffuse-load allocation.
- The refactor must keep separate identities for river nodes and unique snapped pour points. Multiple river nodes may reference one WBE watershed label; unsnapped nodes must be surfaced explicitly rather than silently treated as valid catchments.
- Grouping inputs and membership outputs must support one WBE polygon mapping to multiple river nodes. The current `merge_subcatchments_by_area` one-polygon-per-node contract is provisional and needs adaptation before integration.

### Minimum-Area Grouping ([src/risk_map/spatial/grouping.py](../src/risk_map/spatial/grouping.py))
- `merge_subcatchments_by_area`: Takes non-overlapping WBE node polygons and a downstream-directed river DAG, then merges undersized polygons while walking toward outlets. `min_area` defaults to `DEFAULT_CONFIG.min_catchment_area`.
- Under-minimum tributary groups are combined at their downstream confluence. Groups that reach the configured minimum remain distinct.
- Any final outlet remainder is merged into the nearest finalized upstream group. A connected component whose total area is below the minimum is retained and marked `below_minimum` rather than discarded.
- Returns a GeoDataFrame of unique merged polygons and a Polars DataFrame mapping `subcatchment_id` to `node_id`. The mapping's `allocation_weight` is `1 / member_count`, so each subcatchment's weights sum to one.
- The current grouping helper assumes each node has at most one downstream successor and that WBE's node polygons form a non-overlapping partition.
- Grouping is a single topological pass; the legacy implementation instead appends mapping rows to a DataFrame inside its graph traversal and tests each raw polygon independently rather than accumulating group area.

---

## 3. Testing

- [tests/unit/test_flow.py](../tests/unit/test_flow.py): Validates protocol conformity, linear accumulation calculations, and Polars return types.
- [tests/unit/test_rivers.py](../tests/unit/test_rivers.py): Validates LineString segment length bounds, exact total length conservation, and point-to-river snapping across all reach indices.
- [tests/unit/test_catchment.py](../tests/unit/test_catchment.py): Validates isolated WBE workspaces and raster-path conversion without network or DEM dependencies.
- [tests/unit/test_catchment_grouping.py](../tests/unit/test_catchment_grouping.py): Validates area growth, confluence merging, outlet remainder handling, and normalized load-allocation weights.

## 4. Implemented API Contracts

**Status:** current implementation, including provisional catchment/grouping
helpers. All six public names below are exported by `risk_map.spatial`.
`flow.py` owns discharge estimation, `rivers.py` owns segmentation/snapping,
`catchment.py` owns external hydrology execution, and `grouping.py` owns polygon
membership. No end-to-end adapter currently connects all four responsibilities.

```python
LinearAccumulationFlowEstimator(coeff: float = 1.476e-5,
                                accum_col: str = "US_Accum",
                                flow_speed: float = 0.77)
FlowEstimator.estimate_flow(rivers: gpd.GeoDataFrame) -> pl.Series
FlowEstimator.estimate_flow_speed(rivers: gpd.GeoDataFrame) -> float | pl.Series
split_rivers(rivers: gpd.GeoDataFrame,
             resolution: float = DEFAULT_CONFIG.river_resolution) -> gpd.GeoDataFrame
snap_points_to_rivers(points: gpd.GeoDataFrame, rivers: gpd.GeoDataFrame,
                      max_distance: float = DEFAULT_CONFIG.max_outfall_distance
                      ) -> gpd.GeoDataFrame
delineate_catchments(risk_map: gpd.GeoDataFrame, tile_folder: Path,
                     workspace_root: Path | None = None,
                     buffer_degrees: float = 0.15,
                     stream_threshold: float = 1000.0,
                     pour_point_snap_distance: float = 0.001) -> gpd.GeoDataFrame
merge_subcatchments_by_area(node_catchments: gpd.GeoDataFrame,
                            river_graph: nx.DiGraph,
                            min_area: float = DEFAULT_CONFIG.min_catchment_area,
                            node_id_column: str = "node_id",
                            graph_node_id_attribute: str = "nodeID"
                            ) -> tuple[gpd.GeoDataFrame, pl.DataFrame]
```

For a caller with projected metre-coordinate rivers and outfall points:

```python
from risk_map.spatial import (
    LinearAccumulationFlowEstimator,
    snap_points_to_rivers,
    split_rivers,
)

estimator = LinearAccumulationFlowEstimator()
reaches = split_rivers(rivers, resolution=300.0)
flows = estimator.estimate_flow(reaches)
matches = snap_points_to_rivers(points, reaches, max_distance=700.0)
```

### Flow and River Geometry

- The runtime-checkable `FlowEstimator` protocol checks method presence, not
  numerical validity. Series must align positionally with input rows; the graph
  consumes their NumPy values rather than aligning by IDs. The baseline returns
  a `flow` Series and a constant scalar speed, without mutating its input.
- Missing accumulation column raises `KeyError`. Coefficient, accumulated length,
  and speed are not constrained to positive finite values. With accumulated
  length in metres, the flow coefficient has units m2/s.
- `split_rivers` copies input, explodes MultiLineStrings, repeats attributes per
  piece, retains CRS, and returns a reset row index. Non-line geometries raise
  `TypeError`; nonpositive resolution raises `ValueError`. It assumes the active
  geometry column is named `geometry`. CRS, empty lines, and finite values have
  no explicit validation contract here.
- **Agreed splitting contract:** for positive-length lines in metre coordinates,
  cuts follow distance along the original path. Pieces retain original vertices
  between cuts and their direction, each length is at most `resolution`, and
  summed length matches the input within floating-point tolerance. Short and
  exactly-resolution lines remain one piece; multipart lines are split per part.
  Graph nodes remain segment endpoints, not interior bend vertices. For ordinary
  open lines this retains the previous sampling boundaries and graph size.
  This intentionally differs from the legacy chord geometry and its shortened
  lengths, so corrected decay results need not equal the old curved-line output.
- `snap_points_to_rivers` requires compatible projected CRS coordinates in metres
  but does not validate or reproject them. Nonpositive distance raises
  `ValueError`. Both inputs are left unmodified.
- Snapping returns original point columns and active point geometry plus
  `river_index` (position in the supplied rivers, not its original index label),
  `snapped_point`, and `snap_distance`. Unmatched rows have missing match fields.
  The active geometry remains the original source location.
- Ties keep the first joined row, without a stable asset-ID tie-break. Duplicate
  point index labels are collapsed and results sorted by index. `snapped_point`
  is assigned from a default-index GeoSeries, so non-default point indexes risk
  label misalignment. Current tests exercise only a unique default RangeIndex.

### Delineation Inputs, Outputs and Side Effects

- Requires a nonempty frame with a CRS and geometry column named `geometry`, and
  positive buffer, stream threshold, and snap distance; failures raise
  `ValueError`. Point geometry types and finite thresholds are not validated.
- Reprojects pour points and elevation bounds to EPSG:4326. Buffer and snap
  distance use degrees, not SI metres; stream extraction threshold counts cells.
  Elevation zoom is fixed at 12. These are explicit exceptions to the SI policy.
- Calls elevatr and Whitebox Workflows 2, may access the network, creates the tile
  directory and optional workspace parent, and writes temporary rasters/shapefiles.
  Temporary files are cleaned on exit; downloaded tile cache files are retained.
  Unique workspaces do not establish thread/process safety of the shared tile cache
  or underlying libraries.
- Missing elevation raises `RuntimeError`; missing output CRS raises `ValueError`.
  Other dependency/I/O failures propagate. Output is the Whitebox polygon frame,
  reprojected to input CRS, not clipped, repaired, grouped, or joined to node IDs.
  Attribute labels come from Whitebox rather than a normalized package schema.
- The numeric Browney observations above are recorded exploratory evidence, not
  assertions enforced by the mocked unit test. Persistent pour-point identity and
  duplicate-cell membership still need an agreed integration contract.

### Grouping Inputs and Output Schemas

- Requires a projected CRS whose axes use metres, a node-ID column, a DAG with at
  most one successor per node, and unique graph node-ID attributes. Unknown polygon
  IDs, missing graph IDs, invalid topology, or nonpositive minimum area raise
  `ValueError`. Missing polygons contribute empty geometry. Multiple rows for one
  node ID are unioned; polygons shared between different node IDs are not deduplicated.
- Assumes non-overlapping watershed zones and valid polygon inputs. Overlap is not
  checked: decisions use summed input area, while output `area` uses union area,
  so overlap can invalidate minimum-area flags. Empty-graph behavior and null IDs
  have no agreed contract.
- Inputs are not intentionally mutated. Output frames are mutable and use the
  polygon input CRS. Merged rows contain `subcatchment_id`, `area` (m2),
  `below_minimum` (bool), and `geometry`. Membership rows contain
  `subcatchment_id`, `node_id`, and `allocation_weight` (dimensionless).
  Polars identifier dtypes are inferred from supplied IDs, not fixed by schema.
- Group IDs derive from the finalizing node ID. The nearest finalized upstream
  group is chosen by graph hops, with stringified group ID breaking ties, not by
  metres. Returned row order is not a persistent identity contract.
- Allocation weights are equal per member node, including nodes without polygons;
  they are not area- or reach-length-weighted. These are proposed model choices,
  not an established legacy-equivalent diffuse allocation method.
- A topological ordering is used once, but repeated member-list copying, geometry
  unions, and output construction mean overall runtime is not guaranteed linear.

## 5. Contract Evidence and Open Decisions

| API/file | Contract or assumption | Status | Verification/test | Open question |
|---|---|---|---|---|
| Flow estimator | Protocol shape, ordered Series, baseline values | Implemented | `test_flow.py` | Invalid values, missing column, uncertain hydrology |
| Splitting | Path-preserving cuts, attributes, direction, CRS, no extra bend nodes | Agreed | Discretization, bent-line, graph-size, and multipart tests in `test_rivers.py` | Empty geometries and CRS validation |
| Snapping | Index-zero matching and distance cutoff | Implemented | `test_snap_points_to_rivers_including_index_zero` | Non-default indexes, duplicate labels, ties, CRS validation |
| Delineation | Unique cleaned workspace, raster-path conversion, output CRS | Provisional | Mocked `test_delineation_uses_isolated_wbe_workspaces_and_raster_path` | Real WBE/elevation validity, validation errors, stable pour-point mapping |
| Grouping | Area accumulation, tributaries, missing polygon, outlet remainder | Provisional | Five synthetic tests in `test_catchment_grouping.py` | Validation tests, shared polygon membership, overlap, allocation policy |
