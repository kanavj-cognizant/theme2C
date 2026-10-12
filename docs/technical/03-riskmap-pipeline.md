# 3. The Risk Map Pipeline

This document traces a complete run, from raw data to the final `Risk` column, and explains each stage of the `RiskMap` class and its helpers.

## 3.1 End-to-end sequence (as driven by `Exploration.ipynb`)

```mermaid
sequenceDiagram
    autonumber
    participant NB as Notebook
    participant G as get_and_transform_data
    participant RM as RiskMap
    participant U as utils (rivers, graph, catchment_delineation)
    participant S as Source modules (consents / agriculture / urban)

    NB->>G: get_and_transform_data()
    G-->>NB: cso_all, wwtw_all, determinands, rivers_all
    NB->>NB: load catchment geojson -> cmnt_mask (EPSG:27700)
    NB->>NB: clip rivers, cso, wwtw to cmnt_mask
    NB->>RM: RiskMap(rivers, id_col, resolution, river_flow_func, catchment_mask)
    RM->>U: split_rivers, river_to_graph
    NB->>RM: delineate_catchments()
    RM->>U: catchment_delineation.delineate_catchments
    NB->>RM: add_point_info_to_map(cso ...) and (wwtw ...)
    RM->>U: add_location_on_river_and_closest_edge, graph.add_to_graph
    loop each diffuse source x parameter
        NB->>RM: add_diffuse_info_to_map(info_col, parameter, load_method)
        RM->>S: load_method(rmap, parameter) -> per-polygon load (g/s)
        RM->>RM: catchment_to_river_load -> node attribute
    end
    loop each parameter (P, NH4)
        NB->>RM: add_all_risk([point & diffuse specs], risk_name="P_Risk" / "NH4_Risk")
        RM->>U: graph.add_point_risk / graph.add_diffuse_risk
        RM->>S: risk_method(id, flow) for point sources
    end
    NB->>RM: add_all_risk([simple P_Risk, simple NH4_Risk], risk_name="Risk", mean)
    NB->>RM: get_risk_map()
    RM-->>NB: GeoDataFrame of river nodes with all columns
```

The notebook parameters: `RIVER_RESOLUTION = 300` m, `MAX_OUTFALL_DIST_FROM_RIVER = 700` m, `MAX_RISK_DISTANCE = inf`, river id column `RivID`, and flow function `linear_flow(row["US_Accum"])`.

## 3.2 Data-flow summary

```mermaid
flowchart TD
    subgraph INPUT["Inputs (clipped to catchment)"]
        RV["rivers (LineStrings)"]
        CSO["CSOs (points)"]
        WW["WwTWs (points)"]
    end

    RV --> FLOW["flow = river_flow_func(row)<br/>per ORN link"]
    FLOW --> SPLIT["split_rivers(resolution)<br/>-> rivers_split segments"]
    SPLIT --> GR["river_to_graph (momepy)<br/>nx.DiGraph, node = (x, y)"]
    GR --> RMAP["risk map (node points)"]

    RMAP --> DEL["delineate_catchments<br/>(DEM + WhiteboxTools)"]
    DEL --> JC["joined_catchments<br/>map_poly_join / mapping_points"]

    CSO --> SNAP["snap to nearest river segment<br/>(max 700 m)"]
    WW --> SNAP
    SPLIT --> SNAP
    SNAP --> BFS["add_to_graph<br/>BFS downstream (optionally upstream)"]
    BFS --> NODEPT["node attr csoInfo / wwtwInfo<br/>{(id, flow): distance}"]
    GR --> BFS

    JC --> LM["load_method(rmap, parameter)<br/>agri / livestock / road / building"]
    LM --> C2R["catchment_to_river_load"]
    C2R --> NODEDF["node attr e.g. agri_P_load (g/s)"]

    NODEPT --> PR["graph.add_point_risk"]
    NODEDF --> DR["graph.add_diffuse_risk<br/>(upstream accumulation + decay)"]
    PR --> AGG["add_all_risk: sum -> P_Risk, NH4_Risk"]
    DR --> AGG
    AGG --> FIN["add_all_risk: mean -> Risk"]
    FIN --> OUT["get_risk_map()<br/>GeoDataFrame of nodes"]
```

