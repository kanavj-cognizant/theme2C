# 6. Refactoring Notes

Factual observations about the current implementation that matter when restructuring it. Nothing here has been changed; items are grouped as *structure*, *state*, *implicit contracts*, *defects and hazards*, and *performance*. "Hazard" means behaviour inferred by reading the code; none were reproduced by running it.

## Module import side effects

Importing some modules performs I/O, network access or heavy computation. This makes import order and network availability part of program behaviour.

| Module | Work done at import |
|---|---|
| `utils/consentsRisk.py` | If any of `determinands/wwtw/cso` parquet files is missing, calls `get_and_transform_data()` (downloads everything). Then reads parquet and builds `wwtw_join` / `cso_join` with fill values. |
| `utils/agricultureRisk.py` | Reads 3 CSV lookups, **downloads** the EA WFD operational catchments shapefile (`ENG_CATCH`). |
| `utils/urbanRisk.py` | **Downloads** the rainfall GeoJSON (`RAINFALL`), reads road-width lookup and `os_builtup.parquet` (fails if OS data not yet downloaded). |
| `get_and_transform_data.py` | Computes `DATA_DIR` as a path *relative to the current working directory* at import time. |

## State and caching

- **Module-level caches** (see [01-architecture.md](./01-architecture.md#16-runtime-state)) are keyed by `GeoDataFrame.equals(...)` on the catchments. This comparison is expensive on large frames and ignores which `RiskMap` instance or which parameter requested the data.
- **Shared cache key in `urbanRisk`**: `urban_road` and `urban_buildings` both read and write the single global `joined_catchments_before`. After `urban_road` runs against new catchments it updates the key, so a following `urban_buildings` call can see "unchanged" catchments and reuse a stale `combined_builds` (it is only rebuilt when `combined_builds is None` or the key differs).
- **Cached frames are mutated and returned**: `urban_road`/`urban_buildings` write `load` onto the cached `combined_*` frame and return that same object; callers share it.
- **Delineation cache on disk**: `catchment_delineation` always writes to the fixed folder `data/wbe/` (`raster.tif`, `riskmap.shp`, `polygons.shp`). Two `RiskMap` instances, or two processes, would overwrite each other's intermediates.
- **Graph as the data store**: every intermediate result (point info dicts, loads, per-source risks, final risk) is a node attribute on one mutable `nx.DiGraph`. `get_risk_map()` is a snapshot rebuilt from it; there is no separate result object.
- **`get_river_network(copy=False)`** returns `copy(as_view=True)`. Structure is frozen but node attribute dicts are shared, which is why `graph.add_point_risk(..., inplace=True)` can write through it.
- **Mutation of inputs**: `add_location_on_river_and_closest_edge` adds a `closestEdge` column to `rivers_split` and the intended `drop` is not assigned, so the column persists on the instance's data.

## Implicit contracts

- **Callback contracts** (not enforced or typed beyond docstrings):
  - `river_flow_func(row) -> float` (m³/s).
  - `load_method(rmap, parameter) -> GeoDataFrame` with columns `FID_poly` and `load` (g/s).
  - `risk_method(id, river_flow) -> float` for point sources; `risk_method(concentration) -> float` for diffuse.
  - `distance_scaling(distance_m) -> float`, with `0` for negative distances.
- **Identifier alignment**: watershed `VALUE - 1` is assumed to equal the positional index of the risk-map row, which is assumed to equal the node's `nodeID` / `river_index`. This holds only while pour points are written in the same order as `risk_map` rows.
- **Geometry column name**: `joined_catchments` uses geometry column `poly`, and downstream code calls `.reset_index()` and overlays on it.
- **Call order**: catchments must exist before diffuse info (done lazily by `add_diffuse_info_to_map` and `get_joined_catchments`); point/diffuse info must exist before the corresponding risk; per-source risk names must not be `"Risk"`.
- **River direction**: edges are assumed to be digitised in the direction of flow (downstream = successors).
- **Connectivity**: graph connectivity relies on exact coordinate equality of segment endpoints.
- **Key conventions**: WwTW ids are `PERMIT_EFFLUENT_OUTLET` strings and must match the determinands index; CSO risk functions look up by `Unique ID` but weir settings are joined through `ID`.
- **String-typed pollutant**: `"P"` / `"NH4"` is branched on with `if/elif` in many places (see below).

## Code structure observations

- **Pollutant branching** appears in: `consentsRisk` (`add_wwtw_risk`, `add_cso_risk`, plus separate `*_p_*` and `*_nh4_*` functions), `agricultureRisk` (both load functions), `urbanRisk` (both load functions), the notebook risk specs, and `pollutant_parameters/*`. Adding a pollutant touches all of these.
- **Duplicated code**: `agriculture_land_cover_load` and `agriculture_livestock_load` share about 45 identical lines of distance preparation; `RiskMap.add_point_risk` and `add_diffuse_risk` share the same `inplace=False` conversion block; the WwTW/CSO P and NH4 functions differ only in constants.
- **Notebook as application**: the orchestration (clipping, ordering, risk specs, parameter wiring, aggregation) lives only in `Exploration.ipynb`. There is no configuration object, CLI, or reusable pipeline function.
- **Mixed responsibilities in `get_and_transform_data.py`**: download, parsing, domain filtering, fuzzy matching, caching and land-cover assembly are all in one module.
- **Hard-coded data vintages and URLs**: EDM 2024 annual return, CROME 2023, ArcGIS replica file name, OS download endpoints.
- **Quality tooling**: no `requirements.txt` in the repository (despite the README), no tests, no package metadata. Logging is configured only in the notebook.

## Defects and hazards (observed, not fixed)

| # | Location | Observation |
|---|---|---|
| 1 | `utils/rivers.py` `find_shortest_line` | `if not idx > 0: return None` treats index `0` like "no match", so the river segment at position 0 can never be a nearest segment (NaN is also rejected, which is the intent). |
| 2 | `utils/graph.py` `add_point_risk` (`flow_avg`) | Divides by the node flow. If a node's flow is `0`, this raises `ZeroDivisionError` for Python floats (inf/NaN with NumPy floats). The WwTW/CSO risk functions also divide by the source's `river_flow`. |
| 3 | `RiskMap.catchment_to_river_load` | A catchment's load is split **equally** across the nodes mapped to it, regardless of reach length or position. |
| 4 | `consentsRisk` lookups | `wwtw_join.loc[id, col]` returns a Series if the index is not unique; the notebook's extra join on `DISCHARGE_SITE_TYPE_CODE` suggests duplicate ids can occur. |
| 5 | `merge_annual_and_consents` | The consent key `ID` is carried through `sjoin` as the right-hand index. This depends on geopandas naming the joined index column after the index name; behaviour can differ between geopandas versions. |
| 6 | `urbanRisk` shared cache key | See State and caching. |
| 7 | `get_and_transform_data.__main__` | Exceptions are logged at CRITICAL and swallowed (exit code stays 0). `get_OS_data(True)` runs again after `get_and_transform_data(True)` already triggered it. |
| 8 | `get_rivers_data` | On failure it retries with `verify=False` (TLS verification disabled). |

## Performance characteristics

- **Python-level loops over graph nodes** dominate: `add_to_graph` runs one BFS per point source (with `MAX_RISK_DISTANCE = inf` each BFS can traverse the entire downstream network, so cost is roughly sources x reach); `add_point_risk` iterates every node and every source recorded there; `add_diffuse_risk` is a topological sweep with per-node dict access.
- **Row-wise `DataFrame.apply(axis=1)`** is used in point snapping, fuzzy matching, flow calculation and `add_point_info_to_map`.
- **`map_poly_join.loc[-1] = ...; index += 1`** appends a row per node in `delineate_catchments` (quadratic behaviour on large graphs).
- **Polygon overlays** (land cover x soil x rainfall x catchments x OPCAT) are the heavy geometry steps; they are recomputed whenever the catchments object changes.
- **Network fetches without disk cache**: CROME, rainfall and EA catchments are fetched on every process start (and CROME/rainfall again on each land-cover rebuild).
- **`get_risk_map()` and `add_diffuse_info_to_map`** rebuild the node GeoDataFrame from the graph each call.
- The model grows with catchment size: node count is roughly total river length / `resolution`.

## Suggested seams (descriptive, for planning)

These are the boundaries the current code already respects, and so are natural places to cut:

1. **Ingestion** vs **model** (`get_and_transform_data` produces GeoDataFrames in EPSG:27700; the model consumes only those).
2. **`RiskMap` (graph + catchments)** vs **source models** (already decoupled via callables).
3. **Load** (`FID_poly`, `load`) vs **risk** (concentration / guideline, decay, aggregation): the diffuse path already separates them.
4. **Pollutant parameters** as data (`pollutant_parameters/*`) vs the code that branches on them.