## 3.3 The `RiskMap` class

All work is in EPSG:27700. Constructor signature:

```python
RiskMap(riversdf, id_col, resolution, river_flow_func, catchment_mask=None)
```

### Construction (`__init__`)
1. Validate `id_col`; reproject to 27700; rename `id_col` to `riverID`.
2. `rivers["flow"] = riversdf.apply(river_flow_func, axis=1)` (flow in m³/s per link).
3. `rivers_split = utils.rivers.split_rivers(rivers, resolution)`.
4. `rivers.set_index("riverID")` (the unsplit copy, now indexed by river id).
5. `construct_river_graph()` builds the DiGraph from `rivers_split`.
6. `__construct_risk_map()` converts graph nodes to a GeoDataFrame.
7. Initialise catchment fields to `None` and store `catchment_mask`.

### Key attributes (private, name-mangled `_RiskMap__*`)

| Attribute | Type | Meaning |
|---|---|---|
| `__rivers` | GeoDataFrame | Original links, indexed by `riverID`, with `flow`. |
| `__rivers_split` | GeoDataFrame | Segments of length ~`resolution`; columns `riverID`, `flow`, `geometry`. |
| `__river_network` | `nx.DiGraph` | The model. All computed values are stored as **node attributes**. |
| `__risk_map` | GeoDataFrame | Node points rebuilt from the graph on demand (a snapshot, not the source of truth). |
| `__joined_catchments` | GeoDataFrame | Merged catchment polygons, indexed by `FID_poly`, geometry column named `poly`. |
| `__map_poly_join` | DataFrame | `FID_poly`, `FID_to_join`, `river_index`: which small polygons merge into which. |
| `__mapping_points` | DataFrame | Per node: `river_index`, `geometry`, original `FID`, `poly`. |
| `__catchment_mask` | GeoDataFrame or None | Optional clip for delineated polygons. |

### Public methods

| Method | Role |
|---|---|
| `get_rivers`, `get_rivers_split`, `get_river_network`, `get_risk_map`, `get_joined_catchments` | Accessors (copies by default). `get_risk_map` rebuilds the snapshot every call; `get_joined_catchments` triggers delineation if not yet done. |
| `delineate_catchments(min_area=20000)` | Builds catchments and merge tables (3.5). |
| `add_point_info_to_map(ids, geometry, col_name_graph, max_risk_distance, max_dist_from_river, include_upstream=False)` | Maps point sources to the graph (3.6). Returns the snapped outfall points. |
| `add_diffuse_info_to_map(info_col_name, parameter, load_method)` | Maps a catchment load to graph nodes (3.7). |
| `add_point_risk(...)`, `add_diffuse_risk(...)`, `add_simple(...)` | Compute one risk layer and store it as a node attribute (or return it when `inplace=False`). |
| `add_risk(type, **kwargs)` | Dispatch to the three above by `type` in `{"point", "diffuse", "simple"}`. |
| `add_all_risk(risk_list, aggregation_method=sum, risk_name="Risk", inplace=True)` | Runs each spec in `risk_list`, then aggregates the per-spec node values into a single node attribute `risk_name`. |
| `catchment_to_river_load(load)` | Converts per-polygon load to per-node load (3.7). |

## 3.4 River graph construction

`utils/rivers.py`:

1. **`split_rivers`**: reject non-(Multi)LineStrings; `explode` MultiLineStrings; for each line, interpolate points every `resolution` metres (`np.arange(0, length, resolution)`), append the true endpoint if missing, and emit `LineString([p1, p2])` for consecutive points. Result columns: `riverID`, `flow`, `geometry`, with a fresh 0..n-1 index.
2. **`river_to_graph`**: `momepy.gdf_to_nx(rivers_split, approach="primal", directed=True, multigraph=False)`.
   - Nodes are line endpoints, keyed by `(x, y)` tuples; edge direction follows the line digitisation direction (assumed downstream).
   - Edge attributes include `riverID`, `flow` and momepy's `mm_len` (edge length in m).
   - Node attribute `flow` = max `flow` over incident edges; node attribute `riverID` = set of incident river ids.
3. **`__graph_to_gdf`**: `momepy.nx_to_gdf(graph, points=True, lines=False)`, drop `x`, `y`, `nodeID`, and normalise dict/set cells to lists. The row index of this GeoDataFrame is the node's `nodeID`, which is the identifier used by catchment mapping.

River flow model: `linear_flow(accum_len) = 1.476e-5 * accum_len` where `accum_len` is the ORN `US_Accum` (upstream accumulated length, m).

## 3.5 Catchment delineation

Purpose: assign each river node an upstream catchment polygon so area-based loads can be attributed to the river.

### Step A: hydrology (`utils/catchment_delineation.delineate_catchments`)

```mermaid
flowchart LR
    RMP["risk map nodes"] --> BB["bounds in EPSG:4326 + 0.15 deg buffer"]
    BB --> DEM["elevatr.get_elev_raster(zoom=12)<br/>cached in data/tiles"]
    DEM --> TIF["data/wbe/raster.tif"]
    TIF --> FD["fill_depressions"] --> D8["d8_pointer"] --> ACC["d8_flow_accum (cells)"] --> STR["extract_streams (threshold 1000)"]
    RMP --> SHP["data/wbe/riskmap.shp (nodes, EPSG:4326)"]
    SHP --> SNAP["jenson_snap_pour_points (0.001 deg)"]
    STR --> SNAP
    D8 --> WS["watershed(pour_points)"]
    SNAP --> WS
    WS --> POLY["raster_to_vector_polygons<br/>data/wbe/polygons.shp"]
    POLY --> RES["GeoDataFrame (VALUE, FID, geometry) in EPSG:27700"]
```

Every river node is a pour point, so there is one watershed per node. `VALUE` is the 1-based pour-point order. A module-level cache (`rmap_before`) returns the stored `polygons.shp` when the same risk map is passed again.

### Step B: merging small catchments (`RiskMap.delineate_catchments`)
1. `make_valid` the polygons; if a `catchment_mask` exists, intersect the polygons with it.
2. `value2 = VALUE - 1` aligns polygons to risk-map rows. Left-join risk-map nodes with their polygon -> `mapping_points` (`river_index`, `geometry`, `FID`, `poly`). A node with no polygon gets an empty polygon.
3. `include = polygon area > min_area` (default 20 000 m², i.e. 2 ha).
4. Starting at **outlet nodes** (no successors) and walking **upstream** level by level (BFS over predecessors):
   - an included predecessor keeps its own polygon: row `(FID_poly=p_fid, FID_to_join=p_fid, river_index=p)`;
   - a non-included predecessor adopts the downstream node's `FID` (so the merge chains through several small nodes) and is recorded as `(FID_poly=c_fid, FID_to_join=p_fid, river_index=p)`.
5. Drop rows with `FID_to_join` NaN, attach each `FID_to_join` polygon, make valid, group by `FID_poly` and `union_all` the polygons -> `joined_catchments`.

Outputs stored on the instance: `__joined_catchments` (indexed by `FID_poly`, geometry column `poly`), `__map_poly_join`, `__mapping_points`.

## 3.6 Point sources onto the graph (`add_point_info_to_map`)

```mermaid
flowchart TD
    P["points (CSO or WwTW), ids"] --> NR["gpd.sjoin_nearest to rivers_split<br/>max_distance = 700 m, first match per point"]
    NR --> SL["shortest_line(point, matched segment)<br/>-> closestPoint = end of that line"]
    NR --> CE["closestEdge = segment coords ((x0,y0),(x1,y1))<br/>flow = segment flow"]
    SL --> AG["graph.add_to_graph for each point"]
    CE --> AG
    AG --> DS["downstream: node = edge end (x1,y1)<br/>dist = |closestPoint - node|"]
    DS --> BFS1["directed_graph_bfs along successors<br/>dist += mm_len, stop at cutoff"]
    AG --> US["if include_upstream: node = edge start (x0,y0)<br/>dist = -|closestPoint - node|"]
    US --> BFS2["directed_graph_bfs along predecessors<br/>dist -= mm_len"]
    BFS1 --> ATTR["node[col_name][(id, flow)] = distance"]
    BFS2 --> ATTR
```

- The node attribute is a dict `{(source_id, river_flow_at_source): distance_m}`. Upstream nodes get negative distances; the decay functions return 0 for negative distance.
- Points with no river within `max_dist_from_river` are skipped.
- The BFS skips a node if the source `id` is already recorded there.
- Returns the `closestPoint` series; the notebook stores it as `outfallLocation` for plotting.

## 3.7 Diffuse sources onto the graph (`add_diffuse_info_to_map`)

1. Ensure catchments are delineated.
2. `catchment_load = load_method(self, parameter)`: a GeoDataFrame that **must** have `FID_poly` and `load` (g/s) columns, one row per sub-polygon.
3. `catchment_to_river_load(load)`:
   - `groupby("FID_poly").sum()` of `load`;
   - join to `map_poly_join[["river_index", "FID_poly"]]` (1 catchment : many nodes);
   - divide each catchment's load **equally** among the nodes mapped to it (`load / count`);
   - result: Series indexed by node id (`river_index`).
4. For every graph node, set `node[info_col_name]` = that node's share, or `0` if it has none; rebuild the risk map snapshot.

## 3.8 Computing risk (`add_all_risk`)

`add_all_risk(risk_list, aggregation_method, risk_name)` loops over specs (dicts of `add_risk` keyword arguments), runs each, then for every node sets `node[risk_name] = aggregation_method([node[spec.risk_name] for spec in risk_list])`.

Per-spec types:

| `type` | Calls | Reads node attribute | Writes node attribute |
|---|---|---|---|
| `"point"` | `graph.add_point_risk` | dict `{(id, flow): dist}` | `risk_name` |
| `"diffuse"` | `graph.add_diffuse_risk` | numeric load (g/s) | `risk_name` and a hidden `<info_col>_cum` on a graph *copy* only |
| `"simple"` | no-op when in place | an existing node attribute named `risk_name` | (aggregated by the parent call) |

The notebook calls `add_all_risk` three times:
1. `risk_name="P_Risk"`: sum of `cso_P_Risk`, `wwtw_P_Risk`, `agri_P_Risk`, `livestock_P_Risk`, `urban_road_P_Risk`, `urban_build_P_Risk`.
2. `risk_name="NH4_Risk"`: same six sources for NH4.
3. `risk_name="Risk"`: `np.mean` of the `simple` entries `P_Risk` and `NH4_Risk`.

Formulas are in [04-risk-calculations.md](./04-risk-calculations.md).

## 3.9 Lifecycle of a graph node's attributes

```mermaid
stateDiagram-v2
    [*] --> Structural: river_to_graph
    Structural: flow, riverID, nodeID
    Structural --> WithPointInfo: add_point_info_to_map
    WithPointInfo: + csoInfo, wwtwInfo (dict)
    WithPointInfo --> WithLoads: add_diffuse_info_to_map
    WithLoads: + agri_*_load, livestock_*_load, urban_*_load
    WithLoads --> WithSourceRisks: add_all_risk per source
    WithSourceRisks: + cso_P_Risk, wwtw_P_Risk, agri_P_Risk ...
    WithSourceRisks --> WithPollutantRisk: aggregate (sum)
    WithPollutantRisk: + P_Risk, NH4_Risk
    WithPollutantRisk --> Final: aggregate (mean)
    Final: + Risk
    Final --> [*]: get_risk_map()
```
